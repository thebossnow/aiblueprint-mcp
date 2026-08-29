"""Tests for the Mezcal bundle export (thebossnow/mezcal_adapter_plugin's
import format) — boundary/footprint classification, requirements mapping,
the empty-profile guard, and the compliance-report key translation.
"""

from __future__ import annotations

import json

import pytest

from aiblueprint_mcp.mezcal_export import build_mezcal_export, export_mezcal
from aiblueprint_mcp.types import CommandError


def _make_profile(max_sqft=1200, max_height_ft=16, side_ft=4, rear_ft=4, front_ft=15, lot_pct=None):
    return {
        "requirements": {
            "effective": {
                "max_sqft": max_sqft,
                "max_height_ft": max_height_ft,
                "setback_side_ft": side_ft,
                "setback_rear_ft": rear_ft,
                "setback_front_ft": front_ft,
                "lot_coverage_max_pct": lot_pct,
            },
        },
        "warnings": ["Verify with local building department."],
        "notes": [],
        "disclaimers": [],
    }


def _empty_profile():
    return {
        "requirements": {
            "effective": {
                "max_sqft": None,
                "max_height_ft": None,
                "setback_side_ft": None,
                "setback_rear_ft": None,
                "setback_front_ft": None,
                "lot_coverage_max_pct": None,
            },
        },
        "warnings": [],
        "notes": [],
        "disclaimers": [],
    }


async def test_raises_without_a_closed_boundary(backend):
    with pytest.raises(CommandError):
        await build_mezcal_export(backend, None)


async def test_boundary_only_no_profile(backend):
    await backend.create_rectangle(0, 0, 100, 80, layer="LOT-LINE")
    bundle = await build_mezcal_export(backend, None)
    assert bundle["version"] == 1
    assert bundle["boundary"] == [[0, 0], [100, 0], [100, 80], [0, 80]]
    assert bundle["footprints"] == []
    assert "requirements" not in bundle
    assert "compliance" not in bundle


async def test_footprint_classification_and_labels(backend):
    await backend.create_rectangle(0, 0, 100, 80, layer="LOT-LINE")
    await backend.create_rectangle(10, 10, 40, 40, layer="EXISTING-FOOTPRINT")
    await backend.create_rectangle(60, 10, 90, 30, layer="ADU-FOOTPRINT")
    await backend.create_rectangle(10, 50, 30, 70, layer="PROPOSED-STRUCTURE")
    bundle = await build_mezcal_export(backend, None)

    by_kind = {f["kind"]: f for f in bundle["footprints"]}
    assert set(by_kind) == {"existing", "adu", "proposed"}
    assert by_kind["existing"]["label"] == "Existing Footprint"
    assert by_kind["adu"]["label"] == "Adu Footprint"
    assert all(f["heightFt"] == pytest.approx(12.0) for f in bundle["footprints"])


async def test_empty_resolved_profile_adds_nothing(backend):
    """No county/city ever matched -> every rule is None. Must not fabricate
    a false-PASS compliance report from vacuous checks (see mezcal_export.py
    for why: check_area/check_setbacks/etc. all return passed=True when
    their limit is None)."""
    await backend.create_rectangle(0, 0, 100, 80, layer="LOT-LINE")
    await backend.create_rectangle(60, 10, 90, 30, layer="ADU-FOOTPRINT")
    bundle = await build_mezcal_export(backend, _empty_profile())
    assert "requirements" not in bundle
    assert "compliance" not in bundle
    assert "setbackEnvelope" not in bundle


async def test_requirements_without_adu_footprint_has_no_compliance(backend):
    await backend.create_rectangle(0, 0, 100, 80, layer="LOT-LINE")
    await backend.create_rectangle(10, 10, 40, 40, layer="EXISTING-FOOTPRINT")
    bundle = await build_mezcal_export(backend, _make_profile())
    assert bundle["requirements"]["maxSqft"] == 1200
    assert "compliance" not in bundle


async def test_full_bundle_with_adu_footprint(backend):
    await backend.create_rectangle(0, 0, 100, 100, layer="LOT-LINE")
    await backend.create_rectangle(10, 10, 30, 35, layer="ADU-FOOTPRINT")
    bundle = await build_mezcal_export(backend, _make_profile(max_sqft=600, side_ft=4, rear_ft=4, lot_pct=50))

    req = bundle["requirements"]
    assert req == {
        "setbackFrontFt": 15,
        "setbackRearFt": 4,
        "setbackSideFt": 4,
        "maxHeightFt": 16,
        "maxCoveragePct": 50,
        "maxSqft": 600,
    }

    # ADU footprint takes the jurisdiction max height as its planning default.
    adu = next(f for f in bundle["footprints"] if f["kind"] == "adu")
    assert adu["heightFt"] == 16

    # Setback envelope: boundary offset inward by min(side_ft, rear_ft) = 4.
    assert bundle["setbackEnvelope"] == [[4, 4], [96, 4], [96, 96], [4, 96]]

    compliance = bundle["compliance"]
    assert set(compliance) == {"overall", "area", "setbacks", "coverage"}
    assert compliance["overall"] == "pass"
    assert compliance["area"]["ok"] is True
    assert compliance["setbacks"]["ok"] is True
    assert compliance["coverage"]["ok"] is True

    assert bundle["warnings"] == ["Verify with local building department."]


async def test_compliance_reflects_a_failing_check(backend):
    await backend.create_rectangle(0, 0, 100, 100, layer="LOT-LINE")
    # 30x40 = 1200 sq ft, over the 600 sq ft max.
    await backend.create_rectangle(10, 10, 40, 50, layer="ADU-FOOTPRINT")
    bundle = await build_mezcal_export(backend, _make_profile(max_sqft=600, side_ft=4, rear_ft=4))
    assert bundle["compliance"]["overall"] == "fail"
    assert bundle["compliance"]["area"]["ok"] is False
    assert "EXCEEDS" in bundle["compliance"]["area"]["message"]


async def test_export_mezcal_writes_the_workspace_file(backend, workspace):
    await backend.create_rectangle(0, 0, 100, 80, layer="LOT-LINE")
    await backend.create_rectangle(60, 10, 90, 30, layer="ADU-FOOTPRINT")
    result = await export_mezcal(backend, _make_profile(), path="lot.mezcal.json")
    assert result.ok
    assert result.payload["footprint_count"] == 1
    assert result.payload["has_requirements"] is True
    assert result.payload["has_compliance"] is True

    written = json.loads((workspace / "lot.mezcal.json").read_text())
    assert written["version"] == 1
    assert len(written["footprints"]) == 1


async def test_export_mezcal_surfaces_missing_boundary_as_a_result_error(backend):
    result = await export_mezcal(backend, None)
    assert not result.ok
    assert "boundary" in result.error.lower()
