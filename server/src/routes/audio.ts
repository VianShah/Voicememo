import express from 'express';
import multer from 'multer';
import path from 'path';
import fs from 'fs';
import { convertWebmToWav } from '../lib/ffmpeg';
import { analyzeAudio } from '../lib/gemini';
import { upsertInsightToIndex } from '../lib/pinecone';
import { logger } from '../lib/logger';

const router = express.Router();

// Ensure uploads directory exists
const uploadDir = path.resolve('uploads');
if (!fs.existsSync(uploadDir)) {
  fs.mkdirSync(uploadDir, { recursive: true });
}

const storage = multer.diskStorage({
  destination: (req, file, cb) => {
    cb(null, uploadDir);
  },
  filename: (req, file, cb) => {
    const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1E9);
    // Bug fix: always save as .webm regardless of what the browser sends as originalname
    cb(null, `audio-${uniqueSuffix}.webm`);
  }
});

// Bug fix: add file size limit (250MB for 45-min audio)
const upload = multer({
  storage,
  limits: { fileSize: 250 * 1024 * 1024 }
});

router.post('/upload', upload.single('audio'), async (req, res): Promise<void> => {
  const requestId = `req-${Date.now()}`;
  const pipelineStart = Date.now();

  logger.info(`[${requestId}] ▶ Upload received`, {
    fieldname: req.file?.fieldname,
    size: req.file?.size,
    mimetype: req.file?.mimetype,
    path: req.file?.path,
  });

  if (!req.file) {
    res.status(400).json({ error: 'No audio file uploaded' });
    return;
  }

  try {
    // ── STEP 1: FFmpeg conversion ──────────────────────────────────
    const step1Start = Date.now();
    logger.info(`[${requestId}] [Step 1/3] FFmpeg: converting WebM → WAV`, {
      input: req.file.path,
      inputSizeMB: (req.file.size / 1024 / 1024).toFixed(2),
    });

    const wavPath = await convertWebmToWav(req.file.path);
    const step1Ms = Date.now() - step1Start;
    const wavStat = fs.statSync(wavPath);

    logger.info(`[${requestId}] [Step 1/3] ✓ FFmpeg done`, {
      durationMs: step1Ms,
      outputSizeMB: (wavStat.size / 1024 / 1024).toFixed(2),
      output: wavPath,
    });

    // ── STEP 2: Gemini Files API ──────────────────────────────────
    const step2Start = Date.now();
    logger.info(`[${requestId}] [Step 2/3] Gemini: uploading to Files API + analyzing`);

    const analysis = await analyzeAudio(wavPath, 'audio/wav', requestId);
    const step2Ms = Date.now() - step2Start;

    logger.info(`[${requestId}] [Step 2/3] ✓ Gemini done`, {
      durationMs: step2Ms,
      title: analysis.title,
      transcriptWords: analysis.transcript?.split(' ').length,
      highlightCount: analysis.highlights?.length,
      tokensUsed: analysis._usage ?? 'unavailable',
    });

    // ── STEP 3: Pinecone indexing ──────────────────────────────────
    const step3Start = Date.now();
    const insightId = `ins-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`;

    logger.info(`[${requestId}] [Step 3/3] Pinecone: chunking + upserting`, {
      insightId,
      transcriptLength: analysis.transcript?.length,
    });

    await upsertInsightToIndex(insightId, analysis.transcript, {
      title: analysis.title,
      summary: analysis.summary,
      // Bug fix: Pinecone metadata values must be strings/numbers, not arrays
      tags: (analysis.tags as string[]).join(','),
    }, requestId);
    const step3Ms = Date.now() - step3Start;

    logger.info(`[${requestId}] [Step 3/3] ✓ Pinecone done`, {
      durationMs: step3Ms,
    });

    // ── Cleanup temp files ──────────────────────────────────────────
    try {
      fs.unlinkSync(req.file.path);
      fs.unlinkSync(wavPath);
      logger.info(`[${requestId}] 🗑 Temp files cleaned up`);
    } catch (cleanupErr) {
      logger.warn(`[${requestId}] ⚠ Temp file cleanup failed (non-fatal)`, { cleanupErr });
    }

    const totalMs = Date.now() - pipelineStart;
    logger.info(`[${requestId}] ✅ Pipeline complete`, {
      totalMs,
      insightId,
      breakdown: { ffmpegMs: step1Ms, geminiMs: step2Ms, pineconeMs: step3Ms },
    });

    // Bug fix: clean _usage from public response
    const { _usage, ...publicAnalysis } = analysis;

    res.json({
      ...publicAnalysis,
      id: insightId,
    });

  } catch (error: any) {
    const totalMs = Date.now() - pipelineStart;
    logger.error(`[${requestId}] ❌ Pipeline failed`, {
      totalMs,
      error: error?.message,
      stack: error?.stack,
    });
    res.status(500).json({ error: 'Failed to process audio', detail: error?.message });
  }
});

export default router;
