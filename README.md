# Smart Scheduler

Most voice scheduling assistants are built one of two ways â€” and both have a fundamental tradeoff.

The **STT â†’ LLM â†’ TTS pipeline** (transcribe speech, reason with text, synthesise response) gives you strong reasoning and structured outputs but stacks three sequential model calls before the user hears anything. Latency compounds. For a conversational scheduling task with multiple back-and-forth turns, this feels slow.

The **native audio model approach** (Gemini Live end-to-end) gives you sub-800ms conversational latency and natural speech, but puts the entire reasoning burden on a single model in real-time â€” calendar lookups, availability logic, conflict resolution, time parsing, structured outputs, and conversation management all have to happen inline. In practice, the model hallucinates slot availability, loses context mid-negotiation, and can't reliably execute multi-step scheduling logic without guardrails.

**Smart Scheduler combines both.** Gemini Live handles the conversation layer â€” it speaks, listens, and maintains context with native audio quality and low latency. But the moment a scheduling decision needs to be made, Gemini calls one of three specialised LangGraph backend agents that run deterministic, structured pipelines against your real Google Calendar data. The model never has to reason about your calendar directly. It just gets structured answers back and speaks them naturally.

The result is a system with the conversational fluency of a native audio model and the reliability of a purpose-built scheduling engine.

---

## How It Works

Gemini Live streams audio bidirectionally through a FastAPI WebSocket proxy. When the conversation reaches a scheduling decision point, Gemini emits a tool call that the backend intercepts and routes to the appropriate LangGraph agent. Status events stream to the frontend over SSE in real time. The agent returns a structured result, Gemini speaks it, and the conversation continues.

### Three Backend Agents

**M1 â€” Time Resolution**  
Handles natural language time expressions that can't be resolved from today's date alone. When you say "after my Project Alpha meeting" or "before my flight on Friday", M1 classifies the expression type, queries your calendar for the anchor event, and computes a precise search window. It also handles memory-dependent expressions ("our usual sync-up") and complex date logic ("last weekday of the month"). Simple relative times like "tomorrow afternoon" are resolved inline by Gemini without invoking M1.

**M2 â€” Slot Search**  
Given a time window (from M1 or directly from Gemini), M2 queries the Google Calendar Freebusy API, subtracts busy intervals, and returns available slots that fit your requested duration. Accepts both structured windows and natural language descriptions. M2 is the most-called agent â€” it runs on every scheduling request and is also called internally by M3.

**M3 â€” Conflict Resolution**  
Activated when M2 finds nothing available. M3 runs a retry ladder: it expands the same-day window, tries adjacent business days, then pauses and asks you for a preference when automatic strategies are exhausted. It uses LangGraph's interrupt/resume mechanism to pause mid-graph, surface a question through Gemini's voice, wait for your response, and continue â€” preserving full conversation context throughout. At four failed attempts it escalates via email.

---

## Observability

Every LangGraph agent run is traced end-to-end with **LangSmith** â€” node-level inputs and outputs, LLM call latency, token counts, and confidence scores are captured automatically. A trace link is emitted to the frontend UI after each agent invocation so you can inspect exactly what the agent did during a conversation.

The frontend status indicator maps to individual LangGraph node execution events â€” "Looking up Project Alpha on your calendar..." and "Searching Wednesday..." reflect the actual node currently running in the backend, not generic loading states.

---

## Integrations

**Google Calendar** â€” OAuth 2.0 with token persistence. Freebusy queries, event title search (for anchor event resolution), and event creation. All calendar logic is centralised in a single utility layer; no calendar calls occur inside agent nodes.

**Escalation Email** â€” When M3 exhausts all conflict resolution strategies, it sends a scheduling summary email via SMTP. Gracefully skips if SMTP is not configured (useful for local dev).

---

## Setup

### Prerequisites
- Node.js v18+
- Python 3.10+
- Google Cloud project with Calendar API enabled and OAuth 2.0 credentials configured (Desktop app type, redirect URI `http://localhost:8080/`)
- Gemini API key with Live API access

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create `backend/.env`:
```env
GEMINI_API_KEY=your-gemini-api-key
GOOGLE_CLIENT_ID=your-oauth-client-id
GOOGLE_CLIENT_SECRET=your-oauth-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8080/
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-langsmith-api-key
LANGCHAIN_PROJECT=smart-scheduler
USER_TIMEZONE=Asia/Kolkata
```

Place your `credentials.json` (downloaded from Google Cloud Console) in the `backend/` directory.

```bash
uvicorn main:app --port 8000 --reload
```

> **First run:** A browser window will open for Google Calendar OAuth authorisation. After approval, a `token.pickle` file is saved locally â€” you won't be prompted again on this machine.

### Frontend

```bash
cd frontend
npm install
```

Create `frontend/.env.local`:
```env
NEXT_PUBLIC_BACKEND_WS_URL=ws://127.0.0.1:8000
```

```bash
npm run dev
```

Open `http://localhost:3000`. Click the microphone button, grant audio permissions, and speak.

### Using the App

- **Tap mic** to start a session and begin speaking
- **Tap again** to mute your mic and signal Gemini to respond immediately (bypasses VAD silence detection â€” reduces response latency noticeably)
- **Tap again** to unmute and continue the conversation
- The status indicator shows live backend progress as agents run
- A booking confirmation card appears with a Google Calendar link once an event is created

### Example Requests
- *"Find me a 30-minute slot this Thursday afternoon"*
- *"Book something after my Project Alpha meeting next week"*
- *"I need an hour before my flight on Friday at 6 PM"*
- *"Schedule our usual sync-up â€” Tuesday doesn't work for me"*