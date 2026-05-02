# 🔍 The Insight Recorder — Full Codebase Audit

> Audit run: 2026-04-24 | Auditor: Antigravity

---

## 1. Architecture Overview

The project has **three parallel frontends and two backends** — a sign of iterative development with no cleanup between phases.

```
the-insight-recorder/
├── src/           ← Legacy Vite/React SPA (v1, ABANDONED)
├── server.ts      ← Legacy monolith Express server (v1, ABANDONED)
├── web/           ← Active Next.js 16 frontend (v2, CURRENT)
├── server/        ← Active Express API server (v2, CURRENT)
├── whisper_server.py ← Python FastAPI Whisper service (CURRENT)
├── Dockerfile     ← Hugging Face monolith Dockerfile (STALE, HF-only)
├── Dockerfile.whisper ← Standalone Whisper Dockerfile (CURRENT)
└── docker-compose.yml ← Orchestrates web + server + whisper (CURRENT)
```

**The active, intended stack is:**
- `web/` — Next.js 16 (App Router) frontend
- `server/` — Express 5 API (TypeScript, ts-node-dev)
- `whisper_server.py` — faster-whisper FastAPI service

---

## 2. ✅ What's Working / Features Present

### Backend (`server/`)
| Feature | File | Status |
|---|---|---|
| Audio upload via multipart | `routes/audio.ts` | ✅ Working |
| WebM → WAV via FFmpeg (static binary) | `lib/ffmpeg.ts` | ✅ Working |
| Whisper transcription (HTTP call) | `routes/audio.ts:82` | ✅ Wired up |
| Gemini insight extraction (JSON) | `lib/gemini.ts` | ✅ Working |
| JSON fence stripping (defensive) | `lib/gemini.ts:59` | ✅ Working |
| Pinecone upsert with Inference API | `lib/pinecone.ts` | ✅ Working |
| RAG query (Pinecone → Gemini) | `routes/query.ts` | ✅ Working |
| Health check endpoint | `src/index.ts:49` | ✅ Working |
| Structured JSON logger | `lib/logger.ts` | ✅ Working |
| Global error handler middleware | `src/index.ts:69` | ✅ Working |
| Temp file cleanup after pipeline | `routes/audio.ts:138` | ✅ Working |
| Per-request timing & logging | throughout | ✅ Working |

### Frontend (`web/`)
| Feature | File | Status |
|---|---|---|
| Gallery page (list insights) | `app/page.tsx` | ✅ Working |
| Record page (start/stop/pause) | `app/record/page.tsx` | ✅ Working |
| Pause/resume recording | `RecordControls.tsx` | ✅ Working |
| 45-min auto-stop + progress bar | `RecordControls.tsx` | ✅ Working |
| AudioContext waveform visualiser | `components/Waveform.tsx` | ✅ Working |
| Upload audio to Express server | `lib/audioCapture.ts` | ✅ Working |
| IndexedDB persistence (idb) | `lib/db.ts` | ✅ Working |
| Insight detail page (audio + highlights) | `app/insight/[id]/page.tsx` | ✅ Working |
| AI Chat page (RAG Q&A + history) | `app/chat/page.tsx` | ✅ Working |
| Query history saved to IndexedDB | `lib/db.ts` | ✅ Working |
| ProcessingOverlay component | `ProcessingOverlay.tsx` | ✅ Working |
| File export utility (showSaveFilePicker) | `lib/fileSystem.ts` | ✅ Working |

### Whisper Service
| Feature | File | Status |
|---|---|---|
| FastAPI `/v1/audio/transcriptions` | `whisper_server.py` | ✅ Working |
| Word-level timestamps | `whisper_server.py:29` | ✅ Working |
| Auto language detection | `whisper_server.py:29` | ✅ Working |
| CPU int8 mode for performance | `whisper_server.py:13` | ✅ Working |

---

## 3. 🚫 What's Broken (Hard Bugs)

### BUG-01 — `server/Dockerfile` uses Alpine `apk` on Debian image
**File:** `server/Dockerfile:11`
```dockerfile
FROM node:20          # ← Debian-based image
...
RUN apk add --no-cache ffmpeg   # ← APK is Alpine's package manager. WILL FAIL.
```
**Impact:** Docker build for the server **will always crash**. This is a showstopper for local Docker deployment.
**Fix:** Change to `apt-get install -y ffmpeg` OR switch to `node:20-alpine` and keep `apk`.

