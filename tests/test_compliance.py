"""Tests for the compliance engine geometry checks."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.compliance_engine import ComplianceEngine


def _make_profile(max_sqft=1200, max_height_ft=16, side_ft=4, rear_ft=4, lot_pct=None):
    return {
        "requirements": {
            "effective": {
                "max_sqft": max_sqft,
                "max_height_ft": max_height_ft,
                "setback_side_ft": side_ft,
                "setback_rear_ft": rear_ft,
                "lot_coverage_max_pct": lot_pct,
            },
            "sources": {"max_sqft": "CA Gov Code §65852.2"},
        },
        "warnings": [],
        "notes": [],
        "disclaimers": [],
    }


async def test_check_area_passes(backend):
    profile = _make_profile(max_sqft=600)
    engine = ComplianceEngine(backend, profile)
    r = await backend.create_rectangle(0, 0, 20, 25)  # 500 sq ft
    result = await engine.check_area(r.payload["handle"])
    assert result.passed
    assert result.value == pytest.approx(500.0)


async def test_check_area_fails(backend):
    profile = _make_profile(max_sqft=400)
    engine = ComplianceEngine(backend, profile)
    r = await backend.create_rectangle(0, 0, 20, 25)  # 500 sq ft
    result = await engine.check_area(r.payload["handle"])
    assert not result.passed
    assert "EXCEEDS" in result.message


async def test_check_area_no_limit_always_passes(backend):
    profile = _make_profile(max_sqft=None)
    engine = ComplianceEngine(backend, profile)
    r = await backend.create_rectangle(0, 0, 50, 50)
    result = await engine.check_area(r.payload["handle"])
    assert result.passed


async def test_check_height_passes(backend):
    profile = _make_profile(max_height_ft=16)
    engine = ComplianceEngine(backend, profile)
    result = await engine.check_height(None, ridge_y=15.0, grade_y=0.0)
    assert result.passed
    assert result.value == pytest.approx(15.0)


async def test_check_height_fails(backend):
    profile = _make_profile(max_height_ft=16)
    engine = ComplianceEngine(backend, profile)
    result = await engine.check_height(None, ridge_y=18.0, grade_y=0.0)
    assert not result.passed
    assert "EXCEEDS" in result.message


async def test_check_setbacks_adu_inside_envelope(backend):
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    # 100 x 100 ft lot
    lot = await backend.create_rectangle(0, 0, 100, 100)
    # 20 x 25 ft ADU well inside the lot (starts at 10,10)
    adu = await backend.create_rectangle(10, 10, 30, 35)
    result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
    assert result.passed


async def test_check_setbacks_violation_detected(backend):
    """An ADU intruding into the side-setback zone must FAIL.

    The footprint area (500 sq ft) is far smaller than the buildable envelope,
    so the old area-only comparison passed it; the containment check catches
    that it pokes outside the envelope.
    """
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)
    # Left edge at x=1 is inside the 4 ft side setback (envelope starts near x=4).
    adu = await backend.create_rectangle(1, 10, 21, 35)
    result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
    assert not result.passed
    assert "OUTSIDE" in result.message


async def test_check_setbacks_clockwise_boundary(backend):
    """Winding-aware offset: a clockwise-wound boundary still offsets inward.

    Without winding detection the envelope would balloon OUTWARD and the
    setback violation would be missed.
    """
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    # Clockwise-wound lot (up the left side first → negative signed area).
    lot = await backend.create_polyline(
        [(0, 0), (0, 100), (100, 100), (100, 0)], closed=True
    )
    adu = await backend.create_rectangle(1, 10, 21, 35)  # intrudes side setback
    result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
    assert not result.passed


async def test_check_setbacks_draws_setback_layer(backend):
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)
    adu = await backend.create_rectangle(10, 10, 30, 35)
    result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
    assert len(result.annotated_handles) > 0
    # Verify the setback layer was created
    layers = (await backend.layer_list()).payload["layers"]
    layer_names = [la["name"] for la in layers]
    assert "SETBACK-LINE" in layer_names


async def test_check_lot_coverage_passes(backend):
    profile = _make_profile(lot_pct=50)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)   # 10,000 sq ft
    structure = await backend.create_rectangle(0, 0, 60, 60)  # 3,600 sq ft = 36%
    result = await engine.check_lot_coverage(lot.payload["handle"], [structure.payload["handle"]])
    assert result.passed
    assert "36.0%" in result.value


async def test_check_lot_coverage_fails(backend):
    profile = _make_profile(lot_pct=30)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)   # 10,000 sq ft
    structure = await backend.create_rectangle(0, 0, 70, 70)  # 4,900 sq ft = 49%
    result = await engine.check_lot_coverage(lot.payload["handle"], [structure.payload["handle"]])
    assert not result.passed
    assert "EXCEEDS" in result.message


async def test_check_area_no_limit_message_is_clear(backend):
    """With no max_sqft, the result reports 'no limit checked' rather than 'within None'."""
    profile = _make_profile(max_sqft=None)
    engine = ComplianceEngine(backend, profile)
    r = await backend.create_rectangle(0, 0, 50, 50)
    result = await engine.check_area(r.payload["handle"])
    assert result.passed
    assert "None sq ft limit" not in result.message
    assert "no max_sqft limit" in result.message


async def test_check_lot_coverage_ignores_boundary_handle(backend):
    """Passing the lot boundary in the structure list must not inflate coverage."""
    profile = _make_profile(lot_pct=50)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)        # 10,000 sq ft
    structure = await backend.create_rectangle(0, 0, 60, 60)    # 3,600 sq ft = 36%
    # Include the lot handle itself plus a duplicate structure handle.
    result = await engine.check_lot_coverage(
        lot.payload["handle"],
        [lot.payload["handle"], structure.payload["handle"], structure.payload["handle"]],
    )
    assert result.passed
    assert "36.0%" in result.value


async def test_check_lot_coverage_flags_unmeasurable(backend):
    """A non-area structure (a LINE) is excluded and surfaced in the message."""
    profile = _make_profile(lot_pct=50)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)
    structure = await backend.create_rectangle(0, 0, 60, 60)
    line = await backend.create_line(0, 0, 10, 0)  # no area
    result = await engine.check_lot_coverage(
        lot.payload["handle"],
        [structure.payload["handle"], line.payload["handle"]],
    )
    assert "36.0%" in result.value
    assert "could not be measured" in result.message


async def test_full_report_runs_all_checks(backend):
    profile = _make_profile(max_sqft=600, side_ft=4, rear_ft=4, lot_pct=50)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)
    adu = await backend.create_rectangle(10, 10, 30, 35)
    report = await engine.full_report(
        property_boundary_handle=lot.payload["handle"],
        adu_footprint_handle=adu.payload["handle"],
        all_structure_handles=[adu.payload["handle"]],
    )
    assert "checks" in report
    assert len(report["checks"]) == 3  # area + setbacks + coverage
    assert "passed" in report
    assert "summary" in report
