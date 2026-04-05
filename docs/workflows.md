# Workflows

## W1: Full Request Lifecycle

This traces a complete interaction from user speaking to calendar event created.

### Example: "Find me a slot after my Project Alpha meeting next week for 30 minutes"

```
[BROWSER]
  User clicks mic → getUserMedia() → AudioContext (16kHz)
  ScriptProcessor captures PCM → Int16Array → binary WS frames
  WS frames → WebSocket A → FastAPI

[FASTAPI: browser_to_gemini coroutine]
  Receives binary PCM chunks
  Encodes as base64 → sends to Gemini WS B as realtime_input

[GEMINI LIVE API]
  VAD processes audio stream
  Detects end-of-utterance (600ms silence)
  Understands: "after Project Alpha meeting next week, 30 minutes"
  Decides: call resolve_time_expression
  Sends toolCall event over WS B

[FASTAPI: gemini_to_browser coroutine]
  Receives toolCall event
  Does NOT forward to browser
  Calls: execute_tool("resolve_time_expression", args, thread_id)
  Calls: emit_status(session_id, "Looking up Project Alpha on your calendar...", "time_resolution")

[BROWSER]
  SSE event received → UI shows "🔍 Looking up Project Alpha..."

[M1: TimeResolution]
  classify_expression → "event_anchored"
  lookup_reference_event → Calendar API: search "Project Alpha" → event on Thu April 9, 10:00AM
  compute_window → window: Thu April 9, 10:30AM - 6:00PM
  validate_and_format → full internal result includes resolved_window dict (used by dispatcher only)

[FASTAPI dispatcher]
  emit_status(session_id, "Searching your calendar...", "slot_search")
  Strips resolved_window before forwarding — sends NL-only toolResponse to Gemini:
  {
    status: "resolved",
    natural_language_summary: "Project Alpha is Thursday April 9th at 10 AM. I'll look for a 30-minute slot after that.",
    needs_clarification: null,
    confidence: 0.94
  }

[GEMINI LIVE API]
  Receives NL-only M1 result
  [SPEAKS ALOUD]: "Project Alpha is Thursday April 9th at 10 AM. Let me search for a 30-minute slot after that."
  Audio streams back over WS B → FastAPI → WS A → Browser (user hears it)
  Decides: call search_slots with NL description derived from the summary
  search_slots({ raw_slot_description: "Thursday April 9th 2026, 10:30 AM to 6 PM, 30 minutes", duration_minutes: 30, thread_id: "..." })

[M2: SlotSearch]
  normalize_input → raw_slot_description provided → LLM parses to normalized_window
  query_freebusy → Calendar API: freebusy query Thu April 9, 10:30AM-6PM
  compute_free_slots → gaps ≥ 30min: [10:30, 11:00, 11:30, 3:30PM, 4:00PM, 4:30PM]
  format_response → {
    search_succeeded: true,
    available_slots: [{ start: "10:30", display: "10:30 AM" }, ...],
    natural_language_result: "I found slots at 10:30 AM, 11:00 AM, or 3:30 PM on Thursday."
  }

[FASTAPI dispatcher]
  Sends M2 result to Gemini as toolResponse

[GEMINI LIVE API]
  [SPEAKS ALOUD]: "I found Thursday at 10:30 AM, 11:00 AM, or 3:30 PM. Which works best for you?"
  Audio → browser

[USER RESPONDS]: "10:30 AM works"

[GEMINI LIVE API]
  Calls create_calendar_event("Meeting", "2026-04-09T10:30:00", "2026-04-09T11:00:00")

[FASTAPI dispatcher]
  Calls calendar.create_event(...) → Google Calendar API
  emit_status(session_id, "Booking confirmed!", "booking", event={...}, calendar_link="...")

[GEMINI LIVE API]
  Calls update_memory(summary, booked_event)

[FASTAPI dispatcher]
  Calls memory.update_memory(...) — plain async function

[GEMINI LIVE API]
  [SPEAKS ALOUD]: "Done! Your 30-minute meeting is booked for Thursday April 9th at 10:30 AM."

[BROWSER]
  SSE booking_confirmed event → BookingConfirmation card renders with event details + calendar link
  SSE trace_available event → LangSmith trace link appears in UI
```

---

## W2: Conflict Resolution Workflow

Triggered when M2 returns `search_succeeded: false`.

