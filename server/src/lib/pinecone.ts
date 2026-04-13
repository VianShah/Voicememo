import { Pinecone } from '@pinecone-database/pinecone';
import { GoogleGenerativeAI } from "@google/generative-ai";
import path from 'path';
import dotenv from 'dotenv';
import { logger } from './logger';

dotenv.config({ path: path.resolve(__dirname, '../../../.env') });

const indexName = process.env.PINECONE_INDEX || "insights";

/**
 * PINECONE INFERENCE CONFIG
 * Model: multilingual-e5-large
 * Dimensions: 1024
 * Support: English, Hindi, Gujarati (Native)
 */
const INFERENCE_MODEL = 'multilingual-e5-large';

let _pc: Pinecone | null = null;
function getPinecone() {
  if (!_pc) {
    if (!process.env.PINECONE_API_KEY) throw new Error('PINECONE_API_KEY is not set');
    _pc = new Pinecone({ apiKey: process.env.PINECONE_API_KEY });
  }
  return _pc;
}

/**
 * Pinecone Inference API for embeddings.
 * Much faster for RAG as it eliminates the round-trip to external LLM providers.
 */
async function getEmbedding(text: string, type: 'passage' | 'query' = 'passage'): Promise<number[]> {
  const pc = getPinecone();
  const start = Date.now();
  
  console.log(`⏳ [Pinecone] Requesting Inference API embedding (type: ${type})...`);
  const result = await pc.inference.embed({
    model: INFERENCE_MODEL,
    inputs: [text],
    parameters: { inputType: type }
  });

  const duration = Date.now() - start;
  const dims = (result.data?.[0] as any)?.values?.length;
  console.log(`✅ [Pinecone] Got embedding! Dimensions: ${dims}, Latency: ${duration}ms`);

  logger.debug(`[Pinecone Inference] Generated embedding (${type})`, {
    model: INFERENCE_MODEL,
    durationMs: duration,
    dims: dims
  });

  if (!result.data || !(result.data[0] as any)?.values) {
    throw new Error('Failed to generate embedding from Pinecone Inference');
  }

  return Array.from((result.data[0] as any).values);
}

export function chunkTranscript(text: string, chunkSize = 500, overlap = 50): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];

  const chunks: string[] = [];
  let i = 0;

  while (i < words.length) {
    const chunk = words.slice(i, i + chunkSize).join(' ');
    chunks.push(chunk);
    i += chunkSize - overlap;
    if (i >= words.length) break;
  }

  return chunks;
}

export async function upsertInsightToIndex(
  insightId: string,
  transcript: string,
  metadata: Record<string, string | number>,
  requestId = 'unknown'
) {
  const chunks = chunkTranscript(transcript);
  const index = getPinecone().index(indexName);

  console.log(`\n🚀 [Pinecone] STARTING UPSERT PIPELINE`);
  console.log(`🔹 Insight ID: ${insightId}`);
  console.log(`🔹 Total Chunks to process: ${chunks.length}`);

  logger.info(`[${requestId}] [Pinecone] Starting upsert (Inference Pipeline)`, {
    insightId,
    chunkCount: chunks.length,
    model: INFERENCE_MODEL
  });

  const vectors: any[] = [];

  for (let i = 0; i < chunks.length; i++) {
    console.log(`🔄 [Pinecone] Processing chunk ${i + 1}/${chunks.length}...`);
    const values = await getEmbedding(chunks[i]!, 'passage');
    
    vectors.push({
      id: `${insightId}-c${i}`,
      values,
      metadata: {
        ...metadata,
        text: chunks[i]!.slice(0, 1000), // Enforce 40KB limit
        insightId,
        chunkIndex: i,
      },
    });
    console.log(`📦 [Pinecone] Vector prepared for chunk ${i + 1}`);
  }

  const BATCH_SIZE = 100;
  console.log(`\n📤 [Pinecone] Beginning batch upsert to index '${indexName}'...`);
  for (let b = 0; b < vectors.length; b += BATCH_SIZE) {
    const batch = vectors.slice(b, b + BATCH_SIZE);
    console.log(`➡️ [Pinecone] Sending batch ${Math.floor(b / BATCH_SIZE) + 1} (${batch.length} vectors)`);
    // Bug fix: fast-cast to any to satisfy older/strict UpsertOptions typing in TS
    await index.upsert(batch as any); 
    console.log(`✅ [Pinecone] Batch ${Math.floor(b / BATCH_SIZE) + 1} completed!`);
  }

  console.log(`🎉 [Pinecone] UPSERT PIPELINE COMPLETE!\n`);
  logger.info(`[${requestId}] [Pinecone] ✓ Upsert complete`);
}

export async function queryIndex(queryText: string, requestId = 'unknown') {
  console.log(`\n🔎 [Pinecone] STARTING QUERY PIPELINE`);
  console.log(`🔹 Query Text length: ${queryText.length}`);

  logger.info(`[${requestId}] [Pinecone] Querying index (Inference Pipeline)`, {
    queryLength: queryText.length,
  });

  const embedding = await getEmbedding(queryText, 'query');
  const index = getPinecone().index(indexName);

  console.log(`📤 [Pinecone] Sending vector query to index '${indexName}'...`);
  const queryResponse = await index.query({
    vector: embedding,
    topK: 5,
    includeMetadata: true,
  });

  const matchCount = queryResponse.matches?.length ?? 0;
  const topScore = queryResponse.matches?.[0]?.score?.toFixed(4);
  console.log(`✅ [Pinecone] Search complete! Found ${matchCount} matches. Top score: ${topScore}\n`);

  logger.info(`[${requestId}] [Pinecone] Query complete`, {
    matchCount,
    topScore,
  });

  return queryResponse;
}
