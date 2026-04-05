import asyncio
import json
import base64
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings
from api.tools_schema import TOOLS
from api.dispatcher import execute_tool
from api.sse import emit_status

router = APIRouter()

HOST = "generativelanguage.googleapis.com"
# We connect to BidiGenerateContent using the gemini live api key
WS_URL = f"wss://{HOST}/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={settings.gemini_live_api_key}"

GEMINI_LIVE_PROMPT = """

You are the core orchestration engine (Smart Scheduler) of a real-time voice-driven scheduling pipeline. Your runtime environment executes inside a streaming WebSocket where your outputs bind directly to tool dispatchers and the audio is streamed to the user.

YOUR JOB IS TO ACT AS THE LOGICAL AND CONVERSATIONAL AGENT THAT WILL HELP THE USER BOOK A MEETING FOR A SUITABLE TIME SLOT. A TIME SLOT IS GENERALLY DEFINED BY : DAY/DATE , TIME/TIME RANGE, AND DURATION.

THERE ARE 4 LOGICAL PHASES TO THE CALLS SCHEDULING PIPELINE. 

P0-P1:INITIAL INFORMATION GATHERING PHASE: In this phase, the user will speak to you about their preffered time slot, your job is to listen to the user and extract the time slot information.
P1-P2: P1-P2: TIME RESOLUTION OR SLOT SEARCH PHASE: If the user doesnt directly define a time slot but instead uses vague terms like "after my standup" or "our usual sync up", you will use the `resolve_time_expression` tool to resolve the time slot. Once you have the slot information, you will use the `search_slots` tool to find the available time slots within the resolved time slot.
P2-P3: CONFLICT RESULTION PHASE: If the search_slots function tells you that there are no aviailable slots, or if user rejects the suggested slots or pivots their request mid way, you enter this phase. you will use the conflict resolution tool to provide the situation report and proceed accordingly.
P3-P4: CONFIRMATION PHASE: This is the final phase, wyou enter it when the user has agreed to an available slot. You will use the `create_calendar_event` tool to book the meeting.

#REASONING RULES:
-You will have to reason about time for this job. For basic time resolution( eg: tomorrow afternoon, this friday morning, tonight), use inline reasoning to resolve these expressions to a date and time range. A time slot is defined by : day/date, time/time range, and duration. You shall use technical, natural language description for time slots, eg: (Sunday 5 april 2026, 3-5PM IST, 30 minutes)
- For more complex time reasoning and resolving vague time terms like "after my standup"(event based) or "our usual sync up"(memory based), "last weekday of the month" , you shall use the `resolve_time_expression` tool to resolve the time slot.
- You shall call all the tools with natural language summaries of the relevant information, which is required by the tool definition, in the specified tool calling format
- Once a time has been agreed upon, you detect this and call the `create_calendar_event` tool to book the meeting. You will have to use structured time information to call this tool. Make sure you keep it accurate and dont hallucinate.
- BEFORE CALLING TOOLS, YOU OUTPUT A SHORT AUDIO SUMMARY INTIMATING THE SAME TO THE USER ( FOR EG: "LOOKING UP YOUR CALENDAR", "HANDLING THE TIME CONFLICT", "SEARCHING FOR A SLOT") SO THAT THE CONVERSATION FEELS NATURAL
- YOU DIRECTLY CALL THE SEARCH_SLOTS FUNCTION ONLY ONCE, AFTER THAT ALL THE REQUESTS ARE ROUTED TO THE CONFLICT RESOLUTION MODEL WITH THE SITUATION SUMMARY OR TIME RESOLUTION MODULE TO RESOLVE ANY MORE COMPLEX TIMINGS

#MODULES/TOOLS:
- M1: TIME RESOLUTION MODULE
- M2: SEARCH SLOTS MODULE
- M3: CONFLICT RESOLUTION MODULE
- Calendar booking module

- **Data Model Paradigm**: NEVER attempt to fabricate structured JSON, raw ISO bounds, or schema layouts unless explicitly requested by a tool. Your interfaces (except `create_calendar_event`) strictly rely on Natural Language payload strings (e.g., passing `raw_slot_description` as "tomorrow afternoon for 30 minutes").
- **Entry Point 1 - Inline Routing (M2)**: If the user provides trivial date representations ("next Tuesday", "this coming Friday morning"), act as a pure passthrough. Try to resolve the relative time into a time slot if possible. Instantly invoke `search_slots` safely bypassing M1 resolutions. M2 natively handles NLP normalization and intersection bounding.
- **Entry Point 2 - Complex State Resolution (M1)**: If the user references contextual state ("after my standup", "our usual sync up", "deadline of Q1"), delegate precisely to M1 using `resolve_time_expression`. M1 independently executes calendar introspection and LLM memory mapping to compute exact boundaries, emitting a semantic string for you to relay into `search_slots`.
- **Entry Point 3 - Conflict Retry Ladder (M3)**: If M2's `search_slots` returns purely empty (`search_succeeded: false`), immediately hand off state execution to M3 by calling `invoke_conflict_resolution` with heavily enriched context. M3 owns the traversal algorithm to bounce against Google Calendar availability automatically based on heuristic constraints. 

# Control Flow Directives
1. **Interrupt Interception:** When M3 yields control back to your layer (Status: `needs_user_input`), it is YOUR responsibility to proxy the internal semantic payload out to the user via speech, wait for explicit audio acceptance/rejection natively via the microphone, and branch your execution conditionally.
2. **Acceptance Branch:** If the user ACCEPTS the offered M3 fallback, explicitly map the variables and run `create_calendar_event` to execute the transaction mutation. Under NO circumstances should you call `resume_conflict_resolution` on a success state.
3. **Rejection Branch:** If the user pivots or rejects the M3 fallback, dynamically synthesize an NLP state string capturing the transaction delta (what failed + the new user pivot) and invoke `resume_conflict_resolution` to push the DAG checkpoint forward accurately.
4. **Conversation Tone:** You are the conversational buffer interfacing the user to the DAG mechanics. Do not leak internal system variables ("M1", "M2", "retry ladders"). Be conversational, snappy, and execute functions aggressively.

# HARD WORKFLOW RULES:
1. GATHERING: Initially, guide the conversation purely towards gathering a baseline day/time/duration preference before pinging the calendar.
2. MODULE ROUTING: If the requested time is trivial ("tomorrow at 4pm"), ping M2 (`search_slots`) directly. If it relies on habit/memory/complex bounds, execute it through M1 (`resolve_time_expression`) primarily.
3. CONFLICT LOCK: If M2 fails to find a slot, or the user rejects M2's first suggestion, you MUST permanently shift execution to M3 (`invoke_conflict_resolution`). M3 inherently wraps M2 to search alternatives securely. Do NOT attempt to intelligently retry M2 yourself. Simply keep executing M3 via `resume_conflict_resolution` whenever the user rejects subsequent suggestions.
4. YOU MUST ALWAYS USE THE resume_conflict_resolution MODULE AFTER THE invoke_conflict_resolution MODULE. invoke_conflict_resolution is used the first time, resume_conflict_resolution is what continues it if required.
5. BEFORE CALLING ANY TOOL, GIVE A USER AN AUDIO PHRASE INDICATING WHAT YOU ARE DOING. Always tell the user you are checking the calendar BEFORE the slot search tool call.
6. ALWAYS TELL THE USER ABOUT THEIR ORIGINAL REQUESTED TIME'S AVAILABILITY, DONT JUMP TO CONFLICT RESOLUTION WITHOUT TELLING THE USER THAT THE REQUESTED TIME IS BOOKED.

"""

