"""
atom - A lightweight AI agent framework
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent.tools.context import RequestContext
    from .atom import (
        STREAM_EVENT_REASONING_COMPLETED,
        STREAM_EVENT_REASONING_DELTA,
        STREAM_EVENT_RUN_COMPLETED,
        STREAM_EVENT_RUN_FAILED,
        STREAM_EVENT_RUN_STARTED,
        STREAM_EVENT_TEXT_COMPLETED,
        STREAM_EVENT_TEXT_DELTA,
        STREAM_EVENT_TOOL_COMPLETED,
        STREAM_EVENT_TOOL_FAILED,
        STREAM_EVENT_TOOL_STARTED,
        STREAM_EVENT_TYPES,
        Atom,
        RunResult,
        RunStream,
        SessionInfo,
        SessionSnapshot,
        StreamEvent,
        StreamEventType,
    )
    from .bus.runtime_events import SessionTurnPersisted
    from .runtime_context import RuntimeContextBlock, RuntimeContextProvider


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("atom")
    except PackageNotFoundError:
        # Source checkouts often import atom without installed dist-info.
        return _read_pyproject_version() or "0.3.0"


__version__ = _resolve_version()
# The terminal form of atom's mark: brackets around a nucleus. Plain text rather
# than an emoji, so it occupies one predictable cell in every terminal.
__logo__ = "[•]"

_LAZY_EXPORTS = {
    "Atom": ".atom",
    "RunStream": ".atom",
    "RunResult": ".atom",
    "RequestContext": ".agent.tools.context",
    "RuntimeContextBlock": ".runtime_context",
    "RuntimeContextProvider": ".runtime_context",
    "SessionInfo": ".atom",
    "SessionSnapshot": ".atom",
    "STREAM_EVENT_REASONING_COMPLETED": ".atom",
    "STREAM_EVENT_REASONING_DELTA": ".atom",
    "STREAM_EVENT_RUN_COMPLETED": ".atom",
    "STREAM_EVENT_RUN_FAILED": ".atom",
    "STREAM_EVENT_RUN_STARTED": ".atom",
    "STREAM_EVENT_TEXT_COMPLETED": ".atom",
    "STREAM_EVENT_TEXT_DELTA": ".atom",
    "STREAM_EVENT_TOOL_COMPLETED": ".atom",
    "STREAM_EVENT_TOOL_FAILED": ".atom",
    "STREAM_EVENT_TOOL_STARTED": ".atom",
    "STREAM_EVENT_TYPES": ".atom",
    "StreamEvent": ".atom",
    "StreamEventType": ".atom",
    "SessionTurnPersisted": ".bus.runtime_events",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    mod = import_module(module_path, __name__)
    val = getattr(mod, name)
    globals()[name] = val
    return val


__all__ = [
    "Atom",
    "RunResult",
    "RequestContext",
    "RuntimeContextBlock",
    "RuntimeContextProvider",
    "RunStream",
    "SessionInfo",
    "SessionSnapshot",
    "STREAM_EVENT_REASONING_COMPLETED",
    "STREAM_EVENT_REASONING_DELTA",
    "STREAM_EVENT_RUN_COMPLETED",
    "STREAM_EVENT_RUN_FAILED",
    "STREAM_EVENT_RUN_STARTED",
    "STREAM_EVENT_TEXT_COMPLETED",
    "STREAM_EVENT_TEXT_DELTA",
    "STREAM_EVENT_TOOL_COMPLETED",
    "STREAM_EVENT_TOOL_FAILED",
    "STREAM_EVENT_TOOL_STARTED",
    "STREAM_EVENT_TYPES",
    "StreamEvent",
    "StreamEventType",
    "SessionTurnPersisted",
]
