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

# Point HuggingFace cache to a predictable location inside the volume mount
ENV HF_HOME=/app/models/.cache/huggingface

# Install system dependencies (FFmpeg for audio processing, Redis, and Postgres)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 redis-server postgresql sudo && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Pre-configure PostgreSQL database during image build
RUN service postgresql start && \
    su - postgres -c "psql -c \"CREATE USER voiceinsight WITH PASSWORD 'voiceinsight';\"" && \
    su - postgres -c "psql -c \"CREATE DATABASE voiceinsight OWNER voiceinsight;\""

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the Whisper model so runtime needs NO internet connection
# The model lands in /app/models/.cache/huggingface (matches the volume mount)
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')"

# Copy application code
COPY app/ app/

# Copy built frontend from Stage 1
COPY --from=frontend-builder /build/dist dist/

# Create data directories
RUN mkdir -p data/raw data/snippets

# Make start script executable
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 7860

CMD ["./start.sh"]
