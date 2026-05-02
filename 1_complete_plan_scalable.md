# Phase 1: Complete Project Plan - VoiceInsight AI (Scalable Architecture)

## 1. Vision & Scope
The goal is to build a high-performance, low-cost microservice that transforms voice recordings into "Audio Insights." Users can record memos, have them intelligently summarized via Gemini, and hear specific "impactful" snippets of their own voice that the AI identified as important.

## 2. Phase 2-Ready Architecture
To ensure scalability from 1 to 100 users, the system will use an **Asynchronous Task Queue** pattern from day one:

*   **API Gateway (FastAPI):** Receives audio uploads and provides immediate feedback.
*   **Message Broker (Redis):** Manages a queue of pending audio tasks.
*   **Task Workers (Celery):** Independent processes that handle:
    *   Transcription (Faster-Whisper).
    *   Audio Slicing (pydub).
    *   AI Analysis (Gemini API).
*   **Persistence Layer:** 
    *   **PostgreSQL + pgvector:** For robust metadata and RAG storage (Phase 2+ standard).
    *   **File System/S3:** For raw and processed audio assets.

## 3. Technology Stack
*   **Frameworks:** FastAPI, Celery, Redis.
*   **Transcription:** Faster-Whisper (Local GPU/CPU).
*   **LLM Intelligence:** Google Gemini 2.5 Flash.
*   **Database:** PostgreSQL (with `pgvector` for RAG).
*   **Audio Libs:** pydub, ffmpeg.

## 4. Scaling Strategy
| Phase | Users | Infrastructure | Scaling Method |
| :--- | :--- | :--- | :--- |
| **P1** | 1 | Local / Single PC | Docker Compose (All-in-one). |
| **P2** | 25 | VPS (Hetzner/DigitalOcean) | Horizontal Scaling of Celery Workers. |
| **P3** | 100+ | Cloud (AWS/GCP) | Auto-scaling groups + Managed S3/DB. |

## 5. Key Workflows

### A. The "Smart Transcription" Flow
1.  **Record:** User submits audio.
2.  **Transcribe:** Faster-Whisper generates a JSON transcript with `[start, end]` timestamps for every word.
3.  **Token Optimization:** A "Filler Filter" removes non-essential words (um, ah, like, basically) to reduce the prompt size sent to Gemini.
4.  **Semantic Mapping:** A lookup table is maintained to map the "Clean" text back to the original timestamps.

### B. The "Audio Insight" Generation
1.  **Inquiry:** Gemini analyzes the clean transcript.
2.  **Extraction:** Gemini identifies 3 "Impactful Segments" and returns the exact text quotes.
3.  **Synchronization:** The system finds the timestamps for those quotes in the original audio.
4.  **Snippet Creation:** The system creates 3 mini-audio clips for instant playback.

## 6. Cost Analysis (Per 1000 Recordings)
*   **Transcription:** $0 (Local execution).
*   **Storage:** Minimal (Local disk or S3 Glacier).
*   **LLM Analysis:** ~$0.01 - $0.05 total (Gemini Flash is extremely cost-effective).
*   **Total:** Virtually free beyond server hosting.

## 7. Success Metrics
*   **Sync Accuracy:** The "Audio Insight" snippet must start and end within 100ms of the actual spoken words.
*   **Cost Efficiency:** Processing a 10-minute recording should cost less than $0.001 in API fees.
*   **Latency:** Transcription and initial insight generation should complete within 30 seconds for a 5-minute audio file.
