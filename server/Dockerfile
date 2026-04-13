FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# Install FFmpeg for audio processing
RUN apk add --no-cache ffmpeg

EXPOSE 3001

CMD ["npm", "run", "dev"]
