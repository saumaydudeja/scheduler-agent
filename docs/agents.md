# Agents

## Overview

The backend has three LangGraph modules. Each is a compiled `StateGraph` with a `TypedDict` state. They are independent — no shared in-memory state. Communication is exclusively via return values.

All state types live in `backend/types.py`. All calendar API calls live in `backend/tools/calendar.py`. All LLM calls inside nodes use `ChatGoogleGenerativeAI` (gemini-2.0-flash) with structured output where applicable.

---

## M1: TimeResolution

**File**: `backend/modules/time_resolution/`  
**Entry**: `run_time_resolution(raw_expression, duration_hint, constraints, user_prefs) -> dict`  
**Purpose**: Convert a complex natural language time expression into a concrete structured datetime window.

### When M1 is Called
- References to calendar events: "after my flight", "before Project Alpha"
- Memory-dependent: "our usual sync-up", "my regular standup"
- Complex date logic: "last weekday of the month", "two weeks from kick-off"
- Dynamic buffer: "an hour after my last meeting today", "30 minutes before my flight"
- Contextual deadline: "sometime before my flight that leaves Friday at 6 PM"

### When M1 is NOT Called
Simple relative times resolved inline by Live API: "tomorrow morning", "this Friday at 3pm", "next Tuesday afternoon", "April 7th at 2pm".

### State

```python
class TimeResolutionState(TypedDict):
    # Inputs
    raw_expression: str               # "after my Project Alpha meeting next week"
    duration_hint: Optional[int]      # minutes, if known (may be None)
    additional_constraints: str       # "not too early" / "" if none
    user_preferences: dict            # from memory: usual durations, preferred times

    # Computed during execution
    expression_type: str              # "event_anchored" | "relative_date" | "memory_dependent" | "complex_date" | "deadline_anchored"
    referenced_event: Optional[dict]  # {title, start_iso, end_iso, location} from calendar lookup

    # Outputs
    resolved_window: Optional[dict]   # {date_start, date_end, preferred_start_hour, preferred_end_hour, duration_minutes}
    natural_language_summary: str     # "Project Alpha is Thursday at 10AM. I'll search after that."
    needs_clarification: Optional[str] # "What day would you like?" — only when truly ambiguous
    confidence: float                  # 0-1, low confidence triggers clarification
    status: str                        # "resolved" | "needs_clarification"
```

### Nodes

#### `classify_expression`
LLM call (structured output). Classifies `raw_expression` into one of the five `expression_type` values. Also extracts any event name references, buffer durations, or deadline times mentioned.

```python
# Prompt focus: "Given this time expression, what type is it, and what entities does it reference?"
# Output: { expression_type, referenced_event_name, buffer_minutes, deadline_time }
```

#### `lookup_reference_event`
Runs when `expression_type in ["event_anchored", "deadline_anchored"]`.  
Calls `calendar.get_event_by_title(referenced_event_name, search_days=30)`.  
If no event found: sets `needs_clarification = "I couldn't find [event]. Could you tell me when it is?"` and routes to `validate_and_format` early.

#### `load_from_memory`
Runs when `expression_type == "memory_dependent"`.  
Calls `memory.load_memory(user_id)`.  
Extracts relevant context: usual meeting duration, preferred time windows for recurring meetings.

#### `compute_window`
LLM call (structured output). Given the classified expression, any looked-up event, and user preferences, computes the final structured window.

```python
# Input context:
#   - expression_type and raw_expression
#   - referenced_event (if any): {start_iso, end_iso}
#   - buffer_minutes (if any)
#   - memory context (if any)
#   - today's date and time (injected from config)
# Output: resolved_window dict + confidence score

# Examples:
# "after Project Alpha at 10AM with 30min buffer" → {date_start: "10:30", date_end: "18:00"}
# "before flight at 6PM on Friday, 1 hour meeting" → {date_start: "09:00", date_end: "17:00"}
# "last weekday of April" → {date: "2026-04-30"} (computed via Python dateutil, not LLM)
```

Note: "last weekday of month" and similar pure date-logic cases are handled with Python (`dateutil`/`datetime`) in `compute_window` without an LLM call — deterministic is better than probabilistic for date arithmetic.

