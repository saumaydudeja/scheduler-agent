from typing import Any

from api.sse import emit_status
from modules.time_resolution import run_time_resolution
from modules.slot_search import run_slot_search
from modules.conflict_resolution import run_conflict_resolution
from tools.calendar import create_event
from memory.store import load_memory, update_memory
import time
import structlog
import utils.trace as trace

logger = structlog.get_logger()

DEFAULT_USER_ID = "default_user_1"

async def execute_tool(name: str, args: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Routes a tool call from Gemini to the correct backend module."""
    log = logger.bind(session_id=thread_id)
    # Slim args for the banner — drop thread_id, it's noise
    banner_args = {k: v for k, v in args.items() if k != "thread_id"}
    trace.tool_start(name, banner_args)
    start = time.perf_counter()
    
    result = None
    
    if name == "resolve_time_expression":
        await emit_status(thread_id, "Understanding your request...", "time_resolution", "start")
        
        user_prefs = load_memory(DEFAULT_USER_ID)
        result = await run_time_resolution(
            raw_expression=args.get("raw_expression", ""),
            additional_constraints=args.get("additional_constraints", ""),
            user_preferences=user_prefs
        )
        
        # Strip structured data before returning to Gemini to enforce NL boundary
        if "resolved_window" in result:
            del result["resolved_window"]
            
        await emit_status(thread_id, "Finished analyzing time context.", "time_resolution", "end")

    elif name == "search_slots":
        await emit_status(thread_id, "Checking your calendar...", "slot_search", "normalize_input")
        
        result = await run_slot_search(
            duration_minutes=args.get("duration_minutes", 30),
            raw_slot_description=args.get("raw_slot_description", "")
        )
        
        # Aggressively strip large dense arrays from the tool response so Gemini doesn't freeze processing it.
        # We only really need to pass back the natural language summary and boolean status
        keys_to_remove = ["busy_periods", "available_slots", "normalized_window", "structured_window"]
        for k in keys_to_remove:
            if k in result:
                del result[k]
                
        await emit_status(thread_id, "Finished calendar search.", "slot_search", "end")

    elif name == "invoke_conflict_resolution":
        await emit_status(thread_id, "Looking for alternatives...", "conflict_resolution", "determine_next_window")
        
        result = await run_conflict_resolution(
            situation_summary=args.get("situation_summary", ""),
            thread_id=thread_id
        )
        
        if "tried_windows" in result:
            del result["tried_windows"]
        if "last_search_result" in result:
            del result["last_search_result"]
            
        if result.get("status") == "needs_user_input":
            await emit_status(thread_id, "Waiting for your preference...", "conflict_resolution", "suggest_to_user")

    elif name == "resume_conflict_resolution":
        await emit_status(thread_id, "Continuing search...", "conflict_resolution", "resume")
        
        # On resume, only thread_id and resume_with strictly matter since it loads from checkpoint
        result = await run_conflict_resolution(
            situation_summary="",
            thread_id=thread_id,
            resume_with=args.get("situation_summary", "")
        )
        
        if "tried_windows" in result:
            del result["tried_windows"]
        if "last_search_result" in result:
            del result["last_search_result"]
            
        if result.get("status") == "needs_user_input":
            await emit_status(thread_id, "Waiting for your preference...", "conflict_resolution", "suggest_to_user")

    elif name == "create_calendar_event":
        await emit_status(thread_id, "Booking your meeting...", "dispatcher", "create_calendar_event")
        
        result = await create_event(
            title=args.get("title", "Meeting"),
            start_iso=args.get("start_iso", ""),
            end_iso=args.get("end_iso", ""),
            description=args.get("description", "")
        )
        
        await emit_status(
            thread_id, 
            "Booking confirmed!", 
            "booking", 
            "done", 
            event=result, 
            calendar_link=result.get("html_link")
        )
        
        result = {"status": "success", "event_id": result.get("id"), "html_link": result.get("html_link")}

    elif name == "update_memory":
        await emit_status(thread_id, "Updating preferences...", "dispatcher", "update_memory")
        
        await update_memory(
            user_id=DEFAULT_USER_ID,
            conversation_summary=args.get("conversation_summary", ""),
            booked_event=args.get("booked_event")
        )
        
        result = {"status": "success"}

    else:
        result = {"error": f"Unknown tool: {name}"}

    elapsed = (time.perf_counter() - start) * 1000
    # Build a slim summary of the most important result keys for the tool_end banner
    summary_keys = ["status", "search_succeeded", "natural_language_result",
                    "natural_language_summary", "message_to_speak", "conflict_attempts",
                    "escalation_needed", "event_id"]
    summary = {k: result[k] for k in summary_keys if k in result}
    trace.tool_end(name, elapsed, summary)
    return result
