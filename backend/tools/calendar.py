"""
All Google Calendar API calls live here — nowhere else in the codebase.
No LLM calls. No LangGraph. Pure async Python utilities.
"""
import asyncio
import pickle
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

TOKEN_PATH = Path(__file__).parent.parent / "token.pickle"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_credentials():
    """
    Load OAuth credentials from token.pickle.
    Refreshes silently if expired. Runs full browser OAuth flow on first use.
    """
    creds = None
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        client_config = {
            "installed": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uris": [settings.google_redirect_uri],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=8080)

    with open(TOKEN_PATH, "wb") as f:
        pickle.dump(creds, f)

    return creds


def _build_service():
    """Build an authenticated Google Calendar API client."""
    return build("calendar", "v3", credentials=get_credentials())


# ---------------------------------------------------------------------------
# Freebusy query
# ---------------------------------------------------------------------------

async def query_freebusy(date_start: str, date_end: str, timezone: str) -> list[dict]:
    """
    Query the Calendar Freebusy API for the primary calendar.

    Args:
        date_start: ISO 8601 datetime string (window start)
        date_end:   ISO 8601 datetime string (window end)
        timezone:   IANA timezone string e.g. "Asia/Kolkata"

    Returns:
        List of busy intervals: [{start: ISO, end: ISO}, ...]
    """
    loop = asyncio.get_event_loop()

    tz = ZoneInfo(timezone)
    start_dt = datetime.fromisoformat(date_start)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=tz)
    
    end_dt = datetime.fromisoformat(date_end)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=tz)
        
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=24)

    safe_start = start_dt.isoformat()
    safe_end = end_dt.isoformat()

    def _query():
        service = _build_service()
        body = {
            "timeMin": safe_start,
            "timeMax": safe_end,
            "timeZone": timezone,
            "items": [{"id": "primary"}],
        }
        result = service.freebusy().query(body=body).execute()
        return result.get("calendars", {}).get("primary", {}).get("busy", [])

    busy = await loop.run_in_executor(None, _query)
    return [{"start": b["start"], "end": b["end"]} for b in busy]


# ---------------------------------------------------------------------------
# Free slot computation (pure Python — no API call)
# ---------------------------------------------------------------------------

async def compute_free_slots(
    window: dict,
    busy_periods: list[dict],
    duration_minutes: int,
) -> list[dict]:
    """
    Find free time gaps within a window that can fit a meeting of duration_minutes.

    Args:
        window: {date_start: ISO, date_end: ISO, preferred_start_hour: int,
                 preferred_end_hour: int}
        busy_periods: [{start: ISO, end: ISO}, ...] from query_freebusy
        duration_minutes: required meeting length

    Returns:
        Up to 5 free slots: [{start: ISO, end: ISO, display: "Thursday 10:30 AM"}, ...]
    """
    tz = ZoneInfo(settings.user_timezone)
    duration = timedelta(minutes=duration_minutes)

    # Parse window bounds securely handling naive missing offsets
    win_start = datetime.fromisoformat(window["date_start"])
    if win_start.tzinfo is None:
        win_start = win_start.replace(tzinfo=tz)
    else:
        win_start = win_start.astimezone(tz)

    win_end = datetime.fromisoformat(window["date_end"])
    if win_end.tzinfo is None:
        win_end = win_end.replace(tzinfo=tz)
    else:
        win_end = win_end.astimezone(tz)
        
    if win_end <= win_start:
        win_end = win_start + timedelta(hours=24)

    if "preferred_start_hour" in window:
        win_start = win_start.replace(
            hour=window["preferred_start_hour"], minute=0, second=0, microsecond=0
        )
    if "preferred_end_hour" in window:
        win_end = win_end.replace(
            hour=window["preferred_end_hour"], minute=0, second=0, microsecond=0
        )

    # Parse and sort busy intervals
    parsed_busy = sorted(
        [
            (
                datetime.fromisoformat(b["start"]).astimezone(tz),
                datetime.fromisoformat(b["end"]).astimezone(tz),
            )
            for b in busy_periods
        ],
        key=lambda x: x[0],
    )

    # Merge overlapping busy intervals
    merged: list[tuple[datetime, datetime]] = []
    for b_start, b_end in parsed_busy:
        if merged and b_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b_end))
        else:
            merged.append((b_start, b_end))

    # Walk window, collect free gaps
    free_slots: list[dict] = []
    cursor = win_start

    for b_start, b_end in merged:
        # Collect free slots before this busy block
        while cursor + duration <= b_start and len(free_slots) < 5:
            slot_end = cursor + duration
            free_slots.append(_format_slot(cursor, slot_end))
            cursor += duration
        # Skip past the busy block
        if cursor < b_end:
            cursor = b_end

    # Collect remaining free slots after all busy blocks
    while cursor + duration <= win_end and len(free_slots) < 5:
        slot_end = cursor + duration
        free_slots.append(_format_slot(cursor, slot_end))
        cursor += duration

    return free_slots