#### `validate_and_format`
Non-LLM node. Validates the resolved_window (sensible start/end, not in the past, within 60 days). Sets `status`. Formats `natural_language_summary` from resolved fields.

### Graph Edges

```
classify_expression
    → (event_anchored | deadline_anchored) → lookup_reference_event → compute_window
    → memory_dependent → load_from_memory → compute_window
    → (relative_date | complex_date) → compute_window
compute_window → validate_and_format → END
```

### Return Value to Dispatcher

The full Python return dict (used internally by dispatcher):
```python
{
    "status": "resolved",  # or "needs_clarification"
    "natural_language_summary": "Project Alpha is Thursday at 10 AM. I'll search for slots after that.",
    "resolved_window": {           # internal only — dispatcher strips this before sending to Gemini
        "date_start": "2026-04-09T10:30:00",
        "date_end": "2026-04-09T18:00:00",
        "preferred_start_hour": 10,
        "preferred_end_hour": 18,
        "duration_minutes": 30
    },
    "needs_clarification": None,
    "confidence": 0.94
}
```

What the dispatcher forwards to Gemini as `toolResponse` (NL only — no structured data):
```python
{
    "status": "resolved",
    "natural_language_summary": "Project Alpha is Thursday at 10 AM. I'll search for slots after that.",
    "needs_clarification": None,
    "confidence": 0.94
}
```

---

## M2: SlotSearch

**File**: `backend/modules/slot_search/`  
**Entry**: `run_slot_search(structured_window=None, raw_slot_description=None, duration_minutes) -> dict`  
**Purpose**: Given a time window (structured or NL), find available calendar slots of the requested duration.

### Input Contract
M2 is called from two places with different input forms:
- From `dispatcher.py` (via Gemini tool call): always receives `raw_slot_description` (NL string). Gemini never passes a structured window dict.
- From `M3/nodes.py` (internal Python call): always receives `structured_window` (dict). M3 constructs windows programmatically and bypasses NL parsing.

The `normalize_input` node handles both paths. Downstream nodes always work with `normalized_window`.

### State

```python
class SlotSearchState(TypedDict):
    # Inputs (one of these will be populated)
    raw_slot_description: Optional[str]    # "Thursday morning for 30 minutes"
    structured_window: Optional[dict]      # {date_start, date_end, preferred_start_hour, preferred_end_hour}
    duration_minutes: int

    # Computed
    normalized_window: Optional[dict]      # always populated after normalize_input
    busy_periods: list[dict]               # [{start: ISO, end: ISO}] from freebusy API

    # Outputs
    available_slots: list[dict]            # [{start: ISO, end: ISO, display: "Thursday 9:00 AM"}]
    natural_language_result: str           # "I found slots at 9:30 AM, 11:00 AM, or 3:30 PM"
    search_succeeded: bool
```

### Nodes

#### `normalize_input`
Decision: if `structured_window is not None`, copy to `normalized_window` directly (no LLM call).  
If only `raw_slot_description` is provided, make one LLM call (structured output) to parse it into a window dict.

```python
# Only LLM call in this node, only when needed:
# Input: "Thursday morning for 30 minutes", today_date
# Output: { date_start: "2026-04-09T07:00:00", date_end: "2026-04-09T11:00:00", duration_minutes: 30 }
```

#### `query_freebusy`
Non-LLM. Calls `calendar.query_freebusy(normalized_window.date_start, normalized_window.date_end, timezone)`.  
Returns list of busy periods. Stores in `state["busy_periods"]`.

#### `compute_free_slots`
Non-LLM. Calls `calendar.compute_free_slots(window, busy_periods, duration_min)`.  
Pure Python: walks the window from preferred_start_hour to preferred_end_hour, subtracts busy intervals, collects gaps ≥ duration_min. Populates `available_slots`.  
Sets `search_succeeded = len(available_slots) > 0`.

#### `format_response`
LLM call (or template). Converts `available_slots` list + `search_succeeded` into a natural language result string suitable for the Live API model to process or speak.

```python
# If succeeded: "I found slots at 10:30 AM, 11:00 AM, and 3:30 PM on Thursday April 9th."
# If failed: "Thursday afternoon is fully booked. No 30-minute slots available."
```

### Graph Edges

