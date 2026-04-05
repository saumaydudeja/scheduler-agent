"""
M3 ConflictResolution nodes.

Graph (with MemorySaver checkpointer):
  determine_next_window → search_alternative_window → [route_after_search]
    ├── escalation_needed | attempts >= 4  → escalate → END
    ├── succeeded                          → prepare_success_response → suggest_to_user
    ├── not succeeded, attempts < 2        → determine_next_window  (auto-retry)
    └── not succeeded, attempts >= 2       → suggest_to_user (ask user)

  suggest_to_user calls interrupt() — pauses graph.
  On resume: interrupt() returns updated situation_summary → edge back to determine_next_window.
"""
import asyncio
import json
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.types import interrupt
from pydantic import BaseModel

from agent_types import ConflictState
from config import settings
from modules.slot_search import run_slot_search

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schema for determine_next_window structured output
# ---------------------------------------------------------------------------

class StructuredWindow(BaseModel):
    date_start: str
    date_end: str
    preferred_start_hour: int
    preferred_end_hour: int

class NextWindow(BaseModel):
    current_preferences: dict       # {day_pref: str, time_pref: str}
    current_failed_window: dict     # {date: str, start_hour: int, end_hour: int}
    next_search_window: StructuredWindow
    escalation_needed: bool         # True when no more meaningful options exist


DETERMINE_NEXT_WINDOW_PROMPT = """You are a scheduling assistant resolving a calendar conflict. You are invoked when there is a scheduling conflict between the user and the calendar system.

You are given information about the scheduling conflict in the form of a situation summary that describes the conversation between the system and the user along with a number of attempts tried for conflict resolution.

Your job is to understand the situation, decide the next step based on the conflict_attempts and retry ladder and provide the next search window in the specified format.

You have to parse the situation and extract the required parameters: current preferences(current user preference along with time slot) , current failed window(the window in which there is a conflict in scheduling), next search window

Follow the structural output format strictly.

Today is {weekday}, {today_date}. User timezone: {tz}.
Current conflict_attempts: {conflict_attempts}.
Windows already tried (do NOT suggest these again): {tried_windows}.

Retry ladder:
  attempt 0 — expand the same day: if time_pref was 'afternoon', try full day 8AM–8PM
  attempt 1 — next business day, preserve time_pref
  attempt 2 — another business day candidate; if none left, set escalation_needed=true
  attempt >= 3 — set escalation_needed=true

Parse the situation_summary to extract current_preferences and current_failed_window, then decide next_search_window. All ISO datetimes must include the {tz} offset.

GO STEP BY STEP TO PARSE THE SITUATION CORRECTLY INTO THE PARAMETERS"""

DRAFT_SUCCESS_PROMPT = """You are a scheduling assistant. You successfully found an available time slot after a conflict.
The found slot is: {slot_display}.
Write one warm, brief, conversational sentence telling the user you found this slot and asking if they want to book it. Example: 'I found a completely open slot at 4:30 PM today, does that work for you?'"""

DRAFT_SUGGESTION_PROMPT = """You are a scheduling assistant. You were unable to find an available time slot matching the user's exact request.
Past failures: {tried_windows}
Next area you plan to search: {next_search}
Write one warm, brief, conversational sentence telling the user that the requested time is fully booked, and asking if they are okay pivoting to {next_search} instead."""

