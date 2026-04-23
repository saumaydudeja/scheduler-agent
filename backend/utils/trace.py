"""
utils/trace.py — Non-blocking human-readable reasoning tracer.

All public API functions (tool_start, tool_end, node_enter, node_exit, router_decision)
are fire-and-forget: they enqueue a raw data tuple via put_nowait() which takes < 1µs
on the calling node's hot path.

All string formatting, ANSI coloring, and print() calls happen exclusively in the
background _drain() task, which runs during event loop idle time between I/O awaits.

Usage:
    # In main.py lifespan:
    import utils.trace as trace
    trace.start_trace_writer()

    # In any node:
    trace.node_enter("normalize_input", inputs={"raw": state["raw_slot_description"]})
    ...
    trace.node_exit("normalize_input", delta={"normalized_window": result})
"""
import asyncio
import sys
from typing import Any

# ── ANSI color codes ────────────────────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[96m"
_YELLOW = "\033[93m"
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_GREY   = "\033[90m"
_MAGENTA = "\033[95m"
_BLUE   = "\033[94m"

_queue: asyncio.Queue = asyncio.Queue()
_writer_task = None


def start_trace_writer() -> None:
    """Launch the background drain task. Call once on app startup."""
    global _writer_task
    loop = asyncio.get_event_loop()
    _writer_task = loop.create_task(_drain())


async def _drain() -> None:
    while True:
        event_type, data = await _queue.get()
        try:
            _render(event_type, data)
        except Exception:
            pass  # never crash the drain task
        _queue.task_done()


def _truncate(s: str, max_len: int =300) -> str:
    s = str(s)
    return s if len(s) <= max_len else s[:max_len] + "…"


def _fmt_dict(d: dict, indent: int = 11) -> str:
    """Format a dict as key: value lines with consistent indent."""
    pad = " " * indent
    lines = []
    for k, v in d.items():
        lines.append(f"{pad}{_GREY}{k}{_RESET}: {_truncate(str(v))}")
    return "\n".join(lines)


def _render(event_type: str, data: dict) -> None:
    if event_type == "tool_start":
        name = data["name"]
        args = data.get("args", {})
        print(f"\n{_CYAN}{'═' * 56}{_RESET}")
        print(f"  {_BOLD}{_CYAN}TOOL CALLED: {name}{_RESET}")
        for k, v in args.items():
            print(f"  {_GREY}↳ {k}:{_RESET} {_truncate(str(v))}")
        print(f"{_CYAN}{'═' * 56}{_RESET}")

    elif event_type == "tool_end":
        name = data["name"]
        ms   = data.get("ms", 0)
        summ = data.get("summary", {})
        print(f"{_BLUE}{'─' * 56}{_RESET}")
        print(f"  {_BOLD}TOOL COMPLETE: {name}{_RESET}  [{ms:,.0f}ms]")
        for k, v in summ.items():
            print(f"  {_GREY}{k}:{_RESET} {_truncate(str(v))}")
        print(f"{_BLUE}{'─' * 56}{_RESET}\n")

    elif event_type == "node_enter":
        node   = data["node"]
        inputs = data.get("inputs", {})
        extra  = data.get("extra", "")
        header = f"** NODE: {node}"
        if extra:
            header += f"  [{extra}]"
        print(f"\n  {_BOLD}{_YELLOW}{header}{_RESET} **")
        if inputs:
            print(f"  {_GREY}IN{_RESET}")
            for k, v in inputs.items():
                print(f"       {_GREY}{k}:{_RESET} {_truncate(str(v))}")

    elif event_type == "node_exit":
        node  = data["node"]
        delta = data.get("delta", {})
        outputs = data.get("outputs", {})
        if outputs:
            print(f"  {_GREY}OUT{_RESET}")
            for k, v in outputs.items():
                print(f"       {_GREEN}{k}:{_RESET} {_truncate(str(v))}")
        if delta:
            parts = []
            for k, old_new in delta.items():
                if isinstance(old_new, tuple) and len(old_new) == 2:
                    parts.append(f"{_GREY}{k}:{_RESET} {old_new[0]} {_GREY}→{_RESET} {_GREEN}{old_new[1]}{_RESET}")
                else:
                    parts.append(f"{_GREY}{k}:{_RESET} {_GREEN}{old_new}{_RESET}")
            print(f"  {_MAGENTA}Δ{_RESET}    " + "   ".join(parts))

    elif event_type == "router":
        name = data["name"]
        edge = data["edge"]
        keys = data.get("keys", {})
        key_str = "  ".join(f"{_GREY}{k}={_RESET}{v}" for k, v in keys.items())
        print(f"\n  {_BOLD}» ROUTER:{_RESET} {name} → {_GREEN}{edge}{_RESET}   {key_str}")

    sys.stdout.flush()


# ── Public hot-path API (all non-blocking) ──────────────────────────────────

def tool_start(name: str, args: dict) -> None:
    """Banner printed when a tool call begins. Call from dispatcher."""
    _queue.put_nowait(("tool_start", {"name": name, "args": args}))


def tool_end(name: str, ms: float, summary: dict) -> None:
    """Summary printed when a tool call completes. Call from dispatcher."""
    _queue.put_nowait(("tool_end", {"name": name, "ms": ms, "summary": summary}))


def node_enter(node: str, inputs: dict = None, extra: str = "") -> None:
    """Header + inputs printed at the top of a node. Call before LLM invoke."""
    _queue.put_nowait(("node_enter", {"node": node, "inputs": inputs or {}, "extra": extra}))


def node_exit(node: str, outputs: dict = None, delta: dict = None) -> None:
    """Outputs + state delta printed at the bottom of a node. Call before return."""
    _queue.put_nowait(("node_exit", {"node": node, "outputs": outputs or {}, "delta": delta or {}}))


def router_decision(name: str, edge: str, keys: dict = None) -> None:
    """Prints which edge the router chose and why. Call inside conditional edge fns."""
    _queue.put_nowait(("router", {"name": name, "edge": edge, "keys": keys or {}}))
