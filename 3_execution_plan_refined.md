# Phase 3: Execution Plan - VoiceInsight AI

## 1. Project Directory Structure
A modular structure ensures the "Plug-and-Play" capability and separates concerns to avoid technical debt.

```text
voice-insight-service/
├── app/
│   ├── api/                # API Route Handlers
│   │   ├── v1/
│   │   │   ├── endpoints/
│   │   │   │   ├── upload.py
│   │   │   │   ├── insights.py
│   │   │   │   └── query.py
│   │   │   └── api.py      # Main router
│   ├── core/               # Configuration and Constants
│   │   ├── config.py       # Pydantic Settings
│   │   └── security.py
│   ├── services/           # Business Logic (The "Doing" layer)
│   │   ├── transcription.py# Whisper logic
│   │   ├── audio_engine.py # pydub/slicing logic
│   │   ├── filter_engine.py# Filler word removal
│   │   └── llm/            # Plug-and-Play LLM Providers
│   │       ├── base.py     # Abstract Base Class
│   │       ├── gemini.py
│   │       ├── groq.py
│   │       └── openai.py
│   ├── models/             # Database Schemas (SQLAlchemy/SQLModel)
│   ├── schemas/            # Pydantic Models (Request/Response)
│   ├── workers/            # Celery Task Definitions
│   │   └── tasks.py
│   └── main.py             # FastAPI Entrypoint
├── data/                   # Local storage for audio/DB (ignored in git)
│   ├── raw/
│   ├── snippets/
│   └── db/
├── tests/                  # Unit and Integration tests
├── docker-compose.yml      # Orchestration (Web, Worker, Redis, Postgres)
├── Dockerfile
├── requirements.txt
└── .env.example
```

## 2. Refining Bottlenecks (Architectural Guardrails)

### B1: Resource-Heavy Transcription
*   **Refinement:** Do not load the Whisper model inside the FastAPI process. Load it *only* inside the Celery Worker.
*   **Strategy:** Use a `Singleton` pattern for the model inside the worker so it stays in memory instead of reloading for every task.

### B2: Blocked Event Loop
*   **Refinement:** Use `anyio.to_thread.run_sync` for any synchronous file I/O or CPU-bound audio processing that happens inside FastAPI routes.
*   **Strategy:** Offload *everything* except the initial file upload to the Celery queue.

### B3: LLM Quote Mismatches (The "Fuzzy" Problem)
*   **Refinement:** LLMs often change "um" to "..." or fix grammar in quotes.
*   **Strategy:** Implement a **Fuzzy Phrase Matcher** (using `RapidFuzz`) to locate the most likely start/end time in the original timestamped word list, even if the LLM slightly altered the text.

### B4: Database Contention
*   **Refinement:** Use an **Async Database Driver** (`asyncpg` for PostgreSQL).
*   **Strategy:** Use connection pooling to handle concurrent metadata requests while workers are writing new data.

## 3. Implementation Roadmap (Step-by-Step)

### Step 1: The Foundation
*   Initialize FastAPI + Docker Compose.
*   Set up PostgreSQL + Redis.
*   Create the `LLMProvider` abstract base class.

### Step 2: The Worker & Transcription
*   Implement `Faster-Whisper` service.
*   Create the Celery task for processing audio.
*   Implement the "Filler-Filter" pre-processor.

### Step 3: The Plug-and-Play Layer
*   Implement `GeminiProvider` and `GroqProvider`.
*   Create the Logic to extract 3 insights and match them to timestamps.

### Step 4: Audio Slicing & API
*   Implement the `AudioEngine` to slice snippets based on matched timestamps.
*   Expose the `/insights` and `/upload` endpoints.

## 4. Best Practices to Follow
1.  **Strict Typing:** Use Python Type Hints everywhere.
2.  **Environment Isolation:** All secrets/model choices MUST live in `.env`.
3.  **Graceful Failures:** If transcription fails, the API should return a human-readable "Failed" status, not a 500 error.
4.  **Logging:** Use structured logging (JSON) to track tasks across the API and Workers.
