// Bug fix: fluent-ffmpeg uses CommonJS default export — must use require() or importDefault trick
// with esModuleInterop enabled it resolves, but we add a safety guard.
import ffmpegLib from 'fluent-ffmpeg';
import ffmpegStatic from 'ffmpeg-static';
import path from 'path';
import fs from 'fs';
import { logger } from './logger';

// Point fluent-ffmpeg at the bundled static binary — no system install needed
if (ffmpegStatic) {
  ffmpegLib.setFfmpegPath(ffmpegStatic);
  logger.info(`[FFmpeg] Using static binary: ${ffmpegStatic}`);
}

export async function convertWebmToWav(inputPath: string): Promise<string> {
  const outputPath = inputPath.replace(/\.[^.]+$/, '.wav');  // Bug fix: safer extension swap

  return new Promise((resolve, reject) => {
    const ffmpegStart = Date.now();
    logger.debug('[FFmpeg] Starting conversion', { inputPath, outputPath });

    ffmpegLib(inputPath)
      .audioChannels(1)     // Mono — reduces file size, Gemini handles fine
      .audioFrequency(16000) // 16kHz — standard for speech
      .toFormat('wav')
      .on('start', (cmd) => logger.debug('[FFmpeg] Command', { cmd }))
      .on('progress', (p) => logger.debug('[FFmpeg] Progress', {
        percent: p.percent?.toFixed(1),
        timemark: p.timemark,
      }))
      .on('end', () => {
        const durationMs = Date.now() - ffmpegStart;
        const outStat = fs.statSync(outputPath);
        logger.debug('[FFmpeg] Conversion complete', {
          durationMs,
          outputSizeMB: (outStat.size / 1024 / 1024).toFixed(2),
        });
        resolve(outputPath);
      })
      .on('error', (err) => {
        logger.error('[FFmpeg] Conversion failed', { error: err.message });
        reject(err);
      })
      .save(outputPath);
  });
}
