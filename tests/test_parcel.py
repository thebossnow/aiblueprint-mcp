"""Parcel boundary parsing tests (GeoJSON + survey points)."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.parcel import (
    boundary_ring,
    bounding_box,
    inward_distance,
    parse_geojson,
    parse_survey_points,
    ring_area,
    ring_perimeter,
    ring_signed_area,
)

# An L-shaped (non-rectangular) parcel, area = 100 - 25 = 75.
L_SHAPE = [[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10]]


def test_survey_points_pairs():
    ring = parse_survey_points(L_SHAPE)
    assert len(ring) == 6
    assert ring_area(ring) == pytest.approx(75.0)


def test_survey_points_objects():
    pts = [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}]
    ring = parse_survey_points(pts)
    assert ring == [(0, 0), (4, 0), (4, 3)]
    assert ring_area(ring) == pytest.approx(6.0)  # right triangle 4×3/2


def test_survey_points_strip_closing_vertex():
    ring = parse_survey_points([[0, 0], [4, 0], [4, 3], [0, 0]])
    assert ring[-1] != ring[0] and len(ring) == 3


def test_survey_points_too_few():
    with pytest.raises(ValueError):
        parse_survey_points([[0, 0], [1, 1]])


def test_survey_points_bad_vertex():
    with pytest.raises(ValueError):
        parse_survey_points([[0, 0], [1], [2, 2]])


def test_geojson_polygon():
    poly = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10], [0, 0]]],
    }
    ring = parse_geojson(poly)
    assert len(ring) == 6  # closing vertex dropped
    assert ring_area(ring) == pytest.approx(75.0)


def test_geojson_feature_and_collection():
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]}
    feat = {"type": "Feature", "geometry": poly, "properties": {}}
    fc = {"type": "FeatureCollection", "features": [feat]}
    assert ring_area(parse_geojson(feat)) == pytest.approx(16.0)
    assert ring_area(parse_geojson(fc)) == pytest.approx(16.0)


def test_geojson_multipolygon_takes_first():
    mp = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
            [[[9, 9], [10, 9], [10, 10], [9, 10], [9, 9]]],
        ],
    }
    assert ring_area(parse_geojson(mp)) == pytest.approx(4.0)


def test_geojson_no_polygon():
    with pytest.raises(ValueError):
        parse_geojson({"type": "Point", "coordinates": [0, 0]})


def test_boundary_ring_requires_exactly_one_source():
    with pytest.raises(ValueError):
        boundary_ring()
    with pytest.raises(ValueError):
        boundary_ring(points=L_SHAPE, geojson={"type": "Polygon", "coordinates": [[]]})


def test_metrics():
    ring = [(0, 0), (4, 0), (4, 3), (0, 3)]
    assert ring_area(ring) == pytest.approx(12.0)
    assert ring_perimeter(ring) == pytest.approx(14.0)
    assert bounding_box(ring) == {"min_x": 0, "min_y": 0, "max_x": 4, "max_y": 3}


def test_ring_signed_area_reflects_winding():
    ccw = [(0, 0), (4, 0), (4, 3), (0, 3)]
    cw = list(reversed(ccw))
    assert ring_signed_area(ccw) == pytest.approx(12.0)
    assert ring_signed_area(cw) == pytest.approx(-12.0)
    assert ring_area(ccw) == ring_area(cw) == pytest.approx(12.0)


def test_ring_signed_area_l_shape_is_ccw():
    # boundary_ring/import_boundary apply no winding normalization — this
    # fixture just documents that L_SHAPE, as literally written above,
    # happens to be CCW (positive signed area), not that anything enforces it.
    assert ring_signed_area(L_SHAPE) == pytest.approx(75.0)


def test_inward_distance_flips_sign_with_winding():
    ccw = [(0, 0), (4, 0), (4, 3), (0, 3)]
    cw = list(reversed(ccw))
    # Same ring, same requested inward distance, opposite winding ->
    # entity_offset needs the opposite signed distance to still go inward.
    assert inward_distance(ccw, 1.0) == pytest.approx(1.0)
    assert inward_distance(cw, 1.0) == pytest.approx(-1.0)
    assert inward_distance(ccw, -1.0) == pytest.approx(-1.0)


def test_inward_distance_degenerate_ring_passes_through():
    zero_area = [(0, 0), (1, 0), (2, 0)]  # collinear -> zero signed area
    assert inward_distance(zero_area, 3.0) == 3.0
