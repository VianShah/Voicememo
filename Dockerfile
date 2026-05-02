# ══════════════════════════════════════════════════════════════════════
# VoiceInsight AI — Multi-stage Dockerfile
# Stage 1: Build the Vite React frontend
# Stage 2: Python runtime with FastAPI + Celery + Whisper
# ══════════════════════════════════════════════════════════════════════

# ── Stage 1: Build React frontend ────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /build

COPY package.json ./
RUN npm install

COPY index.html vite.config.ts tsconfig.json ./
COPY src/ src/

RUN npm run build


# ── Stage 2: Python runtime ──────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (FFmpeg for audio processing)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Copy built frontend from Stage 1
COPY --from=frontend-builder /build/dist dist/

# Create data directories
RUN mkdir -p data/raw data/snippets

# Make start script executable
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
