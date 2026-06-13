"""AIBlueprint MCP Server — 6 consolidated tools for site-plan drafting.

Tools: drawing, entity, layer, block, annotation, view

Each tool validates its input against a per-operation schema (validation.py),
then dispatches to operation-specific backend methods.
"""

from __future__ import annotations

import json

import structlog
from mcp.server.fastmcp import FastMCP, Image

from aiblueprint_mcp.backend import AIBlueprintBackend
from aiblueprint_mcp.validation import ValidationError, validate

log = structlog.get_logger()
mcp = FastMCP("aiblueprint-mcp")

_backend: AIBlueprintBackend | None = None


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


# ═══════════════════════════════════════════════════════════════════════
# 1. drawing — File/session management
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def drawing(operation: str, data: dict | None = None) -> str:
    """Drawing file and session management.

    Operations:
      create — New empty drawing. data: {name?} → returns handle
      open   — Open existing DXF (within workspace). data: {path}
      info   — Get layers, entity count, blocks for current drawing.
      save   — Save to path (within workspace). data: {path?}
      list   — List all open drawings in the session.
      switch — Make another open drawing current. data: {handle}
    """
    data = data or {}
    b = await _get_backend()
    try:
        data = _check("drawing", operation, data)
    except ValidationError as ve:
        return _err(f"Invalid input for drawing.{operation}: {ve}")

    if operation == "create":
        r = await b.drawing_create(data.get("name"))
    elif operation == "open":
        r = await b.drawing_open(data["path"])
    elif operation == "info":
        r = await b.drawing_info()
    elif operation == "save":
        r = await b.drawing_save(data.get("path"))
    elif operation == "list":
        r = await b.drawing_list()
    elif operation == "switch":
        r = await b.drawing_switch(data["handle"])
    else:
        return _err(f"Unknown drawing operation: {operation}")
    return _result(r)


# ═══════════════════════════════════════════════════════════════════════
# 2. entity — Entity CRUD + modification
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def entity(
    operation: str,
    x1: float | None = None, y1: float | None = None,
    x2: float | None = None, y2: float | None = None,
    points: list[list[float]] | None = None,
    layer: str | None = None,
    entity_id: str | None = None,
    data: dict | None = None,
) -> str:
    """Entity creation, querying, and modification.

    Create: create_line, create_circle, create_polyline, create_rectangle,
            create_arc, create_text, create_mtext, create_hatch
    Read:   list, get, measure (area/perimeter/length takeoff)
    Modify: copy, move, rotate, scale, mirror, offset, array, fillet, erase

    Top-level x1/y1/x2/y2/points/layer/entity_id are merged into data; see the
    README for the per-operation fields.
    """
    raw = dict(data or {})
    # Fold top-level convenience params into the data dict when provided.
    for k, v in (("x1", x1), ("y1", y1), ("x2", x2), ("y2", y2),
                 ("points", points), ("layer", layer), ("entity_id", entity_id)):
        if v is not None and k not in raw:
            raw[k] = v
    b = await _get_backend()
    try:
        d = _check("entity", operation, raw)
    except ValidationError as ve:
        return _err(f"Invalid input for entity.{operation}: {ve}")

    ops = {
        "create_line": lambda: b.create_line(d["x1"], d["y1"], d["x2"], d["y2"], d.get("layer")),
        "create_circle": lambda: b.create_circle(d["cx"], d["cy"], d["radius"], d.get("layer")),
        "create_polyline": lambda: b.create_polyline(d["points"], d.get("closed", False), d.get("layer")),
        "create_rectangle": lambda: b.create_rectangle(d["x1"], d["y1"], d["x2"], d["y2"], d.get("layer")),
        "create_arc": lambda: b.create_arc(d["cx"], d["cy"], d["radius"], d["start_angle"], d["end_angle"], d.get("layer")),
        "create_text": lambda: b.create_text(d["x"], d["y"], d["text"], d.get("height", 2.5), d.get("rotation", 0.0), d.get("layer")),
        "create_mtext": lambda: b.create_mtext(d["x"], d["y"], d["width"], d["text"], d.get("height", 2.5), d.get("layer")),
        "create_hatch": lambda: b.create_hatch(d["entity_id"], d.get("pattern", "ANSI31"), d.get("scale", 1.0)),
        "list": lambda: b.entity_list(d.get("layer")),
        "get": lambda: b.entity_get(d["entity_id"]),
        "measure": lambda: b.entity_measure(d["entity_id"]),
        "copy": lambda: b.entity_copy(d["entity_id"], d["dx"], d["dy"]),
        "move": lambda: b.entity_move(d["entity_id"], d["dx"], d["dy"]),
        "rotate": lambda: b.entity_rotate(d["entity_id"], d["cx"], d["cy"], d["angle"]),
        "scale": lambda: b.entity_scale(d["entity_id"], d["cx"], d["cy"], d["factor"]),
        "mirror": lambda: b.entity_mirror(d["entity_id"], d["x1"], d["y1"], d["x2"], d["y2"]),
        "offset": lambda: b.entity_offset(d["entity_id"], d["distance"]),
        "array": lambda: b.entity_array(d["entity_id"], d["rows"], d["cols"], d["row_dist"], d["col_dist"]),
        "fillet": lambda: b.entity_fillet(d["id1"], d["id2"], d["radius"]),
        "erase": lambda: b.entity_erase(d["entity_id"]),
    }
    handler = ops.get(operation)
    if handler is None:
        return _err(f"Unknown entity operation: {operation}")
    return _result(await handler())


