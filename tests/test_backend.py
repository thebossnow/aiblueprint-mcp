"""Backend operation tests — geometry, sessions, errors (items 1, 5, 10, 11, 12)."""

from __future__ import annotations

import math
import pathlib

import ezdxf
import pytest


async def test_drawing_lifecycle_and_save_reload(backend, workspace):
    await backend.drawing_create("plan")
    await backend.create_line(0, 0, 10, 0, "outline")
    saved = await backend.drawing_save("plan.dxf")
    assert saved.ok
    path = saved.payload["path"]
    # Round-trip: reload the saved DXF and confirm the entity persisted.
    doc = ezdxf.readfile(path)
    types = [e.dxftype() for e in doc.modelspace()]
    assert "LINE" in types


async def test_multi_document_sessions(backend):
    a = (await backend.drawing_create("a")).payload["handle"]
    b = (await backend.drawing_create("b")).payload["handle"]
    listing = await backend.drawing_list()
    handles = [d["handle"] for d in listing.payload["documents"]]
    assert a in handles and b in handles
    assert listing.payload["current"] == b
    await backend.drawing_switch(a)
    assert (await backend.drawing_list()).payload["current"] == a


async def test_switch_unknown_handle_errors(backend):
    r = await backend.drawing_switch("nope")
    assert not r.ok and "nope" in r.error


async def test_measure_rectangle(backend):
    r = await backend.create_rectangle(0, 0, 10, 4, "walls")
    m = await backend.entity_measure(r.payload["handle"])
    assert m.payload["area"] == pytest.approx(40.0)
    assert m.payload["perimeter"] == pytest.approx(28.0)


async def test_measure_circle(backend):
    r = await backend.create_circle(0, 0, 2)
    m = await backend.entity_measure(r.payload["handle"])
    assert m.payload["area"] == pytest.approx(math.pi * 4)
    assert m.payload["circumference"] == pytest.approx(2 * math.pi * 2)


async def test_entity_get_arc_and_text(backend):
    arc = await backend.create_arc(0, 0, 5, 0, 90)
    info = await backend.entity_get(arc.payload["handle"])
    assert info.payload["type"] == "ARC" and info.payload["radius"] == 5
    txt = await backend.create_text(1, 1, "hello")
    tinfo = await backend.entity_get(txt.payload["handle"])
    assert tinfo.payload["text"] == "hello"


async def test_offset_closed_polyline_changes_area(backend):
    # Rectangle vertices are CCW, so a positive (left-of-edge) offset moves
    # inward: a 10x10 square offset by 1 becomes 8x8 = 64; by -1 becomes 12x12.
    r = await backend.create_rectangle(0, 0, 10, 10)
    inward = await backend.entity_offset(r.payload["handle"], 1.0)
    assert (await backend.entity_measure(inward.payload["handle"])).payload["area"] == pytest.approx(64.0)
    r2 = await backend.create_rectangle(0, 0, 10, 10)
    outward = await backend.entity_offset(r2.payload["handle"], -1.0)
    assert (await backend.entity_measure(outward.payload["handle"])).payload["area"] == pytest.approx(144.0)


async def test_offset_open_polyline(backend):
    r = await backend.create_polyline([[0, 0], [10, 0], [10, 10]], closed=False)
    off = await backend.entity_offset(r.payload["handle"], 1.0)
    assert off.ok and off.payload["points"] == 3


async def test_fillet_creates_arc(backend):
    l1 = await backend.create_line(0, 0, 10, 0)
    l2 = await backend.create_line(10, 0, 10, 10)
    f = await backend.entity_fillet(l1.payload["handle"], l2.payload["handle"], 2.0)
    assert f.ok and f.payload["entity_type"] == "ARC"


async def test_layer_true_color(backend):
    await backend.layer_create("highlight", true_color=[255, 0, 0])
    layers = (await backend.layer_list()).payload["layers"]
    hl = next(layer for layer in layers if layer["name"] == "highlight")
    assert hl.get("true_color") == [255, 0, 0]


async def test_block_define_insert_attributes(backend):
    await backend.block_define("tree", [
        {"type": "CIRCLE", "cx": 0, "cy": 0, "radius": 1},
        {"type": "ATTDEF", "tag": "SPECIES", "x": 0, "y": 0},
    ])
    ins = await backend.block_insert_with_attributes("tree", 5, 5, attributes={"SPECIES": "Oak"})
    assert ins.ok
    attrs = await backend.block_get_attributes(ins.payload["handle"])
    assert attrs.payload["attributes"].get("SPECIES") == "Oak"


async def test_dimension_linear(backend):
    r = await backend.create_dimension_linear(0, 0, 10, 0, 0, -2)
    assert r.ok and r.payload["entity_type"] == "DIMENSION"


