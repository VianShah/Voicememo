import express from "express";
import { createServer as createViteServer } from "vite";
import path from "path";
import fs from "fs";
import multer from "multer";
import ffmpeg from "fluent-ffmpeg";
import { GoogleGenerativeAI } from "@google/generative-ai";
import { GoogleAIFileManager, FileState } from "@google/generative-ai/server";
import { Pinecone } from "@pinecone-database/pinecone";
import dotenv from "dotenv";

dotenv.config();

const app = express();
const PORT = parseInt(process.env.PORT || "7860");

// ── Storage paths ────────────────────────────────────────────────────
// HF Spaces mounts persistent volume at /data (only at runtime, not build)
// Locally, falls back to ./uploads
const STORAGE_DIR = process.env.STORAGE_DIR || path.join(process.cwd(), "uploads");
const UPLOAD_TEMP_DIR = path.join(process.cwd(), "tmp_uploads");

// Ensure directories exist
[STORAGE_DIR, UPLOAD_TEMP_DIR].forEach((dir) => {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
});

// ── JSON body parsing (queries only — audio goes through multer) ────
app.use(express.json({ limit: "1mb" }));

// ── Multer config for audio uploads ─────────────────────────────────
const storage = multer.diskStorage({
  destination: (_req, _file, cb) => cb(null, UPLOAD_TEMP_DIR),
  filename: (_req, _file, cb) => {
    const uniqueSuffix = Date.now() + "-" + Math.round(Math.random() * 1e9);
    cb(null, `audio-${uniqueSuffix}.webm`);
  },
});
const upload = multer({
  storage,
  limits: { fileSize: 250 * 1024 * 1024 }, // 250MB — supports 45min recordings
});

// ── AI & Vector DB clients ──────────────────────────────────────────
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || "");
const fileManager = new GoogleAIFileManager(process.env.GEMINI_API_KEY || "");
const pc = new Pinecone({ apiKey: process.env.PINECONE_API_KEY || "" });
const indexName = process.env.PINECONE_INDEX || "voicememos";

// ── FFmpeg: WebM → WAV conversion ───────────────────────────────────
function convertToWav(inputPath: string): Promise<string> {
  const outputPath = inputPath.replace(/\.[^.]+$/, ".wav");
  return new Promise((resolve, reject) => {
    ffmpeg(inputPath)
      .audioChannels(1)      // Mono — reduces size, Gemini handles fine
      .audioFrequency(16000) // 16kHz — standard for speech recognition
      .toFormat("wav")
      .on("end", () => {
        console.log(`✅ [FFmpeg] Converted: ${path.basename(outputPath)}`);
        resolve(outputPath);
      })
      .on("error", (err) => {
        console.error(`❌ [FFmpeg] Conversion failed:`, err.message);
        reject(err);
      })
      .save(outputPath);
  });
}

