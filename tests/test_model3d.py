"""Tests for view.to_3d / model3d export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiblueprint_mcp.ifc_export import FEET_TO_METERS
from aiblueprint_mcp.room_plan_generator import RoomFloorPlanConfig, RoomFloorPlanGenerator
from aiblueprint_mcp.validation import ValidationError, validate

DOOR_HEIGHT = 6.0 + 8.0 / 12.0


async def _room_with_door(backend, width=10.0, depth=12.0, y0=2.0, y1=8.0, label="SLIDING DOOR"):
    await backend.drawing_create("room")
    await backend.create_rectangle(0, 0, width, depth, "WALL")
    await backend.create_line(-0.5, y0, 0.5, y0, "OPENING")
    await backend.create_line(-0.5, y1, 0.5, y1, "OPENING")
    if label:
        await backend.create_text(-3.5, (y0 + y1) / 2, label, height=1.0, layer="ANNOTATION")


def _load_ir(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def _slab_bbox(ir: dict) -> tuple[list[float], list[float]]:
    slabs = [p for p in ir["parts"] if p["kind"] == "slab"]
    assert slabs, "expected a floor slab"
    return slabs[0]["bbox"]["min"], slabs[0]["bbox"]["max"]


@pytest.mark.asyncio
async def test_room_with_ticks_cuts_door_and_keeps_interior(backend, workspace):
    await _room_with_door(backend)
    r = await backend.to_3d()
    assert r.ok, r.error
    assert r.payload["format"] == "gltf"
    assert r.payload["wall_count"] == 4
    assert r.payload["opening_count"] == 1
    gltf = Path(r.payload["path"])
    assert gltf.exists() and gltf.stat().st_size > 0
    assert Path(r.payload["files"]["gltf_bin"]).exists()
    assert Path(r.payload["files"]["blender_py"]).exists()
    ir = _load_ir(r.payload["ir_path"])
    assert ir["units"] == "feet"
    assert ir["defaults"]["wall_height_ft"] == 8.0
    assert ir["defaults"]["wall_thickness_ft"] == 0.5
    assert all(w["thickness_side"] == "outward" for w in ir["walls"])
    op = ir["openings"][0]
    assert op["kind"] == "door"
    assert op["source"] == "ticks"
    assert op["head"] == pytest.approx(DOOR_HEIGHT)
    # West wall is 12 ft; ticks at y=2 and y=8.
    west = next(w for w in ir["walls"] if min(w["start"][0], w["end"][0]) == 0 and max(w["start"][0], w["end"][0]) == 0)
    assert op["wall_id"] == west["id"]
    lo, hi = sorted([op["t0"], op["t1"]])
    # Parameter t runs along the wall edge; width along the 12 ft wall is 6 ft.
    assert (hi - lo) * 12 == pytest.approx(6.0, abs=0.05)

    mins, maxs = _slab_bbox(ir)
    assert maxs[0] - mins[0] == pytest.approx(10.0)
    assert maxs[1] - mins[1] == pytest.approx(12.0)

    # Thickness is outward: a wall bbox extends 0.5 ft outside the 10x12 room.
    wall_parts = [p for p in ir["parts"] if p["kind"] == "wall"]
    xs = [p["bbox"]["min"][0] for p in wall_parts] + [p["bbox"]["max"][0] for p in wall_parts]
    assert min(xs) == pytest.approx(-0.5)
    assert max(xs) == pytest.approx(10.5)

    # A lintel sits above the door and no wall fills the opening itself.
    lintels = [p for p in ir["parts"] if p["kind"] == "lintel"]
    assert lintels
    assert lintels[0]["bbox"]["min"][2] == pytest.approx(DOOR_HEIGHT)


@pytest.mark.asyncio
async def test_window_ticks_leave_sill_and_head(backend, workspace):
    await backend.drawing_create("room")
    await backend.create_rectangle(0, 0, 10, 12, "WALL")
    await backend.create_line(3, 11.5, 3, 12.5, "OPENING")
    await backend.create_line(6, 11.5, 6, 12.5, "OPENING")
    await backend.create_text(4.5, 13.5, "WINDOW", height=1.0, layer="ANNOTATION")
    r = await backend.to_3d()
    assert r.ok, r.error
    ir = _load_ir(r.payload["ir_path"])
    windows = [o for o in ir["openings"] if o["kind"] == "window"]
    assert len(windows) == 1
    win = windows[0]
    assert win["sill"] == pytest.approx(3.0)
    assert win["head"] == pytest.approx(DOOR_HEIGHT)
    sills = [p for p in ir["parts"] if p["kind"] == "sill"]
    assert sills
    assert sills[0]["bbox"]["min"][2] == pytest.approx(0.0)
    assert sills[0]["bbox"]["max"][2] == pytest.approx(3.0)
    lintels = [p for p in ir["parts"] if p["kind"] == "lintel"]
    assert lintels[0]["bbox"]["min"][2] == pytest.approx(DOOR_HEIGHT)
    assert lintels[0]["bbox"]["max"][2] == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_explicit_openings_override_ticks(backend, workspace):
    await _room_with_door(backend, y0=2.0, y1=8.0)
    r = await backend.to_3d(
        openings=[{"kind": "window", "x1": 0, "y1": 4, "x2": 0, "y2": 7, "sill": 2.5, "head": 7.0}]
    )
    assert r.ok, r.error
    ir = _load_ir(r.payload["ir_path"])
    assert ir["openings"][0]["kind"] == "window"
    assert ir["openings"][0]["source"] == "explicit"
    assert ir["openings"][0]["sill"] == pytest.approx(2.5)
    assert ir["openings"][0]["head"] == pytest.approx(7.0)
    # The tick-inferred 6 ft door should have been dropped as an overlap.
    assert all(o["source"] == "explicit" for o in ir["openings"])


@pytest.mark.asyncio
async def test_title_block_and_dimensions_are_not_meshed(backend, workspace):
    await backend.drawing_create("room")
    await backend.create_rectangle(0, 0, 10, 12, "WALL")
    await backend.create_rectangle(-2, -8, 20, -2, "TITLE-BLOCK")
    await backend.create_text(0, -6, "TITLE", height=2.0, layer="TITLE-BLOCK")
    await backend.create_dimension_aligned(0, 0, 10, 0, -4)
    await backend.create_line(14, 10, 14, 14, "NORTH-ARROW")
    r = await backend.to_3d()
    assert r.ok, r.error
    ir = _load_ir(r.payload["ir_path"])
    assert len(ir["walls"]) == 4
    for w in ir["walls"]:
        assert w["layer"] == "WALL"
    # Title block is below y=-2; no part should live entirely down there
    # as a wall or casework extrusion of that rectangle.
    for part in ir["parts"]:
        if part["kind"] in {"wall", "slab", "casework"}:
            assert part["bbox"]["max"][1] > -1.0 or part["kind"] == "wall"


@pytest.mark.asyncio
async def test_casework_toggle(backend, workspace):
    await backend.drawing_create("room")
    await backend.create_rectangle(0, 0, 10, 12, "WALL")
    await backend.create_rectangle(1, 10, 6, 12, "CABINET")
    await backend.create_rectangle(3, 4, 6, 7, "ISLAND")
    on = await backend.to_3d(path="with_case.gltf", formats=["gltf"], include_casework=True)
    assert on.ok, on.error
    ir_on = _load_ir(on.payload["ir_path"])
    assert ir_on["casework"]
    assert any(p["kind"] == "casework" for p in ir_on["parts"])
    off = await backend.to_3d(path="no_case.gltf", formats=["gltf"], include_casework=False)
    assert off.ok, off.error
    ir_off = _load_ir(off.payload["ir_path"])
    assert ir_off["casework"] == []
    assert all(p["kind"] != "casework" for p in ir_off["parts"])


@pytest.mark.asyncio
async def test_no_wall_geometry_errors(backend, workspace):
    await backend.drawing_create("empty")
    await backend.create_text(0, 0, "NO WALLS", height=2.0, layer="ANNOTATION")
    r = await backend.to_3d()
    assert not r.ok
    assert "No wall geometry" in r.error


@pytest.mark.asyncio
async def test_path_escape_is_rejected(backend, workspace):
    await backend.drawing_create("room")
    await backend.create_rectangle(0, 0, 10, 12, "WALL")
    r = await backend.to_3d(path="../escape.gltf")
    assert not r.ok
    assert "workspace" in r.error.lower() or "escape" in r.error.lower()


@pytest.mark.asyncio
async def test_gltf_is_valid_and_in_metres(backend, workspace):
    await _room_with_door(backend)
    r = await backend.to_3d(formats=["gltf"])
    assert r.ok, r.error
    doc = json.loads(Path(r.payload["path"]).read_text())
    assert doc["asset"]["version"] == "2.0"
    assert doc["nodes"]
    assert Path(r.payload["files"]["gltf_bin"]).stat().st_size == doc["buffers"][0]["byteLength"]
    # Y-up: wall height 8 ft is the max Y accessor bound.
    y_max = max(acc["max"][1] for acc in doc["accessors"] if acc.get("type") == "VEC3" and "max" in acc)
    assert y_max == pytest.approx(8.0 * FEET_TO_METERS, abs=1e-6)
    # X span includes outward thickness: 10.5 ft.
    x_ext = []
    for acc in doc["accessors"]:
        if acc.get("type") == "VEC3" and "min" in acc:
            x_ext.append((acc["min"][0], acc["max"][0]))
    x_min = min(a[0] for a in x_ext)
    x_max = max(a[1] for a in x_ext)
    assert x_max - x_min == pytest.approx(11.0 * FEET_TO_METERS, abs=1e-5)


@pytest.mark.asyncio
async def test_architectural_strings_via_validate_and_to_3d(backend, workspace):
    out = validate("view.to_3d", {"wall_height": "8'", "door_height": "6'-8\"", "wall_thickness": "6\""})
    assert out["wall_height"] == pytest.approx(8.0)
    assert out["door_height"] == pytest.approx(DOOR_HEIGHT)
    assert out["wall_thickness"] == pytest.approx(0.5)
    await _room_with_door(backend)
    r = await backend.to_3d(wall_height=out["wall_height"], door_height=out["door_height"])
    assert r.ok, r.error
    ir = _load_ir(r.payload["ir_path"])
    assert ir["defaults"]["wall_height_ft"] == pytest.approx(8.0)
    assert ir["defaults"]["door_height_ft"] == pytest.approx(DOOR_HEIGHT)


@pytest.mark.asyncio
async def test_room_plan_generator_export_warns_but_succeeds(backend, workspace):
    cfg = RoomFloorPlanConfig(
        room_width=13.4,
        room_depth=10.75,
        room_type="kitchen",
        island_length=4.5,
        island_depth=2.5,
        island_orientation="NS",
        title="EXISTING KITCHEN FLOOR PLAN",
        address="123 Example St, Anytown, CA 92000",
    )
    result = await RoomFloorPlanGenerator(backend).generate(cfg)
    assert result.ok, result.error
    r = await backend.to_3d(path="kitchen.gltf")
    assert r.ok, r.error
    assert r.payload["wall_count"] >= 4
    assert r.payload["opening_count"] >= 1
    assert r.payload["warnings"]  # inferred openings or text-only window
    ir = _load_ir(r.payload["ir_path"])
    kinds = {o["kind"] for o in ir["openings"]}
    assert "door" in kinds
    # The generator labels a window only as text; inference should create one.
    assert "window" in kinds or any("window" in w.lower() for w in r.payload["warnings"])
    assert r.payload["casework_count"] >= 1


@pytest.mark.asyncio
async def test_export_gltf_alias(backend, workspace):
    await backend.drawing_create("room")
    await backend.create_rectangle(0, 0, 10, 12, "WALL")
    r = await backend.export("gltf")
    assert r.ok, r.error
    assert r.payload["format"] == "gltf"
    assert Path(r.payload["path"]).exists()
    assert Path(r.payload["ir_path"]).exists()


@pytest.mark.asyncio
async def test_obj_and_blender_script_are_written(backend, workspace):
    await backend.drawing_create("room")
    await backend.create_rectangle(0, 0, 10, 12, "WALL")
    r = await backend.to_3d(formats=["obj", "blender.py"], path="out.obj")
    assert r.ok, r.error
    obj = Path(r.payload["files"]["obj"])
    script = Path(r.payload["files"]["blender_py"])
    assert obj.exists() and "v " in obj.read_text()
    text = script.read_text()
    assert "import bpy" in text
    assert "PARTS = json.loads" in text


def test_view_to_3d_defaults():
    out = validate("view.to_3d", {})
    assert out["formats"] == ["gltf", "blender.py"]
    assert out["wall_height"] == 8.0
    assert out["include_casework"] is True


def test_view_to_3d_rejects_empty_formats():
    with pytest.raises(ValidationError):
        validate("view.to_3d", {"formats": []})


def test_view_export_accepts_gltf():
    assert validate("view.export", {"format": "gltf"})["format"] == "gltf"
    assert validate("view.export", {"format": "obj"})["format"] == "obj"
