"""
M1 TimeResolution nodes.

Graph:
  classify_expression
    ├── event_anchored | deadline_anchored → lookup_reference_event
    │                                             ├── found     → compute_window
    │                                             └── not found → validate_and_format (early exit)
    ├── memory_dependent → load_from_memory → compute_window
    └── relative_date | complex_date → compute_window
                                             ↓
                                     validate_and_format → END
"""
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from agent_types import TimeResolutionState
from config import settings
import tools.calendar as calendar
import memory.store as memory


# ---------------------------------------------------------------------------
# Pydantic schemas for LLM structured output
# ---------------------------------------------------------------------------

class ExpressionClassification(BaseModel):
    expression_type: str                    # one of the 5 valid types
    referenced_event_name: Optional[str]    # e.g. "Project Alpha", "flight to delhi"


class ComputedWindow(BaseModel):
    date_start: str             # ISO 8601 with tz offset, e.g. "2026-04-09T10:30:00+05:30"
    date_end: str               # ISO 8601 with tz offset
    preferred_start_hour: int   # 0-23
    preferred_end_hour: int     # 0-23
    duration_minutes: int       # from duration_hint or inferred from expression
    confidence: float           # 0.0-1.0
    needs_clarification: Optional[str]  # spoken question if LLM is unsure


    needs_clarification: Optional[str]  # spoken question if LLM is unsure

CLASSIFY_EXPRESSION_PROMPT = """You are a scheduling assistant that classifies natural language time expressions.
Today is {weekday}, {today_date}. The user's timezone is {tz}.

Classify the expression into exactly one of these types:
  event_anchored   — references a specific calendar event (e.g. 'after Project Alpha')
  deadline_anchored — references a deadline tied to an event (e.g. 'before my flight')
  memory_dependent — references a recurring pattern or habit (e.g. 'our usual sync-up')
  relative_date    — simple relative date resolvable from today (e.g. 'next Friday')
  complex_date     — complex calendar logic (e.g. 'last weekday of the month')

Also extract the referenced event name if the expression is event_anchored or deadline_anchored. 
CRITICAL RULE: Scrub all conversational phrasing, possessive adjectives, and determiners (like 'my', 'the', 'our') from the event name so it serves as a raw, clean calendar search term. For example, if the user says 'my flight to delhi', the referenced_event_name must be 'flight to delhi'. If they say 'the marketing sync', it must be 'marketing sync'."""

