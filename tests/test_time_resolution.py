"""
Tests for M1 TimeResolution (backend/modules/time_resolution/).

All LLM, calendar, and memory calls are mocked.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from modules.time_resolution import run_time_resolution
from modules.time_resolution.nodes import (
    ExpressionClassification,
    ComputedWindow,
    lookup_reference_event,
    load_from_memory,
)


# ---------------------------------------------------------------------------
# Fixtures / shared data
# ---------------------------------------------------------------------------

BASE_INPUTS = {
    "raw_expression": "after my Project Alpha meeting next week",
    "duration_hint": 30,
    "additional_constraints": "",
    "user_preferences": {},
}

MOCK_EVENT = {
    "title": "Project Alpha",
    "start_iso": "2026-04-09T10:00:00+05:30",
    "end_iso": "2026-04-09T11:00:00+05:30",
    "location": "Conference Room B",
}

MOCK_WINDOW = ComputedWindow(
    date_start="2026-04-09T10:30:00+05:30",
    date_end="2026-04-09T18:00:00+05:30",
    preferred_start_hour=10,
    preferred_end_hour=18,
    duration_minutes=30,
    confidence=0.95,
    needs_clarification=None,
)

MOCK_CLASSIFICATION_EVENT = ExpressionClassification(
    expression_type="event_anchored",
    referenced_event_name="Project Alpha",
)

MOCK_CLASSIFICATION_MEMORY = ExpressionClassification(
    expression_type="memory_dependent",
    referenced_event_name=None,
)


def _llm_mock_for_classify(classification: ExpressionClassification):
    """Returns a ChatGoogleGenerativeAI mock that returns classification from with_structured_output."""
    mock_cls = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=classification)
    mock_cls.return_value.with_structured_output.return_value = structured
    return mock_cls


def _llm_mock_for_compute(window: ComputedWindow):
    """Returns a ChatGoogleGenerativeAI mock that returns window from with_structured_output."""
    mock_cls = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=window)
    mock_cls.return_value.with_structured_output.return_value = structured
    return mock_cls


def _llm_mock_classify_then_compute(
    classification: ExpressionClassification,
    window: ComputedWindow,
):
    """
    Supports two sequential ChatGoogleGenerativeAI instantiations:
    first for classify_expression, second for compute_window.
    """
    structured_classify = MagicMock()
    structured_classify.ainvoke = AsyncMock(return_value=classification)

    structured_compute = MagicMock()
    structured_compute.ainvoke = AsyncMock(return_value=window)

    instance_classify = MagicMock()
    instance_classify.with_structured_output.return_value = structured_classify

    instance_compute = MagicMock()
    instance_compute.with_structured_output.return_value = structured_compute

    mock_cls = MagicMock(side_effect=[instance_classify, instance_compute])
    return mock_cls


# ---------------------------------------------------------------------------
# Unit: classify_expression routing
# ---------------------------------------------------------------------------

class TestClassifyExpressionRouting:
    @pytest.mark.asyncio
    async def test_event_anchored_sets_partial_referenced_event(self):
        """LLM returns event_anchored → referenced_event populated with name."""
        mock_cls = _llm_mock_for_classify(MOCK_CLASSIFICATION_EVENT)
        with patch("modules.time_resolution.nodes.ChatGoogleGenerativeAI", mock_cls):
            from modules.time_resolution.nodes import classify_expression
            result = await classify_expression({**BASE_INPUTS, "expression_type": "", "referenced_event": None})

        assert result["expression_type"] == "event_anchored"
        assert result["referenced_event"] == {"name": "Project Alpha"}

    @pytest.mark.asyncio
    async def test_memory_dependent_sets_no_referenced_event(self):
        """LLM returns memory_dependent → referenced_event is None."""
        mock_cls = _llm_mock_for_classify(MOCK_CLASSIFICATION_MEMORY)
        with patch("modules.time_resolution.nodes.ChatGoogleGenerativeAI", mock_cls):
            from modules.time_resolution.nodes import classify_expression
            result = await classify_expression({
                **BASE_INPUTS,
                "raw_expression": "our usual sync-up",
                "expression_type": "",
                "referenced_event": None,
            })

        assert result["expression_type"] == "memory_dependent"
        assert result["referenced_event"] is None


# ---------------------------------------------------------------------------
# Unit: lookup_reference_event
# ---------------------------------------------------------------------------

class TestLookupReferenceEvent:
    @pytest.mark.asyncio
    async def test_event_found_populates_referenced_event(self):
        state = {**BASE_INPUTS, "expression_type": "event_anchored", "referenced_event": {"name": "Project Alpha"}}
        with patch("modules.time_resolution.nodes.calendar.get_event_by_title", new=AsyncMock(return_value=MOCK_EVENT)):
            result = await lookup_reference_event(state)

        assert result["referenced_event"]["title"] == "Project Alpha"
        assert "needs_clarification" not in result or result.get("needs_clarification") is None

    @pytest.mark.asyncio
    async def test_event_not_found_sets_clarification(self):
        state = {**BASE_INPUTS, "expression_type": "event_anchored", "referenced_event": {"name": "Unknown Meeting"}}
        with patch("modules.time_resolution.nodes.calendar.get_event_by_title", new=AsyncMock(return_value=None)):
            result = await lookup_reference_event(state)

        assert result["status"] == "needs_clarification"
        assert result["needs_clarification"] is not None
        assert "Unknown Meeting" in result["needs_clarification"]


# ---------------------------------------------------------------------------
# Integration: run_time_resolution full graph
# ---------------------------------------------------------------------------

class TestRunTimeResolution:
    @pytest.mark.asyncio
    async def test_event_anchored_happy_path(self):
        """Event found → window computed → status resolved."""
        mock_cls = _llm_mock_classify_then_compute(MOCK_CLASSIFICATION_EVENT, MOCK_WINDOW)
        with (
            patch("modules.time_resolution.nodes.ChatGoogleGenerativeAI", mock_cls),
            patch("modules.time_resolution.nodes.calendar.get_event_by_title", new=AsyncMock(return_value=MOCK_EVENT)),
        ):
            result = await run_time_resolution(**BASE_INPUTS)

        assert result["status"] == "resolved"
        assert result["resolved_window"] is not None
        assert result["natural_language_summary"] != ""
        assert result["confidence"] > 0

    @pytest.mark.asyncio
    async def test_event_not_found_returns_clarification(self):
        """Event lookup fails → needs_clarification, no resolved_window."""
        mock_cls = _llm_mock_for_classify(MOCK_CLASSIFICATION_EVENT)
        with (
            patch("modules.time_resolution.nodes.ChatGoogleGenerativeAI", mock_cls),
            patch("modules.time_resolution.nodes.calendar.get_event_by_title", new=AsyncMock(return_value=None)),
        ):
            result = await run_time_resolution(**BASE_INPUTS)

        assert result["status"] == "needs_clarification"
        assert result["needs_clarification"] is not None

    @pytest.mark.asyncio
    async def test_memory_dependent_loads_memory(self):
        """memory_dependent → load_from_memory called → preferences merged."""
        mock_cls = _llm_mock_classify_then_compute(MOCK_CLASSIFICATION_MEMORY, MOCK_WINDOW)
        with (
            patch("modules.time_resolution.nodes.ChatGoogleGenerativeAI", mock_cls),
            patch("modules.time_resolution.nodes.memory.load_memory", return_value={"usual_duration": 30}),
        ):
            result = await run_time_resolution(
                raw_expression="our usual sync-up",
                duration_hint=None,
                additional_constraints="",
                user_preferences={},
            )

        assert result["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_return_shape(self):
        """Result always has all 5 expected keys."""
        mock_cls = _llm_mock_classify_then_compute(MOCK_CLASSIFICATION_EVENT, MOCK_WINDOW)
        with (
            patch("modules.time_resolution.nodes.ChatGoogleGenerativeAI", mock_cls),
            patch("modules.time_resolution.nodes.calendar.get_event_by_title", new=AsyncMock(return_value=MOCK_EVENT)),
        ):
            result = await run_time_resolution(**BASE_INPUTS)

        assert set(result.keys()) == {
            "status", "natural_language_summary", "resolved_window",
            "needs_clarification", "confidence",
        }

    @pytest.mark.asyncio
    async def test_low_confidence_sets_clarification(self):
        """compute_window returns low confidence → needs_clarification status."""
        low_conf_window = ComputedWindow(
            date_start="2026-04-09T10:30:00+05:30",
            date_end="2026-04-09T18:00:00+05:30",
            preferred_start_hour=10,
            preferred_end_hour=18,
            duration_minutes=30,
            confidence=0.4,
            needs_clarification="What day did you have in mind?",
        )
        mock_cls = _llm_mock_classify_then_compute(MOCK_CLASSIFICATION_EVENT, low_conf_window)
        with (
            patch("modules.time_resolution.nodes.ChatGoogleGenerativeAI", mock_cls),
            patch("modules.time_resolution.nodes.calendar.get_event_by_title", new=AsyncMock(return_value=MOCK_EVENT)),
        ):
            result = await run_time_resolution(**BASE_INPUTS)

        assert result["status"] == "needs_clarification"
        assert result["needs_clarification"] == "What day did you have in mind?"
