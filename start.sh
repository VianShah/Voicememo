#!/bin/bash

echo "Starting Whisper Server in the background..."
uvicorn whisper_server:app --host 127.0.0.1 --port 9000 &
WHISPER_PID=$!

echo "Starting Node.js Server..."
npm run start &
NODE_PID=$!

# Wait for any process to exit
wait -n

echo "One of the processes exited. Shutting down..."
kill $WHISPER_PID
kill $NODE_PID
exit 1
