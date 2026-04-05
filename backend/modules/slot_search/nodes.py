"""
M2 SlotSearch nodes.

Graph: normalize_input → query_freebusy → compute_free_slots → format_response → END

normalize_input  — LLM (temp=0, structured output) when raw_slot_description provided;
                   passthrough when structured_window provided (M3 path).
query_freebusy   — calls calendar.query_freebusy (no LLM).
compute_free_slots — calls calendar.compute_free_slots (no LLM).
format_response  — LLM (temp=0.3) converts slot list to speakable NL sentence.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from agent_types import SlotSearchState
from config import settings
import tools.calendar as calendar


# ---------------------------------------------------------------------------
# Pydantic schema for normalize_input structured output
# ---------------------------------------------------------------------------

class NormalizedWindow(BaseModel):
    date_start: str          # ISO 8601 with tz offset, e.g. "2026-04-09T10:30:00+05:30"
    date_end: str            # ISO 8601 with tz offset
    preferred_start_hour: int  # 0-23, inferred from NL ("morning" → 8, "10:30 AM" → 10)
    preferred_end_hour: int    # 0-23, inferred from NL ("6 PM" → 18)


NORMALIZE_INPUT_PROMPT = """You are a scheduling assistant that converts natural language time descriptions into structured calendar windows.
Today is {weekday}, {today_date}. The user's timezone is {user_timezone}.
Parse the given description into a search window. Set date_start and date_end as ISO 8601 strings with the correct timezone offset. Set preferred_start_hour and preferred_end_hour (0-23) to reflect the time range implied by the description (e.g. 'morning' → 8–12, 'afternoon' → 12–17, '10:30 AM to 6 PM' → 10–18). When the description refers to a weekday by name (e.g. 'Thursday'), resolve it to the correct upcoming calendar date relative to today ({weekday}, {today_date})."""

# ---------------------------------------------------------------------------
# Node: normalize_input
# ---------------------------------------------------------------------------

async def normalize_input(state: SlotSearchState) -> dict:
    # M3 path — structured_window already parsed, skip LLM
    if state.get("structured_window") is not None:
        return {"normalized_window": state["structured_window"]}

    # Gemini path — parse raw NL into a structured window
    user_tz = ZoneInfo(settings.user_timezone)
    now = datetime.now(user_tz)
    today_date = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")  # e.g. "Friday"

    system_prompt = NORMALIZE_INPUT_PROMPT.format(
        weekday=weekday,
        today_date=today_date,
        user_timezone=settings.user_timezone
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
        api_key=settings.gemini_api_key,
    ).with_structured_output(NormalizedWindow)

    result: NormalizedWindow = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["raw_slot_description"]),
    ])

    return {"normalized_window": result.model_dump()}


# ---------------------------------------------------------------------------
# Node: query_freebusy
# ---------------------------------------------------------------------------

async def query_freebusy(state: SlotSearchState) -> dict:
    window = state["normalized_window"]
    busy = await calendar.query_freebusy(
        window["date_start"],
        window["date_end"],
        settings.user_timezone,
    )
    return {"busy_periods": busy}


# ---------------------------------------------------------------------------
# Node: compute_free_slots
# ---------------------------------------------------------------------------

async def compute_free_slots(state: SlotSearchState) -> dict:
    slots = await calendar.compute_free_slots(
        state["normalized_window"],
        state["busy_periods"],
        state["duration_minutes"],
    )
    return {"available_slots": slots, "search_succeeded": len(slots) > 0}


# ---------------------------------------------------------------------------
# Node: format_response
# ---------------------------------------------------------------------------

async def format_response(state: SlotSearchState) -> dict:
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.3,
        api_key=settings.gemini_api_key,
    )

    if state["search_succeeded"]:
        display_times = ", ".join(s["display"] for s in state["available_slots"])
        user_message = (
            f"Available {state['duration_minutes']}-minute slots: {display_times}. "
            f"Write one concise, friendly sentence listing these options for the user to choose from."
        )
    else:
        user_message = (
            f"No {state['duration_minutes']}-minute slots were found in the requested window. "
            f"Write one concise, friendly sentence telling the user the window is fully booked "
            f"and suggesting they try a different time or day."
        )

    response = await llm.ainvoke([HumanMessage(content=user_message)])
    return {"natural_language_result": response.content.strip()}
