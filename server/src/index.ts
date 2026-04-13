import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import path from 'path';
import { logger } from './lib/logger';
import audioRouter from './routes/audio';
import queryRouter from './routes/query';

dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const app = express();
const PORT = process.env.PORT || 3001;

// ── Global request logger middleware ────────────────────────────────
app.use((req, res, next) => {
  const start = Date.now();
  const mem = process.memoryUsage();

  logger.info('→ Incoming request', {
    method: req.method,
    url: req.url,
    memUsedMB: (mem.heapUsed / 1024 / 1024).toFixed(1),
    memTotalMB: (mem.heapTotal / 1024 / 1024).toFixed(1),
    rssMB: (mem.rss / 1024 / 1024).toFixed(1),
  });

  res.on('finish', () => {
    const memAfter = process.memoryUsage();
    logger.info('← Response sent', {
      method: req.method,
      url: req.url,
      status: res.statusCode,
      durationMs: Date.now() - start,
      memUsedMB: (memAfter.heapUsed / 1024 / 1024).toFixed(1),
    });
  });

  next();
});

app.use(cors());
app.use(express.json({ limit: '1mb' }));  // Bug fix: explicit JSON payload limit (audio goes via multipart, not JSON)

// ── Routes ────────────────────────────────────────────────────────
app.use('/api/audio', audioRouter);
app.use('/api/query', queryRouter);

// ── Health check with system diagnostics ─────────────────────────
app.get('/health', (req, res) => {
  const mem = process.memoryUsage();
  res.json({
    status: 'ok',
    service: 'insight-recorder-server',
    uptime: process.uptime().toFixed(1) + 's',
    memory: {
      heapUsedMB: (mem.heapUsed / 1024 / 1024).toFixed(1),
      heapTotalMB: (mem.heapTotal / 1024 / 1024).toFixed(1),
      rssMB: (mem.rss / 1024 / 1024).toFixed(1),
    },
    env: {
      hasGeminiKey: !!process.env.GEMINI_API_KEY,
      hasPineconeKey: !!process.env.PINECONE_API_KEY,
      pineconeIndex: process.env.PINECONE_INDEX ?? 'unset',
    }
  });
});

// ── Global error handler ──────────────────────────────────────────
app.use((err: any, req: express.Request, res: express.Response, next: express.NextFunction) => {
  logger.error('Unhandled error', {
    url: req.url,
    method: req.method,
    error: err?.message,
    stack: err?.stack?.slice(0, 500),
  });
  res.status(500).json({ error: 'Unexpected server error', detail: err?.message });
});

app.listen(PORT, () => {
  const mem = process.memoryUsage();
  logger.info(`🚀 Server ready`, {
    port: PORT,
    nodeVersion: process.version,
    memUsedMB: (mem.heapUsed / 1024 / 1024).toFixed(1),
    env: process.env.NODE_ENV ?? 'development',
  });
});