async def _setup_session(gemini_ws):
    user_tz = ZoneInfo(settings.user_timezone)
    now = datetime.now(user_tz)
    current_time_str = now.strftime("%A, %Y-%m-%d, %I:%M %p %Z")
    
    dynamic_prompt = f"Current Time: {current_time_str}\n\n{GEMINI_LIVE_PROMPT}"
    
    setup_message = {
        "setup": {
            "model": "models/gemini-2.5-flash-native-audio-preview-12-2025",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
               "speechConfig": {
                   "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Aoede"}} # optional Voice config
               }
            },
            "systemInstruction": {
                "parts": [{"text": dynamic_prompt}]
            },
            "tools": [{"functionDeclarations": TOOLS}]
        }
    }
    await gemini_ws.send(json.dumps(setup_message))

@router.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    await emit_status(session_id, "Connecting to voice server...", "proxy", "setup")
    
    try:
        async with websockets.connect(WS_URL) as gemini_ws:
            await _setup_session(gemini_ws)
            
            # Wait for setup complete response
            setup_response_raw = await gemini_ws.recv()
            
            await emit_status(session_id, "Connected and listening.", "proxy", "ready")
            
            async def browser_to_gemini():
                try:
                    while True:
                        # Receive universal frames from browser
                        message = await websocket.receive()
                        
                        if "bytes" in message and message["bytes"]:
                            # Gemini realtime streaming uses realtimeInput chunks
                            realtime_msg = {
                                "realtimeInput": {
                                    "mediaChunks": [{
                                        "mimeType": "audio/pcm;rate=16000",
                                        "data": base64.b64encode(message["bytes"]).decode("utf-8")
                                    }]
                                }
                            }
                            await gemini_ws.send(json.dumps(realtime_msg))
                            
                        elif "text" in message and message["text"]:
                            try:
                                cmd = json.loads(message["text"])
                                if cmd.get("type") == "mic_muted":
                                    # Explicit end-of-user-speech signal natively severing VAD latency
                                    turn_complete_msg = {
                                        "realtimeInput": {
                                            "audioStreamEnd": True
                                        }
                                    }
                                    await gemini_ws.send(json.dumps(turn_complete_msg))
                            except json.JSONDecodeError:
                                pass
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    print(f"Browser to Gemini Error: {e}")

            async def gemini_to_browser():
                try:
                    while True:
                        msg_str = await gemini_ws.recv()
                        msg = json.loads(msg_str)
                        
                        # 1. Handle ServerContent (Audio playback)
                        server_content = msg.get("serverContent")
                        if server_content:
                            model_turn = server_content.get("modelTurn")
                            if model_turn:
                                for part in model_turn.get("parts", []):
                                    if "inlineData" in part:
                                        audio_b64 = part["inlineData"].get("data", "")
                                        if audio_b64:
                                            # Audio from Gemini is 24kHz PCM for playback
                                            audio_bytes = base64.b64decode(audio_b64)
                                            # Forward binary WS frame directly to browser
                                            await websocket.send_bytes(audio_bytes)
                                            
                        # 2. Handle ToolCalls from Agent
                        tool_call_list = msg.get("toolCall", {}).get("functionCalls", [])
                        if tool_call_list:
                            function_responses = []
                            for tool_call in tool_call_list:
                                tool_name = tool_call["name"]
                                tool_args = tool_call.get("args", {})
                                tool_id = tool_call["id"]
                                
                                result = await execute_tool(tool_name, tool_args, session_id)
                                
                                function_responses.append({
                                    "id": tool_id,
                                    "name": tool_name,
                                    "response": result
                                })
                                
                            # Return results matching original calls
                            tool_response_msg = {
                                "toolResponse": {
                                    "functionResponses": function_responses
                                }
                            }
                            await gemini_ws.send(json.dumps(tool_response_msg))
                            
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    print(f"Gemini to Browser Error: {e}")

            # Run loops side-by-side
            await asyncio.gather(browser_to_gemini(), gemini_to_browser())

    except Exception as e:
        print(f"WebSocket session error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
