from typing import Optional

from langgraph.types import Command

from modules.conflict_resolution.graph import build_conflict_resolution_graph

# Module-level singleton — MemorySaver is stateful and must persist across calls.
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_conflict_resolution_graph()
    return _graph


async def run_conflict_resolution(
    situation_summary: str,
    duration_minutes: int,
    thread_id: str,
    resume_with: Optional[str] = None,
) -> dict:
    graph = _get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    if resume_with:
        # Resume from interrupt — conflict_attempts preserved in checkpoint, NOT reset.
        # resume_with is Gemini's updated situation_summary after user responds.
        result = await graph.ainvoke(Command(resume=resume_with), config=config)
    else:
        result = await graph.ainvoke(
            {
                "situation_summary": situation_summary,
                "duration_minutes": duration_minutes,
                "thread_id": thread_id,
                "conflict_attempts": 0,
                "tried_windows": [],
                "current_preferences": None,
                "current_failed_window": None,
                "current_search_window": None,
                "last_search_result": None,
                "current_attempt_succeeded": False,
                "suggested_slot": None,
                "escalation_needed": False,
                "message_to_speak": "",
                "natural_language_result": "",
                "status": "",
            },
            config=config,
        )

    # When the graph hits interrupt(), ainvoke returns immediately with
    # __interrupt__ set instead of running to END. The state fields like
    # "status" may not be populated yet, so we must check for this first.
    interrupts = result.get("__interrupt__")
    if interrupts:
        message = interrupts[0].value
        return {
            "status": "needs_user_input",
            "suggested_slot": result.get("suggested_slot"),
            "message_to_speak": message,
            "natural_language_result": message,
        }

    # Graph ran to END normally (escalation path)
    return {
        "status": result["status"],
        "suggested_slot": result.get("suggested_slot"),
        "message_to_speak": result["message_to_speak"],
        "natural_language_result": result["natural_language_result"],
    }
