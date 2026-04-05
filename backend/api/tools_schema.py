TOOLS = [
    {
        "name": "resolve_time_expression",
        "description": "M1 (TIME RESOLUTION MODULE). Delegate here when the user specifies complex, abstract, or contextual parameters (e.g. 'after my standup', 'our usual sync', 'deadline of Q1'). M1 executes introspection against user habits or events and computes exact scheduling boundaries natively.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_expression": { "type": "string", "description": "The original time expression from user" },
                "duration_hint": { "type": "integer", "description": "Meeting duration in minutes if known" },
                "additional_constraints": { "type": "string", "description": "Any extra constraints mentioned" },
                "thread_id": { "type": "string" }
            },
            "required": ["raw_expression", "thread_id"]
        }
    },
    {
        "name": "search_slots",
        "description": "M2 (SEARCH SLOTS MODULE). Evaluates the user's availability natively against Google Calendar. Call this immediately for trivial bounds ('next Tuesday 7 PM', 'tomorrow 5 PM'). If this modules returns search_succeeded=false, DO NOT retry searching yourself. Forward execution directly to M3.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_slot_description": { "type": "string", "description": "NL time description including day, date, time range, and duration. E.g. 'Thursday April 9th 2026, 10:30 AM to 6 PM, 30 minutes'" },
                "duration_minutes": { "type": "integer" },
                "thread_id": { "type": "string" }
            },
            "required": ["raw_slot_description", "duration_minutes", "thread_id"]
        }
    },
    {
        "name": "invoke_conflict_resolution",
        "description": "M3 (CONFLICT RESOLUTION MODULE). Call this exactly once if M2 informs you that the requested slot is fully booked. M3 takes over and automatically executes a Retry Ladder against the calendar to isolate the best alternative pivot before bothering the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "situation_summary": { "type": "string", "description": "Comprehensive NL summary: what the user wants, what time window failed, any constraints. E.g. 'User wants a 1-hour meeting Tuesday afternoon. Tuesday 1 PM–5 PM was fully booked.'" },
                "duration_minutes": { "type": "integer" },
                "thread_id": { "type": "string" }
            },
            "required": ["situation_summary", "duration_minutes", "thread_id"]
        }
    },
    {
        "name": "resume_conflict_resolution",
        "description": "Re-executes M3 securely if the user verbally rejects or pivots away from an alternative slot M3 just suggested. It resumes execution traversing down the Retry Ladder. USE THIS TOOL ONLY TO RESUME THE WORK STARTED BY M3 - invoke_conflict_resolution.",
        "parameters": {
            "type": "object",
            "properties": {
                "situation_summary": { "type": "string", "description": "Updated NL summary incorporating the user's explicit rejection/pivot. E.g. 'Full Tuesday failed. User explicitly rejected Wednesday suggestion and now wants to try Thursday morning instead.'" },
                "thread_id": { "type": "string" }
            },
            "required": ["situation_summary", "thread_id"]
        }
    },
    {
        "name": "create_calendar_event",
        "description": "CONFIRMATION COMPONENT. Fires the final calendar booking transaction against Google Calendar. Call this STRICTLY ONLY after the user explicitly accepts a specific date and time mapping.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": { "type": "string" },
                "start_iso": { "type": "string", "description": "ISO 8601 datetime" },
                "end_iso": { "type": "string" },
                "description": { "type": "string" }
            },
            "required": ["title", "start_iso", "end_iso"]
        }
    },
    {
        "name": "update_memory",
        "description": "PREFERENCE MEMORY MODULE. Call this implicitly following a successful call to create_calendar_event to capture systemic constraints, durations, or preferences the user established into persistent memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_summary": { "type": "string" },
                "booked_event": { "type": "object" }
            },
            "required": ["conversation_summary"]
        }
    }
]
