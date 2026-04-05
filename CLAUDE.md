# CLAUDE.md — Smart Scheduler AI Agent

## Read This First

This is a voice-enabled AI scheduling assistant. Before touching any file, read all four docs in order:
1. `docs/prd.md` - problem statement and product being built
2. `docs/architecture.md` — system shape and component map
3. `docs/decisions.md` — 13 design decisions; these are law
4. `docs/agents.md` — node-level design of all three LangGraph modules
5. `docs/workflows.md` — end-to-end request traces

If you are starting a new session on a specific module, read `docs/agents.md` section for that module and `backend/types.py` before writing any code.

---

## Project Summary

A user speaks to a Next.js browser tab. FastAPI proxies audio between the browser and Gemini Live API over two concurrent WebSocket connections. Gemini handles voice conversation and emits `toolCall` events when scheduling logic is needed. The FastAPI dispatcher intercepts these and routes to one of three LangGraph backend modules. Status events are streamed to the browser over SSE. Meetings are booked on Google Calendar.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (App Router), TypeScript |
| Audio capture | Web Audio API → PCM → binary WebSocket frames |
| Audio playback | Web Audio API, AudioContext |
| Backend | FastAPI, Python 3.12, uvicorn, asyncio |
| Voice AI | Gemini Live API (`gemini-2.5-flash-native-audio-preview`) |
| Agent orchestration | LangGraph 0.3+ (`StateGraph`, `MemorySaver`, `interrupt`) |
| Node LLM | `gemini-2.0-flash` via `langchain-google-genai` |
| Calendar | Google Calendar API v3 (OAuth 2.0) |
| Status events | Server-Sent Events (SSE), `asyncio.Queue` per session |
| Tracing | LangSmith (`LANGCHAIN_TRACING_V2=true`) |
| Deploy | Railway (backend), Vercel (frontend) |

---

## Repository Structure

```
smart-scheduler/
├── docs/
│   ├── architecture.md
│   ├── decisions.md
│   ├── agents.md
│   └── workflows.md
├── backend/
│   ├── main.py                        # FastAPI app, lifespan, CORS, router registration
│   ├── config.py                      # Pydantic BaseSettings, reads .env
│   ├── types.py                       # ALL TypedDicts — single source of truth
│   ├── api/
│   │   ├── proxy.py                   # /ws/voice WebSocket endpoint, Gemini relay
│   │   ├── sse.py                     # /stream/status/{session_id} SSE endpoint
│   │   ├── dispatcher.py              # execute_tool() — routes toolCalls to modules
│   │   └── tools_schema.py            # TOOLS list (FunctionDeclaration dicts for Gemini Live)
│   ├── modules/
│   │   ├── time_resolution/
│   │   │   ├── graph.py               # build_time_resolution_graph()
│   │   │   ├── nodes.py               # classify_expression, lookup_reference_event, compute_window, validate_and_format
│   │   │   └── __init__.py            # exports run_time_resolution()
│   │   ├── slot_search/
│   │   │   ├── graph.py               # build_slot_search_graph()
│   │   │   ├── nodes.py               # normalize_input, query_freebusy, compute_free_slots, format_response
│   │   │   └── __init__.py            # exports run_slot_search()  ← used by dispatcher AND M3
│   │   └── conflict_resolution/
│   │       ├── graph.py               # build_conflict_resolution_graph()
│   │       ├── nodes.py               # determine_next_window, search_alternative_window, suggest_to_user, process_user_response, escalate
│   │       └── __init__.py            # exports run_conflict_resolution()
│   ├── tools/
│   │   └── calendar.py                # ALL Google Calendar API calls — nowhere else
│   ├── memory/
│   │   └── store.py                   # load_memory(), update_memory() — JSON file, no DB
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── app/page.tsx
│       ├── components/
│       │   ├── VoiceButton.tsx
│       │   ├── StatusIndicator.tsx
│       │   ├── BookingConfirmation.tsx
│       │   └── TraceLink.tsx
│       ├── hooks/
│       │   ├── useVoiceCapture.ts
│       │   ├── useWebSocket.ts
│       │   └── useStatusStream.ts
│       └── lib/audio.ts
├── tests/
│   ├── test_calendar.py
│   ├── test_slot_search.py
│   ├── test_time_resolution.py
│   └── test_conflict_resolution.py
├── CLAUDE.md
├── .env.example
└── README.md
```

---

## Module Contracts — The Binding API

These are non-negotiable. Every module exposes exactly one public function.

### M1: TimeResolution
```python
# Entry point — exported from modules/time_resolution/__init__.py
async def run_time_resolution(
    raw_expression: str,
    duration_hint: Optional[int],
    additional_constraints: str,
    user_preferences: dict
) -> dict:
    # Full internal return (dispatcher strips resolved_window before sending to Gemini):
    # {
    #   "status": "resolved" | "needs_clarification",
    #   "natural_language_summary": str,   # always populated — only field Gemini sees
    #   "resolved_window": dict | None,    # internal only — NOT forwarded to Gemini
    #   "needs_clarification": str | None,
    #   "confidence": float
    # }
    # Dispatcher sends Gemini only: status, natural_language_summary, needs_clarification, confidence
```