---

### BUG-02 — `server/Dockerfile` runs `npm run dev` in production
**File:** `server/Dockerfile:15`
```dockerfile
CMD ["npm", "run", "dev"]
```
`dev` uses `ts-node-dev` which watches for file changes. Inside Docker there is no `--build` step for the server, so the container depends on the mounted source volume (`./server/src:/app/src`). **This only works if the volume is mounted**. If built standalone (e.g., CI push, HF Spaces), it breaks entirely because there's no compiled output.
**Fix:** Add a `build` script (`tsc`) and use `CMD ["node", "dist/index.js"]`.

---

### BUG-03 — Legacy `src/App.tsx` calls `/api/analyze` which doesn't exist
**File:** `src/App.tsx:91`
```ts
const response = await fetch('/api/analyze', { ... });
```
The active server exposes `/api/audio/upload`, **not** `/api/analyze`. This is a dead endpoint from the legacy monolith (`server.ts`).
**Impact:** The Vite SPA (legacy frontend) is completely broken for audio upload.

---

### BUG-04 — `verify_pinecone.ts` uses wrong default index name
**File:** `server/src/scripts/verify_pinecone.ts:10`
```ts
const indexName = process.env.PINECONE_INDEX || 'voicememos';
```
But `lib/pinecone.ts:9` defaults to:
```ts
const indexName = process.env.PINECONE_INDEX || "insights";
```
Two different fallback names — the verify script will test the wrong index if `PINECONE_INDEX` isn't set.

---