```
normalize_input → query_freebusy → compute_free_slots → format_response → END
```

All edges are unconditional. M2 always runs all nodes. It never interrupts.

### Return Value

```python
{
    "search_succeeded": True,
    "available_slots": [
        {"start": "2026-04-09T10:30:00", "end": "2026-04-09T11:00:00", "display": "Thursday 10:30 AM"},
        {"start": "2026-04-09T11:00:00", "end": "2026-04-09T11:30:00", "display": "Thursday 11:00 AM"},
    ],
    "natural_language_result": "I found slots at 10:30 AM or 11:00 AM on Thursday April 9th."
}
```

---

## M3: ConflictResolution

**File**: `backend/modules/conflict_resolution/`  
**Entry**: `run_conflict_resolution(situation_summary, duration_minutes, thread_id, resume_with=None) -> dict`  
**Purpose**: When M2 finds no available slots, automatically try alternative windows. Escalate to user via interrupt when automatic strategies are exhausted. Preferences are re-derived from Gemini's `situation_summary` on every invocation, including resumes, so mid-conversation pivots are always reflected.

### State

```python
class ConflictState(TypedDict):
    # Inputs — always NL strings from Gemini
    situation_summary: str         # Comprehensive NL from Gemini: what user wants, what failed, how request evolved
                                   # e.g. "User wants a 1-hour meeting Tuesday afternoon. Tuesday 1PM–5PM was fully
                                   #       booked. Full Tuesday also failed. User now wants to try Wednesday afternoon."
    duration_minutes: int
    thread_id: str                 # required for interrupt/resume

    # Derived from situation_summary on every determine_next_window call (LLM parse, temp=0)
    current_preferences: Optional[dict]   # {day_pref, time_pref} — reflects latest user ask
    current_failed_window: Optional[dict] # {date, start_hour, end_hour} — most recent failed window

    # Execution state
    conflict_attempts: int         # incremented each retry cycle; NEVER reset between interrupts; >= 4 triggers email escalation
    tried_windows: list[dict]      # windows M3 has already searched — never re-tried
    current_search_window: Optional[dict]
    last_search_result: Optional[dict]

    # Outputs
    current_attempt_succeeded: bool
    suggested_slot: Optional[dict] # slot M3 found and surfaced — Gemini decides whether to book it
    escalation_needed: bool
    message_to_speak: str          # what Live API should say to user
    natural_language_result: str
    status: str                    # "needs_user_input" | "escalate"
                                   # NOTE: M3 never reaches "resolved" — Gemini detects user acceptance
                                   # and calls create_calendar_event directly without resuming M3
```

### Nodes

#### `determine_next_window`
**Always runs first** — on fresh invocation and on every resume. One LLM call (structured output, `temperature=0`) to parse `situation_summary` into `current_preferences` and `current_failed_window`. This ensures any user pivot (e.g. "try Wednesday instead") is fully reflected in the current search target. Uses `tried_windows` to skip windows already exhausted in this session.