# ═══════════════════════════════════════════════════════════════════════
# 3. layer — Layer management
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def layer(operation: str, data: dict | None = None) -> str:
    """Layer creation and management.

    Operations:
      list            — List all layers.
      create          — data: {name, color?, linetype?, true_color?: [r,g,b]}
      set_current     — data: {name}
      set_properties  — data: {name, color?, linetype?, true_color?: [r,g,b]}
      freeze / thaw   — data: {name}
      lock / unlock   — data: {name}
    """
    data = data or {}
    b = await _get_backend()
    try:
        d = _check("layer", operation, data)
    except ValidationError as ve:
        return _err(f"Invalid input for layer.{operation}: {ve}")

    if operation == "list":
        r = await b.layer_list()
    elif operation == "create":
        r = await b.layer_create(d["name"], d.get("color", "white"),
                                 d.get("linetype", "CONTINUOUS"), d.get("true_color"))
    elif operation == "set_current":
        r = await b.layer_set_current(d["name"])
    elif operation == "set_properties":
        r = await b.layer_set_properties(d["name"], d.get("color"), d.get("linetype"), d.get("true_color"))
    elif operation == "freeze":
        r = await b.layer_freeze(d["name"])
    elif operation == "thaw":
        r = await b.layer_thaw(d["name"])
    elif operation == "lock":
        r = await b.layer_lock(d["name"])
    elif operation == "unlock":
        r = await b.layer_unlock(d["name"])
    else:
        return _err(f"Unknown layer operation: {operation}")
    return _result(r)


# ═══════════════════════════════════════════════════════════════════════
# 4. block — Block operations
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def block(operation: str, data: dict | None = None) -> str:
    """Block definition, insertion, and attribute management.

    Operations:
      list                    — List all block definitions.
      insert                  — data: {name, x, y, scale?, rotation?}
      insert_with_attributes  — data: {name, x, y, scale?, rotation?, attributes: {tag: value}}
      get_attributes          — data: {entity_id}
      update_attribute        — data: {entity_id, tag, value}
      define                  — data: {name, entities: [{type, ...}]}
    """
    data = data or {}
    b = await _get_backend()
    try:
        d = _check("block", operation, data)
    except ValidationError as ve:
        return _err(f"Invalid input for block.{operation}: {ve}")

    if operation == "list":
        r = await b.block_list()
    elif operation == "insert":
        r = await b.block_insert(d["name"], d["x"], d["y"], d.get("scale", 1.0), d.get("rotation", 0.0))
    elif operation == "insert_with_attributes":
        r = await b.block_insert_with_attributes(
            d["name"], d["x"], d["y"], d.get("scale", 1.0), d.get("rotation", 0.0), d.get("attributes"))
    elif operation == "get_attributes":
        r = await b.block_get_attributes(d["entity_id"])
    elif operation == "update_attribute":
        r = await b.block_update_attribute(d["entity_id"], d["tag"], d["value"])
    elif operation == "define":
        r = await b.block_define(d["name"], d.get("entities", []))
    else:
        return _err(f"Unknown block operation: {operation}")
    return _result(r)