### BUG-05 — `insight/[id]/page.tsx` has a memory leak on audio URL
**File:** `web/src/app/insight/[id]/page.tsx:33-35`
```ts
return () => {
  if (audioUrl) URL.revokeObjectURL(audioUrl);  // ← audioUrl is always '' here (stale closure)
};
```
`audioUrl` is read from the closure at the time the effect registers (when it's still `''`), so `revokeObjectURL` is never actually called with the real blob URL. The object URL is leaked for the browser session.

---

### BUG-06 — Pinecone upsert embeds chunks **sequentially**, not in parallel
**File:** `server/src/lib/pinecone.ts:98-113`
```ts
for (let i = 0; i < chunks.length; i++) {
  const values = await getEmbedding(chunks[i]!, 'passage');  // ← awaited inside loop
  ...
}
```
Each chunk makes a synchronous round-trip to Pinecone Inference. For a 10-chunk transcript this takes ~10× the latency of a single call. 
**Fix:** `await Promise.all(chunks.map(...))`.

---

### BUG-07 — `docker-compose.yml` exposes `NEXT_PUBLIC_GEMINI_API_KEY` to the browser
**File:** `docker-compose.yml:18`
```yaml
- NEXT_PUBLIC_GEMINI_API_KEY=${GEMINI_API_KEY}
```
Any variable prefixed with `NEXT_PUBLIC_` is embedded in the JS bundle and **visible to anyone who opens DevTools**. This leaks the Gemini API key publicly. The web frontend does not actually need this key client-side (all AI calls go through the Express server).

---

### BUG-08 — `docker-compose.yml` missing `depends_on` ordering
**File:** `docker-compose.yml`
The `server` service calls `http://whisper:9000` on startup, but there's no `depends_on: whisper` declaration. Docker Compose will start all services concurrently — if the server gets a request before Whisper loads the model (which takes 30–60s on CPU), it will return a 500 error.

---

## 4. ⚠️ What's Incomplete / Missing

### INCOMPLETE-01 — `List` button in gallery is a dead stub
**File:** `web/src/app/page.tsx:39-41`
```tsx
<button className="p-2 text-white/40 hover:text-white transition-colors">
  <List size={24} />   {/* No onClick handler */}
</button>
```
The List/grid toggle button has no functionality.

### INCOMPLETE-02 — `Share2` button is a dead stub everywhere
**Files:** `web/src/app/insight/[id]/page.tsx:60`, `web/src/app/page.tsx:90`
The share button renders but has no `onClick`. The `fileSystem.ts` `exportAudio` utility exists but is never called from any button.

### INCOMPLETE-03 — `Plus` (+) button in gallery has no action
**File:** `web/src/app/page.tsx:82-84`
```tsx
<button className="...">
  <Plus size={24} />   {/* No onClick — no file upload trigger */}
</button>
```
In the legacy `src/App.tsx` this was wired to a hidden `<input type="file">`. The new `web/` gallery lost this feature entirely.

### INCOMPLETE-04 — `audioUtils.ts` (silence stripping) is never called
**File:** `web/src/lib/audioUtils.ts`
A complete, well-written 170-line silence-stripping and audio normalisation module exists but is **imported nowhere**. It was presumably meant to pre-process audio before upload in `audioCapture.ts`.

### INCOMPLETE-05 — `store.ts` (preferences) is never used
**File:** `web/src/lib/store.ts`
`getPreference` / `setPreference` wrappers exist but are imported nowhere. No user preferences (playback speed, theme, language) are persisted.

### INCOMPLETE-06 — `fileSystem.ts` (export audio) is never called
**File:** `web/src/lib/fileSystem.ts`
`exportAudio()` is a complete, well-implemented function (with File System Access API + fallback) that is imported nowhere.

### INCOMPLETE-07 — Highlight "Play Snippet" is broken in `web/` detail page
**File:** `web/src/app/insight/[id]/page.tsx:101-110`
The detail page renders highlights but has **no "Play Snippet" button** — unlike the legacy `src/App.tsx` which had `playSegment(h.startTime, h.endTime)`. The `startTime`/`endTime` timestamps from Whisper/Gemini are stored but unused.

### INCOMPLETE-08 — Tags not displayed on detail page
**File:** `web/src/app/insight/[id]/page.tsx`
`insight.tags` is stored and typed but never rendered on the detail page. The gallery `InsightCard` does show them.

### INCOMPLETE-09 — `mood` field not used in `web/` at all
The `mood` property (`calm | energetic | reflective`) from Gemini drives the dynamic gradient in the legacy `src/App.tsx`, but **the entire `web/` app uses a static hardcoded gradient** — the mood-driven background feature was dropped in the rewrite.

### INCOMPLETE-10 — No error boundary anywhere
Neither the Next.js `web/` nor any page has an `error.tsx` or React Error Boundary. Any runtime crash will show a blank white screen or Next.js generic error page.

### INCOMPLETE-11 — No loading state on gallery initial load
**File:** `web/src/app/page.tsx:16-22`
`getAllInsights()` is called in a `useEffect` with no loading indicator. On first open IndexedDB read can take 50–200ms, leaving the user staring at an empty list then a sudden flash of cards.

### INCOMPLETE-12 — Chat page has no "Clear History" button
**File:** `web/src/app/chat/page.tsx`
A `Trash2` icon is imported (`import { ..., Trash2 } from 'lucide-react'`) but never rendered or wired up. The import is dead weight.

### INCOMPLETE-13 — Whisper `language` form field type mismatch
**File:** `whisper_server.py:21`
```python
language: str = Form(default=None)
```
`default=None` with type `str` is a type mismatch in Pydantic v2 (FastAPI). It should be `Optional[str] = Form(default=None)`. May cause a validation warning or silent error on some FastAPI versions.

### INCOMPLETE-14 — `web/Dockerfile` runs in dev mode
**File:** `web/Dockerfile`
```dockerfile
CMD ["npm", "run", "dev"]
```
Same issue as server — Next.js dev server should not be used in Docker. Should `next build` then `next start`.

---

## 5. 🧹 Loose Ends / Dead Code

| Item | Location | Issue |
|---|---|---|
| `src/` (Vite SPA) | Root `/src` | Entire legacy frontend — abandoned but not removed |
| `server.ts` | Root `server.ts` | 18KB legacy monolith Express server — abandoned |
| `whisper_server.py` at root | Root | Duplicated logic: also inside `Dockerfile.whisper` context |
| `GoogleAIFileManager` import | `server/src/lib/gemini.ts:2` | Imported but never used (leftover from Files API approach) |
| `dist/` folder | Root `/dist` | Legacy Vite build output committed/present |
| `tmp_uploads/` folder | Root | Empty temp dir committed |
| `uploads/` folder | Root | Upload dir from legacy server committed |
| `metadata.json` | Root | Appears to be an Antigravity KI metadata file, not app code |
| `PROPOSAL.md` | Root | Design doc — not needed in repo |
| `implementation_plan.md` | Root | Antigravity planning artifact committed to repo |
| `notes.md` | Root | Dev notes committed to repo |
| `product_devGuide` | Root | Plaintext file with no extension committed |
| `PRD_TheInsightRecorder.md` | Root | PRD doc committed to repo |
| `@ai-sdk/google` + `ai` deps | `web/package.json` | Installed but never imported/used anywhere in `web/` |
| `@pinecone-database/pinecone` | `web/package.json` | Installed but never used client-side |
| Two Gemini SDK versions | Root `package.json` | Both `@google/genai` and `@google/generative-ai` listed |

---

## 6. 🏗️ Structural / Design Issues

| Issue | Severity |
|---|---|
| **Dual frontend problem**: `src/` and `web/` coexist with no clear "this one is active" documentation | High |
| **No API proxy in Next.js**: `web/` calls Express directly on `localhost:3001` (hardcoded). CORS required, no unified domain, breaks in non-localhost environments | High |
| **Insights are IndexedDB-only**: No server-side persistence for insights. If user clears browser data / switches device / opens in different browser — all recordings are permanently lost | High |
| **Audio stored as blob in IndexedDB**: Large audio files (250MB max) stored in browser IndexedDB. This will cause storage quota errors on mobile or after many recordings | Medium |
| **No auth / multi-user isolation**: Pinecone index is shared with no user namespacing. Multiple users would see each other's insights in RAG queries | Medium |
| **Gemini response validation is minimal**: Only checks for valid JSON. No schema validation — missing fields (e.g., no `highlights` array) would cause runtime crashes downstream | Medium |
| **`CORS()` wildcard on Express**: `app.use(cors())` with no origin restriction — any website can make requests to the Express server | Medium |

---

## 7. Summary Scorecard

| Category | Score | Notes |
|---|---|---|
| **Core pipeline (record → transcribe → embed → query)** | 🟡 7/10 | Works but Dockerfile broken for Docker deploy |
| **Frontend completeness** | 🟡 6/10 | Several stub buttons, missing mood feature, no file upload in gallery |
| **Code quality** | 🟢 8/10 | Clean, well-commented, good logging patterns |
| **Data persistence** | 🟠 5/10 | IndexedDB only — no cross-device sync, no server storage |
| **Docker / deployment** | 🔴 4/10 | Server Dockerfile broken (apk on Debian), both containers run in dev mode |
| **Security** | 🔴 4/10 | API key leaked via NEXT_PUBLIC, open CORS, no auth |
| **Dead code cleanup** | 🟠 5/10 | Entire legacy `src/` + `server.ts` still present |
| **Error handling** | 🟡 6/10 | Server has good error handling, frontend has none (no error boundaries) |

---

## 8. Recommended Fix Priority

### 🔴 Do Immediately (Blockers)
1. **Fix `server/Dockerfile`** — swap `apk` → `apt-get`, add `tsc` build step
2. **Fix `web/Dockerfile`** — add `next build`, use `next start`
3. **Add `depends_on: whisper`** in `docker-compose.yml`
4. **Remove `NEXT_PUBLIC_GEMINI_API_KEY`** from `docker-compose.yml`

### 🟡 Fix Soon (Broken UX)
5. Wire `Plus` button in gallery to a hidden `<input type="file">` (re-add file upload)
6. Wire `Share2` button to `exportAudio()` from `fileSystem.ts`
7. Fix blob URL revocation memory leak in `insight/[id]/page.tsx`
8. Add "Play Snippet" button to insight detail page (use `startTime`/`endTime`)
9. Parallelise Pinecone chunk embedding (`Promise.all`)

### 🟢 Clean Up (Quality)
10. Delete `src/` (legacy Vite SPA)
11. Delete `server.ts` (legacy monolith)
12. Remove unused `@ai-sdk/google`, `ai`, `@pinecone-database/pinecone` from `web/package.json`
13. Remove unused `GoogleAIFileManager` import from `server/lib/gemini.ts`
14. Move planning docs out of repo root (`.gitignore` them)
15. Add error boundary (`error.tsx`) to Next.js app
16. Align default Pinecone index name in `verify_pinecone.ts` to `"insights"`
17. Fix `whisper_server.py` Optional type annotation
