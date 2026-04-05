from langgraph.graph import StateGraph, END

from agent_types import TimeResolutionState
from modules.time_resolution.nodes import (
    classify_expression,
    lookup_reference_event,
    load_from_memory,
    compute_window,
    validate_and_format,
)


def _classify_router(state: TimeResolutionState) -> str:
    t = state["expression_type"]
    if t in ("event_anchored", "deadline_anchored"):
        return "lookup_reference_event"
    elif t == "memory_dependent":
        return "load_from_memory"
    return "compute_window"


def _lookup_router(state: TimeResolutionState) -> str:
    # If event not found, needs_clarification is set — skip compute_window
    if state.get("needs_clarification"):
        return "validate_and_format"
    return "compute_window"


def build_time_resolution_graph():
    builder = StateGraph(TimeResolutionState)

    builder.add_node("classify_expression", classify_expression)
    builder.add_node("lookup_reference_event", lookup_reference_event)
    builder.add_node("load_from_memory", load_from_memory)
    builder.add_node("compute_window", compute_window)
    builder.add_node("validate_and_format", validate_and_format)

    builder.set_entry_point("classify_expression")

    builder.add_conditional_edges(
        "classify_expression",
        _classify_router,
        {
            "lookup_reference_event": "lookup_reference_event",
            "load_from_memory": "load_from_memory",
            "compute_window": "compute_window",
        },
    )
    builder.add_conditional_edges(
        "lookup_reference_event",
        _lookup_router,
        {
            "compute_window": "compute_window",
            "validate_and_format": "validate_and_format",
        },
    )
    builder.add_edge("load_from_memory", "compute_window")
    builder.add_edge("compute_window", "validate_and_format")
    builder.add_edge("validate_and_format", END)

    return builder.compile()
