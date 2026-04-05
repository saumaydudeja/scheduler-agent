import asyncio
import json
import time
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

async def emit_status(
    session_id: str, 
    message: str, 
    module: str, 
    node: Optional[str] = None, 
    **kwargs
):
    """
    Emit a status message to the frontend via SSE.
    """
    # Import locally to avoid circular dependency with main.py
    from main import sse_queues
    
    if session_id in sse_queues:
        data = {
            "type": "status",
            "message": message,
            "module": module,
            "node": node,
            "timestamp": time.time()
        }
        data.update(kwargs)
        await sse_queues[session_id].put(data)

async def sse_generator(session_id: str):
    from main import sse_queues
    
    if session_id not in sse_queues:
        sse_queues[session_id] = asyncio.Queue()
        
    queue = sse_queues[session_id]
    
    try:
        while True:
            data = await queue.get()
            yield f"data: {json.dumps(data)}\n\n"
    except asyncio.CancelledError:
        # Expected on client disconnect
        pass

@router.get("/stream/status/{session_id}")
async def stream_status(session_id: str):
    """
    SSE endpoint for streaming status events for a session.
    """
    return StreamingResponse(sse_generator(session_id), media_type="text/event-stream")
