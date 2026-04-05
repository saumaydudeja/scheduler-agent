# Product Requirements Document
## Smart Scheduler AI Agent
**Version**: 1.0 | **Status**: Active Development | **Assignment**: NextDimension Take-Home

---

## 1. Overview

### 1.1 Problem Statement

Scheduling meetings through calendar interfaces requires context-switching, manual availability checking, and back-and-forth coordination. The goal is an agent that handles this conversationally — a user speaks naturally about when they want to meet, and the system figures out the calendar, finds a time, and books it.

### 1.2 Product Goal

Build a voice-enabled AI agent that conducts a natural spoken conversation to help a user find and book a meeting slot on their Google Calendar. The agent must handle ambiguous scheduling requests, understand time references, gracefully handle conflicts, and maintain conversation context across multiple turns.

### 1.3 Scope

This is a single-user MVP for demonstration and evaluation. Multi-user invite workflows, team calendars, and recurring event creation are out of scope.

---

## 2. User Personas

**Primary**: A professional (developer, product manager, or operator) who wants to demonstrate or evaluate an AI scheduling agent. Comfort with technology, minimal patience for broken flows, evaluating both UX quality and engineering robustness.

---

## 3. Core User Journey

```
User opens browser tab
  → Clicks mic button, speaks a scheduling request
  → Hears agent confirm it understood the request
  → Hears available time options
  → Confirms a time slot verbally
  → Hears booking confirmation
  → Sees calendar event created
```

Every step of the path must work reliably. Broken intermediate steps (e.g., silent failures, no response to ambiguous input) are unacceptable.

---

## 4. Functional Requirements

### 4.1 Voice Interface

| ID | Requirement | Priority |
|---|---|---|
| V1 | User can speak scheduling requests via browser microphone | Must Have |
| V2 | Agent responds with synthesized speech | Must Have |
| V3 | End-to-end latency from user stop-speaking to first audio response ≤ 800ms | Must Have |
| V4 | Agent uses natural, conversational speech cadence — not robotic pacing | Must Have |
| V5 | Conversation continues across multiple turns without re-introduction | Must Have |
| V6 | Agent speaks M1 time resolution summary aloud before searching ("Your flight lands Wednesday at 3 PM, let me search after that") | Should Have |

### 4.2 Scheduling Logic

| ID | Requirement | Priority |
|---|---|---|
| S1 | Agent identifies the meeting duration from conversation | Must Have |
| S2 | Agent identifies the preferred date and time from conversation | Must Have |
| S3 | Agent asks one targeted clarifying question when required info is missing | Must Have |
| S4 | Agent checks Google Calendar for the specified time window | Must Have |
| S5 | Agent presents 2-3 available slots when found | Must Have |
| S6 | Agent confirms the user's chosen slot before creating the event | Must Have |
| S7 | Agent creates the calendar event after confirmation | Must Have |

### 4.3 Natural Language Time Parsing

The agent must correctly interpret all of the following input classes:

**Inline resolution (no backend module needed):**
- Explicit datetime: "April 9th at 2 PM for 1 hour"
- Simple relative: "tomorrow morning", "next Friday afternoon"
- Named time-of-day: "this evening", "Tuesday midday"

**Module 1 resolution (complex cases):**
- Event-anchored: "after my Project Alpha meeting", "before my flight on Friday"
- Deadline-anchored: "sometime before my 5 PM call today"
- Dynamic buffer: "an hour after my last meeting of the day"
- Memory-dependent: "our usual sync-up", "the regular standup"
- Complex date logic: "last weekday of the month", "two weeks from the kick-off"
- Multi-constraint: "sometime next week, not Wednesday, not too early"

| ID | Requirement | Priority |
|---|---|---|
| T1 | Resolve simple relative times inline using injected current date | Must Have |
| T2 | Resolve event-anchored references via calendar lookup | Must Have |
| T3 | Resolve deadline-anchored requests ("before my flight at 6 PM") | Must Have |
| T4 | Resolve dynamic buffer calculations ("an hour after my last meeting") | Must Have |
| T5 | Resolve memory-dependent requests using stored user preferences | Should Have |
| T6 | Handle "last weekday of month" and similar date logic | Should Have |
| T7 | Handle multi-constraint vague requests with one clarifying question | Should Have |

### 4.4 Conflict Resolution

