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
import structlog
from utils.telemetry import track_latency
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Optional
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.types import interrupt
from pydantic import BaseModel, model_validator
from typing import Any

from agent_types import ConflictState
from config import settings
from modules.slot_search import run_slot_search
import utils.trace as trace

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Pydantic schema for determine_next_window structured output
# ---------------------------------------------------------------------------

class StructuredWindow(BaseModel):
    date_start: str
    date_end: str
    preferred_start_hour: int
    preferred_end_hour: int

class Preferences(BaseModel):
    day_pref: str    # e.g. "Wednesday", "next business day"
    time_pref: str   # e.g. "evening", "afternoon", "full day 8AM-8PM"

class FailedWindow(BaseModel):
    date: str        # ISO date e.g. "2026-04-15"
    start_hour: int  # 0-23
    end_hour: int    # 0-23

class NextWindow(BaseModel):
    current_preferences: Preferences
    current_failed_window: FailedWindow
    duration_minutes: int           # Extracted naturally from the situation summary
    escalation_needed: bool         # True when no more meaningful options exist



DETERMINE_NEXT_WINDOW_PROMPT = """You are a scheduling assistant resolving a calendar conflict.

You are given information about the scheduling conflict in the form of a situation summary that describes the conflict along with a number of attempts tried for conflict resolution.

Your job is to understand the situation and extract the relevant information from it.
You have to parse the situation and extract the required parameters:

1. current preferences (current user preference along with time slot)
2. current failed window (the window in which there is a conflict in scheduling)
3. intended meeting duration_minutes
4. escalation_needed - set to True ONLY when conflict_attempts >= 2

Follow the structural output format strictly.

Today is {weekday}, {today_date}. User timezone: {tz}.
Current conflict_attempts: {conflict_attempts}.
Windows already tried (do NOT suggest these again): {tried_windows}.

SITUATION SUMMARY:
{situation_summary}"""

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


def _compute_next_window(conflict_attempts: int, failed_window: dict, tz: str) -> dict | None:
    """Deterministically compute the next search window from the retry ladder.
    
    attempt 0 — expand the original day: full day 8AM–8PM
    attempt 1 — next day, preserve original time_pref hours
    attempt >= 2 — escalation, return None
    """
    if conflict_attempts >= 2:
        return None

    failed_date = failed_window["date"]
    failed_dt = datetime.fromisoformat(failed_date)

    if conflict_attempts == 0:
        # Expand same day to full working hours
        return {
            "date_start": f"{failed_date}T08:00:00{_tz_offset(tz)}",
            "date_end": f"{failed_date}T20:00:00{_tz_offset(tz)}",
            "preferred_start_hour": 8,
            "preferred_end_hour": 20,
        }
    elif conflict_attempts == 1:
        # Next day, preserve original time range
        next_day = (failed_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            "date_start": f"{next_day}T{failed_window['start_hour']:02d}:00:00{_tz_offset(tz)}",
            "date_end": f"{next_day}T{failed_window['end_hour']:02d}:00:00{_tz_offset(tz)}",
            "preferred_start_hour": failed_window["start_hour"],
            "preferred_end_hour": failed_window["end_hour"],
        }
    return None


def _tz_offset(tz: str) -> str:
    """Get the UTC offset string for a timezone, e.g. '+05:30'."""
    user_tz = ZoneInfo(tz)
    now = datetime.now(user_tz)
    offset = now.strftime("%z")
    return f"{offset[:3]}:{offset[3:]}"


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

@track_latency
async def determine_next_window(state: ConflictState) -> dict:
    log = logger.bind(module="conflict_resolution", session_id=state.get("thread_id"))
    today_date, weekday, tz = _now_context()
    trace.node_enter("determine_next_window",
        inputs={"situation_summary": state["situation_summary"]},
        extra=f"attempt={state['conflict_attempts']}"
    )

    system_prompt = DETERMINE_NEXT_WINDOW_PROMPT.format(
        weekday=weekday,
        today_date=today_date,
        tz=tz,
        conflict_attempts=state["conflict_attempts"],
        tried_windows=json.dumps(state["tried_windows"]),
        situation_summary=state["situation_summary"],
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
        api_key=settings.gemini_api_key,
    ).with_structured_output(NextWindow)

    result: NextWindow = await llm.ainvoke([
        HumanMessage(content=system_prompt),
    ])

    # Deterministic: compute next_search_window from the retry ladder in code
    failed = result.current_failed_window.model_dump()
    next_window = _compute_next_window(state["conflict_attempts"], failed, tz)
    escalation = result.escalation_needed or (next_window is None)

    trace.node_exit("determine_next_window",
        outputs={
            "preferences": result.current_preferences.model_dump(),
            "failed_window": failed,
            "next_window (computed)": next_window or "ESCALATION",
            "duration_minutes": result.duration_minutes,
            "escalation_needed": escalation,
        },
        delta={"conflict_attempts": (state["conflict_attempts"], state["conflict_attempts"] + 1)}
    )
    return {
        "current_preferences": result.current_preferences.model_dump(),
        "current_failed_window": failed,
        "current_search_window": next_window or {},
        "duration_minutes": result.duration_minutes,
        "escalation_needed": escalation,
        "conflict_attempts": state["conflict_attempts"] + 1,
    }


