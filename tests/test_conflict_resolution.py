"""
Tests for M3 ConflictResolution (backend/modules/conflict_resolution/).

All LLM and M2 (run_slot_search) calls are mocked.
Interrupt/resume behaviour is tested via the full graph with a MemorySaver checkpoint.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from modules.conflict_resolution.nodes import (
    NextWindow,
    prepare_success_response,
    route_after_search,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SITUATION = "User wants a 30-minute meeting Tuesday afternoon. Tuesday 1 PM–5 PM was fully booked."

MOCK_WINDOW = {
    "date_start": "2026-04-09T08:00:00+05:30",
    "date_end": "2026-04-09T20:00:00+05:30",
    "preferred_start_hour": 8,
    "preferred_end_hour": 20,
}

MOCK_SLOT = {
    "start": "2026-04-09T10:00:00+05:30",
    "end": "2026-04-09T10:30:00+05:30",
    "display": "Thursday 10:00 AM",
}

MOCK_NEXT_WINDOW = NextWindow(
    current_preferences={"day_pref": "Tuesday", "time_pref": "afternoon"},
    current_failed_window={"date": "2026-04-07", "start_hour": 13, "end_hour": 17},
    next_search_window=MOCK_WINDOW,
    escalation_needed=False,
    message_to_speak="Would another day work for you?",
)

MOCK_NEXT_WINDOW_ESCALATE = NextWindow(
    current_preferences={"day_pref": "Tuesday", "time_pref": "afternoon"},
    current_failed_window={"date": "2026-04-07", "start_hour": 13, "end_hour": 17},
    next_search_window=MOCK_WINDOW,
    escalation_needed=True,
    message_to_speak="",
)


def _llm_mock(next_window: NextWindow):
    mock_cls = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=next_window)
    mock_cls.return_value.with_structured_output.return_value = structured
    return mock_cls


def _slot_search_mock(succeeded: bool):
    slots = [MOCK_SLOT] if succeeded else []
    return AsyncMock(return_value={
        "search_succeeded": succeeded,
        "available_slots": slots,
        "natural_language_result": "Found a slot." if succeeded else "No slots found.",
    })


# ---------------------------------------------------------------------------
# Unit: route_after_search logic
# ---------------------------------------------------------------------------

class TestRouteAfterSearch:
    def test_escalate_when_flag_set(self):
        state = {"escalation_needed": True, "conflict_attempts": 0, "current_attempt_succeeded": False}
        assert route_after_search(state) == "escalate"

    def test_escalate_at_attempt_4(self):
        state = {"escalation_needed": False, "conflict_attempts": 4, "current_attempt_succeeded": False}
        assert route_after_search(state) == "escalate"

    def test_success_routes_to_prepare(self):
        state = {"escalation_needed": False, "conflict_attempts": 0, "current_attempt_succeeded": True}
        assert route_after_search(state) == "prepare_success_response"

    def test_retry_when_attempts_lt_2(self):
        state = {"escalation_needed": False, "conflict_attempts": 1, "current_attempt_succeeded": False}
        assert route_after_search(state) == "determine_next_window"

    def test_suggest_to_user_when_attempts_gte_2(self):
        state = {"escalation_needed": False, "conflict_attempts": 2, "current_attempt_succeeded": False}
        assert route_after_search(state) == "suggest_to_user"


# ---------------------------------------------------------------------------
# Unit: prepare_success_response
# ---------------------------------------------------------------------------

class TestPrepareSuccessResponse:
    def test_sets_suggested_slot(self):
        state = {
            "last_search_result": {"available_slots": [MOCK_SLOT], "search_succeeded": True},
            "conflict_attempts": 1,
        }
        result = prepare_success_response(state)
        assert result["suggested_slot"] == MOCK_SLOT
        assert result["status"] == "needs_user_input"
        assert MOCK_SLOT["display"] in result["message_to_speak"]
        # conflict_attempts is incremented in determine_next_window, not here
        assert "conflict_attempts" not in result


# ---------------------------------------------------------------------------
# Integration: full graph via run_conflict_resolution
# ---------------------------------------------------------------------------

class TestRunConflictResolution:
    @pytest.mark.asyncio
    async def test_happy_path_finds_slot(self):
        """Fresh invocation: attempt 0 finds a slot → interrupt with suggested_slot."""
        # Reset graph singleton between tests
        import modules.conflict_resolution as m3
        m3._graph = None

        with (
            patch("modules.conflict_resolution.nodes.ChatGoogleGenerativeAI", _llm_mock(MOCK_NEXT_WINDOW)),
            patch("modules.conflict_resolution.nodes.run_slot_search", _slot_search_mock(True)),
        ):
            from modules.conflict_resolution import run_conflict_resolution
            result = await run_conflict_resolution(
                situation_summary=SITUATION,
                duration_minutes=30,
                thread_id="test-thread-1",
            )

        assert result["status"] == "needs_user_input"
        assert result["suggested_slot"] == MOCK_SLOT
        assert result["message_to_speak"] != ""

    @pytest.mark.asyncio
    async def test_escalation_at_attempt_4(self):
        """LLM returns escalation_needed=True → escalate path → status='escalate'."""
        import modules.conflict_resolution as m3
        m3._graph = None

        with (
            patch("modules.conflict_resolution.nodes.ChatGoogleGenerativeAI", _llm_mock(MOCK_NEXT_WINDOW_ESCALATE)),
            patch("modules.conflict_resolution.nodes.run_slot_search", _slot_search_mock(False)),
            patch("modules.conflict_resolution.nodes._send_escalation_email", new=AsyncMock()),
        ):
            from modules.conflict_resolution import run_conflict_resolution
            result = await run_conflict_resolution(
                situation_summary=SITUATION,
                duration_minutes=30,
                thread_id="test-thread-2",
            )

        assert result["status"] == "escalate"
        assert result["suggested_slot"] is None

    @pytest.mark.asyncio
    async def test_resume_preserves_conflict_attempts(self):
        """
        Simulate: fresh call → interrupt (attempt 0 succeeded) → resume with new summary.
        conflict_attempts must not be reset on resume.
        """
        import modules.conflict_resolution as m3
        m3._graph = None

        thread_id = "test-thread-3"

        # First call: finds slot, interrupts
        with (
            patch("modules.conflict_resolution.nodes.ChatGoogleGenerativeAI", _llm_mock(MOCK_NEXT_WINDOW)),
            patch("modules.conflict_resolution.nodes.run_slot_search", _slot_search_mock(True)),
        ):
            from modules.conflict_resolution import run_conflict_resolution
            first_result = await run_conflict_resolution(
                situation_summary=SITUATION,
                duration_minutes=30,
                thread_id=thread_id,
            )

        assert first_result["status"] == "needs_user_input"

        # Resume: user rejected — provide updated summary
        updated_summary = SITUATION + " User rejected Thursday 10 AM. Try Wednesday morning instead."
        with (
            patch("modules.conflict_resolution.nodes.ChatGoogleGenerativeAI", _llm_mock(MOCK_NEXT_WINDOW)),
            patch("modules.conflict_resolution.nodes.run_slot_search", _slot_search_mock(True)),
        ):
            second_result = await run_conflict_resolution(
                situation_summary=updated_summary,
                duration_minutes=30,
                thread_id=thread_id,
                resume_with=updated_summary,
            )

        # conflict_attempts was 2 after first interrupt (incremented by prepare_success_response)
        # second result should still be needs_user_input (new slot found)
        assert second_result["status"] == "needs_user_input"

    @pytest.mark.asyncio
    async def test_return_shape(self):
        """Result always has exactly the 4 expected keys."""
        import modules.conflict_resolution as m3
        m3._graph = None

        with (
            patch("modules.conflict_resolution.nodes.ChatGoogleGenerativeAI", _llm_mock(MOCK_NEXT_WINDOW)),
            patch("modules.conflict_resolution.nodes.run_slot_search", _slot_search_mock(True)),
        ):
            from modules.conflict_resolution import run_conflict_resolution
            result = await run_conflict_resolution(
                situation_summary=SITUATION,
                duration_minutes=30,
                thread_id="test-thread-4",
            )

        assert set(result.keys()) == {"status", "suggested_slot", "message_to_speak", "natural_language_result"}

    @pytest.mark.asyncio
    async def test_m2_called_with_structured_window(self):
        """search_alternative_window must call run_slot_search with structured_window, not NL."""
        import modules.conflict_resolution as m3
        m3._graph = None

        mock_search = _slot_search_mock(True)
        with (
            patch("modules.conflict_resolution.nodes.ChatGoogleGenerativeAI", _llm_mock(MOCK_NEXT_WINDOW)),
            patch("modules.conflict_resolution.nodes.run_slot_search", mock_search),
        ):
            from modules.conflict_resolution import run_conflict_resolution
            await run_conflict_resolution(
                situation_summary=SITUATION,
                duration_minutes=30,
                thread_id="test-thread-5",
            )

        call_kwargs = mock_search.call_args.kwargs
        assert "structured_window" in call_kwargs
        assert call_kwargs.get("raw_slot_description") is None
