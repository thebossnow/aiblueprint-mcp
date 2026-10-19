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
    # The envelope must actually be *smaller* than the lot (offset inward) —
    # not just "some polygon the much-smaller ADU happens to fit inside",
    # which passed even when the envelope offset the wrong direction. A
    # 4 ft-uniform inward offset of a 100x100 lot -> 92x92 = 8464 sq ft.
    envelope_handle = result.annotated_handles[0]
    envelope_area = (await backend.entity_measure(envelope_handle)).payload["area"]
    assert envelope_area == pytest.approx(8464.0)
    assert envelope_area < 10_000.0  # smaller than the lot itself


async def test_check_setbacks_offsets_inward_regardless_of_boundary_winding(backend):
    """entity_offset's sign is only "inward" for a CCW-wound ring (see
    backend.py's _offset_polyline docstring); import_boundary applies no
    winding normalization (see parcel.py), so a boundary can come in wound
    either way. check_setbacks must get this right for both."""
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    ccw_points = [[0, 0], [100, 0], [100, 100], [0, 100]]
    cw_points = list(reversed(ccw_points))

    ccw_lot = await backend.import_boundary(points=ccw_points, layer="LOT-CCW")
    cw_lot = await backend.import_boundary(points=cw_points, layer="LOT-CW")
    adu = await backend.create_rectangle(10, 10, 30, 35)

    for lot in (ccw_lot, cw_lot):
        result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
        envelope_area = (await backend.entity_measure(result.annotated_handles[0])).payload["area"]
        assert envelope_area == pytest.approx(8464.0), f"winding {lot.payload['handle']} offset the wrong way"


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


async def test_check_setbacks_l_shaped_missing_corner_fails(backend):
    """An ADU in the notch of an L-shaped lot must fail even though it sits
    inside the lot's/envelope's bounding box."""
    profile = _make_profile(side_ft=2, rear_ft=2)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.import_boundary(
        points=[[0, 0], [100, 0], [100, 50], [50, 50], [50, 100], [0, 100]]
    )
    adu = await backend.create_rectangle(70, 70, 80, 80)  # in the missing NE corner
    result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
    assert not result.passed
    inside = await backend.create_rectangle(10, 10, 20, 20)
    ok = await engine.check_setbacks(lot.payload["handle"], inside.payload["handle"])
    assert ok.passed


# ── Setback envelope geometry (negative buffer) ───────────────────────────
# The envelope used to be built by moving each lot edge straight inward
# (entity_offset). On narrow lots and necked/flag lots those offset edges
# cross, and buffer(0) then "repairs" the self-intersecting ring into a
# plausible-looking polygon, so the check could pass or fail wrongly.


async def test_check_setbacks_rectangle_envelope_unchanged(backend):
    """A plain rectangle gets exactly the same 92x92 envelope as before."""
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 100, 100)
    adu = await backend.create_rectangle(4, 4, 24, 29)  # touches the setback line
    result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
    assert result.passed
    assert len(result.annotated_handles) == 1
    env = backend._doc.entitydb.get(result.annotated_handles[0])
    assert env.dxf.layer == "SETBACK-LINE"
    pts = sorted((round(x, 6), round(y, 6)) for x, y in env.get_points(format="xy"))
    assert pts == [(4, 4), (4, 96), (96, 4), (96, 96)]  # square (mitre) corners


async def test_check_setbacks_narrow_lot_no_buildable_area_fails(backend):
    """A 6 ft wide lot with 4 ft side setbacks has no buildable area at all.

    Edge-offsetting flipped this into an inverted 2 ft strip that buffer(0)
    accepted as valid, so a skinny footprint inside it falsely passed.
    """
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.create_rectangle(0, 0, 6, 100)
    adu = await backend.create_rectangle(2.5, 10, 3.5, 20)
    result = await engine.check_setbacks(lot.payload["handle"], adu.payload["handle"])
    assert not result.passed
    assert "no buildable area" in result.message.lower()
    assert result.annotated_handles == []


async def test_check_setbacks_flag_lot_neck_is_not_buildable(backend):
    """Flag lot: a 6 ft access neck below a 60x60 body.

    With 4 ft setbacks the neck is fully consumed, so the buildable area is
    just the body shrunk to 52x52, and a footprint in the neck must fail.
    """
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.import_boundary(
        points=[[0, 0], [6, 0], [6, 40], [60, 40], [60, 100], [0, 100], [0, 40]]
    )
    in_neck = await backend.create_rectangle(2.5, 5, 3.5, 35)
    result = await engine.check_setbacks(lot.payload["handle"], in_neck.payload["handle"])
    assert not result.passed
    env_area = (await backend.entity_measure(result.annotated_handles[0])).payload["area"]
    assert env_area == pytest.approx(52 * 52)

    in_body = await backend.create_rectangle(10, 50, 30, 75)
    ok = await engine.check_setbacks(lot.payload["handle"], in_body.payload["handle"])
    assert ok.passed


async def test_check_setbacks_necked_lot_splits_footprint_must_fit_one_piece(backend):
    """Dumbbell lot: two 40x40 squares joined by a 6 ft neck.

    The setbacks split the buildable area into two separate pieces. A
    footprint bridging both pieces across the neck must fail; one inside a
    single piece passes.
    """
    profile = _make_profile(side_ft=4, rear_ft=4)
    engine = ComplianceEngine(backend, profile)
    lot = await backend.import_boundary(
        points=[
            [0, 0], [40, 0], [40, 17], [60, 17], [60, 0], [100, 0],
            [100, 40], [60, 40], [60, 23], [40, 23], [40, 40], [0, 40],
        ]
    )
    bridge = await backend.create_rectangle(20, 19, 80, 21)
    result = await engine.check_setbacks(lot.payload["handle"], bridge.payload["handle"])
    assert not result.passed
    assert len(result.annotated_handles) == 2  # one setback outline per piece

    one_piece = await backend.create_rectangle(10, 10, 30, 30)
    ok = await engine.check_setbacks(lot.payload["handle"], one_piece.payload["handle"])
    assert ok.passed
