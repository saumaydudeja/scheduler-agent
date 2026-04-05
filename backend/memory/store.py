import json
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

MEMORY_DIR = Path(__file__).parent / "data"

UPDATE_MEMORY_PROMPT = """Extract structured user preference facts from this conversation summary to update a persistent memory store.

Existing memory:
{existing_memory}

Conversation summary:
{conversation_summary}

Booked event:
{booked_event}

Return a JSON object merging the existing memory with any new facts you observe (preferred times, usual durations, frequently referenced events, recurring meeting patterns). Keep existing fields unless clearly contradicted. Return only valid JSON with no markdown or explanation."""

def _memory_path(user_id: str) -> Path:
    MEMORY_DIR.mkdir(exist_ok=True)
    return MEMORY_DIR / f"{user_id}.json"


def load_memory(user_id: str) -> dict:
    path = _memory_path(user_id)
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


async def update_memory(
    user_id: str,
    conversation_summary: str,
    booked_event: Optional[dict] = None,
) -> None:
    existing = load_memory(user_id)

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    prompt = UPDATE_MEMORY_PROMPT.format(
        existing_memory=json.dumps(existing, indent=2),
        conversation_summary=conversation_summary,
        booked_event=json.dumps(booked_event, indent=2) if booked_event else "None"
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    try:
        updated = json.loads(response.content)
    except (json.JSONDecodeError, AttributeError):
        updated = existing  # fall back to existing if LLM output is unparseable

    with open(_memory_path(user_id), "w") as f:
        json.dump(updated, f, indent=2)
