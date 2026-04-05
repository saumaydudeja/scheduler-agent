from typing import Optional, TypedDict


# ---------------------------------------------------------------------------
# M1: TimeResolution
# ---------------------------------------------------------------------------

class TimeResolutionState(TypedDict):
    # Inputs
    raw_expression: str               # "after my Project Alpha meeting next week"
    duration_hint: Optional[int]      # minutes, if known (may be None)
    additional_constraints: str       # "not too early" / "" if none
    user_preferences: dict            # from memory: usual durations, preferred times

    # Computed during execution
    expression_type: str              # "event_anchored" | "relative_date" | "memory_dependent"
                                      # | "complex_date" | "deadline_anchored"
    referenced_event: Optional[dict]  # {title, start_iso, end_iso, location} from calendar lookup

    # Outputs
    resolved_window: Optional[dict]   # {date_start, date_end, preferred_start_hour,
                                      #  preferred_end_hour, duration_minutes}
                                      # INTERNAL ONLY — dispatcher strips this before sending to Gemini
    natural_language_summary: str     # always populated; only field Gemini sees on success
    needs_clarification: Optional[str]  # spoken question — populated when status="needs_clarification"
    confidence: float                 # 0.0–1.0; low confidence triggers clarification
    status: str                       # "resolved" | "needs_clarification"


# ---------------------------------------------------------------------------
# M2: SlotSearch
# ---------------------------------------------------------------------------

class SlotSearchState(TypedDict):
    # Inputs — one of the first two will be populated
    raw_slot_description: Optional[str]   # from Gemini (always NL): "Thursday April 9th, 10:30 AM to 6 PM"
    structured_window: Optional[dict]     # from M3 internal Python calls only — never from Gemini
    duration_minutes: int

    # Computed
    normalized_window: Optional[dict]     # always set after normalize_input node
    busy_periods: list[dict]              # [{start: ISO, end: ISO}] from freebusy API

    # Outputs
    available_slots: list[dict]           # [{start: ISO, end: ISO, display: str}]
    natural_language_result: str          # "I found slots at 10:30 AM or 11:00 AM on Thursday."
    search_succeeded: bool


# ---------------------------------------------------------------------------
# M3: ConflictResolution
# ---------------------------------------------------------------------------

class ConflictState(TypedDict):
    # Inputs — always NL strings from Gemini
    situation_summary: str         # Comprehensive NL: what user wants, what failed, any pivots.
                                   # Updated on every resume to reflect latest user preference.
                                   # e.g. "User wants a 1-hour meeting Tuesday afternoon.
                                   #       Tuesday 1PM–5PM was fully booked."
    duration_minutes: int
    thread_id: str                 # required for interrupt/resume via MemorySaver

    # Derived from situation_summary on every determine_next_window call (LLM, temp=0)
    current_preferences: Optional[dict]    # {day_pref, time_pref} — reflects latest user ask
    current_failed_window: Optional[dict]  # {date, start_hour, end_hour} — most recent failed window

    # Execution state
    conflict_attempts: int         # monotonically increments; NEVER reset between resumes
                                   # >= 4 triggers email escalation
    tried_windows: list[dict]      # windows already searched this session — never re-tried
    current_search_window: Optional[dict]
    last_search_result: Optional[dict]

    # Outputs
    current_attempt_succeeded: bool
    suggested_slot: Optional[dict]  # slot found — Gemini books it if user accepts; M3 never books
    escalation_needed: bool
    message_to_speak: str           # always populated; what Live API speaks to user
    natural_language_result: str    # always populated; complete speakable sentence
    status: str                     # "needs_user_input" | "escalate"
                                    # NOTE: never "resolved" — booking is always Gemini's responsibility
