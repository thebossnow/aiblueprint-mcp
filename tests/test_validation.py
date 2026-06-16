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


def test_view_export_rejects_unknown_format():
    with pytest.raises(ValidationError):
        validate("view.export", {"format": "dwg"})
