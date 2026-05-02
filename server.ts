import express from "express";
import { createServer as createViteServer } from "vite";
import path from "path";
import fs from "fs";
import multer from "multer";
import ffmpeg from "fluent-ffmpeg";
import { GoogleGenerativeAI } from "@google/generative-ai";
import { Pinecone } from "@pinecone-database/pinecone";
import dotenv from "dotenv";

dotenv.config();

const app = express();
const PORT = parseInt(process.env.PORT || "7860");

// ── Job Tracker for Async Processing ────────────────────────────────
interface Job {
  id: string;
  status: "uploading" | "converting" | "transcribing" | "analyzing" | "completed" | "failed";
  progress: number;
  result?: any;
  error?: string;
}
const jobs = new Map<string, Job>();

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

interface WhisperWord {
  word: string;
  start: number;
  end: number;
}

interface WhisperSegment {
  text: string;
  start: number;
  end: number;
  words?: WhisperWord[];
}

interface WhisperResponse {
  text: string;
  segments: WhisperSegment[];
  words?: WhisperWord[];
  language?: string;
}

const WHISPER_URL = process.env.WHISPER_URL || "http://127.0.0.1:9000";

async function transcribeWithWhisper(wavPath: string): Promise<WhisperResponse> {
  const formData = new FormData();
  const fileBuffer = fs.readFileSync(wavPath);
  formData.append("file", new Blob([fileBuffer], { type: "audio/wav" }), "audio.wav");
  formData.append("model", "whisper-1");
  formData.append("response_format", "verbose_json");

  console.log(`⏳ [Whisper] Uploading to local Whisper STT...`);
  const response = await fetch(`${WHISPER_URL}/v1/audio/transcriptions`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Whisper STT failed: ${response.status} ${await response.text()}`);
  }

  return response.json();
}

function resolveHighlightTimestamps(highlights: any[], wordTimestamps: WhisperWord[]): any[] {
  if (!wordTimestamps || wordTimestamps.length === 0) {
    return highlights.map((h, i) => ({ id: h.id ?? `h${i + 1}`, ...h }));
  }

  return highlights.map((h, i) => {
    const highlightWords = h.text.toLowerCase().replace(/[^\w\s]/g, "").split(/\s+/).filter(Boolean);
    if (highlightWords.length === 0) return { id: `h${i + 1}`, ...h };
    
    const firstWord = highlightWords[0];
    
    let startIdx = -1;
    for (let j = 0; j < wordTimestamps.length; j++) {
      const cleanW = wordTimestamps[j].word.toLowerCase().replace(/[^\w\s]/g, "");
      if (cleanW === firstWord || cleanW.includes(firstWord) || firstWord.includes(cleanW)) {
        
        let matchCount = 1;
        let k = 1;
        while (k < highlightWords.length && j + k < wordTimestamps.length) {
          const w1 = highlightWords[k];
          const w2 = wordTimestamps[j + k].word.toLowerCase().replace(/[^\w\s]/g, "");
          if (w2 === w1 || w2.includes(w1) || w1.includes(w2)) {
            matchCount++;
          }
          k++;
        }
        
        // If >= 50% words matched in sequence, accept it
        if (matchCount / highlightWords.length >= 0.5) {
          startIdx = j;
          break;
        }
      }
    }

    if (startIdx >= 0) {
      const endIdx = Math.min(startIdx + highlightWords.length - 1, wordTimestamps.length - 1);
      return {
        id: `h${i + 1}`,
        text: h.text,
        tag: h.tag,
        startTime: wordTimestamps[startIdx].start,
        endTime: wordTimestamps[endIdx].end,
      };
    }

    return { id: `h${i + 1}`, ...h };
  });
}

async function analyzeTranscriptWithGemini(
  transcript: string,
  wordTimestamps: WhisperWord[],
  requestId: string
): Promise<any> {
  const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });

  const prompt = `You are an expert insight extractor for voice memos.
Analyze this transcript carefully.

TRANSCRIPT:
${transcript}

WORD TIMESTAMPS (for reference — use these to select precise highlight boundaries):
${JSON.stringify((wordTimestamps || []).slice(0, 500))}

Return ONLY valid JSON (no markdown, no backticks) with this structure:
{
  "title": "a short, catchy title (max 8 words)",
  "summary": "2-3 sentence summary of what was discussed",
  "mood": "calm|energetic|reflective",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "highlights": [
    {
      "text": "verbatim quote from the transcript (must match exactly)",
      "tag": "#Realization|#ActionItem|#Memory",
      "startTime": 0,
      "endTime": 10
    }
  ]
}

CRITICAL RULES:
1. The "text" in highlights MUST be a verbatim substring of the transcript.
2. The startTime and endTime SHOULD be precise based on the word timestamps if available.
3. Select the 3 most impactful segments as highlights.
4. Each highlight should be approximately 10-20 seconds of speech.`;

  const result = await model.generateContent(prompt);

  let raw = result.response.text().trim();
  raw = raw.replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```\s*$/i, "").trim();

  let parsed: any;
  try {
    parsed = JSON.parse(raw);
  } catch {
    console.error(`❌ [Gemini] JSON parse failed. Raw output:`, raw.slice(0, 300));
    throw new Error("Gemini returned malformed JSON");
  }

  if (parsed.highlights) {
    parsed.highlights = resolveHighlightTimestamps(parsed.highlights, wordTimestamps || []);
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

// ── POST /api/analyze — async multipart audio upload ───────────────
app.post("/api/analyze", upload.single("audio"), async (req, res) => {
  const jobId = `job-${Date.now()}`;
  const requestId = jobId;
  
  if (!req.file) {
    res.status(400).json({ error: "No audio file uploaded" });
    return;
  }

  // Initialize Job
  const job: Job = { id: jobId, status: "converting", progress: 10 };
  jobs.set(jobId, job);

  // Return Job ID immediately to prevent browser timeout
  res.json({ jobId });

  // EXECUTE PIPELINE IN BACKGROUND
  (async () => {
    try {
      const wavPath = await convertToWav(req.file!.path);
      job.status = "transcribing";
      job.progress = 30;
      
      const whisperResult = await transcribeWithWhisper(wavPath);
      job.status = "analyzing";
      job.progress = 60;

      const analysis = await analyzeTranscriptWithGemini(
        whisperResult.text, 
        whisperResult.words || [], 
        requestId
      );
      analysis.transcript = whisperResult.text;
      
      const insightId = `ins-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`;
      job.status = "analyzing"; // Finalizing DB
      job.progress = 85;

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

      fs.copyFileSync(wavPath, audioStoragePath);
      
      // Cleanup
      try {
        fs.unlinkSync(req.file!.path);
        fs.unlinkSync(wavPath);
      } catch {}

      // Complete
      job.status = "completed";
      job.progress = 100;
      job.result = { ...analysis, id: insightId, audioUrl };

    } catch (error: any) {
      console.error(`❌ [${jobId}] Job failed:`, error?.message);
      job.status = "failed";
      job.error = error?.message || "Internal processing error";
      try { if (req.file?.path) fs.unlinkSync(req.file.path); } catch {}
    }
  })();
});

// ── GET /api/job-progress/:id — Polling endpoint ───────────────────
app.get("/api/job-progress/:id", (req, res) => {
  const job = jobs.get(req.params.id);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  res.json(job);
  
  // Cleanup completed/failed jobs from memory after they are fetched
  if (job.status === "completed" || job.status === "failed") {
    setTimeout(() => jobs.delete(req.params.id), 10000); 
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
