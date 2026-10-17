"""Tests for architectural length parsing and its use in tool validation."""

from __future__ import annotations

import json

import pytest

from aiblueprint_mcp.units import parse_length
from aiblueprint_mcp.validation import ValidationError, validate


@pytest.mark.parametrize(
    ("text", "feet"),
    [
        (12, 12.0),
        ("12", 12.0),
        ("12.5", 12.5),
        ("12 ft", 12.0),
        ("5'-6\"", 5.5),
        ("5' 6\"", 5.5),
        ("5'6", 5.5),
        ("66\"", 5.5),
        ("6in", 0.5),
        ("3'", 3.0),
        ("5 5", 5 + 5 / 12),
        ("5' 6 1/2\"", 5 + 6.5 / 12),
        ("1/2\"", 1 / 24),
        ("-3'", -3.0),
        ("-2'-6\"", -2.5),
    ],
)
def test_parse_length(text, feet):
    assert parse_length(text) == pytest.approx(feet)


@pytest.mark.parametrize("bad", ["", "abc", "'", "5'-x", "0/0\"", "1 2 3", True, None])
def test_parse_length_rejects(bad):
    with pytest.raises(ValueError):
        parse_length(bad)


def test_validate_converts_length_fields():
    d = validate("entity.create_line", {"x1": "0", "y1": 0, "x2": "10'-6\"", "y2": "5 5"})
    assert d["x2"] == pytest.approx(10.5)
    assert d["y2"] == pytest.approx(5 + 5 / 12)


def test_validate_converts_nested_points_and_rejects_bad():
    d = validate("entity.create_polyline", {"points": [[0, 0], ["20'", "10'-6\""]]})
    assert d["points"][1] == [20.0, 10.5]
    with pytest.raises(ValidationError):
        validate("entity.create_circle", {"cx": 0, "cy": 0, "radius": "nonsense"})


def test_validate_leaves_angles_alone():
    with pytest.raises(ValidationError):
        validate("entity.rotate", {"entity_id": "1", "cx": 0, "cy": 0, "angle": "45'"})


def test_positive_constraint_still_applies_after_parsing():
    with pytest.raises(ValidationError):
        validate("entity.create_circle", {"cx": 0, "cy": 0, "radius": "-2'"})


async def test_server_accepts_architectural_lengths(server_env):
    srv = server_env
    await srv.drawing(operation="create")
    r = json.loads(await srv.entity(operation="create_rectangle", x1=0, y1=0, x2="20'", y2="10'-6\""))
    assert r["ok"] is True
    m = json.loads(await srv.entity(operation="measure", entity_id=r["handle"]))
    assert m["area"] == pytest.approx(20 * 10.5)
