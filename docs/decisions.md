# Design Decisions

## D1: Backend as Gemini Proxy (Not Direct Browser-to-Gemini)

**Decision**: The browser connects to FastAPI over WebSocket. FastAPI opens a second WebSocket to Gemini Live API and relays audio bidirectionally.

**Why not connect the browser directly to Gemini?**
- Tool calls must run Python code (LangGraph, Google Calendar API). These can only execute server-side.
- API keys must stay server-side.
- SSE status events are emitted during module execution — impossible without backend interception.
- M3 interrupt/resume requires server-side `thread_id` state management.

**Trade-off**: Adds ~10-20ms relay latency. Acceptable given the 800ms budget.

---

## D2: Three Separate LangGraph Modules, Not One Monolithic Agent

**Decision**: Split scheduling logic into M1 (TimeResolution), M2 (SlotSearch), and M3 (ConflictResolution) — each a separate compiled `StateGraph`.

**Why not one big agent?**
- A single agent with all tools would have uncontrolled reasoning paths. The LLM might call calendar APIs redundantly or in the wrong order.
- Each module has a clearly scoped state `TypedDict`. Smaller state = fewer tokens per node LLM call = lower latency.
- M3 needs `interrupt()` for user input. Isolating interrupt/resume to M3 keeps M1 and M2 simple, predictable pipelines.
- Independent testability: each module can be unit tested with mocked calendar tools.

**Trade-off**: More files. Requires explicit inter-module contracts. Worth it.

---

## D3: M3 Calls M2 via Python Import, Not HTTP

**Decision**: ConflictResolution imports `run_slot_search` directly from `modules.slot_search` and calls it as a Python function inside its graph nodes.

**Why not HTTP?**
- Introducing an HTTP hop inside a graph node adds 20-50ms and a new failure surface (network errors, retries).
- `run_slot_search` is a compiled LangGraph invocation — it's already async-safe. No thread-safety issues.
- The same `run_slot_search` function is used by both `dispatcher.py` and M3's nodes. Single implementation, two call paths. Any bug fix in M2 is automatically inherited by M3.

**Rule**: `run_slot_search` is exported from `modules/slot_search/__init__.py`. This is the only import path.

---

## D4: Gemini Live API for Voice (Not Separate STT/TTS Stack)

**Decision**: Use Gemini Live API (`gemini-2.5-flash-native-audio-preview`) as the single voice + reasoning layer. No separate STT, no separate TTS.

**Why not Deepgram + Gemini + ElevenLabs pipeline?**
- Each service hop adds latency. STT (~150ms) + LLM first token (~300ms) + TTS (~100ms) = ~550ms minimum, with queuing overhead pushing it above 800ms under load.
- Gemini Live handles STT, reasoning, tool calling, and TTS in a single WebSocket session. Internal latencies are sub-200ms end-to-end for short responses.
- Tool call interception is native to the Gemini Live protocol — no glue code.

**Trade-off**: Locked to Gemini for the voice layer. Acceptable for this scope.

---

## D5: Module 1 Is Only Called for Truly Complex Time Resolution

**Decision**: The system prompt equips the Live API model to resolve simple relative times inline ("tomorrow afternoon", "this Friday at 3pm"). M1 is only invoked for:
- References to calendar events ("after my Project Alpha meeting")
- Memory-dependent requests ("our usual sync-up")
- Complex date logic ("last weekday of the month")
- Dynamic buffer calculations ("an hour after my last meeting today")

**Why not route everything through M1?**
- M1 has 3-4 LLM node calls + a potential calendar lookup. For "Friday afternoon", that's 400ms of unnecessary latency.
- The Live API model with today's date in the system prompt can trivially compute "next Friday = April 10th".
- Over-routing to M1 would also break the natural conversational feel — the model should respond immediately to simple requests.

**Rule**: The system prompt defines time-of-day buckets explicitly (morning=7-11AM, afternoon=1-5PM, etc.) and injects `today_date` and `current_time` at session start.

---

## D6: M2 Accepts Both Structured and Natural Language Input

**Decision**: `run_slot_search` accepts either `structured_window: dict` or `raw_slot_description: str`. The `normalize_input` node handles both.

**Why?**
- When called from `dispatcher.py` after M1 has already resolved time, the input is a clean structured window dict.
- When called from `dispatcher.py` directly (user said "Friday afternoon for 30 minutes"), the Live API model passes a NL description.
- When called from M3 internally, the input is always a newly constructed structured window.