| ID | Requirement | Priority |
|---|---|---|
| C1 | Agent does not fail silently when no slots are found | Must Have |
| C2 | Agent automatically expands search to full day if preferred window is booked | Must Have |
| C3 | Agent automatically tries next 1-2 business days before asking user | Must Have |
| C4 | Agent pauses and asks user when automatic strategies are exhausted | Must Have |
| C5 | Agent handles mid-conversation requirement changes (e.g., duration change from 30min to 1hr) | Must Have |
| C6 | Agent offers escalation (e.g., scheduling email) when fully blocked | Should Have |

### 4.5 Memory

| ID | Requirement | Priority |
|---|---|---|
| M1 | Agent stores usual meeting duration preference after each booking | Should Have |
| M2 | Agent stores preferred time windows from past bookings | Should Have |
| M3 | Agent recalls "usual sync-up" duration in subsequent sessions | Should Have |

### 4.6 UI & Status Feedback

| ID | Requirement | Priority |
|---|---|---|
| U1 | Browser shows real-time status indicator during backend processing ("Checking your calendar...") | Must Have |
| U2 | Browser shows booking confirmation card with event title, time, and calendar link after booking | Must Have |
| U3 | Status updates map to specific LangGraph nodes for granularity | Should Have |
| U4 | LangSmith trace link is shown in UI after each agent invocation | Nice to Have |
| U5 | Voice button provides clear visual feedback (listening, processing, speaking states) | Must Have |

---

## 5. Non-Functional Requirements

### 5.1 Performance

| Metric | Target |
|---|---|
| First audio response after user stops speaking | ≤ 800ms |
| Slot search completion (M2) | ≤ 1.5s |
| Complex time resolution (M1) | ≤ 2s |
| Full conflict resolution cycle (M3, no interrupt) | ≤ 3s |
| Page load to first ready state | ≤ 3s |

### 5.2 Reliability

- Agent must never silently fail — every error path must produce a spoken response
- Google Calendar API auth token must auto-refresh without user intervention
- WebSocket disconnection must be detected and surfaced (not silently dropped)

### 5.3 Code Quality

- Type hints on all function signatures
- All async code uses `await` — no blocking calls in hot paths
- Unit tests for all calendar utility functions
- Unit tests for each LangGraph module (mocking calendar API calls)
- Integration test for full tool call dispatch cycle with mocked Gemini events

---

## 6. Technical Constraints

| Constraint | Reason |
|---|---|
| Must use Google Calendar API | Assignment requirement |
| Must use an LLM provider (Gemini, OpenAI, etc.) | Assignment requirement |
| Must be voice-enabled | Assignment requirement |
| Must be deployed online | Assignment requirement (Vercel/GCP/Railway) |
| Python for all backend logic | Engineering preference; LangGraph is Python-native |
| No database for MVP | Scope control; JSON file for memory is sufficient |
| Single-user OAuth for MVP | Scope control |

---

## 7. Evaluation Criteria (from assignment)

The submission is evaluated on these dimensions. Requirements above are mapped to them.

| Criterion | Covered By |
|---|---|
| Agentic Logic — context across turns, tool call decisions | S1–S7, T1–T7, C1–C6 |
| Prompt Engineering — system prompt quality, NL extraction | T1–T7, V6 |
| Coding & API Integration — code quality, Calendar API | All 4.x sections |
| Voice-Enabled Agent — natural speech, ≤800ms latency | V1–V6 |
| Advanced Conflict Resolution — graceful fallback | C1–C6 |
| Smarter Time Parsing — complex NL time understanding | T1–T7 |
| Problem-Solving — overall approach and challenges | Architecture docs |

**Bonus points criteria:**
- Innovative features that meaningfully improve the product
- See Section 9 for bonus feature candidates

---

## 8. Out of Scope (MVP)

- Multi-attendee scheduling and invite management
- Recurring event creation
- Email or calendar notification integration
- Multi-user / multi-calendar support
- Mobile app
- Authentication beyond single-user Google OAuth
- Calendar event editing or deletion
- Video conferencing link generation (Google Meet, Zoom)

---

## 9. Bonus Features (Post-MVP)

These are innovation candidates for bonus evaluation points. Ranked by impact-to-effort ratio.

### B1: Proactive Conflict Surfacing
When the user confirms a slot, before creating the event, check if they have adjacent meetings with no buffer. Warn them: "You have a meeting ending at 2:00 PM. Would you still like to book 2:00 PM, or shall I find a 10-minute buffer?"

### B2: Smart Duration Inference
If the user says "a quick chat", default to 15 minutes. If they say "a workshop", suggest 90 minutes. Persist observed patterns (if the user always books "syncs" for 30 minutes, remember that). Reduces clarifying questions.

### B3: Attendee Availability Check
Accept an optional attendee email. Use the Calendar API's freebusy query to check their availability alongside the user's. Only suggest slots where both are free. Speaks naturally: "You're both free Thursday at 3 PM or Friday morning."

