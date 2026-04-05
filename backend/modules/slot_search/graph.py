from langgraph.graph import StateGraph, END

from agent_types import SlotSearchState
from modules.slot_search.nodes import (
    normalize_input,
    query_freebusy,
    compute_free_slots,
    format_response,
)


def build_slot_search_graph():
    builder = StateGraph(SlotSearchState)

    builder.add_node("normalize_input", normalize_input)
    builder.add_node("query_freebusy", query_freebusy)
    builder.add_node("compute_free_slots", compute_free_slots)
    builder.add_node("format_response", format_response)

    builder.set_entry_point("normalize_input")
    builder.add_edge("normalize_input", "query_freebusy")
    builder.add_edge("query_freebusy", "compute_free_slots")
    builder.add_edge("compute_free_slots", "format_response")
    builder.add_edge("format_response", END)

    return builder.compile()
