import functools
import time
import structlog

logger = structlog.get_logger()

def track_latency(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        elapsed = (time.perf_counter() - start) * 1000
        # Emits latency strictly without modifying the wrapped function's return state
        logger.info("latency", func=func.__name__, latency_ms=round(elapsed, 2))
        return result
    return wrapper
