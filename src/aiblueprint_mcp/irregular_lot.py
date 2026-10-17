"""Geometry helpers for site plans on non-rectangular (irregular) lots.

Real parcels are rarely perfect rectangles. Given an arbitrary boundary ring,
this module:

  - classifies each edge as front/rear/side by the compass direction its
    outward normal points (matching the rectangular generator's convention:
    front is south-facing/-Y, rear is north-facing/+Y, side is east/west)
  - computes the buildable envelope: the lot polygon with a local setback
    strip removed along each edge, sized by that edge's classification.
    Each strip hugs only its own edge (extended slightly past both endpoints
    so adjacent strips overlap and miter cleanly at shared corners, convex
    or reflex) rather than acting as a global constraint — a per-edge
    setback is inherently a *local* restriction near that particular
    property line, not a line extending infinitely across the whole lot.
    (An earlier version of this module intersected global inward half-planes
    instead: mathematically equivalent to the correct answer for a convex
    polygon, but wrong on a concave one — a single edge near a notch could
    reach across and wrongly exclude buildable area on the far side of the
    lot that has nothing to do with it.) Naively moving each vertex inward
    along its edge normals and reconnecting (the approach ``entity_offset``
    uses for a uniform offset) self-intersects on reflex corners, so that
    isn't reused here either.
  - searches for a placement of a fixed-size ADU footprint inside that
    envelope, preferring the position closest to the requested side (rear
    center/left/right), falling back to a bounded grid search when the
    envelope isn't a clean rectangle (e.g. an L-shaped notch).

Requires shapely for robust polygon boolean operations (GEOS-backed) rather
than hand-rolled polygon clipping, which is exactly the kind of code that
looks fine on a rectangle and quietly produces garbage on a concave lot.
"""

from __future__ import annotations

import math
from typing import Literal

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

Point = tuple[float, float]
EdgeDirection = Literal["front", "rear", "side"]


def _dedupe_consecutive(ring: list[Point]) -> list[Point]:
    """Drop consecutive duplicate vertices (including the wrap-around edge).

    A repeated vertex — whether a duplicated closing point or duplicate
    survey data mid-ring — produces a zero-length edge, which every
    direction/offset computation below divides by.
    """
    if not ring:
        return ring
    out = [ring[0]]
    for p in ring[1:]:
        if p != out[-1]:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def signed_area(ring: list[Point]) -> float:
    """Shoelace signed area — positive for a counter-clockwise ring."""
    n = len(ring)
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def ensure_ccw(ring: list[Point]) -> list[Point]:
    """Return the ring in counter-clockwise order (reversed if needed).

    Downstream edge-normal math assumes a consistent winding order.
    """
    return ring if signed_area(ring) > 0 else list(reversed(ring))


def classify_edge_direction(a: Point, b: Point) -> EdgeDirection:
    """Classify edge a->b by the nearest cardinal direction of its outward
    normal, assuming ``a``/``b`` come from a CCW-ordered ring.

    Buckets span 90 degrees centered on each cardinal direction: south-facing
    edges (front) and north-facing edges (rear) get their own bucket; east-
    and west-facing edges both count as "side" since the generator applies
    the same setback distance to either side of the lot.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return "side"
    ux, uy = dx / length, dy / length
    nx, ny = uy, -ux  # outward normal for a CCW ring
    angle = math.degrees(math.atan2(ny, nx))
    if -135 <= angle < -45:
        return "front"
    if 45 <= angle < 135:
        return "rear"
    return "side"


def _edge_setback_strip(a: Point, b: Point, distance: float) -> Polygon:
    """The local region within ``distance`` of edge a->b, on its inward side.

    Extended past both endpoints by (distance + a fixed margin) along the
    edge's own direction so that neighboring edges' strips overlap at a
    shared corner rather than leaving a sliver ungoverned by either one —
    correct regardless of whether that corner is convex or reflex.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    margin = distance + 1.0
    ea = (a[0] - ux * margin, a[1] - uy * margin)
    eb = (b[0] + ux * margin, b[1] + uy * margin)
    # single_sided buffer: positive distance = left side of a->b's direction.
    # Our outward normal (uy, -ux) is the *right* side, so the inward side —
    # the side we want to strip away — is the left side, i.e. positive here.
    return LineString([ea, eb]).buffer(distance, single_sided=True, cap_style="flat")


def buildable_envelope(
    ring: list[Point], *, front_sb: float, rear_sb: float, side_sb: float
) -> Polygon:
    """Region of ``ring`` satisfying every edge's directional setback.

    Pass ``front_sb=0`` to exclude the front setback from the constraint
    (used for ADU auto-placement, matching the rectangular generator's
    convention that only side/rear setbacks constrain the ADU — front
    setback is drawn as a primary-structure reference line only).
    """
    ring = ensure_ccw(_dedupe_consecutive(ring))
    lot = Polygon(ring)
    if not lot.is_valid:
        lot = make_valid(lot)

    n = len(ring)
    setbacks = {"front": front_sb, "rear": rear_sb, "side": side_sb}
    strips = []
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        sb = setbacks[classify_edge_direction(a, b)]
        if sb <= 0:
            continue
        strips.append(_edge_setback_strip(a, b, sb))

    if not strips:
        return lot
    return lot.difference(unary_union(strips))


def _rect_at(x: float, y: float, w: float, d: float) -> Polygon:
    return Polygon([(x, y), (x + w, y), (x + w, y + d), (x, y + d)])


def place_adu(
    envelope: Polygon,
    adu_w: float,
    adu_d: float,
    position: Literal["rear_center", "rear_left", "rear_right"],
    *,
    grid_steps: int = 24,
) -> Point | None:
    """Find an (x, y) for the ADU's south-west corner fully inside ``envelope``.

    Tries the anchored position matching the rectangular generator's
    semantics first (cheap, and correct whenever the envelope's bounding box
    is itself the buildable area). Falls back to a bounded grid search over
    the envelope's bounding box for lot shapes where that doesn't fit — e.g.
    an L-shaped notch clipping the naive anchor — preferring the valid
    position closest to the original anchor. Returns ``None`` if no position
    fits anywhere in the envelope.
    """
    if envelope.is_empty:
        return None
    minx, miny, maxx, maxy = envelope.bounds
    if adu_w > maxx - minx or adu_d > maxy - miny:
        return None

    if position == "rear_left":
        anchor_x = minx
    elif position == "rear_right":
        anchor_x = maxx - adu_w
    else:
        anchor_x = (minx + maxx - adu_w) / 2
    anchor_y = maxy - adu_d

    if envelope.contains(_rect_at(anchor_x, anchor_y, adu_w, adu_d)):
        return (anchor_x, anchor_y)

    span_x = maxx - minx - adu_w
    span_y = maxy - miny - adu_d
    best: Point | None = None
    best_dist = float("inf")
    for iy in range(grid_steps + 1):
        y = miny + span_y * iy / grid_steps if grid_steps else miny
        for ix in range(grid_steps + 1):
            x = minx + span_x * ix / grid_steps if grid_steps else minx
            if envelope.contains(_rect_at(x, y, adu_w, adu_d)):
                dist = (x - anchor_x) ** 2 + (y - anchor_y) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best = (x, y)
    return best


def ring_bounds(ring: list[Point]) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of ``ring``.

    Named distinctly from parcel.bounding_box (which returns a dict) so the
    two same-purpose helpers in sibling modules can't be swapped by mistake.
    """
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))