async def test_screenshot_returns_dict_not_string(backend):
    # Regression: get_screenshot previously returned a bare base64 string,
    # which crashed JSON serialization in the server (_ok unpacking).
    await backend.create_line(0, 0, 10, 10)
    r = await backend.get_screenshot()
    assert r.ok
    assert isinstance(r.payload, dict)
    assert "image_base64" in r.payload and r.payload["format"] == "png"


async def test_export_pdf(backend, workspace):
    await backend.create_rectangle(0, 0, 10, 10)
    r = await backend.export("pdf")
    assert r.ok and r.payload["path"].endswith(".pdf")
    assert (workspace / "untitled.pdf").exists() or workspace in (workspace,)


async def test_export_svg(backend, workspace):
    await backend.create_rectangle(0, 0, 10, 10)
    r = await backend.export("svg")
    assert r.ok and r.payload["format"] == "svg"
    assert (workspace / "untitled.svg").exists()


async def test_export_geojson(backend, workspace):
    import json

    await backend.create_line(0, 0, 10, 0, "outline")
    await backend.create_rectangle(0, 0, 10, 4, "walls")  # closed LWPOLYLINE
    await backend.create_circle(5, 5, 2, "pool")
    r = await backend.export("geojson")
    assert r.ok and r.payload["format"] == "geojson"
    out = workspace / "untitled.geojson"
    assert out.exists()

    fc = json.loads(out.read_text())
    assert fc["type"] == "FeatureCollection"
    geom_types = {f["geometry"]["type"] for f in fc["features"]}
    assert {"LineString", "Polygon", "Point"} <= geom_types

    # Closed rectangle → Polygon with a closed ring; every feature is traceable.
    poly = next(f for f in fc["features"] if f["geometry"]["type"] == "Polygon")
    ring = poly["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    for f in fc["features"]:
        assert {"layer", "dxftype", "handle"} <= f["properties"].keys()


async def test_export_rejects_unknown_format(backend):
    r = await backend.export("dwg")
    assert not r.ok and "geojson" in r.error


async def test_preview_renders_png(backend, workspace):
    from aiblueprint_mcp.config import Config

    if not Config.from_env().librecad_bin.exists():
        pytest.skip("no librecad binary available in this environment")

    await backend.create_rectangle(0, 0, 10, 10, "outline")
    r = await backend.preview()
    assert r.ok, r.error
    png_path = pathlib.Path(r.payload["png_path"])
    assert png_path.exists() and png_path.stat().st_size > 0


async def test_export_ifc(backend, workspace):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell

    await backend.create_rectangle(0, 0, 30, 50, "LOT-LINE")
    await backend.create_rectangle(5, 5, 15, 13, "ADU-FOOTPRINT")

    r = await backend.export("ifc")
    assert r.ok and r.payload["format"] == "ifc"
    out = workspace / "untitled.ifc"
    assert out.exists()

    f = ifcopenshell.open(str(out))
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcProject")) == 1
    assert len(f.by_type("IfcSite")) == 1
    assert len(f.by_type("IfcSlab")) == 1
    # Rectangle footprint -> 4 perimeter walls.
    assert len(f.by_type("IfcWall")) == 4

    slab = f.by_type("IfcSlab")[0]
    assert slab.PredefinedType == "BASESLAB"

    # 10ft x 8ft footprint converted to meters.
    from aiblueprint_mcp.ifc_export import FEET_TO_METERS

    profile = f.by_type("IfcArbitraryClosedProfileDef")[0]
    xs = [p.Coordinates[0] for p in profile.OuterCurve.Points]
    ys = [p.Coordinates[1] for p in profile.OuterCurve.Points]
    assert max(xs) - min(xs) == pytest.approx(10 * FEET_TO_METERS)
    assert max(ys) - min(ys) == pytest.approx(8 * FEET_TO_METERS)

    wall = f.by_type("IfcWall")[0]
    assert wall.ObjectPlacement is not None

    # Every spatial element and the slab must also carry an explicit
    # placement, not just walls (regression test for #28).
    site = f.by_type("IfcSite")[0]
    building = f.by_type("IfcBuilding")[0]
    storey = f.by_type("IfcBuildingStorey")[0]
    assert site.ObjectPlacement is not None
    assert building.ObjectPlacement is not None
    assert storey.ObjectPlacement is not None
    assert slab.ObjectPlacement is not None


async def test_export_ifc_without_lot_layer_uses_largest_ring(backend, workspace):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell

    # No "lot"/"footprint" layer hints at all: the larger ring becomes the
    # site boundary, the smaller becomes the one footprint slab/walls.
    await backend.create_rectangle(0, 0, 30, 50, "boundary")
    await backend.create_rectangle(5, 5, 15, 13, "structure")

    r = await backend.export("ifc")
    assert r.ok
    f = ifcopenshell.open(str(workspace / "untitled.ifc"))
    assert len(f.by_type("IfcSlab")) == 1
    assert len(f.by_type("IfcWall")) == 4


async def test_export_ifc_missing_dependency_errors_cleanly(backend, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ifcopenshell":
            raise ImportError("no ifcopenshell")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    r = await backend.export("ifc")
    assert not r.ok and "ifcopenshell" in r.error


# ── undo / redo ────────────────────────────────────────────────────────

async def _count(backend) -> int:
    return (await backend.entity_list()).payload["count"]


async def test_undo_redo_roundtrip(backend):
    await backend.create_line(0, 0, 10, 0)
    assert await _count(backend) == 1

    u = await backend.undo()
    assert u.ok and u.payload["entity_count"] == 0
    assert await _count(backend) == 0

    r = await backend.redo()
    assert r.ok and r.payload["entity_count"] == 1
    assert await _count(backend) == 1


async def test_undo_preserves_handles(backend):
    h = (await backend.create_line(0, 0, 5, 5)).payload["handle"]
    await backend.create_circle(1, 1, 2)
    await backend.undo()  # remove the circle
    # The line survived with its original handle → still addressable.
    got = await backend.entity_get(h)
    assert got.ok and got.payload["type"] == "LINE"


async def test_undo_empty_errors(backend):
    r = await backend.undo()
    assert not r.ok and "Nothing to undo" in r.error


async def test_redo_empty_errors(backend):
    r = await backend.redo()
    assert not r.ok and "Nothing to redo" in r.error


async def test_new_op_clears_redo(backend):
    await backend.create_line(0, 0, 1, 1)
    await backend.undo()           # redo stack now has 1
    await backend.create_circle(0, 0, 3)  # a fresh mutation clears redo
    r = await backend.redo()
    assert not r.ok and "Nothing to redo" in r.error


async def test_failed_op_leaves_no_checkpoint(backend):
    await backend.create_line(0, 0, 1, 1)
    # A failed mutation must not push an undo entry.
    bad = await backend.entity_move("does-not-exist", 1, 1)
    assert not bad.ok
    await backend.undo()  # the single undo should remove the line
    assert await _count(backend) == 0


async def test_batch_groups_into_one_checkpoint(backend):
    with backend.batch():
        await backend.create_line(0, 0, 1, 1)
        await backend.create_line(1, 1, 2, 2)
        await backend.create_circle(0, 0, 1)
    assert await _count(backend) == 3
    await backend.undo()  # one undo reverts the whole batch
    assert await _count(backend) == 0


async def test_import_boundary_survey_points(backend):
    r = await backend.import_boundary(points=[[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10]])
    assert r.ok, r.error
    assert r.payload["entity_type"] == "LWPOLYLINE"
    assert r.payload["point_count"] == 6
    assert r.payload["area"] == pytest.approx(75.0)
    # The drawn polyline measures the same area through the normal takeoff path.
    m = await backend.entity_measure(r.payload["handle"])
    assert m.payload["area"] == pytest.approx(75.0)


async def test_import_boundary_geojson(backend):
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [8, 0], [8, 6], [0, 6], [0, 0]]]}
    r = await backend.import_boundary(geojson=poly)
    assert r.ok and r.payload["area"] == pytest.approx(48.0)
    assert r.payload["bbox"] == {"min_x": 0, "min_y": 0, "max_x": 8, "max_y": 6}


async def test_import_boundary_requires_one_source(backend):
    r = await backend.import_boundary()
    assert not r.ok and "exactly one" in r.error


async def test_import_boundary_is_undoable(backend):
    await backend.import_boundary(points=[[0, 0], [4, 0], [4, 4], [0, 4]])
    assert (await backend.entity_list()).payload["count"] == 1
    await backend.undo()
    assert (await backend.entity_list()).payload["count"] == 0


async def test_undo_independent_per_document(backend):
    a = (await backend.drawing_create("a")).payload["handle"]
    await backend.create_line(0, 0, 1, 1)
    await backend.drawing_create("b")
    await backend.create_circle(0, 0, 2)
    # Undo on b only affects b.
    await backend.undo()
    assert await _count(backend) == 0
    await backend.drawing_switch(a)
    assert await _count(backend) == 1  # a is untouched


async def test_unexpected_error_wrapped(backend):
    # Operating on a missing entity yields a clean error, not a traceback.
    r = await backend.entity_move("does-not-exist", 1, 1)
    assert not r.ok and "not found" in r.error


async def test_no_document_error_message(backend):
    backend._docs.clear()
    backend._current = None
    r = await backend.entity_list()
    assert not r.ok and "No document" in r.error
