FROM node:20-alpine

WORKDIR /app

# Install FFmpeg for server-side audio conversion
RUN apk add --no-cache ffmpeg

COPY package*.json ./
RUN npm install

COPY . .

# Build the Vite React frontend
RUN npm run build

# Persistent storage for audio recordings (HF Spaces mounts /data at runtime)
ENV STORAGE_DIR=/data/recordings

# Expose port 7860 for Hugging Face Spaces
EXPOSE 7860

# Start the Express server
CMD ["npm", "run", "start"]