def _format_slot(start: datetime, end: datetime) -> dict:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "display": start.strftime("%A %-I:%M %p"),  # e.g. "Thursday 10:30 AM"
    }


# ---------------------------------------------------------------------------
# Event search
# ---------------------------------------------------------------------------

async def get_event_by_title(query: str, search_days: int = 30) -> Optional[dict]:
    """
    Fuzzy-search the primary calendar for an event matching query.
    Searches backwards and forwards historically up to search_days.

    Returns:
        {title, start_iso, end_iso, location} for the best match, or None.
    """
    loop = asyncio.get_event_loop()

    def _search():
        service = _build_service()
        now = datetime.now(dt_timezone.utc)
        time_min = now
        time_max = now + timedelta(days=search_days)

        print(f"[CALENDAR ENGINE] Executing API Search for Query: '{query}'")

        try:
            result = (
                service.events()
                .list(
                    calendarId="primary",
                    q=query,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=10,
                )
                .execute()
            )
        except Exception as e:
            print(f"[CALENDAR ENGINE] API Error: {e}")
            return None

        items = result.get("items", [])
        
        # LOG INJECTION FOR USER VISIBILITY
        print(f"[CALENDAR ENGINE] Google API returned {len(items)} raw matches.")
        for idx, i in enumerate(items):
            print(f"  -> Match {idx+1}: {i.get('summary', 'No Title')} (Starts: {i.get('start', {}).get('dateTime', 'All-day')})")

        if not items:
            return None

        # Relaxed matching: The API already natively filters using `q=`.
        # Just grab the very first valid event that has a physical start time mapped.
        matched = None
        for e in items:
            if "start" in e and ("dateTime" in e["start"] or "date" in e["start"]):
                matched = e
                break

        if matched is None:
            print("[CALENDAR ENGINE] No valid start bounds found in any matches.")
            return None

        start = matched["start"].get("dateTime", matched["start"].get("date"))
        end = matched["end"].get("dateTime", matched["end"].get("date"))
        
        print(f"[CALENDAR ENGINE] Selected mapping: {matched.get('summary')} @ {start}")
        
        return {
            "title": matched.get("summary", ""),
            "start_iso": start,
            "end_iso": end,
            "location": matched.get("location", ""),
        }

    return await loop.run_in_executor(None, _search)


# ---------------------------------------------------------------------------
# Event creation
# ---------------------------------------------------------------------------

async def create_event(
    title: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
) -> dict:
    """
    Create an event on the primary calendar.

    Returns:
        {id, title, start_iso, end_iso, html_link}
    """
    loop = asyncio.get_event_loop()

    def _create():
        service = _build_service()
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": settings.user_timezone},
            "end": {"dateTime": end_iso, "timeZone": settings.user_timezone},
        }
        event = service.events().insert(calendarId="primary", body=body).execute()
        return {
            "id": event.get("id"),
            "title": event.get("summary"),
            "start_iso": event["start"].get("dateTime"),
            "end_iso": event["end"].get("dateTime"),
            "html_link": event.get("htmlLink"),
        }

    return await loop.run_in_executor(None, _create)
