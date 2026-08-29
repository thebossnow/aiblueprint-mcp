"""AIBlueprint MCP Server — 8 tools for site-plan drafting + jurisdiction compliance.

Tools: drawing, entity, layer, block, annotation, view, project, compliance

Each tool validates its input against a per-operation schema (validation.py),
then dispatches to operation-specific backend methods.
"""

from __future__ import annotations

import json
import sys

import structlog
from mcp.server.fastmcp import FastMCP, Image

from aiblueprint_mcp.backend import AIBlueprintBackend
from aiblueprint_mcp.compliance_engine import ComplianceEngine
from aiblueprint_mcp.project_state import ProjectSession
from aiblueprint_mcp.validation import ValidationError, validate

# structlog defaults to stdout, which would corrupt the MCP JSON-RPC stdio
# stream — every log record must go to stderr instead.
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

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
      undo   — Revert the current drawing to the previous checkpoint.
      redo   — Re-apply the most recently undone checkpoint.
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
    elif operation == "undo":
        r = await b.undo()
    elif operation == "redo":
        r = await b.redo()
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
            create_arc, create_text, create_mtext, create_hatch,
            import_boundary (irregular parcel from survey points or GeoJSON)
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
        "import_boundary": lambda: b.import_boundary(d.get("points"), d.get("geojson"), d.get("layer", "LOT-LINE")),
        "create_arc": lambda: b.create_arc(d["cx"], d["cy"], d["radius"], d["start_angle"], d["end_angle"], d.get("layer")),
        "create_text": lambda: b.create_text(d["x"], d["y"], d["text"], d.get("height", 2.5), d.get("rotation", 0.0), d.get("layer"), d.get("align")),
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
                                d.get("rotation", 0.0), d.get("layer"), d.get("align"))
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
      export       — Write PNG/PDF/SVG/GeoJSON to the workspace. data: {format?, path?}
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
        try:
            d = _check("view", operation, data)
        except ValidationError as ve:
            return _err(f"Invalid input for view.{operation}: {ve}")
        r = await b.export(d.get("format", "pdf"), d.get("path"))
    else:
        return _err(f"Unknown view operation: {operation}")
    return _result(r)


# ═══════════════════════════════════════════════════════════════════════
# 7. project — Jurisdiction-aware intake questionnaire
# ═══════════════════════════════════════════════════════════════════════

def _get_project() -> ProjectSession:
    global _project
    if _project is None:
        _project = ProjectSession()
    return _project


@mcp.tool()
async def project(operation: str, data: dict | None = None) -> str:
    """Project intake questionnaire and jurisdiction rule resolver.

    Before drawing, use this tool to collect project and location details.
    It resolves a layered rule set: CA state → county → city → HOA/CC&Rs,
    always taking the most restrictive value at each layer.

    Operations:
      start    — Start a fresh intake (resets any existing session).
      question — Get the next question to ask the user. Returns question
                 details: id, text, help, type, options, step, total_steps.
      answer   — Submit an answer. data: {question_id, value}
      profile  — Get the fully resolved project profile + requirements + citations.
                 Available at any point; complete after all questions are answered.
      status   — Show questionnaire progress (answered / total / percent).
      reset    — Clear the session and start over.
      counties          — List all CA counties in the database.
      cities            — List all CA cities in the database.
      generate_site_plan — Generate a code-compliant DXF site plan from the
                           resolved profile. Provide exactly one lot source:
                           {lot_width, lot_depth} for a rectangular lot, or
                           {lot_boundary: [[x,y],...]} / {lot_boundary_handle}
                           (from entity.import_boundary) for an irregular
                           (e.g. L-shaped, trapezoidal) lot. Plus optional
                           adu_width?, adu_depth?, adu_position?, draw_name?
                           Returns drawing handle + layout summary.
      generate_room_plan — Generate a dimensioned interior room floor plan
                           (kitchen, bath, laundry, office, generic) as DXF.
                           data: {room_width, room_depth, room_type?,
                           island_length?, island_depth?, island_orientation?,
                           ...} Returns drawing handle + layout summary.
      export_mezcal — Export the current drawing's boundary, setback
                      envelope, and building footprints — merged with the
                      resolved zoning requirements and a compliance report
                      when a project profile is available — as one JSON
                      bundle for the Pascal editor's Mezcal adapter plugin
                      (thebossnow/mezcal_adapter_plugin). data: {path?}
                      (defaults to "<drawing name>.mezcal.json" in the
                      workspace). Requires a closed boundary polyline —
                      draw one or use entity.import_boundary first.
    """
    data = data or {}
    p = _get_project()

    if operation == "start":
        p.reset()
        q = p.next_question()
        return _ok({
            "message": "Project intake started. Use project('question') to get the first question.",
            "first_question": _format_question(q) if q else None,
        })

    elif operation == "question":
        q = p.next_question()
        if q is None:
            return _ok({"complete": True, "message": "All questions answered. Use project('profile') to see requirements."})
        return _ok({"complete": False, **_format_question(q)})

    elif operation == "answer":
        qid = data.get("question_id")
        value = data.get("value")
        if not qid:
            return _err("answer requires data: {question_id, value}")
        next_q = p.answer(qid, value)
        prog = p.progress()
        if next_q is None:
            return _ok({
                "recorded": {qid: value},
                "progress": prog,
                "complete": True,
                "message": "All questions answered. Use project('profile') for your resolved requirements.",
            })
        return _ok({
            "recorded": {qid: value},
            "progress": prog,
            "complete": False,
            "next_question": _format_question(next_q),
        })

    elif operation == "profile":
        profile = p.resolved_profile()
        return _ok({"profile": profile})

    elif operation == "status":
        return _ok({"progress": p.progress(), "answers": p.all_answers()})

    elif operation == "reset":
        p.reset()
        return _ok({"message": "Project session cleared."})

    elif operation == "counties":
        from aiblueprint_mcp.jurisdiction import JurisdictionLoader
        loader = JurisdictionLoader("ca")
        return _ok({"counties": [c.title() for c in loader.known_counties()]})

    elif operation == "cities":
        from aiblueprint_mcp.jurisdiction import JurisdictionLoader
        loader = JurisdictionLoader("ca")
        return _ok({"cities": [c.title() for c in loader.known_cities()]})

    elif operation == "generate_site_plan":
        try:
            d = _check("project", "generate_site_plan", data)
        except ValidationError as ve:
            return _err(f"Invalid input for project.generate_site_plan: {ve}")
        from aiblueprint_mcp.plan_generator import SitePlanConfig, SitePlanGenerator
        b = await _get_backend()
        gen = SitePlanGenerator(b, p)
        r = await gen.generate(SitePlanConfig(**d))
        return _result(r)

    elif operation == "generate_room_plan":
        try:
            d = _check("project", "generate_room_plan", data)
        except ValidationError as ve:
            return _err(f"Invalid input for project.generate_room_plan: {ve}")
        from aiblueprint_mcp.room_plan_generator import RoomFloorPlanConfig, RoomFloorPlanGenerator
        b = await _get_backend()
        gen = RoomFloorPlanGenerator(b)
        r = await gen.generate(RoomFloorPlanConfig(**d))
        return _result(r)

    elif operation == "export_mezcal":
        from aiblueprint_mcp.mezcal_export import export_mezcal

        b = await _get_backend()
        r = await export_mezcal(b, p.resolved_profile(), data.get("path"))
        return _result(r)

    else:
        return _err(f"Unknown project operation: {operation}")


