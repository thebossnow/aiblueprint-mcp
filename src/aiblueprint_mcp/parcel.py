"""Irregular parcel boundaries from GeoJSON or survey points.

The site-plan tooling historically assumed rectangular lots. Real parcels are
rarely rectangular, so this module turns two common boundary sources into a
plain ring of ``(x, y)`` vertices that the backend can draw as a closed
LWPOLYLINE and the compliance engine can measure/offset like any other boundary.

Coordinates are treated as planar drawing units (1 unit = 1 ft by convention).
No projection/georeferencing is performed — callers supply coordinates already
in the drawing's coordinate space.
"""

from __future__ import annotations

from typing import Any

Point = tuple[float, float]


def boundary_ring(
    *, points: Any | None = None, geojson: Any | None = None
) -> list[Point]:
    """Resolve a boundary ring from exactly one source.

    Pass either ``points`` (a list of ``[x, y]`` survey vertices) or ``geojson``
    (a Geometry, Feature, or FeatureCollection containing a Polygon). Returns an
    open ring (no repeated closing vertex) of at least three vertices.

    Raises ``ValueError`` with an LLM-readable message on bad input.
    """
    if (points is None) == (geojson is None):
        raise ValueError("Provide exactly one of 'points' or 'geojson'.")
    ring = parse_survey_points(points) if points is not None else parse_geojson(geojson)
    return ring


def parse_survey_points(points: Any) -> list[Point]:
    """Normalize a list of survey vertices into a clean ring.

    Accepts ``[[x, y], ...]`` or ``[{"x": .., "y": ..}, ...]``. Drops a trailing
    vertex that duplicates the first (so callers may pass closed or open rings).
    """
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise ValueError("Survey points must be a list of at least 3 [x, y] vertices.")
    ring: list[Point] = []
    for i, p in enumerate(points):
        if isinstance(p, dict):
            try:
                x, y = float(p["x"]), float(p["y"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Point {i} must have numeric 'x' and 'y'.") from None
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError):
                raise ValueError(f"Point {i} must be a numeric [x, y] pair.") from None
        else:
            raise ValueError(f"Point {i} must be an [x, y] pair or {{x, y}} object.")
        ring.append((x, y))
    return _dedupe_closing(ring)


def parse_geojson(obj: Any) -> list[Point]:
    """Extract the first Polygon's exterior ring from a GeoJSON object.

    Accepts a Polygon/MultiPolygon Geometry, a Feature wrapping one, or a
    FeatureCollection (the first polygonal feature wins). Inner rings (holes)
    are ignored — only the parcel's outer boundary is returned.
    """
    geom = _find_polygon_geometry(obj)
    if geom is None:
        raise ValueError("GeoJSON contains no Polygon or MultiPolygon geometry.")
    coords = geom.get("coordinates")
    if geom.get("type") == "MultiPolygon":
        if not coords:
            raise ValueError("MultiPolygon has no polygons.")
        coords = coords[0]  # first polygon
    if not isinstance(coords, (list, tuple)) or not coords:
        raise ValueError("Polygon has no coordinate rings.")
    exterior = coords[0]
    ring: list[Point] = []
    for i, c in enumerate(exterior):
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            raise ValueError(f"Polygon vertex {i} must be an [x, y] pair.")
        ring.append((float(c[0]), float(c[1])))
    ring = _dedupe_closing(ring)
    if len(ring) < 3:
        raise ValueError("Polygon exterior ring needs at least 3 distinct vertices.")
    return ring


def _find_polygon_geometry(obj: Any) -> dict | None:
    if not isinstance(obj, dict):
        raise ValueError("GeoJSON must be an object.")
    t = obj.get("type")
    if t in ("Polygon", "MultiPolygon"):
        return obj
    if t == "Feature":
        return _find_polygon_geometry(obj.get("geometry") or {})
    if t == "FeatureCollection":
        for feat in obj.get("features") or []:
            geom = (feat or {}).get("geometry") or {}
            if geom.get("type") in ("Polygon", "MultiPolygon"):
                return geom
        return None
    return None


def _dedupe_closing(ring: list[Point]) -> list[Point]:
    """Drop a trailing vertex equal to the first (GeoJSON rings repeat it)."""
    if len(ring) >= 2 and ring[0] == ring[-1]:
        return ring[:-1]
    return ring


def ring_signed_area(ring: list[Point]) -> float:
    """Signed polygon area via the shoelace formula.

    Positive for a counter-clockwise ring, negative for clockwise, zero for a
    degenerate (self-intersecting or zero-area) ring. Unlike ``ring_area``,
    which discards the sign, this is what tells you which way a ring winds —
    needed because nothing in this module normalizes winding direction.
    ``create_rectangle`` always produces CCW rectangles (by construction of
    its point order), but ``boundary_ring``/``parse_survey_points``/
    ``parse_geojson`` above pass survey points or GeoJSON coordinates through
    as-given: a caller can hand either winding, and this module does not
    reorder them. Code that offsets a ring "inward" (see ``inward_distance``)
    must account for that instead of assuming CCW.
    """
    n = len(ring)
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def ring_area(ring: list[Point]) -> float:
    """Absolute polygon area via the shoelace formula."""
    return abs(ring_signed_area(ring))


def inward_distance(ring: list[Point], distance: float) -> float:
    """Sign ``distance`` so that offsetting ``ring`` by the result moves its
    edges toward the interior, regardless of the ring's winding direction.

    ``AIBlueprintBackend.entity_offset``'s positive/negative distance offsets
    "to the left of each directed edge" (see its docstring in backend.py) —
    which is the *interior* side only for a counter-clockwise-wound ring, and
    the *exterior* side for a clockwise one. Since nothing upstream of this
    guarantees a winding direction (see ``ring_signed_area``), callers that
    want a setback/buildable envelope — not just "some offset" — need to
    check the ring's actual winding rather than hardcode a sign. A
    zero-area ring (degenerate input) passes ``distance`` through unchanged;
    the offset itself will fail on that input regardless.
    """
    signed_area = ring_signed_area(ring)
    if signed_area == 0:
        return distance
    return distance if signed_area > 0 else -distance


def ring_perimeter(ring: list[Point]) -> float:
    n = len(ring)
    total = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    return total


def bounding_box(ring: list[Point]) -> dict[str, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}
