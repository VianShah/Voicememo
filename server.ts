import express from "express";
import { createServer as createViteServer } from "vite";
import path from "path";
import { GoogleGenerativeAI } from "@google/generative-ai";
import { Pinecone } from "@pinecone-database/pinecone";
import dotenv from "dotenv";

dotenv.config();

const app = express();
const PORT = 3000;

app.use(express.json({ limit: '50mb' }));

// Initialize Gemini with the standard SDK
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || "");
const pc = new Pinecone({ apiKey: process.env.PINECONE_API_KEY || "" });
const indexName = process.env.PINECONE_INDEX || "insights";

// Helper to get embeddings using Pinecone Inference API (Trilingual: English, Hindi, Gujarati)
async function getEmbedding(text: string, type: 'passage' | 'query' = 'passage'): Promise<number[]> {
  console.log(`⏳ [Pinecone] Requesting Inference API embedding (type: ${type})...`);
  const result = await pc.inference.embed({
    model: 'multilingual-e5-large',
    inputs: [text],
    parameters: { inputType: type }
  });
  
  if (!result.data || !(result.data[0] as any)?.values) {
    throw new Error('Failed to generate embedding from Pinecone Inference');
  }

  const values = Array.from((result.data[0] as any).values) as number[];
  console.log(`✅ [Pinecone] Got embedding! Dimensions: ${values.length}`);
  return values;
}

// API Routes
app.post("/api/analyze", async (req, res) => {
  try {
    const { audioBase64, mimeType } = req.body;
    const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });

    const prompt = `
      Return the result in JSON format with the following structure:
      {
        "title": "record title",
        "transcript": "full transcript",
        "summary": "brief summary",
        "mood": "calm/energetic/reflective",
        "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
        "highlights": [
          {
            "text": "snippet text",
            "tag": "#Realization/#ActionItem/#Memory",
            "startTime": 0,
            "endTime": 15
          }
        ]
      }
    `;

    const result = await model.generateContent([
      { text: prompt },
      {
        inlineData: {
          mimeType: mimeType,
          data: audioBase64,
        },
      },
    ]);

    let text = result.response.text();
    // Clean markdown if present
    text = text.replace(/```json\n?/, "").replace(/```/, "").trim();
    const analysis = JSON.parse(text);

    // Generate embedding for the transcript
    const embedding = await getEmbedding(analysis.transcript);

    // Upsert to Pinecone
    const index = pc.index(indexName);
    const id = Math.random().toString(36).substr(2, 9);

    await index.upsert({
      records: [{
        id,
        values: embedding,
        metadata: {
          title: analysis.title,
          summary: analysis.summary,
          transcript: analysis.transcript,
          timestamp: Date.now()
        }
      }]
    });

    res.json({ ...analysis, id });
  } catch (error) {
    console.error("Analysis error:", error);
    res.status(500).json({ error: "Failed to analyze audio" });
  }
});

app.post("/api/query", async (req, res) => {
  try {
    const { query } = req.body;

    // 1. Get embedding for query (using 'query' inputType for higher accuracy)
    const queryEmbedding = await getEmbedding(query, 'query');

    // 2. Search Pinecone
    const index = pc.index(indexName);
    const queryResponse = await index.query({
      vector: queryEmbedding,
      topK: 5,
      includeMetadata: true,
    });

    const context = queryResponse.matches?.map(match => `
      Title: ${match.metadata?.title}
      Summary: ${match.metadata?.summary}
      Transcript: ${match.metadata?.transcript}
    `).join("\n---\n") || "";

    // 3. Generate answer with Gemini
    const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });
    const prompt = `
      You are an AI assistant for "The Insight Recorder". 
      Answer the user's question based ONLY on the provided context.
      Context:
      ${context}

      Question: ${query}
    `;

    const result = await model.generateContent(prompt);
    res.json({ answer: result.response.text() });
  } catch (error) {
    console.error("Query error:", error);
    res.status(500).json({ error: "Failed to query insights" });
  }
});

// Vite middleware setup
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
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
