"""Runtime configuration for aiblueprint-mcp.

Configuration is resolved lazily (when a Config is constructed), not at import
time, so tests and embedding hosts can override environment variables before
the backend starts.

Environment variables:
  AIBLUEPRINT_LIBRECAD_BIN — path to the librecad executable
  AIBLUEPRINT_WORKSPACE    — working directory for file I/O and preview renders
  DISPLAY                  — X11 display for LibreCAD GUI rendering (default :0)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _find_librecad(env_value: str | None) -> Path:
    """Resolve the LibreCAD binary from an explicit path or common locations."""
    if env_value:
        return Path(env_value)
    candidates = [
        Path.home() / "workspace/LibreCAD/unix/librecad",
        Path("/usr/bin/librecad"),
        Path("/usr/local/bin/librecad"),
        Path.home() / "LibreCAD/unix/librecad",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # fallback — caller surfaces a clear error if missing


@dataclass
class Config:
    """Resolved runtime configuration."""

    librecad_bin: Path
    workspace: Path
    display: str

    @classmethod
    def from_env(cls) -> Config:
        """Build a Config from the current process environment."""
        workspace = Path(
            os.environ.get("AIBLUEPRINT_WORKSPACE", str(Path.home() / "workspace"))
        ).expanduser().resolve()
        return cls(
            librecad_bin=_find_librecad(os.environ.get("AIBLUEPRINT_LIBRECAD_BIN")),
            workspace=workspace,
            display=os.environ.get("DISPLAY", ":0"),
        )

    def ensure_workspace(self) -> Path:
        """Create the workspace directory if it does not exist."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace

    def resolve_path(self, path: str | os.PathLike[str]) -> Path:
        """Resolve a user-supplied path, confining it inside the workspace.

        Relative paths are taken relative to the workspace. Absolute paths and
        ``..`` traversal that escape the workspace are rejected to prevent
        arbitrary filesystem reads/writes.
        """
        self.ensure_workspace()
        p = Path(path).expanduser()
        candidate = (self.workspace / p).resolve() if not p.is_absolute() else p.resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(
                f"Path '{path}' escapes the workspace ({self.workspace}). "
                "Use a path inside the workspace or set AIBLUEPRINT_WORKSPACE."
            ) from exc
        return candidate
