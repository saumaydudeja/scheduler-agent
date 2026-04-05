from typing import Optional

from modules.slot_search.graph import build_slot_search_graph
from agent_types import SlotSearchState


async def run_slot_search(
    duration_minutes: int,
    raw_slot_description: Optional[str] = None,
    structured_window: Optional[dict] = None,
) -> dict:
    graph = build_slot_search_graph()
    initial_state: SlotSearchState = {
        "raw_slot_description": raw_slot_description,
        "structured_window": structured_window,
        "duration_minutes": duration_minutes,
        "normalized_window": None,
        "busy_periods": [],
        "available_slots": [],
        "natural_language_result": "",
        "search_succeeded": False,
    }
    result = await graph.ainvoke(initial_state)
    return {
        "search_succeeded": result["search_succeeded"],
        "available_slots": result["available_slots"],
        "natural_language_result": result["natural_language_result"],
    }
