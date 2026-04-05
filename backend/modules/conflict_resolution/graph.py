from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END

from agent_types import ConflictState
from modules.conflict_resolution.nodes import (
    determine_next_window,
    search_alternative_window,
    route_after_search,
    draft_success_message,
    draft_suggestion_message,
    suggest_to_user,
    escalate,
)


def build_conflict_resolution_graph():
    builder = StateGraph(ConflictState)

    builder.add_node("determine_next_window", determine_next_window)
    builder.add_node("search_alternative_window", search_alternative_window)
    builder.add_node("draft_success_message", draft_success_message)
    builder.add_node("draft_suggestion_message", draft_suggestion_message)
    builder.add_node("suggest_to_user", suggest_to_user)
    builder.add_node("escalate", escalate)

    builder.set_entry_point("determine_next_window")
    builder.add_edge("determine_next_window", "search_alternative_window")
    builder.add_conditional_edges(
        "search_alternative_window",
        route_after_search,
        {
            "escalate": "escalate",
            "draft_success_message": "draft_success_message",
            "draft_suggestion_message": "draft_suggestion_message",
            "determine_next_window": "determine_next_window",
        },
    )
    builder.add_edge("draft_success_message", "suggest_to_user")
    builder.add_edge("draft_suggestion_message", "suggest_to_user")
    # After interrupt resumes, suggest_to_user returns updated situation_summary
    # and routes back to determine_next_window to re-parse the user's new ask.
    builder.add_edge("suggest_to_user", "determine_next_window")
    builder.add_edge("escalate", END)

    checkpointer = InMemorySaver()
    return builder.compile(checkpointer=checkpointer)