Decides next window to try based on `conflict_attempts`:
- Attempt 0: Parse `situation_summary`; expand same day — if `time_pref` was "afternoon", try full day 8AM–8PM
- Attempt 1: Next business day — compute next 1-2 weekdays, preserve `time_pref` from `current_preferences`
- Attempt 2: Another next business day candidate, or set `message_to_speak` to ask user for a new preference if no good candidates remain
- Attempt 3: Try the preference from the resumed `situation_summary` (user's new ask after interrupt)
- Attempt >= 4: Set `escalation_needed = True` → route to `escalate`

#### `search_alternative_window`
Calls `run_slot_search(structured_window=current_search_window, duration_minutes=...)` directly.  
**This is a direct Python function call to M2, not a graph invocation or HTTP call.**  
Appends `current_search_window` to `tried_windows`. Sets `current_attempt_succeeded` and `last_search_result`.

```python
# Inside this node:
from modules.slot_search import run_slot_search

result = await run_slot_search(
    structured_window=state["current_search_window"],
    duration_minutes=state["duration_minutes"]
)
# track what we've tried to avoid repeating
tried_windows = state["tried_windows"] + [state["current_search_window"]]
return {
    "tried_windows": tried_windows,
    "current_attempt_succeeded": result["search_succeeded"],
    "last_search_result": result,
}
```

#### `route_after_search`
Conditional edge function. Not a node — a router:
- If `escalation_needed` (`conflict_attempts >= 4`): → `escalate`
- If `current_attempt_succeeded`: → `prepare_success_response`
- If `conflict_attempts >= 2` and not succeeded: → `suggest_to_user` (interrupt, ask for new preference)
- Else: increment `conflict_attempts`, loop back to `determine_next_window`

#### `prepare_success_response`
Non-LLM. Sets `suggested_slot`, formats `message_to_speak` ("I found {day} at {time}. Does that work?"). Sets `status = "needs_user_input"`. Increments `conflict_attempts`. Leads to interrupt.  
**Booking is Gemini's responsibility**, not M3's. If the user accepts, Gemini calls `create_calendar_event` directly. If the user rejects, Gemini calls `resume_conflict_resolution` with an updated `situation_summary`.

#### `suggest_to_user`
This node calls `interrupt()`. Graph execution pauses here.  
`message_to_speak` is already set by `prepare_success_response` or `determine_next_window`.  
Dispatcher receives the interrupt, sends `message_to_speak` (and `suggested_slot` if present) back to Live API as tool result.  
Live API speaks the message. Two outcomes:
- **User accepts the slot**: Gemini calls `create_calendar_event` directly. M3 is done — `resume_conflict_resolution` is NOT called.
- **User rejects or pivots**: Gemini synthesises an updated `situation_summary` and calls `resume_conflict_resolution`. M3 resumes with `conflict_attempts` preserved (not reset) and re-enters `determine_next_window`.

#### `escalate`
Calls an internal email function (`send_escalation_email(to=config.ESCALATION_EMAIL, body=situation_summary)`).  
Sets `status = "escalate"`, `natural_language_result = "I've sent a summary email to [email]. Someone will follow up to schedule this manually."`.  
This is the terminal state — M3 ends here.

### Graph Edges

```
determine_next_window → search_alternative_window → route_after_search
  route_after_search → (attempts >= 4) → escalate → END           [email sent]
  route_after_search → (succeeded) → prepare_success_response → suggest_to_user
  route_after_search → (not succeeded, attempts < 2) → determine_next_window [loop, increment attempts]
  route_after_search → (attempts >= 2, no slots) → suggest_to_user [interrupt, ask for new preference]
  suggest_to_user → (user accepts) → Gemini calls create_calendar_event directly — M3 NOT resumed
  suggest_to_user → (user rejects/pivots) → resume_conflict_resolution called by Gemini
                                          → M3 resumes, conflict_attempts preserved
                                          → determine_next_window [new situation_summary, same counter]
  escalate → END
```

Notes:
- `process_user_response` is intentionally absent. Gemini interprets user replies and synthesises updated `situation_summary`.
- M3 never calls `create_calendar_event`. Booking is always Gemini's decision.
- `conflict_attempts` is never reset. It monotonically increases across interrupts until it hits 4 and triggers email escalation.

### Graph Builder

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def build_conflict_resolution_graph():
    builder = StateGraph(ConflictState)
    builder.add_node("determine_next_window", determine_next_window)
    builder.add_node("search_alternative_window", search_alternative_window)
    builder.add_node("prepare_success_response", prepare_success_response)
    builder.add_node("suggest_to_user", suggest_to_user)
    builder.add_node("escalate", escalate)

    builder.set_entry_point("determine_next_window")
    builder.add_edge("determine_next_window", "search_alternative_window")
    builder.add_conditional_edges("search_alternative_window", route_after_search, {...})
    builder.add_edge("prepare_success_response", "suggest_to_user")
    builder.add_edge("escalate", END)

    checkpointer = MemorySaver()  # required for interrupt/resume
    return builder.compile(checkpointer=checkpointer, interrupt_before=["suggest_to_user"])
```

### Resume Pattern

```python
# In dispatcher.py:

async def run_conflict_resolution(
    situation_summary: str,
    duration_minutes: int,
    thread_id: str,
    resume_with: Optional[str] = None   # updated situation_summary from Gemini on resume
) -> dict:
    graph = get_conflict_graph()  # compiled singleton
    config = {"configurable": {"thread_id": thread_id}}

    if resume_with:
        # Resume from interrupt — resume_with is Gemini's updated situation_summary
        # conflict_attempts is NOT reset — preserved from checkpoint
        # Graph resumes at suggest_to_user node, then re-enters determine_next_window with new summary
        result = await graph.ainvoke(
            Command(resume=resume_with),
            config=config
        )
    else:
        # Fresh invocation
        initial_state = {
            "situation_summary": situation_summary,
            "duration_minutes": duration_minutes,
            "thread_id": thread_id,
            "conflict_attempts": 0,
            "tried_windows": [],
        }
        result = await graph.ainvoke(initial_state, config=config)

    return result
```

---

## Tool Definitions for Gemini Live (`backend/api/tools_schema.py`)

```python
TOOLS = [
    {
        "name": "resolve_time_expression",
        "description": "Resolves complex natural language time expressions that reference calendar events, memory, or require advanced date logic. NOT for simple relative times like 'tomorrow' or 'next Friday'.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_expression": { "type": "string", "description": "The original time expression from user" },
                "duration_hint": { "type": "integer", "description": "Meeting duration in minutes if known" },
                "additional_constraints": { "type": "string", "description": "Any extra constraints mentioned" },
                "thread_id": { "type": "string" }
            },
            "required": ["raw_expression", "thread_id"]
        }
    },
    {
        "name": "search_slots",
        "description": "Search Google Calendar for available meeting slots. Always pass a natural language time description — never a structured object.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_slot_description": { "type": "string", "description": "NL time description including day, date, time range, and duration. E.g. 'Thursday April 9th 2026, 10:30 AM to 6 PM, 30 minutes'" },
                "duration_minutes": { "type": "integer" },
                "thread_id": { "type": "string" }
            },
            "required": ["raw_slot_description", "duration_minutes", "thread_id"]
        }
    },
    {
        "name": "invoke_conflict_resolution",
        "description": "Called when search_slots returns search_succeeded=false. Tries alternative times automatically, then escalates to user if needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "situation_summary": { "type": "string", "description": "Comprehensive NL summary: what the user wants, what time window failed, any constraints. E.g. 'User wants a 1-hour meeting Tuesday afternoon. Tuesday 1 PM–5 PM was fully booked.'" },
                "duration_minutes": { "type": "integer" },
                "thread_id": { "type": "string" }
            },
            "required": ["situation_summary", "duration_minutes", "thread_id"]
        }
    },
    {
        "name": "resume_conflict_resolution",
        "description": "Resume conflict resolution after the user responds to a question. Provide an updated situation summary reflecting the user's latest preference.",
        "parameters": {
            "type": "object",
            "properties": {
                "situation_summary": { "type": "string", "description": "Updated NL summary incorporating the user's response. E.g. 'User originally wanted Tuesday afternoon (fully booked). Full Tuesday also failed. User now wants to try Wednesday afternoon instead.'" },
                "thread_id": { "type": "string" }
            },
            "required": ["situation_summary", "thread_id"]
        }
    },
    {
        "name": "create_calendar_event",
        "description": "Create a Google Calendar event after the user has confirmed the slot.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": { "type": "string" },
                "start_iso": { "type": "string", "description": "ISO 8601 datetime" },
                "end_iso": { "type": "string" },
                "description": { "type": "string" }
            },
            "required": ["title", "start_iso", "end_iso"]
        }
    },
    {
        "name": "update_memory",
        "description": "Called at end of conversation to update user preferences and meeting history.",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_summary": { "type": "string" },
                "booked_event": { "type": "object" }
            },
            "required": ["conversation_summary"]
        }
    }
]
```

---

## LLM Configuration for Agent Nodes

All LangGraph node LLM calls use `gemini-2.0-flash` (not the Live model — that's only for voice).

```python
from langchain_google_genai import ChatGoogleGenerativeAI

# For structured output nodes (classification, window computation):
llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
structured_llm = llm.with_structured_output(OutputSchema)

# For formatting nodes (natural language generation):
llm_generative = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0.3)
```

LangSmith auto-traces every LLM call in every node when `LANGCHAIN_TRACING_V2=true` is set. No instrumentation code needed.
