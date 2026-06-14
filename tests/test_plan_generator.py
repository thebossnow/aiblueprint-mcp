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
