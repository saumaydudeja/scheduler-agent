from typing import Optional

from modules.time_resolution.graph import build_time_resolution_graph
from agent_types import TimeResolutionState


async def run_time_resolution(
    raw_expression: str,
    duration_hint: Optional[int],
    additional_constraints: str,
    user_preferences: dict,
) -> dict:
    graph = build_time_resolution_graph()
    initial_state: TimeResolutionState = {
        "raw_expression": raw_expression,
        "duration_hint": duration_hint,
        "additional_constraints": additional_constraints,
        "user_preferences": user_preferences,
        # initialize computed fields
        "expression_type": "",
        "referenced_event": None,
        "resolved_window": None,
        "natural_language_summary": "",
        "needs_clarification": None,
        "confidence": 0.0,
        "status": "",
    }
    result = await graph.ainvoke(initial_state)
    return {
        "status": result["status"],
        "natural_language_summary": result["natural_language_summary"],
        "resolved_window": result["resolved_window"],   # dispatcher strips this before sending to Gemini
        "needs_clarification": result["needs_clarification"],
        "confidence": result["confidence"],
    }
