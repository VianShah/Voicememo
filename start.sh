#!/bin/bash
set -e

echo "╔══════════════════════════════════════════════════════╗"
echo "║         VoiceInsight AI — Starting Services          ║"
echo "╚══════════════════════════════════════════════════════╝"

# Start Celery worker in background
echo "🔧 Starting Celery worker..."
celery -A app.workers.celery_app:celery_app worker \
    --loglevel=info \
    --concurrency=2 &
CELERY_PID=$!

# Start FastAPI server
echo "🚀 Starting FastAPI server..."
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 &
API_PID=$!

echo "✅ All services started (API: $API_PID, Celery: $CELERY_PID)"

# Wait for any process to exit
wait -n

echo "⚠ One of the processes exited. Shutting down..."
kill $CELERY_PID 2>/dev/null || true
kill $API_PID 2>/dev/null || true
exit 1
