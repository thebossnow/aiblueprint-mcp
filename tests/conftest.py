"""Shared fixtures: isolated workspace + fresh backend per test."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.backend import AIBlueprintBackend
from aiblueprint_mcp.config import Config


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AIBLUEPRINT_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
async def backend(workspace):
    b = AIBlueprintBackend(Config.from_env())
    await b.initialize()
    return b


@pytest.fixture(autouse=False)
async def server_env(workspace, backend):
    """Wire the server module's global singletons to our test backend."""
    import aiblueprint_mcp.server as srv
    original_backend = srv._backend
    original_project = srv._project
    srv._backend = backend
    srv._project = None  # fresh project per test
    yield srv
    srv._backend = original_backend
    srv._project = original_project