### M2: SlotSearch
```python
# Entry point — exported from modules/slot_search/__init__.py
# CALLED FROM TWO PLACES: dispatcher.py AND conflict_resolution/nodes.py
async def run_slot_search(
    duration_minutes: int,
    raw_slot_description: Optional[str] = None,  # always used when called from dispatcher (Gemini)
    structured_window: Optional[dict] = None      # only used for M3 internal Python calls
) -> dict:
    # Returns:
    # {
    #   "search_succeeded": bool,
    #   "available_slots": list[dict],      # [{start, end, display}]
    #   "natural_language_result": str
    # }
```

### M3: ConflictResolution
```python
# Entry point — exported from modules/conflict_resolution/__init__.py
async def run_conflict_resolution(
    situation_summary: str,            # comprehensive NL from Gemini: what user wants, what failed, any pivots
    duration_minutes: int,
    thread_id: str,
    resume_with: Optional[str] = None  # updated situation_summary from Gemini on interrupt resume
) -> dict:
    # Returns:
    # {
    #   "status": "needs_user_input" | "escalate",
    #   "suggested_slot": dict | None,     # slot found — Gemini books it if user accepts; NOT booked by M3
    #   "message_to_speak": str,           # always populated
    #   "natural_language_result": str
    # }
    # NOTE: M3 never reaches "resolved". If user accepts suggested_slot, Gemini calls
    # create_calendar_event directly without resuming M3.
    # conflict_attempts is NEVER reset between resumes — at >= 4 M3 sends an escalation email.
```

---

## Non-Negotiable Rules

Read these before writing a single line.

1. **`run_slot_search` is the only import path for M2.** Export it from `modules/slot_search/__init__.py`. M3 does `from modules.slot_search import run_slot_search`. Never duplicate the implementation.

2. **M3 calls M2 via Python import, never HTTP.** No `httpx`, no `requests`, no internal API call. Direct async function invocation.

3. **The Gemini Live model never handles structured time data.** All tool inputs from Gemini are natural language strings. All tool outputs sent back to Gemini are NL strings. Structured dicts (windows, ISO datetimes, preference objects) exist only inside LangGraph agent state and in `tools/calendar.py`. The dispatcher strips `resolved_window` from M1's return before forwarding to Gemini.

4. **All TypedDicts live in `backend/agent_types.py`.** Never define a TypedDict inline in a node file or graph file. Always import from `agent_types.py`. (`types.py` re-exports for compatibility but is not the source of truth.)

5. **All Google Calendar API calls live in `backend/tools/calendar.py`.** No calendar API calls anywhere else — not in nodes, not in dispatcher.

6. **`interrupt()` is used exclusively in M3.** M1 and M2 are pure pipelines — they never pause.

7. **`thread_id` flows through every tool call.** It is required for M3 interrupt/resume. The dispatcher always passes it. The Live API always includes it in tool call args.

8. **Every module output includes `natural_language_result: str`.** This is what the dispatcher returns to Gemini. It must always be a complete, speakable sentence.

9. **No synchronous code in hot paths.** Everything in `api/`, `modules/`, and `tools/calendar.py` must be `async def`. Use `httpx.AsyncClient`, not `requests`.

10. **Node functions return only the state keys they update.** Partial returns only — never return the full state from a node.

11. **LLM calls in nodes use `temperature=0` for structured output, `temperature=0.3` for natural language generation.**

12. **M3 never calls `create_calendar_event`.** When M3 surfaces a `suggested_slot`, it interrupts and returns the slot to Gemini. If the user accepts, Gemini calls `create_calendar_event` directly without resuming M3. Only call `resume_conflict_resolution` when the user rejects or pivots.

13. **`conflict_attempts` in M3 is never reset between interrupts.** It monotonically increments across the entire conflict resolution session. At `conflict_attempts >= 4`, M3 sends an escalation email and terminates.

---

## State Types Quick Reference

Full definitions in `backend/types.py`. Abbreviated here for orientation.

```python
# M1 state — key fields
TimeResolutionState:
  raw_expression, duration_hint, additional_constraints, user_preferences  # inputs
  expression_type       # "event_anchored"|"relative_date"|"memory_dependent"|"complex_date"|"deadline_anchored"
  referenced_event      # dict from calendar lookup, or None
  resolved_window       # {date_start, date_end, preferred_start_hour, preferred_end_hour, duration_minutes}
  natural_language_summary, needs_clarification, confidence, status       # outputs

# M2 state — key fields
SlotSearchState:
  raw_slot_description, structured_window, duration_minutes   # inputs (one of first two populated)
  normalized_window     # always set after normalize_input node
  busy_periods          # from calendar freebusy query
  available_slots       # [{start: ISO, end: ISO, display: str}]
  natural_language_result, search_succeeded                   # outputs

# M3 state — key fields
ConflictState:
  situation_summary, duration_minutes, thread_id   # inputs (situation_summary is NL, updated on every resume)
  current_preferences, current_failed_window       # parsed from situation_summary by determine_next_window (LLM, temp=0)
  conflict_attempts     # monotonically increments; NEVER reset between resumes; >= 4 triggers email escalation
  tried_windows         # list of windows already searched — never re-tried
  current_search_window, last_search_result        # execution state
  suggested_slot, escalation_needed, message_to_speak        # outputs (M3 never books — Gemini does)
  natural_language_result, status                            # "needs_user_input" | "escalate"
```

