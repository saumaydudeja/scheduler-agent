import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Per-session SSE queues: session_id -> asyncio.Queue
# Populated when a session connects; drained by the SSE endpoint.
sse_queues: dict[str, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    sse_queues.clear()


app = FastAPI(title="Smart Scheduler", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers — uncomment as each module is implemented
from api.proxy import router as proxy_router
from api.sse import router as sse_router
app.include_router(proxy_router)
app.include_router(sse_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
