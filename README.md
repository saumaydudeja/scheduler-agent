# Smart Scheduler Agent

A voice-first, full-stack AI scheduling assistant powered by the **Gemini 2.0 Live API**, **LangGraph**, and **Google Calendar**. 

This application lets you manage your calendar naturally through real-time voice conversations. It can handle complex scheduling requests ("book a meeting a few hours after my flight lands"), seamlessly find available time slots, and even negotiate alternative times with you if your calendar is full—all without needing to open a traditional UI.

## 🧠 The Architectural Approach

Building voice agents usually forces a rough trade-off:
* **The Traditional Pipeline (STT -> LLM -> TTS):** Offers incredibly high reasoning ability because you can map complex logic to powerful foundational models safely, but deeply suffers from awkwardly high conversation latency. 
* **Native Voice Models (e.g., Gemini Live API):** Deliver ultra-low latency and hyper-natural conversational flow. However, pushing *all* reasoning, calendar math, and JSON structuring rigidly into a single live audio model places extreme strain on its context logic, predictably leading to frequent loops and hallucinations during complex scheduling.

**Our Approach combines the best of both worlds.** We use the **Gemini 2.0 Flash Live API** exclusively for conversational fluency and parameter extraction, dynamically offloading the heavy mathematical lifting safely to our deterministic **LangGraph** backend structure. 

## 🤖 The LangGraph Agents (M1, M2, M3)

Under the hood, Gemini is seamlessly provided access (via structured Tool Calls) to three independent LangGraph agents. This explicitly prevents the model from infinitely looping or hallucinating constraints.

* **M1 (Time Resolution):** Handles all complex, memory-dependent, or event-anchored time expressions (e.g., "a few hours after my flight lands"). It uses a combination of calendar lookups and LLM analysis to translate organic human speech cleanly into rigid mathematical bounding boxes.
* **M2 (Slot Search):** The deterministic search engine. Once M1 computes the bounds, M2 queries your live calendar's `freebusy` matrix, instantly locating and validating up to 5 perfectly tailored gaps.
* **M3 (Conflict Resolution):** The "Negotiator." If your calendar is full, execution permanently locks into M3. This agent intelligently searches further out, contextualizes the conflict, and negotiates directly with you over the audio stream until you enthusiastically agree on an alternative slot.

## 📡 Observability & Integrations

* **Logging Traces:** Because deterministic background agents move fast, we built a Server-Sent Events (SSE) layer that physically streams the internal mathematical traces and tool routing decisions of M1/M2/M3 directly to the live Transcript Pane on the frontend dashboard. You always know exactly what the agent is computing.
* **Integrations:** Natively secured via OAuth 2.0 framework pipelines for strict Google Calendar (and Mail) API bindings, executing explicit read/write controls exactly according to your boundaries.

---

## 💻 Setup & Execution 

### 1. Prerequisites
* **Node.js** (v18 or higher)
* **Python 3.10+** (Recommend via `pyenv` or `conda`)
* **Google Cloud Console Setup:** Needs an active project with Google Calendar API and Gemini Live API provisioned. Generate an OAuth Client ID strictly configured with Web App Authorized redirect URLs pointing to `http://localhost:8080/`.

### 2. Backend Environment (Python)

1. Open a terminal and navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Set up the Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create a `.env` file directly in the root of the project with your API keys:
   ```env
   GEMINI_API_KEY="your-gemini-api"
   GEMINI_LIVE_API_KEY="your-gemini-live-api"
   GOOGLE_CLIENT_ID="your-oauth-client-id"
   GOOGLE_CLIENT_SECRET="your-oauth-secret"
   GOOGLE_REDIRECT_URI="http://localhost:8080/"
   ```
5. Boot up the FastAPI Server:
   ```bash
   uvicorn main:app --port 8000 --reload
   ```
   > **Note on First Run:** During your very first voice request, a browser window will pop up asking for Google Authentication permission. Once authorized, the backend saves a `token.pickle` file locally. You will not need to log in again on this machine!

### 3. Frontend Environment (Next.js)

1. Open a secondary terminal tab and navigate to the frontend:
   ```bash
   cd frontend
   ```
2. Install the React dependencies:
   ```bash
   npm install
   ```
3. Create a `.env.local` inside the frontend directory so it knows where to find the backend:
   ```env
   NEXT_PUBLIC_BACKEND_WS_URL=ws://127.0.0.1:8000/ws/voice
   ```
4. Start the frontend:
   ```bash
   npm run dev
   ```
5. Navigate to `http://localhost:3000` in your browser. Click the microphone, grant audio permissions, and start talking to your assistant!