# ═══════════════════════════════════════════════════════════════════════
# 5. annotation — Text, dimensions, leaders
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def annotation(operation: str, data: dict | None = None) -> str:
    """Annotation: text, dimensions, and leaders.

    Operations:
      create_text               — data: {x, y, text, height?, rotation?, layer?}
      create_dimension_aligned  — data: {x1, y1, x2, y2, offset, dim_overrides?}
      create_dimension_linear   — data: {x1, y1, x2, y2, dim_x, dim_y, dim_overrides?}
      create_dimension_angular  — data: {cx, cy, x1, y1, x2, y2, dim_overrides?}
      create_dimension_radius   — data: {cx, cy, radius, angle, dim_overrides?}
      create_leader             — data: {points: [[x,y],...], text}

    dim_overrides: {dimtxt, dimasz, dimlunit, dimclrd, dimclre, dimclrt, dimtxsty}
    """
    data = data or {}
    b = await _get_backend()
    try:
        d = _check("annotation", operation, data)
    except ValidationError as ve:
        return _err(f"Invalid input for annotation.{operation}: {ve}")

    if operation == "create_text":
        r = await b.create_text(d["x"], d["y"], d["text"], d.get("height", 2.5),
                                d.get("rotation", 0.0), d.get("layer"))
    elif operation == "create_dimension_aligned":
        r = await b.create_dimension_aligned(d["x1"], d["y1"], d["x2"], d["y2"], d["offset"], d.get("dim_overrides"))
    elif operation == "create_dimension_linear":
        r = await b.create_dimension_linear(d["x1"], d["y1"], d["x2"], d["y2"], d["dim_x"], d["dim_y"], d.get("dim_overrides"))
    elif operation == "create_dimension_angular":
        r = await b.create_dimension_angular(d["cx"], d["cy"], d["x1"], d["y1"], d["x2"], d["y2"], d.get("dim_overrides"))
    elif operation == "create_dimension_radius":
        r = await b.create_dimension_radius(d["cx"], d["cy"], d["radius"], d["angle"], d.get("dim_overrides"))
    elif operation == "create_leader":
        r = await b.create_leader(d["points"], d["text"])
    else:
        return _err(f"Unknown annotation operation: {operation}")
    return _result(r)


# ═══════════════════════════════════════════════════════════════════════
# 6. view — Preview, screenshot, export
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def view(operation: str, data: dict | None = None):
    """Preview, screenshot, and export.

    Operations:
      screenshot   — Render current drawing as a PNG image (matplotlib).
      preview      — Save DXF + render PNG via LibreCAD. Returns file paths.
      export       — Write PNG/PDF/SVG to the workspace. data: {format?, path?}
    """
    data = data or {}
    b = await _get_backend()

    if operation == "screenshot":
        r = await b.get_screenshot()
        if r.ok and r.payload:
            import base64
            return Image(data=base64.b64decode(r.payload["image_base64"]), format="png")
        return _err(r.error or "screenshot failed")
    elif operation == "preview":
        r = await b.preview()
    elif operation == "export":
        r = await b.export(data.get("format", "pdf"), data.get("path"))
    else:
        return _err(f"Unknown view operation: {operation}")
    return _result(r)


async def main():
    """Run the MCP server over stdio."""
    import logging

    # ezdxf logs verbose INFO records; keep stdio clean for the MCP protocol.
    logging.getLogger("ezdxf").setLevel(logging.WARNING)
    await mcp.run_stdio_async()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