DRAFT_ESCALATION_PROMPT = """You are a scheduling assistant. You have completely exhausted all logical calendar search options for the user.
Write one warm, brief sentence apologizing and informing the user that you've exhausted automatic options and have notified your human team to follow up."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_context() -> tuple[str, str, str]:
    user_tz = ZoneInfo(settings.user_timezone)
    now = datetime.now(user_tz)
    return now.strftime("%Y-%m-%d"), now.strftime("%A"), settings.user_timezone


async def _send_escalation_email(situation_summary: str) -> None:
    """Send escalation email. Gracefully skips if SMTP not configured."""
    smtp_host = getattr(settings, "smtp_host", "")
    if not smtp_host or not settings.escalation_email:
        logger.warning(
            "Escalation email not sent — SMTP_HOST or ESCALATION_EMAIL not configured. "
            "Summary: %s", situation_summary
        )
        return

    msg = MIMEText(
        f"Scheduling escalation — manual follow-up required.\n\n"
        f"Situation summary:\n{situation_summary}"
    )
    msg["Subject"] = "Scheduling Assistant — Escalation Required"
    msg["From"] = getattr(settings, "smtp_user", "noreply@scheduler")
    msg["To"] = settings.escalation_email

    def _send():
        smtp_port = getattr(settings, "smtp_port", 587)
        smtp_user = getattr(settings, "smtp_user", "")
        smtp_password = getattr(settings, "smtp_password", "")
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _send)
    except Exception as exc:
        logger.error("Failed to send escalation email: %s", exc)


# ---------------------------------------------------------------------------
# Node: determine_next_window
# ---------------------------------------------------------------------------

async def determine_next_window(state: ConflictState) -> dict:
    today_date, weekday, tz = _now_context()

    system_prompt = DETERMINE_NEXT_WINDOW_PROMPT.format(
        weekday=weekday,
        today_date=today_date,
        tz=tz,
        conflict_attempts=state["conflict_attempts"],
        tried_windows=json.dumps(state["tried_windows"])
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
        api_key=settings.gemini_api_key,
    ).with_structured_output(NextWindow)

    result: NextWindow = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["situation_summary"]),
    ])

    return {
        "current_preferences": result.current_preferences,
        "current_failed_window": result.current_failed_window,
        "current_search_window": result.next_search_window.model_dump(),
        "escalation_needed": result.escalation_needed,
        "conflict_attempts": state["conflict_attempts"] + 1,
    }


# ---------------------------------------------------------------------------
# Node: search_alternative_window
# ---------------------------------------------------------------------------

async def search_alternative_window(state: ConflictState) -> dict:
    result = await run_slot_search(
        structured_window=state["current_search_window"],
        duration_minutes=state["duration_minutes"],
    )
    tried = list(state["tried_windows"]) + [state["current_search_window"]]
    return {
        "tried_windows": tried,
        "current_attempt_succeeded": result["search_succeeded"],
        "last_search_result": result,
    }


# ---------------------------------------------------------------------------
# Conditional edge: route_after_search
# ---------------------------------------------------------------------------

def route_after_search(state: ConflictState) -> str:
    if state["escalation_needed"] or state["conflict_attempts"] >= 4:
        return "escalate"
    if state["current_attempt_succeeded"]:
        return "draft_success_message"
    if state["conflict_attempts"] >= 2:
        return "draft_suggestion_message"
    return "determine_next_window"


# ---------------------------------------------------------------------------
# Node: draft_success_message
# ---------------------------------------------------------------------------

async def draft_success_message(state: ConflictState) -> dict:
    slot = state["last_search_result"]["available_slots"][0]
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3, api_key=settings.gemini_api_key)
    response = await llm.ainvoke([HumanMessage(content=DRAFT_SUCCESS_PROMPT.format(slot_display=slot["display"]))])
    return {
        "suggested_slot": slot,
        "message_to_speak": response.content.strip(),
        "natural_language_result": response.content.strip(),
        "status": "needs_user_input",
    }


# ---------------------------------------------------------------------------
# Node: draft_suggestion_message
# ---------------------------------------------------------------------------

async def draft_suggestion_message(state: ConflictState) -> dict:
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3, api_key=settings.gemini_api_key)
    response = await llm.ainvoke([HumanMessage(content=DRAFT_SUGGESTION_PROMPT.format(
        tried_windows=json.dumps(state["tried_windows"]),
        next_search=json.dumps(state.get("current_search_window", {}))
    ))])
    return {
        "message_to_speak": response.content.strip(),
        "natural_language_result": response.content.strip(),
        "status": "needs_user_input",
    }


# ---------------------------------------------------------------------------
# Node: suggest_to_user  (interrupt point)
# ---------------------------------------------------------------------------

def suggest_to_user(state: ConflictState) -> dict:
    # interrupt() pauses the graph and returns the resume value when continued.
    # The resume value is Gemini's updated situation_summary.
    updated_summary = interrupt(state["message_to_speak"])
    # On resume: update situation_summary so determine_next_window can re-parse it.
    return {"situation_summary": updated_summary}


# ---------------------------------------------------------------------------
# Node: escalate
# ---------------------------------------------------------------------------

async def escalate(state: ConflictState) -> dict:
    await _send_escalation_email(state["situation_summary"])
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3, api_key=settings.gemini_api_key)
    response = await llm.ainvoke([HumanMessage(content=DRAFT_ESCALATION_PROMPT)])
    
    return {
        "status": "escalate",
        "message_to_speak": response.content.strip(),
        "natural_language_result": response.content.strip(),
        "escalation_needed": True,
    }
