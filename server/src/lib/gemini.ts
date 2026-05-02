import { GoogleGenerativeAI } from "@google/generative-ai";
import { GoogleAIFileManager, FileState } from "@google/generative-ai/server";
import dotenv from "dotenv";
import fs from "fs";
import path from "path";
import { logger } from "./logger";

dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || "");
const fileManager = new GoogleAIFileManager(process.env.GEMINI_API_KEY || "");

export async function extractInsights(transcript: string, requestId = 'unknown') {
  const genStart = Date.now();
  const model = genAI.getGenerativeModel({ model: "gemini-2.0-flash" });

  const prompt = `You are an expert insight extractor for voice memos.
Analyze this transcript carefully.
Return ONLY valid JSON (no markdown, no backticks) with this structure:
{
  "title": "a short, catchy title (max 8 words)",
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
}

TRANSCRIPT:
${transcript}`;

  logger.info(`[${requestId}] [Gemini] Calling generateContent`, {
    model: 'gemini-2.0-flash',
    promptLength: prompt.length,
  });

  const result = await model.generateContent(prompt);
  const genMs = Date.now() - genStart;

  // ── Usage metadata ─────────────────────────────────────────
  const usageMeta = result.response.usageMetadata;
  logger.info(`[${requestId}] [Gemini] Generation complete`, {
    durationMs: genMs,
    promptTokens: usageMeta?.promptTokenCount,
    candidateTokens: usageMeta?.candidatesTokenCount,
    totalTokens: usageMeta?.totalTokenCount,
  });

  // ── Parse JSON safely ──────────────────────────────────────
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

  // Include transcript in the final structure
  parsed.transcript = transcript;

  // Attach usage for internal logging (stripped before HTTP response)
  parsed._usage = {
    promptTokens: usageMeta?.promptTokenCount,
    candidateTokens: usageMeta?.candidatesTokenCount,
    totalTokens: usageMeta?.totalTokenCount,
  };

  return parsed;
}
