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


async def test_coverage_against_imported_irregular_boundary(backend):
    """An imported irregular parcel flows through coverage like any boundary."""
    profile = _make_profile(lot_pct=50)
    engine = ComplianceEngine(backend, profile)
    # L-shaped lot, area = 75 sq ft.
    lot = await backend.import_boundary(
        points=[[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10]]
    )
    structure = await backend.create_rectangle(0, 0, 5, 6)  # 30 sq ft = 40% of 75
    result = await engine.check_lot_coverage(lot.payload["handle"], [structure.payload["handle"]])
    assert result.passed
    assert "40.0%" in result.value


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