### Example: "Find me a 1-hour slot this Tuesday afternoon"

```
[Live API calls search_slots]
  raw_slot_description: "Tuesday April 7th 2026, 1 PM to 5 PM, 60 minutes"
  duration_minutes: 60

[M2: SlotSearch]
  normalize_input → LLM parses NL to normalized_window
  query_freebusy → Tuesday April 7, 1PM-5PM fully booked
  search_succeeded: false
  natural_language_result: "Tuesday afternoon is fully booked."

[Live API receives failure]
  Calls invoke_conflict_resolution({
    situation_summary: "User wants a 1-hour meeting Tuesday afternoon. Tuesday April 7th 1 PM–5 PM was fully booked.",
    duration_minutes: 60,
    thread_id: "session-abc"
  })

[M3: conflict_attempts=0 — expand same day]
  determine_next_window → parse situation_summary → current_preferences: {day: Tuesday, time_pref: afternoon}
  → expand to full Tuesday 8AM-8PM; add to tried_windows
  search_alternative_window → run_slot_search(Tuesday 8AM-8PM, 60min) [M2 internal Python call]
    → still no 60-minute gaps on Tuesday
    → search_succeeded: false; conflict_attempts → 1

[M3: conflict_attempts=1 — next business day]
  determine_next_window → re-parse situation_summary → Wednesday April 8, afternoon (time_pref preserved)
  search_alternative_window → run_slot_search(Wednesday afternoon, 60min)
    → slots found: [2:00 PM, 3:00 PM]
    → search_succeeded: true; conflict_attempts → 2

  suggested_slot: { start: "2026-04-08T14:00:00", end: "2026-04-08T15:00:00" }
  message_to_speak: "Tuesday is fully booked for an hour. I found Wednesday afternoon at 2:00 PM or 3:00 PM. Would either work?"
  status: "needs_user_input"
  → interrupt() ← graph pauses here, conflict_attempts=2 saved to checkpoint

[FASTAPI dispatcher]
  Receives interrupt result
  Sends { message_to_speak, suggested_slot, status, natural_language_result } to Gemini as toolResponse

[GEMINI LIVE API]
  [SPEAKS ALOUD]: "Tuesday is fully booked for an hour. I found Wednesday at 2 PM or 3 PM — would either work?"

--- PATH A: User accepts ---

[USER RESPONDS]: "2 PM works"

[GEMINI LIVE API]
  Detects user acceptance of a slot that was already surfaced
  Does NOT call resume_conflict_resolution
  Calls create_calendar_event("Meeting", "2026-04-08T14:00:00", "2026-04-08T15:00:00") directly
  M3 session is complete — no resume needed

[FASTAPI dispatcher]
  Calls calendar.create_event(...)
  ... booking confirmed

--- PATH B: User rejects, requests different time ---

[USER RESPONDS]: "Can you try Thursday morning instead?"

[GEMINI LIVE API]
  Detects user rejection and new preference
  Calls resume_conflict_resolution({
    situation_summary: "User originally wanted Tuesday afternoon (fully booked). Wednesday afternoon was offered but user wants Thursday morning instead.",
    thread_id: "session-abc"
  })

[FASTAPI dispatcher]
  Calls run_conflict_resolution(..., resume_with="User originally wanted Tuesday afternoon (fully booked). Wednesday afternoon was offered but user wants Thursday morning instead.")
  M3 resumes: situation_summary updated, conflict_attempts=2 PRESERVED (not reset)
  determine_next_window → re-parses new summary → current_preferences: {day: Thursday, time_pref: morning}
  Thursday morning not in tried_windows → sets current_search_window
  search_alternative_window → run_slot_search(Thursday morning, 60min)
    → slots found: [9:00 AM, 10:00 AM]; conflict_attempts → 3

  suggested_slot: { start: "2026-04-09T09:00:00", end: "2026-04-09T10:00:00" }
  message_to_speak: "Thursday morning has 9 AM or 10 AM free. Which works?"
  status: "needs_user_input"
  → interrupt() again, conflict_attempts=3 saved to checkpoint

[GEMINI LIVE API]
  [SPEAKS ALOUD]: "Thursday morning has 9 AM or 10 AM free. Which works?"

[USER RESPONDS]: "9 AM"

[GEMINI LIVE API]
  Detects user acceptance — calls create_calendar_event directly, does NOT resume M3

--- PATH C: conflict_attempts reaches 4 (escalation) ---

[If user keeps rejecting after the interrupt at conflict_attempts=3]
  Gemini calls resume_conflict_resolution with another updated summary
  M3 resumes, conflict_attempts=3 → determine_next_window → conflict_attempts becomes 4
  route_after_search detects escalation_needed=True → routes to escalate node

[M3: escalate node]
  Calls send_escalation_email(to=config.ESCALATION_EMAIL, body=situation_summary)
  Returns:
    status: "escalate"
    natural_language_result: "I've sent a summary email to [email]. Someone will follow up to schedule this manually."

[FASTAPI dispatcher]
  Sends escalation result to Gemini as toolResponse

[GEMINI LIVE API]
  [SPEAKS ALOUD]: "I wasn't able to find a time that works. I've sent an email to [email] — someone will reach out to get this scheduled."
```