---

## LangGraph Patterns Used in This Project

### Standard pipeline (M1, M2)
```python
builder = StateGraph(StateType)
builder.add_node("node_name", node_function)
builder.set_entry_point("first_node")
builder.add_edge("node_a", "node_b")
graph = builder.compile()
result = await graph.ainvoke(initial_state)
```

### Conditional routing
```python
def router(state: StateType) -> str:
    if state["expression_type"] == "event_anchored":
        return "lookup_reference_event"
    return "compute_window"

builder.add_conditional_edges("classify_expression", router, {
    "lookup_reference_event": "lookup_reference_event",
    "compute_window": "compute_window"
})
```

### Interrupt/resume (M3 only)
```python
# Build with checkpointer
checkpointer = MemorySaver()
graph = builder.compile(checkpointer=checkpointer, interrupt_before=["suggest_to_user"])

# First invocation — runs until interrupt
result = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": thread_id}})
# result["__interrupt__"] is set; graph is paused

# Resume after user responds — resume value is Gemini's updated situation_summary
from langgraph.types import Command
result = await graph.ainvoke(Command(resume=updated_situation_summary), config={"configurable": {"thread_id": thread_id}})
```

### Streaming node events for SSE
```python
async for event in graph.astream(initial_state, config=config, stream_mode="updates"):
    node_name = list(event.keys())[0]
    await emit_status(session_id, STATUS_MAP[node_name], node_name)
```

---

## Gemini Live API Event Protocol

### Receive tool call from Gemini
```json
{
  "toolCall": {
    "functionCalls": [{
      "id": "call_abc123",
      "name": "search_slots",
      "args": { "raw_slot_description": "Thursday April 9th 2026, 10:30 AM to 6 PM, 30 minutes", "duration_minutes": 30, "thread_id": "session-xyz" }
    }]
  }
}
```

### Send tool result back to Gemini
```json
{
  "toolResponse": {
    "functionResponses": [{
      "id": "call_abc123",
      "response": { "result": { "search_succeeded": true, "natural_language_result": "..." } }
    }]
  }
}
```

### Send audio chunk to Gemini
```json
{
  "realtimeInput": {
    "mediaChunks": [{ "mimeType": "audio/pcm;rate=16000", "data": "<base64 PCM>" }]
  }
}
```

---

## SSE Status Event Map

When LangGraph nodes execute, emit the corresponding SSE event:

```python
STATUS_MAP = {
    # M1
    "classify_expression":      "Understanding your request...",
    "lookup_reference_event":   "Looking up {event} on your calendar...",
    "compute_window":           "Resolving time reference...",
    # M2
    "normalize_input":          "Preparing calendar search...",
    "query_freebusy":           "Checking your availability...",
    "format_response":          "Finding open slots...",
    # M3
    "determine_next_window":    "Looking for alternatives...",
    "search_alternative_window":"Searching {day}...",
    "suggest_to_user":          "Waiting for your preference...",
    # Dispatcher
    "create_calendar_event":    "Booking your meeting...",
    "update_memory":            "Updating your preferences...",
}
```

---

## Build Order

Do not skip steps. Do not build the next item without tests passing on the current one.

```
Step 1: backend/types.py                        — all TypedDicts, no logic
Step 2: backend/tools/calendar.py + tests       — query_freebusy, compute_free_slots, create_event, get_event_by_title
Step 3: backend/modules/slot_search/ + tests    — M2, including both NL and structured input paths
Step 4: backend/modules/time_resolution/ + tests — M1, all five expression_type branches
Step 5: backend/modules/conflict_resolution/ + tests — M3, full retry ladder + interrupt/resume
Step 6: backend/api/ + integration tests        — proxy, dispatcher, sse, tools_schema
Step 7: frontend/                               — connect to running backend
```

---

## Environment Variables

```bash
# backend/.env
GEMINI_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=smart-scheduler
USER_TIMEZONE=Asia/Kolkata
ESCALATION_EMAIL=
```

---

## How to Run (Dev)

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev       # runs on :3000

# Tests
cd backend
pytest tests/ -v
```

---

## Common Pitfalls

- **Do not** use `asyncio.run()` inside async functions — you are already inside an event loop
- **Do not** use `graph.invoke()` — always use `graph.ainvoke()` or `graph.astream()`
- **Do not** call the Google Calendar API without checking credentials first — `get_credentials()` handles token refresh
- **Do not** pass the full `ConflictState` to `run_slot_search` — extract and pass only `current_search_window` (as `structured_window`) and `duration_minutes`
- **Do not** define the system prompt as a static string — it is built dynamically with `today_date`, `now_time`, `thread_id`, and `memory_summary` injected at session start
- **Do not** stream audio as HTTP — WebSocket only
- When testing M3 interrupt/resume: the `thread_id` must be identical between the initial call and the resume call
