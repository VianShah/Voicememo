import express from 'express';
import { queryIndex } from '../lib/pinecone';
import { GoogleGenerativeAI } from "@google/generative-ai";
import dotenv from 'dotenv';
import { logger } from '../lib/logger';

dotenv.config();

const router = express.Router();
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || "");

router.post('/', async (req, res): Promise<void> => {
  const requestId = `qry-${Date.now()}`;
  const queryStart = Date.now();
  const { query } = req.body;

  logger.info(`[${requestId}] ▶ Query received`, { query: query?.slice(0, 100) });

  if (!query || typeof query !== 'string') {
    res.status(400).json({ error: 'Query must be a non-empty string' });
    return;
  }

  try {
    // ── Step 1: Embed + retrieve from Pinecone ──────────────────
    const step1Start = Date.now();
    const queryResponse = await queryIndex(query, requestId);
    const step1Ms = Date.now() - step1Start;

    logger.info(`[${requestId}] [Query] Pinecone retrieval`, {
      durationMs: step1Ms,
      matches: queryResponse.matches?.length,
    });

    // Bug fix: filter low-confidence matches (score < 0.7 is likely hallucination territory)
    const relevantMatches = (queryResponse.matches ?? []).filter(m => (m.score ?? 0) > 0.5);

    if (relevantMatches.length === 0) {
      logger.warn(`[${requestId}] [Query] No relevant matches found in Pinecone`);
      res.json({ answer: "I couldn't find anything relevant in your recordings for that question." });
      return;
    }

    const context = relevantMatches.map(m =>
      `[Insight: ${m.metadata?.title ?? 'Untitled'}]\n"${m.metadata?.text ?? ''}"`
    ).join('\n\n---\n\n');

    // ── Step 2: Generate answer via Gemini ──────────────────────
    const step2Start = Date.now();
    const model = genAI.getGenerativeModel({ model: "gemini-2.0-flash" });

    const prompt = `You are a personal AI assistant for "The Insight Recorder" app.
Answer the user's question using ONLY the transcript excerpts provided below.
If the answer isn't in the excerpts, say you don't have that in their recordings.
Be concise and direct.

CONTEXT FROM RECORDINGS:
${context}

USER QUESTION: ${query}`;

    logger.info(`[${requestId}] [Query] Calling Gemini for answer`, {
      contextChunks: relevantMatches.length,
      promptLength: prompt.length,
    });

    const result = await model.generateContent(prompt);
    const step2Ms = Date.now() - step2Start;
    const usage = result.response.usageMetadata;

    logger.info(`[${requestId}] [Query] ✅ Answer generated`, {
      durationMs: step2Ms,
      totalMs: Date.now() - queryStart,
      promptTokens: usage?.promptTokenCount,
      candidateTokens: usage?.candidatesTokenCount,
      totalTokens: usage?.totalTokenCount,
    });

    res.json({ answer: result.response.text() });

  } catch (error: any) {
    logger.error(`[${requestId}] ❌ Query failed`, {
      totalMs: Date.now() - queryStart,
      error: error?.message,
    });
    res.status(500).json({ error: 'Failed to process query', detail: error?.message });
  }
});

export default router;
