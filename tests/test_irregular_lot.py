"""Tests for irregular_lot.py geometry helpers."""

from __future__ import annotations

from shapely.geometry import Point

from aiblueprint_mcp.irregular_lot import (
    buildable_envelope,
    classify_edge_direction,
    ensure_ccw,
    place_adu,
    ring_bounds,
    signed_area,
)

# 60x100 rectangle with a 20x40 notch removed from the NE (rear-right) corner.
L_RING = [(0, 0), (60, 0), (60, 60), (40, 60), (40, 100), (0, 100)]

# Wider at the front (y=0, width 60) than the rear (y=100, width 40).
TRAPEZOID_RING = [(0, 0), (60, 0), (50, 100), (10, 100)]


def test_signed_area_positive_for_ccw_ring():
    assert signed_area(L_RING) > 0


def test_ensure_ccw_reverses_cw_ring():
    cw_ring = list(reversed(L_RING))
    assert signed_area(cw_ring) < 0
    assert signed_area(ensure_ccw(cw_ring)) > 0


def test_classify_edge_direction_rectangle():
    ring = ensure_ccw([(0, 0), (60, 0), (60, 100), (0, 100)])
    assert classify_edge_direction(ring[0], ring[1]) == "front"  # bottom edge, south-facing
    assert classify_edge_direction(ring[1], ring[2]) == "side"   # east edge
    assert classify_edge_direction(ring[2], ring[3]) == "rear"   # top edge, north-facing
    assert classify_edge_direction(ring[3], ring[0]) == "side"   # west edge


def test_buildable_envelope_shrinks_rectangle_by_setbacks():
    ring = [(0, 0), (60, 0), (60, 100), (0, 100)]
    env = buildable_envelope(ring, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    minx, miny, maxx, maxy = env.bounds
    assert (minx, maxx) == (4.0, 56.0)
    assert (miny, maxy) == (0.0, 96.0)  # front_sb=0 leaves the front edge untouched


def test_buildable_envelope_excludes_notch_on_l_shaped_lot():
    env = buildable_envelope(L_RING, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    assert not env.is_empty
    minx, miny, maxx, maxy = env.bounds
    # The notch's own inner walls are boundary edges too, so their setbacks
    # cap the buildable region well short of the outer lot's east edge (60).
    assert maxx < 60


def test_buildable_envelope_empty_when_setbacks_exceed_lot():
    ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
    env = buildable_envelope(ring, front_sb=0.0, rear_sb=8.0, side_sb=8.0)
    assert env.is_empty


def test_buildable_envelope_setbacks_are_local_not_global_on_concave_lot():
    """A per-edge setback must only restrict area *near that edge* — an
    earlier implementation intersected each edge's setback line as a global
    half-plane, which is only equivalent to the correct answer for a convex
    polygon. On this L-shaped lot, the notch's own boundary edges wrongly
    reached across the whole lot and excluded (10, 90): a point >4 ft from
    every real property line and nowhere near the notch (which occupies
    x:[40,60], y:[60,100])."""
    env = buildable_envelope(L_RING, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    assert env.contains(Point(10, 90))
    # ...while the notch itself, and its own setback zone, stay excluded.
    assert not env.contains(Point(50, 80))
    assert not env.contains(Point(38, 62))


def test_buildable_envelope_dedupes_consecutive_duplicate_vertex():
    """A duplicated mid-ring vertex (not just a repeated closing point) is a
    zero-length edge that must not crash the direction/offset math."""
    ring_with_dupe = [(0, 0), (60, 0), (60, 0), (60, 100), (0, 100)]
    clean_ring = [(0, 0), (60, 0), (60, 100), (0, 100)]
    env_dupe = buildable_envelope(ring_with_dupe, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    env_clean = buildable_envelope(clean_ring, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    assert env_dupe.bounds == env_clean.bounds


def test_buildable_envelope_dedupes_repeated_closing_vertex():
    ring_closed = [*L_RING, L_RING[0]]
    env_closed = buildable_envelope(ring_closed, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    env_open = buildable_envelope(L_RING, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    assert env_closed.bounds == env_open.bounds


def test_place_adu_rear_center_on_rectangle():
    env = buildable_envelope([(0, 0), (60, 0), (60, 100), (0, 100)], front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    pos = place_adu(env, 20, 25, "rear_center")
    assert pos is not None
    x, y = pos
    assert x == 20.0  # centered: (4 + 56 - 20) / 2
    assert y == 71.0  # flush to rear: 96 - 25


def test_place_adu_rear_right_avoids_l_shaped_notch():
    env = buildable_envelope(L_RING, front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    pos_center = place_adu(env, 20, 25, "rear_center")
    pos_right = place_adu(env, 20, 25, "rear_right")
    assert pos_center is not None and pos_right is not None
    # Both must be fully contained in the envelope (the whole point of the
    # grid-search fallback) — not just placed at a naive corner that would
    # land inside the removed notch.
    from aiblueprint_mcp.irregular_lot import _rect_at
    assert env.contains(_rect_at(*pos_center, 20, 25))
    assert env.contains(_rect_at(*pos_right, 20, 25))


def test_place_adu_returns_none_when_too_big():
    env = buildable_envelope([(0, 0), (30, 0), (30, 40), (0, 40)], front_sb=0.0, rear_sb=4.0, side_sb=4.0)
    assert place_adu(env, 100, 100, "rear_center") is None


def test_ring_bounds():
    assert ring_bounds(TRAPEZOID_RING) == (0.0, 0.0, 60.0, 100.0)
