# Architecture

## System Overview

Smart Scheduler is a voice-enabled AI scheduling assistant. The user speaks to a browser-based UI; the system understands natural language scheduling requests, resolves time references, checks Google Calendar availability, and books events — all through a back-and-forth voice conversation.

The system has two major halves:

- **Frontend** (Next.js): Captures mic audio, streams it over WebSocket, plays back audio responses, and renders live status updates via SSE.
- **Backend** (FastAPI): Acts as the hub — proxies audio between browser and Gemini Live API, intercepts tool calls, dispatches to LangGraph modules, and emits SSE events.

---

## High-Level Component Map

```
Browser (Next.js)
  │
  ├── WebSocket A (binary PCM audio + JSON control)
  │
FastAPI Backend
  │
  ├── WebSocket B (Gemini Live API protocol)
  │       └── Intercept: toolCall events → Tool Dispatcher
  │                           ├── M1: TimeResolution (LangGraph)
  │                           ├── M2: SlotSearch (LangGraph)
  │                           ├── M3: ConflictResolution (LangGraph)
  │                           └── create_calendar_event, update_memory (plain Python)
  │
  └── SSE Stream → Browser (status events, trace links, booking confirmations)
```

---

## Backend Modules

### FastAPI Application (`backend/main.py`)
Entry point. Mounts CORS middleware, registers routers, initialises per-session state on lifespan startup.

### WebSocket Proxy (`backend/api/proxy.py`)
Single `/ws/voice?session_id=xxx` endpoint. On connection:
1. Opens WebSocket B to Gemini Live API.
2. Injects session config (system prompt + tools list).
3. Runs two coroutines concurrently via `asyncio.gather`:
   - `browser_to_gemini`: reads binary PCM from browser WS, encodes as base64, sends to Gemini.
   - `gemini_to_browser`: reads Gemini events; forwards audio deltas to browser; intercepts `toolCall` events for local dispatch.

### Tool Dispatcher (`backend/api/dispatcher.py`)
`execute_tool(name, args, thread_id) -> dict`  
Routes tool call by name to the correct module. Returns a dict with a `natural_language_result` field in all cases. Emits SSE status events before and after each module invocation.

### Tools Schema (`backend/api/tools_schema.py`)
Single source of truth for all Gemini Live tool definitions. A flat list of `FunctionDeclaration`-compatible dicts. Imported into proxy.py for session config and into tests for schema validation.

### SSE Stream (`backend/api/sse.py`)
`/stream/status/{session_id}` endpoint. Each session gets an `asyncio.Queue`. Any backend code calls `await emit_status(session_id, ...)` to push events. The SSE generator drains the queue in real time. Keeps browser status indicator in sync with agent execution.

### Calendar Tools (`backend/tools/calendar.py`)
All Google Calendar API calls live here. No LLM calls. No LangGraph. Pure Python functions:
- `get_credentials()` — OAuth token management (token.pickle for MVP)
- `query_freebusy(date_start, date_end, timezone)` — returns busy periods
- `compute_free_slots(date_start, date_end, busy_periods, duration_min)` — returns available slots
- `create_event(title, start_iso, end_iso, description)` — creates calendar event
- `get_event_by_title(query, search_days)` — fuzzy title search for reference events

### Memory Store (`backend/memory/store.py`)
Non-agentic. JSON file per user (MVP). Stores: usual meeting duration, preferred times, frequently referenced events.
- `load_memory(user_id) -> dict`
- `update_memory(user_id, conversation_summary, booked_event)` — LLM-assisted extraction, plain async function.

---

## Three LangGraph Modules

All three are compiled LangGraph `StateGraph` instances. Each is a self-contained pipeline with its own `TypedDict` state. They do not share state at runtime — communication is via return values.

See `agents.md` for full node-level design of each module.

| Module | Purpose | Interrupt | Calls other modules |
|---|---|---|---|
| M1: TimeResolution | Complex NL time expression → NL summary (structured window internal only) | No | No |
| M2: SlotSearch | NL time description → available slots (NL result) | No | No |
| M3: ConflictResolution | No slots → automatic retries → interrupt user → email escalation at attempt 4 | Yes (user input) | Yes (M2 via Python import) |

### NL-Only Boundary
All data crossing between Gemini Live and the LangGraph modules is natural language. Structured dicts (datetime windows, preference objects, ISO strings) exist only inside agent state and in `tools/calendar.py`. The dispatcher strips `resolved_window` from M1's return before sending to Gemini. Gemini never constructs or parses structured objects.

### M2 Input Contract
M2 is called from two places with different input forms:
1. `dispatcher.py` (via Gemini tool call) — always receives `raw_slot_description` (NL string)
2. `conflict_resolution/nodes.py` (M3 internal Python call) — always receives `structured_window` (dict)

Both call the same function: `run_slot_search()` exported from `modules/slot_search/__init__.py`. M3 never calls M2 via HTTP. This is a hard architectural rule.

---

## Frontend

### Audio Pipeline (browser)
- `getUserMedia({ audio: true })` → `AudioContext` at 16kHz
- `ScriptProcessor` (or `AudioWorklet`) → `Float32Array` samples → converted to `Int16Array` → binary WebSocket frames
- Playback: received Int16 PCM (24kHz) → `Float32Array` → `AudioBuffer` → `AudioContext.destination`

### WebSocket Client (`hooks/useWebSocket.ts`)
Manages WebSocket A lifecycle. Sends audio chunks (binary). Receives binary audio (play) and JSON frames (status, confirmation, trace link).

### SSE Client (`hooks/useStatusStream.ts`)
`EventSource` on `/stream/status/{sessionId}`. Updates React state for status indicator and booking confirmation card.

---

## Data Flow: Audio Format

| Direction | Format | Sample Rate | Encoding |
|---|---|---|---|
| Browser → Backend → Gemini | 16-bit PCM mono | 16kHz | base64 in JSON |
| Gemini → Backend → Browser | 16-bit PCM mono | 24kHz | base64 → binary WS frames |

---

## Deployment

| Component | Platform | Notes |
|---|---|---|
| Backend | Railway | Native WebSocket support; persistent file for token.pickle |
| Frontend | Vercel | Next.js native; env var NEXT_PUBLIC_BACKEND_WS_URL |

---

## External APIs

| API | Usage | Auth |
|---|---|---|
| Gemini Live API | Voice conversation, tool calls | API key |
| Gemini (non-live) | LangGraph node LLM calls | Same API key |
| Google Calendar v3 | freebusy.query, events.insert, events.list | OAuth 2.0 |
| LangSmith | Trace capture for all LangGraph runs | API key |
