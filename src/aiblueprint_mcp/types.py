"""Shared types for aiblueprint-mcp."""

from dataclasses import dataclass
from typing import Any


class CommandError(Exception):
    """Raised by backend operations for expected, user-facing failures.

    The op decorator converts these into ``CommandResult(ok=False, error=...)``
    so callers get a clean message instead of a traceback.
    """


@dataclass
class CommandResult:
    """Result from any backend operation."""

    ok: bool
    payload: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok}
        if self.payload is not None:
            d.update(self.payload)
        if self.error is not None:
            d["error"] = self.error
        return d