// ── Gemini Files API: upload, poll, analyze ─────────────────────────
async function analyzeAudioViaFilesAPI(
  wavPath: string,
  requestId: string
): Promise<any> {
  const fileSizeMB = (fs.statSync(wavPath).size / 1024 / 1024).toFixed(2);
  console.log(`⏳ [Gemini] Uploading ${fileSizeMB}MB to Files API...`);

  // 1. Upload to Gemini Files API (supports up to 2GB)
  const uploadResult = await fileManager.uploadFile(wavPath, {
    mimeType: "audio/wav",
    displayName: `insight-${requestId}`,
  });

  console.log(`✅ [Gemini] Uploaded: ${uploadResult.file.name} (state: ${uploadResult.file.state})`);

  // 2. Poll until file is ACTIVE (large files can stay in PROCESSING)
  let fileState = uploadResult.file.state as string;
  let pollAttempts = 0;
  const MAX_POLLS = 30; // 30 × 3s = 90s max wait

  while (fileState === FileState.PROCESSING) {
    if (pollAttempts >= MAX_POLLS) {
      throw new Error("Gemini file processing timed out after 90s");
    }
    await new Promise((r) => setTimeout(r, 3000));
    const updatedFile = await fileManager.getFile(uploadResult.file.name);
    fileState = updatedFile.state as string;
    pollAttempts++;
    console.log(`🔄 [Gemini] Polling... attempt ${pollAttempts}, state: ${fileState}`);
  }

  if (fileState === FileState.FAILED) {
    throw new Error("Gemini File API processing FAILED");
  }

  console.log(`✅ [Gemini] File is ACTIVE — starting analysis...`);

  // 3. Analyze with Gemini
  const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });

  const prompt = `You are an expert insight extractor for voice memos.
Analyze this audio recording carefully.
Return ONLY valid JSON (no markdown, no backticks) with this structure:
{
  "title": "a short, catchy title (max 8 words)",
  "transcript": "verbatim full transcript (supports English, Hindi, Gujarati)",
  "summary": "2-3 sentence summary of what was discussed",
  "mood": "calm|energetic|reflective",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "highlights": [
    {
      "text": "verbatim quote that was impactful",
      "tag": "#Realization|#ActionItem|#Memory",
      "startTime": 0,
      "endTime": 15
    }
  ]
}`;

  const result = await model.generateContent([
    {
      fileData: {
        mimeType: uploadResult.file.mimeType,
        fileUri: uploadResult.file.uri,
      },
    },
    { text: prompt },
  ]);

  const usage = result.response.usageMetadata;
  console.log(`✅ [Gemini] Analysis complete (tokens: ${usage?.totalTokenCount})`);

  // 4. Parse JSON safely
  let raw = result.response.text().trim();
  raw = raw.replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```\s*$/i, "").trim();

  let parsed: any;
  try {
    parsed = JSON.parse(raw);
  } catch {
    console.error(`❌ [Gemini] JSON parse failed. Raw output:`, raw.slice(0, 300));
    throw new Error("Gemini returned malformed JSON");
  }

  // Ensure highlights always have an id
  if (parsed.highlights) {
    parsed.highlights = parsed.highlights.map((h: any, i: number) => ({
      id: h.id ?? `h${i + 1}`,
      ...h,
    }));
  }

  // 5. Cleanup file from Files API
  try {
    await fileManager.deleteFile(uploadResult.file.name);
    console.log(`🗑 [Gemini] Remote file deleted from Files API`);
  } catch {
    console.warn(`⚠ [Gemini] Could not delete remote file (non-fatal)`);
  }

  return parsed;
}

// ── Transcript chunking ─────────────────────────────────────────────
function chunkTranscript(text: string, chunkSize = 500, overlap = 50): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const chunks: string[] = [];
  let i = 0;
  while (i < words.length) {
    chunks.push(words.slice(i, i + chunkSize).join(" "));
    i += chunkSize - overlap;
    if (i >= words.length) break;
  }
  return chunks;
}

// ── Pinecone: batch embed + upsert ──────────────────────────────────
async function embedAndUpsert(
  insightId: string,
  transcript: string,
  metadata: Record<string, string | number>,
  requestId: string
): Promise<void> {
  const chunks = chunkTranscript(transcript);
  if (chunks.length === 0) {
    console.warn(`⚠ [${requestId}] No chunks to embed — empty transcript`);
    return;
  }

  console.log(`⏳ [Pinecone] Embedding ${chunks.length} chunk(s) in a single batch call...`);

  // BATCH EMBED: one API call for ALL chunks (instead of N sequential calls)
  const batchStart = Date.now();
  const result = await pc.inference.embed({
    model: "multilingual-e5-large",
    inputs: chunks,
    parameters: { inputType: "passage" },
  });
  const batchMs = Date.now() - batchStart;

  console.log(`✅ [Pinecone] Batch embedding done in ${batchMs}ms (${chunks.length} chunks)`);

  // Build vectors from batch results
  const vectors = result.data.map((embedding: any, i: number) => ({
    id: `${insightId}-c${i}`,
    values: Array.from(embedding.values) as number[],
    metadata: {
      ...metadata,
      text: chunks[i]!.slice(0, 1000), // Enforce Pinecone 40KB metadata limit
      insightId,
      chunkIndex: i,
    },
  }));

  // Batch upsert to Pinecone
  const index = pc.index(indexName);
  const BATCH_SIZE = 100;
  for (let b = 0; b < vectors.length; b += BATCH_SIZE) {
    const batch = vectors.slice(b, b + BATCH_SIZE);
    await index.upsert(batch as any);
    console.log(`✅ [Pinecone] Upserted batch ${Math.floor(b / BATCH_SIZE) + 1} (${batch.length} vectors)`);
  }

  console.log(`🎉 [Pinecone] Upsert complete for insight ${insightId}`);
}

// ══════════════════════════════════════════════════════════════════════
// API ROUTES
// ══════════════════════════════════════════════════════════════════════

// ── POST /api/analyze — multipart audio upload + full pipeline ──────
app.post("/api/analyze", upload.single("audio"), async (req, res) => {
  const requestId = `req-${Date.now()}`;
  const pipelineStart = Date.now();

  console.log(`\n▶ [${requestId}] Upload received:`, {
    size: req.file?.size,
    mimetype: req.file?.mimetype,
    path: req.file?.path,
  });

  if (!req.file) {
    res.status(400).json({ error: "No audio file uploaded" });
    return;
  }

  try {
    // ── STEP 1: FFmpeg conversion ─────────────────────────────────
    const step1Start = Date.now();
    console.log(`[${requestId}] [Step 1/3] FFmpeg: converting to WAV...`);
    const wavPath = await convertToWav(req.file.path);
    const step1Ms = Date.now() - step1Start;
    console.log(`[${requestId}] [Step 1/3] ✓ FFmpeg done (${step1Ms}ms)`);

    // ── STEP 2: Gemini Files API analysis ─────────────────────────
    const step2Start = Date.now();
    console.log(`[${requestId}] [Step 2/3] Gemini: analyzing audio...`);
    const analysis = await analyzeAudioViaFilesAPI(wavPath, requestId);
    const step2Ms = Date.now() - step2Start;
    console.log(`[${requestId}] [Step 2/3] ✓ Gemini done (${step2Ms}ms)`);

    // ── STEP 3: Pinecone batch embed + upsert ─────────────────────
    const step3Start = Date.now();
    const insightId = `ins-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`;
    console.log(`[${requestId}] [Step 3/3] Pinecone: batch embedding...`);

    // Build audio URL for persistent storage
    const audioFilename = `${insightId}.wav`;
    const audioStoragePath = path.join(STORAGE_DIR, audioFilename);
    const audioUrl = `/api/recordings/${audioFilename}`;

    await embedAndUpsert(
      insightId,
      analysis.transcript,
      {
        title: analysis.title,
        summary: analysis.summary,
        tags: (analysis.tags as string[]).join(","),
        audioUrl,
        timestamp: Date.now(),
      },
      requestId
    );
    const step3Ms = Date.now() - step3Start;
    console.log(`[${requestId}] [Step 3/3] ✓ Pinecone done (${step3Ms}ms)`);

    // ── Save audio to persistent storage ──────────────────────────
    try {
      fs.copyFileSync(wavPath, audioStoragePath);
      console.log(`💾 [${requestId}] Audio saved to ${audioStoragePath}`);
    } catch (storageErr) {
      console.warn(`⚠ [${requestId}] Failed to save audio to persistent storage:`, storageErr);
    }

    // ── Cleanup temp files ────────────────────────────────────────
    try {
      fs.unlinkSync(req.file.path);
      fs.unlinkSync(wavPath);
      console.log(`🗑 [${requestId}] Temp files cleaned up`);
    } catch {
      console.warn(`⚠ [${requestId}] Temp file cleanup failed (non-fatal)`);
    }

    const totalMs = Date.now() - pipelineStart;
    console.log(`\n✅ [${requestId}] Pipeline complete in ${totalMs}ms`, {
      ffmpegMs: step1Ms,
      geminiMs: step2Ms,
      pineconeMs: step3Ms,
    });

    res.json({
      ...analysis,
      id: insightId,
      audioUrl,
    });
  } catch (error: any) {
    console.error(`❌ [${requestId}] Pipeline failed:`, error?.message);
    // Cleanup temp file on error
    try {
      if (req.file?.path) fs.unlinkSync(req.file.path);
    } catch {}
    res.status(500).json({ error: "Failed to process audio", detail: error?.message });
  }
});

// ── POST /api/query — RAG search ────────────────────────────────────
app.post("/api/query", async (req, res) => {
  try {
    const { query } = req.body;

    if (!query || typeof query !== "string") {
      res.status(400).json({ error: "Query must be a non-empty string" });
      return;
    }

    // 1. Get embedding for query
    console.log(`🔎 [Query] Embedding query: "${query.slice(0, 80)}..."`);
    const queryEmbedding = await pc.inference.embed({
      model: "multilingual-e5-large",
      inputs: [query],
      parameters: { inputType: "query" },
    });

    const queryVector = Array.from((queryEmbedding.data[0] as any).values) as number[];

    // 2. Search Pinecone
    const index = pc.index(indexName);
    const queryResponse = await index.query({
      vector: queryVector,
      topK: 5,
      includeMetadata: true,
    });

    // Filter low-confidence matches
    const relevantMatches = (queryResponse.matches ?? []).filter(
      (m) => (m.score ?? 0) > 0.5
    );

    if (relevantMatches.length === 0) {
      res.json({ answer: "I couldn't find anything relevant in your recordings for that question." });
      return;
    }

    const context = relevantMatches
      .map(
        (m) =>
          `[Insight: ${m.metadata?.title ?? "Untitled"}]\n"${m.metadata?.text ?? ""}"`
      )
      .join("\n\n---\n\n");

    // 3. Generate answer with Gemini
    const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });
    const prompt = `You are a personal AI assistant for "The Insight Recorder" app.