COMPUTE_WINDOW_PROMPT = """You are a scheduling assistant that handles tricky time expressions where standard scheduling isn't enough. You are invoked when the user asks for a meeting that depends on other calendar events, specific constraints, or their past preferences.

You are given information about the user's time expression and the surrounding context. 

Your job is to understand what the user wants, apply logical scheduling rules, and provide the specific search boundaries so the calendar system knows exactly where to look.

You must parse the situation and extract these scheduling parameters:
- date_start & date_end: The specific dates the meeting could happen between.
- preferred_start_hour & preferred_end_hour: The hour constraints during those days when the meeting should fall (e.g. afternoon means start hour 12, end hour 17).
- duration_minutes: How long the meeting should last.
- confidence: How sure you are that you've correctly guessed their intent (0.0 to 1.0).
- needs_clarification: If your confidence is below 0.6, write a warm, brief question asking the user to clarify. Otherwise, leave it empty.

Follow these scheduling strategies:
- If the expression is anchored to an event (e.g., 'after Project Alpha'), look at the event's timeline and set your date_start safely after it finishes. 
- If the user relies on vague timing ('morning', 'afternoon', 'EOD'), translate them into standard integer hours (e.g. morning = 8 to 12).
- If they don't state how long the meeting should be, infer a practical default duration based on what they are asking for (e.g., 'quick sync' = 15 mins, 'planning' = 60 mins).
- If the request relies on their memory or past habits (e.g., 'usual standup'), read the user_preferences carefully to find the answer.

Follow the structural output format strictly.

Today is {weekday}, {today_date}. User timezone: {tz}.
Expression type: {expression_type}
Raw expression: {raw_expression}
Duration hint: {duration_hint} minutes
Additional constraints: {additional_constraints}
Referenced event: {referenced_event}
User preferences: {user_preferences}

Compute the search window boundaries. All ISO datetimes must include the {tz} offset.

GO STEP BY STEP TO PARSE THE EXPRESSION CORRECTLY INTO THE PARAMETERS"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_context() -> tuple[str, str, str]:
    """Return (today_date, weekday, user_timezone) for prompt injection."""
    user_tz = ZoneInfo(settings.user_timezone)
    now = datetime.now(user_tz)
    return now.strftime("%Y-%m-%d"), now.strftime("%A"), settings.user_timezone


# ---------------------------------------------------------------------------
# Node: classify_expression
# ---------------------------------------------------------------------------

async def classify_expression(state: TimeResolutionState) -> dict:
    today_date, weekday, tz = _now_context()

    system_prompt = CLASSIFY_EXPRESSION_PROMPT.format(
        weekday=weekday,
        today_date=today_date,
        tz=tz
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
        api_key=settings.gemini_api_key,
    ).with_structured_output(ExpressionClassification)

    result: ExpressionClassification = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["raw_expression"]),
    ])

    partial_event = {"name": result.referenced_event_name} if result.referenced_event_name else None
    return {
        "expression_type": result.expression_type,
        "referenced_event": partial_event,
    }


# ---------------------------------------------------------------------------
# Node: lookup_reference_event
# ---------------------------------------------------------------------------

async def lookup_reference_event(state: TimeResolutionState) -> dict:
    event_name = state["referenced_event"]["name"]
    event = await calendar.get_event_by_title(event_name)

    if event is None:
        msg = (
            f"I couldn't find '{event_name}' on your calendar. "
            f"Could you tell me when it is?"
        )
        return {
            "referenced_event": None,
            "needs_clarification": msg,
            "natural_language_summary": msg,
            "status": "needs_clarification",
            "confidence": 0.0,
        }

    return {"referenced_event": event}


# ---------------------------------------------------------------------------
# Node: load_from_memory
# ---------------------------------------------------------------------------

async def load_from_memory(state: TimeResolutionState) -> dict:
    loop = asyncio.get_event_loop()
    loaded = await loop.run_in_executor(None, memory.load_memory, "default")
    merged = {**state["user_preferences"], **loaded}
    return {"user_preferences": merged}


# ---------------------------------------------------------------------------
# Node: compute_window
# ---------------------------------------------------------------------------

async def compute_window(state: TimeResolutionState) -> dict:
    today_date, weekday, tz = _now_context()

    system_prompt = COMPUTE_WINDOW_PROMPT.format(
        weekday=weekday,
        today_date=today_date,
        tz=tz,
        expression_type=state["expression_type"],
        raw_expression=state["raw_expression"],
        duration_hint=state["duration_hint"],
        additional_constraints=state["additional_constraints"] or 'none',
        referenced_event=json.dumps(state["referenced_event"]),
        user_preferences=json.dumps(state["user_preferences"])
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
        api_key=settings.gemini_api_key,
    ).with_structured_output(ComputedWindow)

    result: ComputedWindow = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content="Resolve the window."),
    ])

    resolved_window = {
        "date_start": result.date_start,
        "date_end": result.date_end,
        "preferred_start_hour": result.preferred_start_hour,
        "preferred_end_hour": result.preferred_end_hour,
        "duration_minutes": result.duration_minutes,
    }

    print(f"[M1 ENGINE] LLM Computed Bounds:")
    print(f"  -> date_start: {result.date_start} | date_end: {result.date_end}")
    print(f"  -> hours: {result.preferred_start_hour} to {result.preferred_end_hour}")
    print(f"  -> confidence: {result.confidence} | clarify: {result.needs_clarification}")

    return {
        "resolved_window": resolved_window,
        "confidence": result.confidence,
        "needs_clarification": result.needs_clarification,
    }


# ---------------------------------------------------------------------------
# Node: validate_and_format
# ---------------------------------------------------------------------------

def validate_and_format(state: TimeResolutionState) -> dict:
    # Early-exit path — needs_clarification already fully set by lookup_reference_event
    if state.get("needs_clarification") and state.get("status") == "needs_clarification":
        return {
            "status": state["status"],
            "natural_language_summary": state["natural_language_summary"],
            "needs_clarification": state["needs_clarification"],
        }

    # Needs clarification from compute_window (low confidence or unsure)
    if state.get("needs_clarification"):
        return {
            "status": "needs_clarification",
            "natural_language_summary": state["needs_clarification"],
        }

    window = state.get("resolved_window")

    if window:
        user_tz = ZoneInfo(settings.user_timezone)
        now = datetime.now(user_tz)
        sixty_days = now + timedelta(days=60)

        try:
            start_dt = datetime.fromisoformat(window["date_start"])
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=user_tz)

            end_dt = datetime.fromisoformat(window["date_end"])
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=user_tz)

            # Fixed: Only fail if start is strictly > end. 
            # Single-day bookings commonly map start == end securely.
            if start_dt > end_dt:
                print(f"[M1 ENGINE] Validation Failed: start_dt {start_dt} > end_dt {end_dt}")
                return {
                    "status": "needs_clarification",
                    "needs_clarification": "I couldn't determine a valid time window. Could you clarify when you'd like to meet?",
                    "natural_language_summary": "I couldn't determine a valid time window. Could you clarify when you'd like to meet?",
                }
            if start_dt < now:
                return {
                    "status": "needs_clarification",
                    "needs_clarification": "That time appears to be in the past. Could you tell me a future date?",
                    "natural_language_summary": "That time appears to be in the past. Could you tell me a future date?",
                }
            if start_dt > sixty_days:
                return {
                    "status": "needs_clarification",
                    "needs_clarification": "That date is more than 60 days away. Could you pick a sooner time?",
                    "natural_language_summary": "That date is more than 60 days away. Could you pick a sooner time?",
                }
        except (ValueError, KeyError):
            return {
                "status": "needs_clarification",
                "needs_clarification": "I couldn't parse the time window. Could you clarify when you'd like to meet?",
                "natural_language_summary": "I couldn't parse the time window. Could you clarify when you'd like to meet?",
            }

    # Build natural_language_summary from template
    summary = "I've resolved the time window and will search for available slots."
    if window:
        try:
            start_dt = datetime.fromisoformat(window["date_start"])
            day_str = start_dt.strftime("%A %B %-d")
            start_hour = window["preferred_start_hour"]
            end_hour = window["preferred_end_hour"]
            duration = window["duration_minutes"]

            event = state.get("referenced_event")
            if event and event.get("title"):
                event_start = datetime.fromisoformat(event["start_iso"])
                event_time_str = event_start.strftime("%-I:%M %p")
                summary = (
                    f"{event['title']} is on {day_str} at {event_time_str}. "
                    f"I'll look for a {duration}-minute slot after that, "
                    f"between {start_hour}:00 and {end_hour}:00."
                )
            else:
                summary = (
                    f"I'll search {day_str} between {start_hour}:00 and {end_hour}:00 "
                    f"for a {duration}-minute slot."
                )
        except (ValueError, KeyError):
            pass

    return {
        "status": "resolved",
        "natural_language_summary": summary,
    }