def _format_question(q) -> dict:
    return {
        "question_id": q.id,
        "question": q.text,
        "help": q.help,
        "type": q.type,
        "options": q.options,
        "unit": q.unit,
        "required": q.required,
        "step": q.step,
        "total_steps": q.total_steps,
    }


# ═══════════════════════════════════════════════════════════════════════
# 8. compliance — Check drawing geometry against project requirements
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def compliance(operation: str, data: dict | None = None) -> str:
    """Check drawing geometry against the resolved project requirements.

    Requires: a project profile (run project tool first) and an open drawing
    with entity handles for the property boundary and ADU footprint.

    Operations:
      check_area     — Verify ADU footprint sq ft against max_sqft.
                       data: {footprint_handle}
      check_setbacks — Draw the setback envelope and verify ADU is inside.
                       data: {boundary_handle, footprint_handle}
      check_coverage — Verify total lot coverage %.
                       data: {boundary_handle, structure_handles: [h1, h2, ...]}
      check_height   — Verify ridge height against max_height_ft.
                       data: {elevation_handle, ridge_y, grade_y}
      report         — Run all applicable checks and return a full report.
                       data: {boundary_handle?, footprint_handle?, structure_handles?}
      requirements   — Show the effective requirements for this project (no geometry needed).
    """
    data = data or {}
    p = _get_project()
    b = await _get_backend()

    if operation == "requirements":
        profile = p.resolved_profile()
        if not profile:
            return _err("No project profile yet. Complete the project intake first.")
        return _ok({
            "effective_requirements": profile.get("requirements", {}),
            "warnings": profile.get("warnings", []),
            "notes": profile.get("notes", []),
            "disclaimers": profile.get("disclaimers", []),
        })

    profile = p.resolved_profile()
    if not profile:
        return _err("No project profile. Run project('start') and answer questions first.")

    engine = ComplianceEngine(b, profile)

    if operation == "check_area":
        h = data.get("footprint_handle")
        if not h:
            return _err("check_area requires data: {footprint_handle}")
        result = await engine.check_area(h)
        return _ok(result.to_dict())

    elif operation == "check_setbacks":
        bh = data.get("boundary_handle")
        fh = data.get("footprint_handle")
        if not bh or not fh:
            return _err("check_setbacks requires data: {boundary_handle, footprint_handle}")
        result = await engine.check_setbacks(bh, fh)
        return _ok(result.to_dict())

    elif operation == "check_coverage":
        bh = data.get("boundary_handle")
        sh = data.get("structure_handles", [])
        if not bh:
            return _err("check_coverage requires data: {boundary_handle, structure_handles}")
        result = await engine.check_lot_coverage(bh, sh)
        return _ok(result.to_dict())

    elif operation == "check_height":
        eh = data.get("elevation_handle")
        ridge_y = data.get("ridge_y")
        grade_y = data.get("grade_y")
        if ridge_y is None or grade_y is None:
            return _err("check_height requires data: {elevation_handle?, ridge_y, grade_y}")
        result = await engine.check_height(eh, float(ridge_y), float(grade_y))
        return _ok(result.to_dict())

    elif operation == "report":
        report = await engine.full_report(
            property_boundary_handle=data.get("boundary_handle"),
            adu_footprint_handle=data.get("footprint_handle"),
            all_structure_handles=data.get("structure_handles"),
        )
        return _ok(report)

    else:
        return _err(f"Unknown compliance operation: {operation}")


async def main():
    """Run the MCP server over stdio."""
    import logging

    # ezdxf logs verbose INFO records; keep stdio clean for the MCP protocol.
    logging.getLogger("ezdxf").setLevel(logging.WARNING)
    await mcp.run_stdio_async()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
