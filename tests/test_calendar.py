"""
Tests for backend/tools/calendar.py

Google API calls are mocked so no real credentials are needed.
compute_free_slots is pure Python — tested directly without mocking.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from tools.calendar import compute_free_slots, query_freebusy, get_event_by_title, create_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso(dt_str: str) -> str:
    """Return a timezone-aware ISO string for Asia/Kolkata (+05:30)."""
    return dt_str  # already ISO in tests; real tz handling tested via compute_free_slots


# ---------------------------------------------------------------------------
# compute_free_slots — pure Python, no mocking
# ---------------------------------------------------------------------------

class TestComputeFreeSlots:
    BASE_WINDOW = {
        "date_start": "2026-04-09T08:00:00+05:30",
        "date_end":   "2026-04-09T18:00:00+05:30",
        "preferred_start_hour": 8,
        "preferred_end_hour": 18,
    }

    @pytest.mark.asyncio
    async def test_no_busy_returns_slots(self):
        slots = await compute_free_slots(self.BASE_WINDOW, [], duration_minutes=30)
        assert len(slots) == 5
        assert all("start" in s and "end" in s and "display" in s for s in slots)

    @pytest.mark.asyncio
    async def test_fully_booked_returns_empty(self):
        busy = [{"start": "2026-04-09T08:00:00+05:30", "end": "2026-04-09T18:00:00+05:30"}]
        slots = await compute_free_slots(self.BASE_WINDOW, busy, duration_minutes=30)
        assert slots == []

    @pytest.mark.asyncio
    async def test_busy_in_middle_returns_slots_around_it(self):
        busy = [{"start": "2026-04-09T10:00:00+05:30", "end": "2026-04-09T14:00:00+05:30"}]
        slots = await compute_free_slots(self.BASE_WINDOW, busy, duration_minutes=60)
        starts = [s["start"] for s in slots]
        # All slots must be outside the busy block
        for s in slots:
            slot_start = datetime.fromisoformat(s["start"])
            slot_end = datetime.fromisoformat(s["end"])
            busy_start = datetime.fromisoformat(busy[0]["start"])
            busy_end = datetime.fromisoformat(busy[0]["end"])
            assert slot_end <= busy_start or slot_start >= busy_end

    @pytest.mark.asyncio
    async def test_overlapping_busy_intervals_merged(self):
        busy = [
            {"start": "2026-04-09T09:00:00+05:30", "end": "2026-04-09T11:00:00+05:30"},
            {"start": "2026-04-09T10:30:00+05:30", "end": "2026-04-09T12:30:00+05:30"},
        ]
        slots = await compute_free_slots(self.BASE_WINDOW, busy, duration_minutes=30)
        for s in slots:
            slot_start = datetime.fromisoformat(s["start"])
            # No slot should start inside the merged block (9:00–12:30)
            merged_start = datetime.fromisoformat("2026-04-09T09:00:00+05:30")
            merged_end = datetime.fromisoformat("2026-04-09T12:30:00+05:30")
            assert not (merged_start <= slot_start < merged_end)

    @pytest.mark.asyncio
    async def test_preferred_hours_respected(self):
        window = {
            "date_start": "2026-04-09T00:00:00+05:30",
            "date_end":   "2026-04-09T23:59:00+05:30",
            "preferred_start_hour": 13,
            "preferred_end_hour": 17,
        }
        slots = await compute_free_slots(window, [], duration_minutes=30)
        for s in slots:
            slot_start = datetime.fromisoformat(s["start"])
            assert slot_start.hour >= 13

    @pytest.mark.asyncio
    async def test_capped_at_five_results(self):
        slots = await compute_free_slots(self.BASE_WINDOW, [], duration_minutes=30)
        assert len(slots) <= 5

    @pytest.mark.asyncio
    async def test_slot_too_large_returns_empty(self):
        # Window is 8AM–6PM = 600 minutes; 601 minutes cannot fit
        slots = await compute_free_slots(self.BASE_WINDOW, [], duration_minutes=601)
        assert slots == []

    @pytest.mark.asyncio
    async def test_display_format(self):
        slots = await compute_free_slots(self.BASE_WINDOW, [], duration_minutes=30)
        assert len(slots) > 0
        # display should be like "Thursday 8:00 AM"
        assert "AM" in slots[0]["display"] or "PM" in slots[0]["display"]


# ---------------------------------------------------------------------------
# query_freebusy — mocked Google API
# ---------------------------------------------------------------------------

MOCK_BUSY = [
    {"start": "2026-04-09T10:00:00+05:30", "end": "2026-04-09T11:00:00+05:30"},
    {"start": "2026-04-09T14:00:00+05:30", "end": "2026-04-09T15:30:00+05:30"},
]


def _make_freebusy_service(busy_list):
    mock_result = {"calendars": {"primary": {"busy": busy_list}}}
    mock_service = MagicMock()
    mock_service.freebusy().query().execute.return_value = mock_result
    return mock_service


class TestQueryFreebusy:
    @pytest.mark.asyncio
    async def test_returns_busy_intervals(self):
        with patch("tools.calendar._build_service", return_value=_make_freebusy_service(MOCK_BUSY)):
            result = await query_freebusy(
                "2026-04-09T08:00:00+05:30",
                "2026-04-09T18:00:00+05:30",
                "Asia/Kolkata",
            )
        assert len(result) == 2
        assert result[0]["start"] == MOCK_BUSY[0]["start"]
        assert result[1]["end"] == MOCK_BUSY[1]["end"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_busy(self):
        with patch("tools.calendar._build_service", return_value=_make_freebusy_service([])):
            result = await query_freebusy(
                "2026-04-09T08:00:00+05:30",
                "2026-04-09T18:00:00+05:30",
                "Asia/Kolkata",
            )
        assert result == []


# ---------------------------------------------------------------------------
# get_event_by_title — mocked Google API
# ---------------------------------------------------------------------------

MOCK_EVENT = {
    "id": "abc123",
    "summary": "Project Alpha",
    "start": {"dateTime": "2026-04-09T10:00:00+05:30"},
    "end":   {"dateTime": "2026-04-09T11:00:00+05:30"},
    "location": "Conference Room B",
}


def _make_events_service(items):
    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {"items": items}
    return mock_service


class TestGetEventByTitle:
    @pytest.mark.asyncio
    async def test_returns_event_when_found(self):
        with patch("tools.calendar._build_service", return_value=_make_events_service([MOCK_EVENT])):
            result = await get_event_by_title("Project Alpha")
        assert result is not None
        assert result["title"] == "Project Alpha"
        assert result["start_iso"] == "2026-04-09T10:00:00+05:30"
        assert result["end_iso"] == "2026-04-09T11:00:00+05:30"
        assert result["location"] == "Conference Room B"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        with patch("tools.calendar._build_service", return_value=_make_events_service([])):
            result = await get_event_by_title("Nonexistent Meeting")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_first_match_only(self):
        second_event = {**MOCK_EVENT, "summary": "Project Alpha Review"}
        with patch("tools.calendar._build_service", return_value=_make_events_service([MOCK_EVENT, second_event])):
            result = await get_event_by_title("Project Alpha")
        assert result["title"] == "Project Alpha"


# ---------------------------------------------------------------------------
# create_event — mocked Google API
# ---------------------------------------------------------------------------

MOCK_CREATED = {
    "id": "evt456",
    "summary": "Team Sync",
    "start": {"dateTime": "2026-04-09T10:30:00+05:30"},
    "end":   {"dateTime": "2026-04-09T11:00:00+05:30"},
    "htmlLink": "https://calendar.google.com/event?eid=abc",
}


def _make_insert_service(created_event):
    mock_service = MagicMock()
    mock_service.events().insert().execute.return_value = created_event
    return mock_service


class TestCreateEvent:
    @pytest.mark.asyncio
    async def test_returns_created_event_fields(self):
        with patch("tools.calendar._build_service", return_value=_make_insert_service(MOCK_CREATED)):
            result = await create_event(
                title="Team Sync",
                start_iso="2026-04-09T10:30:00+05:30",
                end_iso="2026-04-09T11:00:00+05:30",
            )
        assert result["id"] == "evt456"
        assert result["title"] == "Team Sync"
        assert result["start_iso"] == "2026-04-09T10:30:00+05:30"
        assert result["html_link"] == "https://calendar.google.com/event?eid=abc"

    @pytest.mark.asyncio
    async def test_description_optional(self):
        with patch("tools.calendar._build_service", return_value=_make_insert_service(MOCK_CREATED)):
            result = await create_event(
                title="Team Sync",
                start_iso="2026-04-09T10:30:00+05:30",
                end_iso="2026-04-09T11:00:00+05:30",
                description="Quarterly planning",
            )
        assert result["id"] is not None
