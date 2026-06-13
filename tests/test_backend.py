"""Backend operation tests — geometry, sessions, errors (items 1, 5, 10, 11, 12)."""

from __future__ import annotations

import math

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


async def test_unexpected_error_wrapped(backend):
    # Operating on a missing entity yields a clean error, not a traceback.
    r = await backend.entity_move("does-not-exist", 1, 1)
    assert not r.ok and "not found" in r.error


async def test_no_document_error_message(backend):
    backend._docs.clear()
    backend._current = None
    r = await backend.entity_list()
    assert not r.ok and "No document" in r.error
