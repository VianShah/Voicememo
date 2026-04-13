import { GoogleGenerativeAI } from "@google/generative-ai";
import { GoogleAIFileManager, FileState } from "@google/generative-ai/server";
import dotenv from "dotenv";
import fs from "fs";
import path from "path";
import { logger } from "./logger";

dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || "");
const fileManager = new GoogleAIFileManager(process.env.GEMINI_API_KEY || "");

export async function analyzeAudio(filePath: string, mimeType: string, requestId = 'unknown') {
  // ── 1. Upload to Gemini Files API ─────────────────────────────
  const uploadStart = Date.now();
  const fileSizeMB = (fs.statSync(filePath).size / 1024 / 1024).toFixed(2);

  logger.info(`[${requestId}] [Gemini] Uploading file to Files API`, {
    filePath,
    fileSizeMB,
    mimeType,
  });

  const uploadResult = await fileManager.uploadFile(filePath, {
    mimeType,
    displayName: `insight-${requestId}`,
  });

  const uploadMs = Date.now() - uploadStart;
  logger.info(`[${requestId}] [Gemini] File uploaded`, {
    durationMs: uploadMs,
    fileUri: uploadResult.file.uri,
    fileName: uploadResult.file.name,
    state: uploadResult.file.state,
  });

  // ── 2. Poll until file is ACTIVE ───────────────────────────────
  // Bug fix: Large files can stay in PROCESSING state — must poll before using
  let fileState = uploadResult.file.state as string;
  let pollAttempts = 0;
  const MAX_POLLS = 20;

  while (fileState === FileState.PROCESSING) {
    if (pollAttempts >= MAX_POLLS) {
      throw new Error('Gemini file processing timed out after 60s');
    }
    await new Promise(r => setTimeout(r, 3000)); // 3s intervals
    const updatedFile = await fileManager.getFile(uploadResult.file.name);
    fileState = updatedFile.state as string;
    pollAttempts++;
    logger.debug(`[${requestId}] [Gemini] Polling file state`, {
      attempt: pollAttempts,
      state: fileState,
    });
  }

  if (fileState === FileState.FAILED) {
    throw new Error('Gemini File API processing FAILED');
  }

  logger.info(`[${requestId}] [Gemini] File is ACTIVE — starting generation`, {
    pollAttempts,
  });

  // ── 3. Run the analysis ────────────────────────────────────────
  const genStart = Date.now();
  const model = genAI.getGenerativeModel({ model: "gemini-2.0-flash" });

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
      "id": "h1",
      "text": "verbatim quote that was impactful",
      "tag": "#Realization|#ActionItem|#Memory",
      "startTime": 0,
      "endTime": 15
    }
  ]
}`;

  logger.info(`[${requestId}] [Gemini] Calling generateContent`, {
    model: 'gemini-2.0-flash',
    promptLength: prompt.length,
  });

  const result = await model.generateContent([
    {
      fileData: {
        mimeType: uploadResult.file.mimeType,
        fileUri: uploadResult.file.uri,
      },
    },
    { text: prompt },
  ]);

  const genMs = Date.now() - genStart;

  // ── 4. Usage metadata ─────────────────────────────────────────
  const usageMeta = result.response.usageMetadata;
  logger.info(`[${requestId}] [Gemini] Generation complete`, {
    durationMs: genMs,
    promptTokens: usageMeta?.promptTokenCount,
    candidateTokens: usageMeta?.candidatesTokenCount,
    totalTokens: usageMeta?.totalTokenCount,
  });

  // ── 5. Parse JSON safely ──────────────────────────────────────
  let raw = result.response.text().trim();
  // Strip any accidental markdown fences Gemini may add
  raw = raw.replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/```\s*$/i, '').trim();

  let parsed: any;
  try {
    parsed = JSON.parse(raw);
  } catch (parseErr) {
    logger.error(`[${requestId}] [Gemini] JSON parse failed`, { raw: raw.slice(0, 300) });
    throw new Error('Gemini returned malformed JSON');
  }

  // Bug fix: ensure highlights always have an id
  if (parsed.highlights) {
    parsed.highlights = parsed.highlights.map((h: any, i: number) => ({
      id: h.id ?? `h${i + 1}`,
      ...h,
    }));
  }

  // Attach usage for internal logging (stripped before HTTP response)
  parsed._usage = {
    promptTokens: usageMeta?.promptTokenCount,
    candidateTokens: usageMeta?.candidatesTokenCount,
    totalTokens: usageMeta?.totalTokenCount,
  };

  // ── 6. Cleanup file from Files API ───────────────────────────
  try {
    await fileManager.deleteFile(uploadResult.file.name);
    logger.info(`[${requestId}] [Gemini] Remote file deleted from Files API`);
  } catch (e) {
    logger.warn(`[${requestId}] [Gemini] Could not delete remote file (non-fatal)`);
  }

  return parsed;
}
