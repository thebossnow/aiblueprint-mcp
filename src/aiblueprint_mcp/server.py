"""AIBlueprint MCP Server — 8 tools for site-plan drafting + jurisdiction compliance.

Tools: drawing, entity, layer, block, annotation, view, project, compliance

Each tool validates its input against a per-operation schema (validation.py),
then dispatches to operation-specific backend methods.
"""

from __future__ import annotations

import json

import structlog
from mcp.server.fastmcp import FastMCP, Image

from aiblueprint_mcp.backend import AIBlueprintBackend
from aiblueprint_mcp.compliance_engine import ComplianceEngine
from aiblueprint_mcp.project_state import ProjectSession
from aiblueprint_mcp.validation import ValidationError, validate

log = structlog.get_logger()
mcp = FastMCP("aiblueprint-mcp")

_backend: AIBlueprintBackend | None = None
_project: ProjectSession | None = None


async def _get_backend() -> AIBlueprintBackend:
    """Lazy-initialize the backend singleton."""
    global _backend
    if _backend is None:
        _backend = AIBlueprintBackend()
        result = await _backend.initialize()
        if not result.ok:
            raise RuntimeError(f"Backend init failed: {result.error}")
        log.info("backend_initialized", backend=_backend.name)
    return _backend


def _ok(data: dict) -> str:
    return json.dumps({"ok": True, **data}, default=str)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


def _result(r) -> str:
    """Serialize a CommandResult to a JSON string."""
    return _ok(r.payload or {}) if r.ok else _err(r.error or "Unknown error")


def _check(tool: str, operation: str, data: dict) -> dict:
    """Validate input for ``tool.operation``; raises ValidationError on failure."""
    return validate(f"{tool}.{operation}", data)
