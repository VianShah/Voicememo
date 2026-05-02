FROM python:3.11-slim

WORKDIR /app
run mkdir -p /app
# Install FFmpeg for server-side audio conversion and Node.js for the app server
RUN apt-get update && \
    apt-get install -y ffmpeg curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies for Whisper STT
RUN pip install --no-cache-dir fastapi uvicorn python-multipart "faster-whisper==1.0.3"

# Copy package files and install Node dependencies
COPY package*.json ./
RUN npm install

# Copy application files
COPY . .

# Build the Vite React frontend
RUN npm run build

# Make start script executable
RUN chmod +x start.sh

# Persistent storage for audio recordings (HF Spaces mounts /data at runtime)
ENV STORAGE_DIR=/data/recordings

# Expose port 7860 for Hugging Face Spaces
EXPOSE 7860

# Start both Whisper API and Node.js Server
CMD ["./start.sh"]