### B4: Google Meet Link Auto-Generation
When creating the event, automatically add a Google Meet conference link via the `conferenceData` field in `events.insert`. Agent confirms: "I've also added a Google Meet link."

### B5: Visual Transcript Pane
In the UI, show a live rolling transcript of the conversation alongside the voice interface. Highlights tool calls when they fire. Gives evaluators clear visibility into what the agent is doing.

### B6: Slot Heatmap
After running a freebusy query, render a simple day-view heatmap in the UI showing busy/free blocks for the searched day. Visual feedback makes the agent feel more capable and transparent.

### B7: Post-Booking Nudge
After booking, the agent says: "Done! Want me to set a reminder 10 minutes before?" If yes, updates the event with a notification. One extra tool call, meaningfully better UX.

---

## 10. Conversation Design Principles

These govern how the agent speaks and behaves, distinct from technical requirements.

1. **One question at a time.** Never ask "What day and what time and how long?" in a single turn.
2. **Confirm before committing.** Always state the event details before calling `create_calendar_event`. Never create without verbal confirmation.
3. **Speak the plan.** After complex time resolution, the agent speaks what it understood before searching. ("Your flight is Friday at 6 PM, so I'll look for a slot before 5 PM.")
4. **Never say "I cannot."** Reframe: "Tuesday is fully booked — would Wednesday morning work instead?"
5. **Acknowledge changes.** If the user changes duration or date mid-conversation, the agent explicitly confirms it heard the change: "Got it, switching to a 1-hour meeting."
6. **Keep options to 2-3.** Do not read out 7 available slots. Pick the best 2-3 and offer them.
7. **Short confirmations.** After booking: "Done! Thursday April 9th at 10:30 AM is booked." Not a paragraph.

---

## 11. Key Scenarios and Expected Behaviour

### Scenario 1: Happy Path
**Input**: "I need to schedule a 30-minute call with my team sometime Thursday afternoon."
**Expected**: Agent searches Thursday afternoon, offers 2-3 slots, user picks one, event created.
**Pass criteria**: Booking confirmed, event visible in Google Calendar.

### Scenario 2: Event-Anchored Time
**Input**: "Find me a slot after my Project Alpha meeting next week."
**Expected**: M1 triggered, calendar queried for "Project Alpha", window resolved, M2 searches after that event, slots presented.
**Pass criteria**: Agent speaks "Project Alpha is on [correct date/time], let me search after that" before presenting slots.

### Scenario 3: Full Conflict Resolution
**Input**: User requests a 1-hour slot on a fully booked day.
**Expected**: M2 returns failure, M3 expands to full day (still fails), M3 tries next business day, finds slot, agent says "Tuesday is fully booked for an hour. I found Wednesday at 2 PM — does that work?"
**Pass criteria**: Agent never returns a hard failure. Conversation continues to a resolution.

### Scenario 4: Mid-Conversation Duration Change
**Input**: Agent has just offered slots for 30 minutes. User says "Actually, make it an hour."
**Expected**: Agent re-runs M2 with updated duration, same day/time preference retained.
**Pass criteria**: New slots reflect 1-hour availability. Original context (day, time preference) preserved.

### Scenario 5: Ambiguous Request
**Input**: "I'm free sometime next week, but not too early and not Wednesday."
**Expected**: Agent asks one clarifying question: "Any particular day you prefer, or should I look across the whole week?"
**Pass criteria**: Agent does not try to book without sufficient information. Asks one question only.

### Scenario 6: Deadline-Anchored
**Input**: "I need to meet for 45 minutes before my flight on Friday at 6 PM."
**Expected**: M1 triggered with deadline_anchored type, window set to Friday before ~5 PM, M2 searches that window.
**Pass criteria**: No slot offered after 5:15 PM (accounting for 45-minute duration + buffer).

---

## 12. Acceptance Criteria for Submission

The project is considered complete when:

- [ ] User can speak to the app and hear responses with ≤800ms latency
- [ ] All 6 conversation scenarios in Section 11 produce the expected behaviour
- [ ] A meeting can be booked end-to-end from voice input to Google Calendar event
- [ ] Conflict resolution suggests alternatives instead of failing
- [ ] Complex time references (event-anchored, deadline-anchored) are resolved correctly
- [ ] Status indicators update in real time during backend processing
- [ ] App is deployed and accessible via a public URL
- [ ] Repository is public with a complete README (setup, design explanation, demo video link)
- [ ] 2-3 minute demo video shows a complete booking conversation with clear audio
