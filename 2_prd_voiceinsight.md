# Phase 2: Product Requirements Document (PRD) - VoiceInsight AI

## 1. Executive Summary
VoiceInsight AI is a scalable microservice designed to convert voice recordings into searchable, queryable, and actionable "Audio Insights." The system prioritizes cost-efficiency, low-latency playback of key moments, and a **model-agnostic** architecture.

## 2. User Stories
*   **Recording & Transcription:** As a user, I want to upload a voice memo and receive a clean transcript so that I can read what I said.
*   **Token Optimization:** As a developer, I want filler words (um, uh, etc.) removed before sending text to an LLM to minimize API costs.
*   **Audio Insights:** As a user, I want the system to identify "impactful" moments and let me play the specific audio snippet associated with those moments.
*   **Model Flexibility:** As a developer, I want to switch between Gemini, Groq, OpenAI, or DeepSeek by changing a single environment variable.
*   **Scalability:** As a system owner, I want the system to handle multiple concurrent users without blocking the main API thread.

## 3. Functional Requirements

### FR1: Audio Processing & Transcription
*   **Support Formats:** MP3, WAV, M4A.
*   **Local Transcription:** Must use `Faster-Whisper` to generate word-level timestamps `[start_time, end_time]`.
*   **Enhancement:** Option to normalize audio and reduce background noise before processing.

### FR2: The "Filler-Filter" Engine
*   **Logic:** A pre-processor that strips a customizable list of filler words.
*   **Mapping:** Must maintain a "Coordinate Map" that links the index of words in the *Cleaned* text back to their original *Timestamped* positions.

### FR3: Model-Agnostic "Plug-and-Play" LLM Layer
*   **Interface:** A standardized `LLMProvider` interface.
*   **Implementations:** Initial support for:
    *   **Google Gemini** (Native multi-modal capability).
    *   **Groq** (Ultra-low latency Llama-3).
    *   **OpenAI** (GPT-4o/o1).
    *   **DeepSeek** (Cost-optimized reasoning).
*   **Configuration:** Selection via `LLM_PROVIDER` environment variable.

### FR4: Audio Insight Generation
*   **Extraction:** The LLM must return exactly 3 insights, each with the original "source quote" from the transcript.
*   **Snippet Slicing:** The system must automatically slice 3 separate `.mp3` files based on the timestamps of the source quotes.

## 4. Technical Specifications

### Architecture: Asynchronous Task Queue
*   **Web Framework:** FastAPI.
*   **Task Management:** Celery with Redis broker.
*   **Database:** PostgreSQL with `pgvector` for RAG capabilities.
*   **Storage:** Local file system (Phase 1) with abstraction for S3 (Phase 2/3).

### API Endpoints
| Endpoint | Method | Input | Output |
| :--- | :--- | :--- | :--- |
| `/v1/upload` | POST | Audio File | `task_id` |
| `/v1/status/{id}` | GET | `task_id` | Status (Pending/Processing/Done) |
| `/v1/insights/{id}` | GET | `task_id` | JSON Insights + Snippet URLs |
| `/v1/query` | POST | Question | LLM Answer + Audio Citations |

## 5. Non-Functional Requirements
*   **Cost:** API costs per 10-minute note must remain < $0.005.
*   **Accuracy:** Audio snippets must be within ±100ms of the intended speech.
*   **Portability:** The entire stack must be deployable via `docker-compose`.

## 6. Constraints & Risks
*   **Hardware:** Local transcription requires sufficient RAM (4GB min) or VRAM (if using GPU).
*   **Fuzzy Matching:** LLMs sometimes slightly alter quotes. The snippet matcher must use "Fuzzy String Matching" to find the original timestamps.