**Implementation**: `normalize_input` checks `state["structured_window"] is not None` first. If present, pass through. If not, make one LLM call to parse `raw_slot_description` into a structured window.

---

## D7: Interrupt/Resume Only in M3

**Decision**: `langgraph.types.interrupt()` is used exclusively in M3's `suggest_to_user` node. M1 and M2 never interrupt.

**Why?**
- Interrupt/resume requires a `thread_id` and a `MemorySaver` checkpointer. Adding this to M1 and M2 would complicate their state management for zero benefit — they are pure transformation pipelines that don't need user input mid-execution.
- M3 needs to pause when all automatic retry strategies are exhausted and ask the user: "Tuesday is fully booked. Would Wednesday work?" The Live API relays this question as audio, user responds, and the backend calls M3 resume with the answer.

**Resume pattern**: Backend receives user's audio answer → Gemini processes it → Gemini calls `resume_conflict_resolution` tool with `user_response` arg → dispatcher calls `run_conflict_resolution(..., resume_with=user_response)` → M3 resumes from `suggest_to_user` node.

---

## D8: Gemini Live Speaks M1's Summary Before Calling M2

**Decision**: When M1 is invoked, its `natural_language_summary` is returned to Gemini as the tool result. The system prompt instructs Gemini to speak this summary aloud before calling `search_slots`.

**Why?**
- Sub-800ms latency only applies to the audio-to-first-audio loop. Complex time resolution takes 400-600ms. If the model silently calls M1 then M2 before speaking, the user experiences 800-1200ms of silence.
- Speaking the summary ("Your flight lands Wednesday at 3 PM — let me search for Thursday morning slots") fills the processing time and provides transparency. User perceives immediate responsiveness.
- Zero additional engineering: Gemini natively generates audio responses between tool calls if the system prompt instructs it to.

---

## D9: All TypedDicts in a Single `types.py`

**Decision**: `backend/types.py` is the single source of truth for all state types. All modules import from here.

**Why?**
- Prevents state shape drift between modules (e.g., M2 returning `available_slots` and M3 expecting `slots`).
- Claude Code sessions for different modules need consistent type contracts. Pointing all sessions to the same file ensures coherence.
- When the state shape of M2's output changes, the ripple effect on M3's input is immediately visible in one file.

---

## D10: SSE for Status Events, Not WebSocket

**Decision**: Status events ("Resolving time...", "Searching calendar...") are pushed over a separate SSE endpoint, not over the existing audio WebSocket.

**Why not reuse the audio WebSocket for status?**
- The audio WebSocket is high-throughput binary. Multiplexing JSON status messages onto it requires framing logic (type byte prefix, etc.) and careful parsing on both sides.
- SSE is purpose-built for server-push text events. `EventSource` in the browser handles reconnection automatically.
- Separation of concerns: audio pipeline never blocks on status events and vice versa.

---

## D11: LangSmith Tracing via Environment Variables

**Decision**: Set `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY`, and `LANGCHAIN_PROJECT` in environment. No instrumentation code.

**Why not custom logging?**
- LangSmith auto-instruments every LangGraph node, LLM call, and tool invocation with input/output, latency, and token count — for free.
- After each module run, capture the `run_id` and emit a `trace_available` SSE event with the LangSmith URL as a clickable link in the UI.
- This gives real-time reasoning traces during demos without building a custom trace viewer.

---

## D12: Memory Is a JSON File for MVP

**Decision**: User memory (usual duration, preferred time windows, frequently referenced events) is stored in a JSON file per user. No database.

**Why?**
- The assignment is a single-user demo. Database overhead is unwarranted.
- JSON file is readable, debuggable, and trivially editable during development.
- `update_memory` uses a lightweight LLM call to extract structured facts from the conversation summary. This is a non-agentic async function — not a LangGraph graph.

**Upgrade path**: Replace JSON file store with Redis or Postgres with zero changes to the module contracts.

---

## D13: Build and Test Order

**Decision**: Build in dependency order. Never proceed to the next module without passing tests on the current one.

```
1. types.py + calendar.py       ← no dependencies
2. M2: SlotSearch               ← depends on calendar.py
3. M1: TimeResolution           ← depends on calendar.py
4. M3: ConflictResolution       ← depends on M2 (Python import)
5. api/ (proxy, dispatcher, sse) ← depends on all 3 modules
6. frontend/                    ← depends on running backend
```

**Why this order?** M2 is the most load-bearing piece — both M3 and the dispatcher call it. Get it right first. M3 importing M2 means M2 must exist before M3 can even be imported.