# ---------------------------------------------------------------------------
# Node: search_alternative_window
# ---------------------------------------------------------------------------

@track_latency
async def search_alternative_window(state: ConflictState) -> dict:
    log = logger.bind(module="conflict_resolution", session_id=state.get("thread_id"))
    trace.node_enter("search_alternative_window", inputs={
        "window": state.get("current_search_window"),
        "duration_minutes": state.get("duration_minutes"),
    })

    if state.get("escalation_needed") or not state.get("current_search_window"):
        trace.node_exit("search_alternative_window", outputs={"skipped": "escalation_needed or no window"})
        return {
            "tried_windows": state["tried_windows"],
            "current_attempt_succeeded": False,
            "last_search_result": {"search_succeeded": False, "available_slots": [], "natural_language_result": ""},
        }

    result = await run_slot_search(
        structured_window=state["current_search_window"],
        duration_minutes=state["duration_minutes"],
    )
    tried = list(state["tried_windows"]) + [state["current_search_window"]]
    slots = result.get("available_slots", [])
    trace.node_exit("search_alternative_window",
        outputs={
            "search_succeeded": result["search_succeeded"],
            "slots": [s.get("display") for s in slots] if slots else "none",
        },
        delta={
            "tried_windows": (len(state["tried_windows"]), len(tried)),
            "current_attempt_succeeded": result["search_succeeded"],
        }
    )
    return {
        "tried_windows": tried,
        "current_attempt_succeeded": result["search_succeeded"],
        "last_search_result": result,
    }


# ---------------------------------------------------------------------------
# Conditional edge: route_after_search
# ---------------------------------------------------------------------------

def route_after_search(state: ConflictState) -> str:
    keys = {
        "escalation": state["escalation_needed"],
        "attempts": state["conflict_attempts"],
        "succeeded": state["current_attempt_succeeded"],
    }
    if state["escalation_needed"] or state["conflict_attempts"] >= 4:
        trace.router_decision("route_after_search", "escalate", keys)
        return "escalate"
    if state["current_attempt_succeeded"]:
        trace.router_decision("route_after_search", "draft_success_message", keys)
        return "draft_success_message"
    trace.router_decision("route_after_search", "draft_suggestion_message", keys)
    return "draft_suggestion_message"


# ---------------------------------------------------------------------------
# Node: draft_success_message
# ---------------------------------------------------------------------------

@track_latency
async def draft_success_message(state: ConflictState) -> dict:
    log = logger.bind(module="conflict_resolution", session_id=state.get("thread_id"))
    slot = state["last_search_result"]["available_slots"][0]
    trace.node_enter("draft_success_message", inputs={"slot": slot.get("display")})
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3, api_key=settings.gemini_api_key)
    response = await llm.ainvoke([HumanMessage(content=DRAFT_SUCCESS_PROMPT.format(slot_display=slot["display"]))])
    trace.node_exit("draft_success_message", outputs={"message": response.content.strip()})
    return {
        "suggested_slot": slot,
        "message_to_speak": response.content.strip(),
        "natural_language_result": response.content.strip(),
        "status": "needs_user_input",
    }


# ---------------------------------------------------------------------------
# Node: draft_suggestion_message
# ---------------------------------------------------------------------------

@track_latency
async def draft_suggestion_message(state: ConflictState) -> dict:
    log = logger.bind(module="conflict_resolution", session_id=state.get("thread_id"))
    trace.node_enter("draft_suggestion_message", inputs={
        "tried_windows": len(state["tried_windows"]),
        "next_search": state.get("current_search_window"),
    })
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3, api_key=settings.gemini_api_key)
    response = await llm.ainvoke([HumanMessage(content=DRAFT_SUGGESTION_PROMPT.format(
        tried_windows=json.dumps(state["tried_windows"]),
        next_search=json.dumps(state.get("current_search_window", {}))
    ))])
    trace.node_exit("draft_suggestion_message", outputs={"message": response.content.strip()})
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

@track_latency
async def escalate(state: ConflictState) -> dict:
    log = logger.bind(module="conflict_resolution", session_id=state.get("thread_id"))
    trace.node_enter("escalate", inputs={"conflict_attempts": state.get("conflict_attempts")})
    await _send_escalation_email(state["situation_summary"])
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3, api_key=settings.gemini_api_key)
    response = await llm.ainvoke([HumanMessage(content=DRAFT_ESCALATION_PROMPT)])
    trace.node_exit("escalate", outputs={"message": response.content.strip()})
    return {
        "status": "escalate",
        "message_to_speak": response.content.strip(),
        "natural_language_result": response.content.strip(),
        "escalation_needed": True,
    }
