"""Tests for the auto site-plan generator."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.plan_generator import SitePlanConfig, SitePlanGenerator
from aiblueprint_mcp.project_state import ProjectSession


def _la_session() -> ProjectSession:
    """Minimal session answered for Los Angeles (CA state defaults apply)."""
    s = ProjectSession()
    s.answer("project_type", "ADU — Detached")
    s.answer("county", "Los Angeles")
    s.answer("city", "Other — Not Listed")
    s.answer("hoa_exists", False)
    s.answer("lot_size_sqft", 7200)
    s.answer("fire_zone", "Standard")
    s.answer("coastal_zone", False)
    s.answer("adu_target_sqft", 500)
    s.answer("adu_bedrooms", 1)
    return s


async def test_generate_explicit_dims(workspace, backend):
    """Explicit adu_width/adu_depth round-trips through payload."""
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_width=60, lot_depth=120, adu_width=20, adu_depth=25))

    assert r.ok, r.error
    p = r.payload
    assert p["adu_width_ft"] == 20.0
    assert p["adu_depth_ft"] == 25.0
    assert p["adu_area_sqft"] == 500.0
    assert p["lot_width_ft"] == 60
    assert p["lot_depth_ft"] == 120
    assert p["side_setback_ft"] == 4.0
    assert p["rear_setback_ft"] == 4.0
    assert p["handle"]
    assert p["lot_handle"]
    assert p["adu_handle"]


async def test_generate_site_plan_is_one_undo_step(workspace, backend):
    """The whole site plan collapses into a single undo checkpoint."""
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_width=60, lot_depth=120, adu_width=20, adu_depth=25))
    assert r.ok, r.error
    assert (await backend.entity_list()).payload["count"] > 5  # many entities drawn

    undone = await backend.undo()
    assert undone.ok and undone.payload["entity_count"] == 0  # one undo clears the plan
    assert undone.payload["undo_depth"] == 0


async def test_generate_auto_size(workspace, backend):
    """Auto-sized ADU stays within ±100 sq ft of target."""
    s = ProjectSession()
    s.answer("project_type", "ADU — Detached")
    s.answer("county", "San Diego")
    s.answer("city", "Encinitas")
    s.answer("hoa_exists", False)
    s.answer("lot_size_sqft", 10000)
    s.answer("fire_zone", "Standard")
    s.answer("coastal_zone", False)
    s.answer("adu_target_sqft", 750)
    s.answer("adu_bedrooms", 2)

    gen = SitePlanGenerator(backend, s)
    r = await gen.generate(SitePlanConfig(lot_width=80, lot_depth=125))

    assert r.ok, r.error
    p = r.payload
    assert abs(p["adu_area_sqft"] - 750) <= 100
    assert p["adu_width_ft"] > 0
    assert p["adu_depth_ft"] > 0


async def test_generate_defaults_with_empty_profile(workspace, backend):
    """Empty session falls back to CA state defaults (4 ft setbacks)."""
    gen = SitePlanGenerator(backend, ProjectSession())
    r = await gen.generate(SitePlanConfig(lot_width=50, lot_depth=100, adu_width=20, adu_depth=25))

    assert r.ok, r.error
    assert r.payload["side_setback_ft"] == 4.0
    assert r.payload["rear_setback_ft"] == 4.0


async def test_generate_adu_too_wide(workspace, backend):
    """ADU wider than buildable width returns a clear error."""
    gen = SitePlanGenerator(backend, ProjectSession())
    # 20 ft lot − (4+4) ft setbacks = 12 ft buildable; 15 ft ADU is too wide
    r = await gen.generate(SitePlanConfig(lot_width=20, lot_depth=60, adu_width=15, adu_depth=20))

    assert not r.ok
    assert "buildable width" in r.error.lower()


async def test_generate_adu_too_deep(workspace, backend):
    """ADU + rear setback deeper than lot depth returns a clear error."""
    gen = SitePlanGenerator(backend, ProjectSession())
    # 30 ft lot depth, ADU 28 ft + 4 ft setback = 32 ft — exceeds lot
    r = await gen.generate(SitePlanConfig(lot_width=60, lot_depth=30, adu_width=20, adu_depth=28))

    assert not r.ok
    assert "lot depth" in r.error.lower()


async def test_generate_layers_created(workspace, backend):
    """Plan generator creates all expected layers."""
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_width=60, lot_depth=120, adu_width=20, adu_depth=25))
    assert r.ok

    lr = await backend.layer_list()
    layer_names = {la["name"] for la in lr.payload["layers"]}
    for expected in (
        "LOT-LINE", "SETBACK-LINE", "ADU-FOOTPRINT",
        "DIMENSION", "ANNOTATION", "TITLE-BLOCK", "NORTH-ARROW",
    ):
        assert expected in layer_names, f"Missing layer: {expected}"


@pytest.mark.parametrize("pos", ["rear_center", "rear_left", "rear_right"])
async def test_generate_adu_positions(workspace, backend, pos):
    """All three ADU position variants succeed and report the requested position."""
    gen = SitePlanGenerator(backend, _la_session())
    cfg = SitePlanConfig(lot_width=60, lot_depth=120, adu_width=20, adu_depth=25, adu_position=pos)
    r = await gen.generate(cfg)

    assert r.ok, f"{pos}: {r.error}"
    assert r.payload["adu_position"] == pos


async def test_generate_custom_name(workspace, backend):
    """draw_name is reflected in the payload."""
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(
        SitePlanConfig(lot_width=60, lot_depth=120, adu_width=20, adu_depth=25, draw_name="my_plan")
    )
    assert r.ok, r.error
    assert r.payload["drawing_name"] == "my_plan"


async def test_generate_hoa_setback_respected(workspace, backend):
    """HOA setback override (larger setback) is reflected in plan dims."""
    s = ProjectSession()
    s.answer("project_type", "ADU — Detached")
    s.answer("county", "Los Angeles")
    s.answer("city", "Other — Not Listed")
    s.answer("hoa_exists", True)
    s.answer("hoa_max_height_ft", 14)
    s.answer("hoa_additional_setback_side_ft", 7)   # HOA requires 7 ft total; state is 4 ft
    s.answer("hoa_additional_setback_rear_ft", 6)   # HOA requires 6 ft total; state is 4 ft
    s.answer("hoa_arch_review_required", False)
    s.answer("hoa_notes", "")
    s.answer("lot_size_sqft", 9000)
    s.answer("fire_zone", "Standard")
    s.answer("coastal_zone", False)
    s.answer("adu_target_sqft", 500)
    s.answer("adu_bedrooms", 1)

    gen = SitePlanGenerator(backend, s)
    r = await gen.generate(SitePlanConfig(lot_width=70, lot_depth=130, adu_width=20, adu_depth=25))
    assert r.ok, r.error
    assert r.payload["side_setback_ft"] == 7.0
    assert r.payload["rear_setback_ft"] == 6.0


# ── Irregular (non-rectangular) lots — issue #22 ─────────────────────────

# 60x100 rectangle with a 20x40 notch removed from the NE (rear-right) corner.
L_SHAPED_LOT = [[0, 0], [60, 0], [60, 60], [40, 60], [40, 100], [0, 100]]

# Wider at the front (y=0, width 60) than the rear (y=100, width 40).
TRAPEZOIDAL_LOT = [[0, 0], [60, 0], [50, 100], [10, 100]]


async def test_generate_l_shaped_lot_via_boundary(workspace, backend):
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_boundary=L_SHAPED_LOT, adu_width=20, adu_depth=25))

    assert r.ok, r.error
    p = r.payload
    assert p["lot_shape"] == "irregular"
    assert p["lot_area_sqft"] == 5200  # 60*100 - 20*40 notch
    assert p["adu_width_ft"] == 20.0
    assert p["adu_depth_ft"] == 25.0
    assert p["lot_handle"] and p["adu_handle"]

    lr = await backend.layer_list()
    layer_names = {la["name"] for la in lr.payload["layers"]}
    for expected in ("LOT-LINE", "SETBACK-LINE", "ADU-FOOTPRINT", "DIMENSION", "ANNOTATION", "TITLE-BLOCK", "NORTH-ARROW"):
        assert expected in layer_names, f"Missing layer: {expected}"


async def test_generate_trapezoidal_lot_via_boundary(workspace, backend):
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_boundary=TRAPEZOIDAL_LOT, adu_width=20, adu_depth=25))

    assert r.ok, r.error
    assert r.payload["lot_shape"] == "irregular"
    assert r.payload["lot_area_sqft"] == 5000  # trapezoid area


async def test_generate_irregular_lot_via_boundary_handle(workspace, backend):
    """lot_boundary_handle resolves an already-imported LWPOLYLINE."""
    await backend.drawing_create("boundary_import")
    imp = await backend.import_boundary(points=L_SHAPED_LOT, layer="LOT-LINE")
    assert imp.ok, imp.error

    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_boundary_handle=imp.payload["handle"], adu_width=20, adu_depth=25))
    assert r.ok, r.error
    assert r.payload["lot_shape"] == "irregular"


async def test_generate_irregular_lot_adu_placement_avoids_notch(workspace, backend):
    """rear_right placement on an L-shaped lot must not overlap the removed notch.

    A naive rectangle-style "anchor to the buildable bbox's east edge" placement
    (x = bbox_maxx - adu_w = 56 - 20 = 36, still inside the setback-shrunk
    envelope's overall bbox but overlapping the notch's own setback zone)
    doesn't fit; the grid-search fallback must find a position clear of it.
    """
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(
        SitePlanConfig(lot_boundary=L_SHAPED_LOT, adu_width=20, adu_depth=25, adu_position="rear_right")
    )
    assert r.ok, r.error
    assert r.payload["adu_x_ft"] == 16.0
    assert r.payload["adu_y_ft"] == 71.0
    adu_x2 = r.payload["adu_x_ft"] + r.payload["adu_width_ft"]
    assert adu_x2 <= 40  # clear of the notch, which starts at x=40


async def test_generate_irregular_lot_auto_size(workspace, backend):
    """Auto-sized ADU on an irregular lot stays close to target."""
    s = ProjectSession()
    s.answer("project_type", "ADU — Detached")
    s.answer("county", "San Diego")
    s.answer("city", "Encinitas")
    s.answer("hoa_exists", False)
    s.answer("lot_size_sqft", 10000)
    s.answer("fire_zone", "Standard")
    s.answer("coastal_zone", False)
    s.answer("adu_target_sqft", 400)
    s.answer("adu_bedrooms", 1)

    gen = SitePlanGenerator(backend, s)
    r = await gen.generate(SitePlanConfig(lot_boundary=TRAPEZOIDAL_LOT))
    assert r.ok, r.error
    assert abs(r.payload["adu_area_sqft"] - 400) <= 100


async def test_generate_irregular_lot_setbacks_too_large(workspace, backend):
    """Setbacks that consume the whole buildable area return a clear error."""
    s = ProjectSession()
    s.answer("project_type", "ADU — Detached")
    s.answer("county", "Los Angeles")
    s.answer("city", "Other — Not Listed")
    s.answer("hoa_exists", True)
    s.answer("hoa_max_height_ft", 14)
    s.answer("hoa_additional_setback_side_ft", 20)
    s.answer("hoa_additional_setback_rear_ft", 20)
    s.answer("hoa_arch_review_required", False)
    s.answer("hoa_notes", "")
    s.answer("lot_size_sqft", 600)
    s.answer("fire_zone", "Standard")
    s.answer("coastal_zone", False)
    s.answer("adu_target_sqft", 500)
    s.answer("adu_bedrooms", 1)

    gen = SitePlanGenerator(backend, s)
    small_square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    r = await gen.generate(SitePlanConfig(lot_boundary=small_square))

    assert not r.ok
    assert "no buildable area" in r.error.lower()


async def test_generate_rejects_both_rectangular_and_irregular_source(workspace, backend):
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(
        SitePlanConfig(lot_width=60, lot_depth=100, lot_boundary=TRAPEZOIDAL_LOT)
    )
    assert not r.ok
    assert "exactly one lot source" in r.error.lower()


async def test_generate_rejects_no_lot_source(workspace, backend):
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig())
    assert not r.ok
    assert "exactly one lot source" in r.error.lower()


async def test_generate_rejects_bad_boundary_handle(workspace, backend):
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_boundary_handle="nonexistent"))
    assert not r.ok


async def test_generate_handles_boundary_with_repeated_closing_point(workspace, backend):
    """A ring that repeats its first point as its last (GeoJSON convention)
    must not crash on a zero-length edge — it should dedupe and match the
    open-ring result exactly."""
    closed_ring = [*L_SHAPED_LOT, L_SHAPED_LOT[0]]
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_boundary=closed_ring, adu_width=20, adu_depth=25))
    assert r.ok, r.error
    assert r.payload["lot_area_sqft"] == 5200


async def test_generate_rejects_open_boundary_handle(workspace, backend):
    """lot_boundary_handle must reference a closed LWPOLYLINE, not an open sketch."""
    await backend.drawing_create("open_sketch")
    line = await backend.create_polyline(L_SHAPED_LOT, closed=False, layer="MISC")
    assert line.ok

    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_boundary_handle=line.payload["handle"]))
    assert not r.ok
    assert "closed" in r.error.lower()


async def test_generate_rejects_invalid_adu_position(workspace, backend):
    gen = SitePlanGenerator(backend, _la_session())
    r = await gen.generate(SitePlanConfig(lot_width=60, lot_depth=100, adu_position="front_left"))
    assert not r.ok
    assert "adu_position" in r.error.lower()


async def test_generate_irregular_lot_survives_degenerate_setback_zone(workspace, backend):
    """A large rear setback (40 ft, via HOA override) collapses the *full*
    setback-zone polygon (which also includes the fixed 20 ft front
    reference) to a zero-area LineString on this L-shaped lot, while the ADU
    envelope (rear+side only) stays a valid, placeable Polygon. This must not
    crash drawing the setback-zone reference boundary."""
    s = ProjectSession()
    s.answer("project_type", "ADU — Detached")
    s.answer("county", "Los Angeles")
    s.answer("city", "Other — Not Listed")
    s.answer("hoa_exists", True)
    s.answer("hoa_max_height_ft", 14)
    s.answer("hoa_additional_setback_side_ft", 4)
    s.answer("hoa_additional_setback_rear_ft", 40)
    s.answer("hoa_arch_review_required", False)
    s.answer("hoa_notes", "")
    s.answer("lot_size_sqft", 6000)
    s.answer("fire_zone", "Standard")
    s.answer("coastal_zone", False)
    s.answer("adu_target_sqft", 25)
    s.answer("adu_bedrooms", 1)

    gen = SitePlanGenerator(backend, s)
    r = await gen.generate(SitePlanConfig(lot_boundary=L_SHAPED_LOT, adu_width=5, adu_depth=5))
    assert r.ok, r.error
