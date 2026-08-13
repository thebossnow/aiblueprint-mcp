"""Input validation tests (item 6)."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.validation import ValidationError, validate


def test_valid_line_applies_defaults():
    out = validate("entity.create_line", {"x1": 0, "y1": 0, "x2": 1, "y2": 1})
    assert out["x1"] == 0 and out["layer"] is None


def test_missing_required_field_raises():
    with pytest.raises(ValidationError):
        validate("entity.create_line", {"x1": 0, "y1": 0})


def test_negative_radius_rejected():
    with pytest.raises(ValidationError):
        validate("entity.create_circle", {"cx": 0, "cy": 0, "radius": -5})


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        validate("entity.create_circle", {"cx": 0, "cy": 0, "radius": 5, "bogus": 1})


def test_polyline_needs_two_points():
    with pytest.raises(ValidationError):
        validate("entity.create_polyline", {"points": [[0, 0]]})


def test_true_color_length():
    with pytest.raises(ValidationError):
        validate("layer.create", {"name": "x", "true_color": [1, 2]})


def test_unknown_key_passes_through():
    data = {"anything": 1}
    assert validate("view.nonexistent", data) == data


def test_namespaced_create_disambiguation():
    # layer.create vs drawing.create must not collide
    assert validate("drawing.create", {"name": "a"})["name"] == "a"
    assert validate("layer.create", {"name": "walls"})["color"] == "white"


def test_view_export_defaults_to_pdf():
    assert validate("view.export", {})["format"] == "pdf"


def test_view_export_accepts_geojson():
    assert validate("view.export", {"format": "geojson"})["format"] == "geojson"


def test_view_export_accepts_ifc():
    assert validate("view.export", {"format": "ifc"})["format"] == "ifc"


def test_view_export_rejects_unknown_format():
    with pytest.raises(ValidationError):
        validate("view.export", {"format": "dwg"})


def test_generate_site_plan_accepts_rectangular_source():
    out = validate("project.generate_site_plan", {"lot_width": 60, "lot_depth": 100})
    assert out["lot_width"] == 60 and out["lot_boundary"] is None


def test_generate_site_plan_accepts_irregular_boundary():
    out = validate("project.generate_site_plan", {"lot_boundary": [[0, 0], [60, 0], [60, 100], [0, 100]]})
    assert out["lot_width"] is None and out["lot_boundary"]


def test_generate_site_plan_accepts_boundary_handle():
    out = validate("project.generate_site_plan", {"lot_boundary_handle": "3A"})
    assert out["lot_boundary_handle"] == "3A"


def test_generate_site_plan_rejects_no_lot_source():
    with pytest.raises(ValidationError):
        validate("project.generate_site_plan", {})


def test_generate_site_plan_rejects_multiple_lot_sources():
    with pytest.raises(ValidationError):
        validate("project.generate_site_plan", {
            "lot_width": 60, "lot_depth": 100, "lot_boundary_handle": "3A",
        })


def test_generate_site_plan_rejects_partial_rectangular_source():
    with pytest.raises(ValidationError):
        validate("project.generate_site_plan", {"lot_width": 60})


def test_generate_site_plan_rejects_malformed_boundary_point():
    with pytest.raises(ValidationError):
        validate("project.generate_site_plan", {
            "lot_boundary": [[0, 0], [60], [60, 100], [0, 100]],
        })
