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
