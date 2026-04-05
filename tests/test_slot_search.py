"""
Tests for M2 SlotSearch (backend/modules/slot_search/).

All LLM calls and Google Calendar calls are mocked.
Tests verify node behaviour and the run_slot_search() public contract.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from modules.slot_search import run_slot_search
from modules.slot_search.nodes import (
    NormalizedWindow,
    normalize_input,
    compute_free_slots as compute_free_slots_node,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

STRUCTURED_WINDOW = {
    "date_start": "2026-04-09T10:30:00+05:30",
    "date_end": "2026-04-09T18:00:00+05:30",
    "preferred_start_hour": 10,
    "preferred_end_hour": 18,
}

MOCK_SLOTS = [
    {"start": "2026-04-09T10:30:00+05:30", "end": "2026-04-09T11:00:00+05:30", "display": "Thursday 10:30 AM"},
    {"start": "2026-04-09T11:00:00+05:30", "end": "2026-04-09T11:30:00+05:30", "display": "Thursday 11:00 AM"},
]

MOCK_BUSY = [
    {"start": "2026-04-09T11:30:00+05:30", "end": "2026-04-09T13:00:00+05:30"},
]


def _make_llm_mock(structured_output=None, text_output="I found slots at 10:30 AM and 11:00 AM."):
    """Returns a mock that behaves like ChatGoogleGenerativeAI."""
    mock_llm = MagicMock()

    if structured_output is not None:
        # with_structured_output path (normalize_input)
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(return_value=structured_output)
        mock_llm.with_structured_output.return_value = structured_llm
    else:
        # plain ainvoke path (format_response)
        response = MagicMock()
        response.content = text_output
        mock_llm.ainvoke = AsyncMock(return_value=response)

    return mock_llm


# ---------------------------------------------------------------------------
# normalize_input — unit tests
# ---------------------------------------------------------------------------

class TestNormalizeInput:
    @pytest.mark.asyncio
    async def test_structured_window_passthrough(self):
        """structured_window provided → normalized_window set directly, no LLM call."""
        state = {
            "structured_window": STRUCTURED_WINDOW,
            "raw_slot_description": None,
            "duration_minutes": 30,
        }
        with patch("modules.slot_search.nodes.ChatGoogleGenerativeAI") as mock_cls:
            result = await normalize_input(state)

        mock_cls.assert_not_called()
        assert result["normalized_window"] == STRUCTURED_WINDOW

    @pytest.mark.asyncio
    async def test_nl_description_calls_llm(self):
        """raw_slot_description provided → LLM called, normalized_window populated."""
        nl_window = NormalizedWindow(
            date_start="2026-04-09T10:30:00+05:30",
            date_end="2026-04-09T18:00:00+05:30",
            preferred_start_hour=10,
            preferred_end_hour=18,
        )
        state = {
            "structured_window": None,
            "raw_slot_description": "Thursday April 9th, 10:30 AM to 6 PM",
            "duration_minutes": 30,
        }
        with patch("modules.slot_search.nodes.ChatGoogleGenerativeAI", return_value=_make_llm_mock(structured_output=nl_window)):
            result = await normalize_input(state)

        assert result["normalized_window"]["date_start"] == "2026-04-09T10:30:00+05:30"
        assert result["normalized_window"]["preferred_start_hour"] == 10
        assert result["normalized_window"]["preferred_end_hour"] == 18


# ---------------------------------------------------------------------------
# run_slot_search — integration tests (full graph, mocked I/O)
# ---------------------------------------------------------------------------

class TestRunSlotSearch:
    @pytest.mark.asyncio
    async def test_happy_path_returns_slots(self):
        """Full graph with slots available → search_succeeded=True, slots and NL result populated."""
        with (
            patch("modules.slot_search.nodes.ChatGoogleGenerativeAI", return_value=_make_llm_mock(
                text_output="I found slots at 10:30 AM and 11:00 AM on Thursday."
            )),
            patch("modules.slot_search.nodes.calendar.query_freebusy", new=AsyncMock(return_value=MOCK_BUSY)),
            patch("modules.slot_search.nodes.calendar.compute_free_slots", new=AsyncMock(return_value=MOCK_SLOTS)),
        ):
            result = await run_slot_search(
                duration_minutes=30,
                structured_window=STRUCTURED_WINDOW,
            )

        assert result["search_succeeded"] is True
        assert len(result["available_slots"]) == 2
        assert result["natural_language_result"] != ""

    @pytest.mark.asyncio
    async def test_no_slots_found(self):
        """No free gaps → search_succeeded=False, natural_language_result still populated."""
        with (
            patch("modules.slot_search.nodes.ChatGoogleGenerativeAI", return_value=_make_llm_mock(
                text_output="Thursday is fully booked. Try a different day."
            )),
            patch("modules.slot_search.nodes.calendar.query_freebusy", new=AsyncMock(return_value=[])),
            patch("modules.slot_search.nodes.calendar.compute_free_slots", new=AsyncMock(return_value=[])),
        ):
            result = await run_slot_search(
                duration_minutes=30,
                structured_window=STRUCTURED_WINDOW,
            )

        assert result["search_succeeded"] is False
        assert result["available_slots"] == []
        assert result["natural_language_result"] != ""

    @pytest.mark.asyncio
    async def test_return_shape(self):
        """Result dict contains exactly the three expected keys."""
        with (
            patch("modules.slot_search.nodes.ChatGoogleGenerativeAI", return_value=_make_llm_mock(text_output="Found slots.")),
            patch("modules.slot_search.nodes.calendar.query_freebusy", new=AsyncMock(return_value=[])),
            patch("modules.slot_search.nodes.calendar.compute_free_slots", new=AsyncMock(return_value=MOCK_SLOTS)),
        ):
            result = await run_slot_search(duration_minutes=30, structured_window=STRUCTURED_WINDOW)

        assert set(result.keys()) == {"search_succeeded", "available_slots", "natural_language_result"}

    @pytest.mark.asyncio
    async def test_structured_window_skips_normalize_llm(self):
        """M3 call path: structured_window provided → LLM with_structured_output never called."""
        mock_llm_cls = MagicMock()
        # plain ainvoke for format_response
        resp = MagicMock()
        resp.content = "Slots found."
        mock_llm_cls.return_value.ainvoke = AsyncMock(return_value=resp)

        with (
            patch("modules.slot_search.nodes.ChatGoogleGenerativeAI", mock_llm_cls),
            patch("modules.slot_search.nodes.calendar.query_freebusy", new=AsyncMock(return_value=[])),
            patch("modules.slot_search.nodes.calendar.compute_free_slots", new=AsyncMock(return_value=MOCK_SLOTS)),
        ):
            await run_slot_search(duration_minutes=30, structured_window=STRUCTURED_WINDOW)

        # with_structured_output must NOT have been called (that's the NL parsing path)
        mock_llm_cls.return_value.with_structured_output.assert_not_called()

    @pytest.mark.asyncio
    async def test_nl_input_uses_llm_for_normalization(self):
        """Gemini call path: raw_slot_description triggers LLM normalization."""
        nl_window = NormalizedWindow(
            date_start="2026-04-09T10:30:00+05:30",
            date_end="2026-04-09T18:00:00+05:30",
            preferred_start_hour=10,
            preferred_end_hour=18,
        )
        mock_llm_cls = MagicMock()
        # with_structured_output chain (normalize_input)
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(return_value=nl_window)
        mock_llm_cls.return_value.with_structured_output.return_value = structured_llm
        # plain ainvoke (format_response — second instantiation)
        resp = MagicMock()
        resp.content = "Found slots."
        mock_llm_cls.return_value.ainvoke = AsyncMock(return_value=resp)

        with (
            patch("modules.slot_search.nodes.ChatGoogleGenerativeAI", mock_llm_cls),
            patch("modules.slot_search.nodes.calendar.query_freebusy", new=AsyncMock(return_value=[])),
            patch("modules.slot_search.nodes.calendar.compute_free_slots", new=AsyncMock(return_value=MOCK_SLOTS)),
        ):
            result = await run_slot_search(
                duration_minutes=30,
                raw_slot_description="Thursday April 9th, 10:30 AM to 6 PM",
            )

        structured_llm.ainvoke.assert_called_once()
        assert result["search_succeeded"] is True
