"""Server tool-dispatch tests — JSON contract, validation, native image."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import Image

from aiblueprint_mcp import server


@pytest.fixture(autouse=True)
def fresh_server_backend(workspace):
    # Force each test to build a new backend bound to the isolated workspace.
    server._backend = None
    yield
    server._backend = None


async def test_drawing_create_returns_handle():
    out = json.loads(await server.drawing("create", {"name": "site"}))
    assert out["ok"] and out["handle"].startswith("dwg_")


async def test_entity_create_line_via_toplevel_params():
    await server.drawing("create", {"name": "s"})
    out = json.loads(await server.entity("create_line", x1=0, y1=0, x2=5, y2=5))
    assert out["ok"] and out["entity_type"] == "LINE"


async def test_validation_error_is_friendly():
    await server.drawing("create", {"name": "s"})
    out = json.loads(await server.entity("create_circle", data={"cx": 0, "cy": 0, "radius": -1}))
    assert not out["ok"]
    assert "Invalid input for entity.create_circle" in out["error"]


async def test_screenshot_returns_image_object():
    # Regression for the original crash: view screenshot must now succeed and
    # return a displayable Image rather than raising on JSON serialization.
    await server.drawing("create", {"name": "s"})
    await server.entity("create_line", x1=0, y1=0, x2=10, y2=10)
    result = await server.view("screenshot")
    assert isinstance(result, Image)


async def test_unknown_operation():
    out = json.loads(await server.drawing("frobnicate"))
    assert not out["ok"] and "Unknown drawing operation" in out["error"]


async def test_export_via_view_tool(workspace):
    await server.drawing("create", {"name": "s"})
    await server.entity("create_rectangle", x1=0, y1=0, x2=4, y2=4)
    out = json.loads(await server.view("export", {"format": "svg"}))
    assert out["ok"] and out["format"] == "svg"