Answer the user's question using ONLY the transcript excerpts provided below.
If the answer isn't in the excerpts, say you don't have that in their recordings.
Be concise and direct.

CONTEXT FROM RECORDINGS:
${context}

USER QUESTION: ${query}`;

    const result = await model.generateContent(prompt);
    res.json({ answer: result.response.text() });
  } catch (error: any) {
    console.error("❌ Query error:", error?.message);
    res.status(500).json({ error: "Failed to query insights", detail: error?.message });
  }
});

// ── GET /api/recordings — list all stored recordings ────────────────
app.get("/api/recordings", (_req, res) => {
  try {
    if (!fs.existsSync(STORAGE_DIR)) {
      res.json([]);
      return;
    }
    const files = fs
      .readdirSync(STORAGE_DIR)
      .filter((f) => f.endsWith(".wav"))
      .map((f) => {
        const stat = fs.statSync(path.join(STORAGE_DIR, f));
        return {
          id: f.replace(".wav", ""),
          filename: f,
          url: `/api/recordings/${f}`,
          sizeMB: (stat.size / 1024 / 1024).toFixed(2),
          savedAt: stat.mtime.toISOString(),
        };
      })
      .sort((a, b) => new Date(b.savedAt).getTime() - new Date(a.savedAt).getTime());

    res.json(files);
  } catch (error: any) {
    console.error("❌ Recordings list error:", error?.message);
    res.status(500).json({ error: "Failed to list recordings" });
  }
});

// ── GET /api/recordings/:filename — serve audio files ───────────────
app.use("/api/recordings", express.static(STORAGE_DIR, {
  setHeaders: (res) => {
    res.setHeader("Content-Type", "audio/wav");
    res.setHeader("Accept-Ranges", "bytes");
  },
}));

// ── Health check ────────────────────────────────────────────────────
app.get("/api/health", (_req, res) => {
  const mem = process.memoryUsage();
  const recordingCount = fs.existsSync(STORAGE_DIR)
    ? fs.readdirSync(STORAGE_DIR).filter((f) => f.endsWith(".wav")).length
    : 0;

  res.json({
    status: "ok",
    uptime: process.uptime().toFixed(1) + "s",
    storagePath: STORAGE_DIR,
    recordingCount,
    memory: {
      heapUsedMB: (mem.heapUsed / 1024 / 1024).toFixed(1),
      rssMB: (mem.rss / 1024 / 1024).toFixed(1),
    },
  });
});

// ══════════════════════════════════════════════════════════════════════
// SERVER STARTUP
// ══════════════════════════════════════════════════════════════════════
async function startServer() {
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (_req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`\n🚀 Server running on http://localhost:${PORT}`);
    console.log(`📁 Audio storage: ${STORAGE_DIR}`);
    console.log(`🔑 Gemini key: ${process.env.GEMINI_API_KEY ? "set" : "MISSING"}`);
    console.log(`🔑 Pinecone key: ${process.env.PINECONE_API_KEY ? "set" : "MISSING"}`);
    console.log(`📦 Pinecone index: ${indexName}\n`);
  });
}

startServer();