### M3 Retry Ladder

| conflict_attempts | Strategy | Action |
|---|---|---|
| 0 | Expand same day | Search full day if time_pref was narrower |
| 1 | Next business day, preserve time_pref | Run M2 |
| 2 | Another business day candidate, or interrupt if exhausted | Run M2 or ask user for new preference |
| 3 | Try user's new preference from resume | Run M2, interrupt with result |
| >= 4 | Escalate | Send email with situation_summary; Gemini tells user someone will follow up |

**Key invariants:**
- `conflict_attempts` never resets between interrupts
- If user accepts a surfaced slot, Gemini books directly — M3 is not resumed
- If user rejects, Gemini resumes M3 with updated summary and the counter continues

---

## W3: Mid-Conversation Requirement Change

### Example: User changes duration after slots are presented

```
[STATE at this point]
  Live API knows: day=tomorrow, time_pref=morning, duration=30min
  M2 returned: slots [9:30 AM, 11:00 AM]
  Gemini spoke: "I have 9:30 AM or 11:00 AM available tomorrow morning."

[USER]: "Actually, my colleague is joining, we'll need a full hour. Are those still free?"

[LIVE API reasoning]
  Detects duration change: 30min → 60min
  Retains: day=tomorrow, time_pref=morning
  Does NOT call M1 (no time resolution needed)
  Calls search_slots directly with updated NL description:
    raw_slot_description: "tomorrow morning, 60 minutes"
    duration_minutes: 60

[M2: SlotSearch]
  New freebusy query with same window but 60-minute gap requirement
  → 9:30 AM is still free for 60min (next event at 11:30 AM)
  → 11:00 AM: only 30min free (event at 11:30 AM)
  → available_slots: [{ start: "9:30 AM", display: "9:30 AM (1 hour)" }]
  → natural_language_result: "9:30 AM still works for an hour, but 11:00 AM only has 30 minutes free."

[GEMINI]: "9:30 AM still works for a full hour, but 11:00 AM only has 30 minutes free. Shall I book 9:30 AM?"
```

**Key point**: The Live API model retains all prior context. Duration is just another parameter. Re-calling M2 with the updated parameter is all that's needed. No special "change detection" code required.

---

## W4: Memory-Dependent Request

### Example: "Let's schedule our usual sync-up"

```
[LIVE API reasoning]
  "usual sync-up" → memory-dependent → must call resolve_time_expression

[M1: TimeResolution]
  classify_expression → "memory_dependent"
  load_from_memory → memory.json: { usual_sync_up_duration: 30, usual_sync_up_time: "afternoon" }
  compute_window → no specific date found → status: "needs_clarification"
  validate_and_format → dispatcher strips resolved_window (None here), returns NL-only to Gemini:
    {
      status: "needs_clarification",
      natural_language_summary: "For your usual sync-up I see you normally do 30-minute afternoon meetings. What day would you like?",
      needs_clarification: "What day would you like for the sync-up?",
      confidence: 0.5
    }

[LIVE API]
  Receives needs_clarification — speaks needs_clarification question to user
  [SPEAKS]: "What day would you like for the sync-up?"
  M1 session is complete. No resume — Gemini holds the context from natural_language_summary.

[USER]: "Thursday works"

[LIVE API]
  Re-evaluates through W6 decision tree with enriched context:
  Knows: duration=30min, time_pref=afternoon (from natural_language_summary), day=Thursday (from user)
  → simple resolution, no M1 needed
  Calls search_slots(raw_slot_description="Thursday afternoon, 30 minutes", duration_minutes=30)
  ... normal slot search flow
```

