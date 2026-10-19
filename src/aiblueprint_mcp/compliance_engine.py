"""Compliance engine — checks a drawing against an active project profile.

All checks return a CheckResult. Optionally they annotate the DXF with
violation markers or setback lines. Checks are purely additive; they never
modify or delete design entities.

Usage:
    engine = ComplianceEngine(backend, profile_dict)
    result = await engine.check_footprint(property_boundary_handle, adu_footprint_handle)
    result = await engine.check_area(adu_footprint_handle)
    report = await engine.full_report(property_boundary_handle, adu_footprint_handle)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

# Pieces of buildable area smaller than this (sq ft) are numerical slivers
# left by the buffer, not usable land.
_MIN_PIECE_AREA = 1e-6


def _buildable_pieces(lot: Polygon, setback_ft: float) -> list[Polygon]:
    """Shrink ``lot`` inward by ``setback_ft`` and return the buildable pieces.

    Uses a negative buffer with mitre joins, so square lot corners stay
    square (a 100x100 lot with a 4 ft setback becomes exactly 92x92). The
    result can be empty (lot too narrow) or split into several pieces (a
    necked lot); pieces are returned largest first, slivers dropped.
    """
    shrunk = lot.buffer(-float(setback_ft), join_style="mitre")
    if shrunk.is_empty:
        return []
    if isinstance(shrunk, Polygon):
        geoms = [shrunk]
    elif isinstance(shrunk, MultiPolygon):
        geoms = list(shrunk.geoms)
    else:  # GeometryCollection or similar — keep only areal parts
        geoms = [g for g in getattr(shrunk, "geoms", []) if isinstance(g, Polygon)]
    pieces = [g for g in geoms if g.is_valid and g.area > _MIN_PIECE_AREA]
    return sorted(pieces, key=lambda g: g.area, reverse=True)


@dataclass
class CheckResult:
    name: str
    passed: bool
    value: Any = None
    limit: Any = None
    source: str = ""
    message: str = ""
    annotated_handles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.name,
            "passed": self.passed,
            "value": self.value,
            "limit": self.limit,
            "source": self.source,
            "message": self.message,
            "annotated_handles": self.annotated_handles,
        }


class ComplianceEngine:
    def __init__(self, backend, profile: dict[str, Any]):
        self._b = backend
        self._profile = profile
        self._rules: dict[str, Any] = profile.get("requirements", {}).get("effective", {})
        self._sources: dict[str, str] = profile.get("requirements", {}).get("sources", {})

    # ── Individual checks ──────────────────────────────────────────────

    async def check_area(self, footprint_handle: str) -> CheckResult:
        """Verify ADU footprint area against max_sqft."""
        max_sqft = self._rules.get("max_sqft")
        r = await self._b.entity_measure(footprint_handle)
        if not r.ok:
            return CheckResult("area", False, message=f"Could not measure entity: {r.error}")

        actual = r.payload.get("area")
        if actual is None:
            return CheckResult("area", False, message="Entity has no area (not a closed polyline).")

        # Convert from drawing units to sq ft — assumes 1 drawing unit = 1 ft
        if max_sqft is None:
            # No limit in the effective rules — report area but don't claim a PASS
            # against a limit that was never checked.
            return CheckResult(
                name="area",
                passed=True,
                value=round(actual, 1),
                limit=None,
                source=self._sources.get("max_sqft", ""),
                message=(
                    f"ADU footprint area {actual:.1f} sq ft — no max_sqft limit in the "
                    "effective rules, so no area limit was checked. Verify manually."
                ),
            )
        passed = actual <= max_sqft
        return CheckResult(
            name="area",
            passed=passed,
            value=round(actual, 1),
            limit=max_sqft,
            source=self._sources.get("max_sqft", ""),
            message=(
                f"ADU footprint area {actual:.1f} sq ft is within the {max_sqft} sq ft limit."
                if passed
                else f"ADU footprint area {actual:.1f} sq ft EXCEEDS the {max_sqft} sq ft limit."
            ),
        )

    async def check_setbacks(
        self, property_boundary_handle: str, adu_footprint_handle: str, annotate: bool = True
    ) -> CheckResult:
        """Verify ADU footprint is inside the setback-reduced buildable envelope.

        Creates offset polylines representing the setback lines and checks whether
        the ADU footprint is fully inside the buildable envelope.
        """
        side_ft = self._rules.get("setback_side_ft")
        rear_ft = self._rules.get("setback_rear_ft")
        annotated = []

        if side_ft is None and rear_ft is None:
            return CheckResult(
                "setbacks", True,
                message="No setback data available for this jurisdiction — verify manually.",
            )

        # Use the smaller of side/rear as a uniform offset for a conservative check.
        # A full directional setback check requires knowing which side is front/rear/side
        # (needs the street orientation). We flag this limitation.
        offset_dist = min(
            v for v in [side_ft, rear_ft] if v is not None
        )

        # Build the buildable envelope by shrinking the whole lot polygon
        # inward with a negative buffer (mitre joins keep rectangular corners
        # square). The previous approach moved each lot edge straight inward
        # (entity_offset); on narrow lots and necked/flag lots those offset
        # edges cross each other, and buffer(0) "repaired" the resulting
        # self-intersecting ring into a plausible-looking polygon, so the
        # check could pass or fail wrongly. A negative buffer is also
        # winding-independent, so CW and CCW boundaries behave the same.
        boundary_r = await self._b.entity_get(property_boundary_handle)
        if not boundary_r.ok or not boundary_r.payload.get("points"):
            return CheckResult(
                "setbacks", False,
                message=f"Could not read property boundary geometry: {boundary_r.error or 'no points'}",
            )
        side_str = f"{side_ft} ft" if side_ft else "N/A"
        rear_str = f"{rear_ft} ft" if rear_ft else "N/A"
        limit_str = f"side {side_str}, rear {rear_str}"
        source = self._sources.get("setback_side_ft", "")

        boundary_pts = [(float(p[0]), float(p[1])) for p in boundary_r.payload["points"]]
        lot = Polygon(boundary_pts) if len(boundary_pts) >= 3 else Polygon()
        if lot.is_empty or not lot.is_valid or lot.area <= 0:
            return CheckResult(
                "setbacks", False,
                limit=limit_str,
                source=source,
                message=(
                    "Property boundary is not a valid closed polygon "
                    "(too few points, zero area, or edges that cross) — "
                    "cannot compute a setback envelope."
                ),
            )

        pieces = _buildable_pieces(lot, offset_dist)
        if not pieces:
            return CheckResult(
                name="setbacks",
                passed=False,
                value="ADU footprint, buildable envelope 0.0 sq ft",
                limit=limit_str,
                source=source,
                message=(
                    f"No buildable area: a {offset_dist} ft setback from every lot line "
                    "leaves nothing of this lot to build on (the lot is too narrow). "
                    "Setback violation."
                ),
            )

        # Draw one dashed red setback outline per buildable piece (largest
        # first, so annotated_handles[0] stays the main envelope).
        # Ensure the SITE_DASH linetype exists (plan_generator may or may not have run).
        doc = self._b._doc
        if "SITE_DASH" not in doc.linetypes:
            ltype = doc.linetypes.new("SITE_DASH")
            ltype.setup_pattern("A,4,-2", length=6.0)
        await self._b.layer_create("SETBACK-LINE", color="red", linetype="SITE_DASH")
        for piece in pieces:
            ring = list(orient(piece, sign=1.0).exterior.coords)[:-1]
            drawn = await self._b.create_polyline(ring, closed=True, layer="SETBACK-LINE")
            if drawn.ok:
                annotated.append(drawn.payload["handle"])

        # Determine pass/fail by actual polygon containment: the whole ADU
        # footprint must lie inside ONE buildable piece (edges included,
        # within a small tolerance for round-off). A necked lot can split
        # into separate pieces, and a footprint bridging them across the
        # unbuildable neck is a violation. A bounding box would pass an ADU
        # sitting in the missing corner of an L-shaped lot, and the old
        # area-only comparison passed any small ADU anywhere.
        adu_r = await self._b.entity_measure(adu_footprint_handle)
        adu_area = adu_r.payload.get("area", 0) if adu_r.ok else 0
        env_area = sum(piece.area for piece in pieces)

        adu_pts = self._entity_points(adu_footprint_handle)
        adu_poly = Polygon(adu_pts).buffer(0) if adu_pts and len(adu_pts) >= 3 else None

        if adu_poly is not None and not adu_poly.is_empty:
            passed = any(piece.buffer(1e-6).covers(adu_poly) for piece in pieces)
            if passed:
                detail = "ADU footprint is fully within the buildable envelope."
            elif len(pieces) > 1 and unary_union(pieces).buffer(1e-6).intersects(adu_poly):
                detail = (
                    f"The setbacks split this lot into {len(pieces)} separate buildable areas, "
                    "and the ADU footprint does not fit inside any single one of them — "
                    "setback violation."
                )
            else:
                detail = "ADU footprint extends OUTSIDE the buildable envelope — setback violation."
        else:
            # Fallback when geometry isn't a readable polyline: area comparison
            # against the largest buildable piece.
            largest = pieces[0].area
            passed = adu_area <= largest
            detail = (
                f"Area check only: ADU {adu_area:.1f} sq ft "
                f"{'≤' if passed else '>'} envelope {largest:.1f} sq ft. "
                "Verify all four sides meet setback requirements."
            )

        return CheckResult(
            name="setbacks",
            passed=passed,
            value=f"ADU {adu_area:.1f} sq ft, buildable envelope {env_area:.1f} sq ft",
            limit=limit_str,
            source=source,
            message=f"Setback envelope ({offset_dist} ft uniform offset) drawn. {detail}",
            annotated_handles=annotated,
        )

    def _entity_points(self, handle: str) -> list[tuple[float, float]] | None:
        """Return an LWPOLYLINE's 2D vertices, or None if not readable."""
        try:
            ent = self._b._doc.entitydb.get(handle)
            if ent is None or ent.dxftype() != "LWPOLYLINE":
                return None
            return [(float(p[0]), float(p[1])) for p in ent.get_points(format="xy")]
        except Exception:
            return None

    async def check_lot_coverage(
        self, lot_boundary_handle: str, all_structure_handles: list[str]
    ) -> CheckResult:
        """Check total lot coverage (all structures) against max_pct limit."""
        max_pct = self._rules.get("lot_coverage_max_pct")
        lot_r = await self._b.entity_measure(lot_boundary_handle)
        if not lot_r.ok:
            return CheckResult("lot_coverage", False, message=f"Cannot measure lot: {lot_r.error}")
        lot_area = lot_r.payload.get("area", 0)

        # De-duplicate handles and never count the lot boundary itself as a
        # structure — both mistakes would silently distort the coverage figure.
        seen: set[str] = set()
        skipped: list[str] = []
        total_covered = 0.0
        for h in all_structure_handles:
            if h == lot_boundary_handle or h in seen:
                continue
            seen.add(h)
            r = await self._b.entity_measure(h)
            area = r.payload.get("area") if r.ok else None
            if area:
                total_covered += area
            else:
                # A structure we couldn't measure (open polyline, non-area
                # entity, bad handle) would otherwise silently undercount.
                skipped.append(h)

        pct = (total_covered / lot_area * 100) if lot_area > 0 else 0
        passed = max_pct is None or pct <= max_pct
        skip_note = (
            f" {len(skipped)} structure(s) could not be measured and were excluded "
            f"(coverage may be understated): {', '.join(skipped)}."
            if skipped else ""
        )
        return CheckResult(
            name="lot_coverage",
            passed=passed,
            value=f"{pct:.1f}%",
            limit=f"{max_pct}%" if max_pct else "None",
            source=self._sources.get("lot_coverage_max_pct", ""),
            message=(
                f"Total lot coverage {pct:.1f}% of {lot_area:.0f} sq ft lot "
                f"({'within' if passed else 'EXCEEDS'} {max_pct}% limit).{skip_note}"
            ),
        )

    async def check_height(self, elevation_handle: str, ridge_y: float, grade_y: float) -> CheckResult:
        """Check building height (ridge - grade) against max_height_ft.

        Since height isn't directly readable from a polyline entity, the caller
        passes the Y-coordinate of the ridge and the Y-coordinate of average grade
        on the elevation drawing (1 drawing unit = 1 ft).
        """
        height = abs(ridge_y - grade_y)
        max_h = self._rules.get("max_height_ft")
        passed = max_h is None or height <= max_h
        return CheckResult(
            name="height",
            passed=passed,
            value=round(height, 2),
            limit=max_h,
            source=self._sources.get("max_height_ft", ""),
            message=(
                f"Building height {height:.2f} ft "
                f"({'≤' if passed else 'EXCEEDS'} {max_h} ft limit)."
                if max_h else
                f"Building height {height:.2f} ft — no height limit in effective rules."
            ),
        )

    # ── Full report ────────────────────────────────────────────────────

    async def full_report(
        self,
        property_boundary_handle: str | None = None,
        adu_footprint_handle: str | None = None,
        all_structure_handles: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run all applicable checks and return a consolidated compliance report."""
        checks: list[CheckResult] = []

        if adu_footprint_handle:
            checks.append(await self.check_area(adu_footprint_handle))

        if property_boundary_handle and adu_footprint_handle:
            checks.append(await self.check_setbacks(property_boundary_handle, adu_footprint_handle))

        if property_boundary_handle and all_structure_handles:
            checks.append(await self.check_lot_coverage(property_boundary_handle, all_structure_handles))

        passed_all = all(c.passed for c in checks)
        return {
            "passed": passed_all,
            "summary": f"{'All checks PASSED' if passed_all else 'One or more checks FAILED'} ({len(checks)} checks run).",
            "checks": [c.to_dict() for c in checks],
            "warnings": self._profile.get("warnings", []),
            "notes": self._profile.get("notes", []),
            "disclaimers": self._profile.get("disclaimers", []),
        }
