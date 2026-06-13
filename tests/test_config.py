"""Config resolution and path-safety tests (items 7, 8)."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.config import Config


def test_from_env_reads_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AIBLUEPRINT_WORKSPACE", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.workspace == tmp_path.resolve()


def test_resolve_relative_path_inside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AIBLUEPRINT_WORKSPACE", str(tmp_path))
    cfg = Config.from_env()
    resolved = cfg.resolve_path("plans/site.dxf")
    assert str(resolved).startswith(str(tmp_path.resolve()))


def test_resolve_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("AIBLUEPRINT_WORKSPACE", str(tmp_path))
    cfg = Config.from_env()
    with pytest.raises(ValueError, match="escapes the workspace"):
        cfg.resolve_path("../../etc/passwd")


def test_resolve_rejects_absolute_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("AIBLUEPRINT_WORKSPACE", str(tmp_path))
    cfg = Config.from_env()
    with pytest.raises(ValueError):
        cfg.resolve_path("/etc/passwd")