**Why M1 does not use interrupt/resume:**
M1 terminates after returning `needs_clarification`. The partial context it resolved (duration, time_pref from memory) is returned in `natural_language_summary` — Gemini holds it in conversation context. After the user answers, Gemini re-evaluates through the W6 decision tree. In most cases (memory-dependent, event not found) the enriched request is now simple enough to resolve inline. In rare cases where the user's answer is itself complex, Gemini calls `resolve_time_expression` fresh with the full enriched expression. No M1 state needs to be preserved between calls.

---

## W5: SSE Status Event Map

LangGraph nodes emit SSE events via `emit_status()`. Mapping of nodes to UI messages:

| Module | Node | UI Message |
|---|---|---|
| M1 | classify_expression | "Understanding your request..." |
| M1 | lookup_reference_event | "Looking up {event name} on your calendar..." |
| M1 | compute_window | "Resolving time reference..." |
| M2 | normalize_input | "Preparing calendar search..." |
| M2 | query_freebusy | "Checking your availability..." |
| M2 | format_response | "Finding open slots..." |
| M3 | determine_next_window | "Looking for alternatives..." |
| M3 | search_alternative_window | "Searching {day}..." |
| M3 | suggest_to_user | "Waiting for your preference..." |
| dispatcher | create_calendar_event | "Booking your meeting..." |
| dispatcher | update_memory | "Updating preferences..." |

Status events include: `{ type, message, module, node, timestamp }`. The UI can show just `message` or the full breakdown depending on granularity preference.

---

## W6: The Tool Call Decision Tree (Live API System Prompt Logic)

```
User speaks
  │
  ├── Is all time info explicit? (date + time + duration all known)
  │     YES → call search_slots(raw_slot_description=NL, duration_minutes)
  │
  ├── Simple relative time? ("tomorrow", "next Friday", "this afternoon")
  │     YES → resolve inline using today's date from system prompt
  │           → call search_slots(raw_slot_description=NL, duration_minutes)
  │
  ├── Complex time reference? (event-anchored, memory-dependent, complex date logic)
  │     YES → call resolve_time_expression(raw_expression, ...)
  │              │
  │              ├── status="resolved"
  │              │     → SPEAK natural_language_summary
  │              │     → call search_slots(raw_slot_description=natural_language_summary, duration_minutes)
  │              │
  │              └── status="needs_clarification"
  │                    → SPEAK needs_clarification question → wait for user
  │                    → M1 is DONE — no resume, no state preserved
  │                    → re-enter W6 decision tree with enriched request:
  │                          ├── answer is simple ("Thursday") → resolve inline → search_slots directly
  │                          └── answer is complex ("after my budget review meeting") → call resolve_time_expression fresh with any new or extra information gained from the user
  │
  └── After search_slots:
        │
        ├── search_succeeded=true
        │     → SPEAK available slots → wait for user confirmation
        │     → user confirms → call create_calendar_event(title, start_iso, end_iso)
        │
        └── search_succeeded=false
              → call invoke_conflict_resolution(situation_summary=NL, duration_minutes, thread_id)
                    │
                    ├── status="needs_user_input"
                    │     → SPEAK message_to_speak
                    │     → wait for user response
                    │     │
                    │     ├── user ACCEPTS suggested_slot
                    │     │     → call create_calendar_event directly — do NOT resume M3
                    │     │
                    │     └── user REJECTS or PIVOTS
                    │           → synthesise updated situation_summary reflecting user's new ask
                    │           → call resume_conflict_resolution(situation_summary=NL, thread_id)
                    │           → loop back to M3 status handling (conflict_attempts preserved)
                    │
                    └── status="escalate"
                          → SPEAK natural_language_result ("email has been sent to [email]...")
                          → conversation ends
```

**Gemini-side rules encoded in system prompt:**
- All tool calls use natural language strings — never pass structured dicts or ISO datetimes as tool args (except `create_calendar_event` which takes ISO start/end)
- After M3 returns `needs_user_input`, Gemini is solely responsible for interpreting the user's reply — book directly if accepted, resume M3 if rejected
- Never call `resume_conflict_resolution` if the user accepted the slot — that would restart M3 unnecessarily
- The `situation_summary` passed to `invoke_conflict_resolution` and `resume_conflict_resolution` must include all context: original ask, what failed, any preference changes, current ask
