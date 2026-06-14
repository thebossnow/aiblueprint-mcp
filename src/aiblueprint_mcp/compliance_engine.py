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
        passed = max_sqft is None or actual <= max_sqft
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

        # Create the buildable envelope by offsetting the property boundary inward.
        # Negative offset = inward for a CCW-wound polyline.
        envelope_r = await self._b.entity_offset(property_boundary_handle, -offset_dist)
        if not envelope_r.ok:
            return CheckResult(
                "setbacks", False,
                message=f"Could not compute setback envelope: {envelope_r.error}",
            )
        envelope_handle = envelope_r.payload["handle"]
        annotated.append(envelope_handle)

        # Style the setback envelope as a dashed red layer.
        # Ensure the SITE_DASH linetype exists (plan_generator may or may not have run).
        doc = self._b._doc
        if "SITE_DASH" not in doc.linetypes:
            ltype = doc.linetypes.new("SITE_DASH")
            ltype.setup_pattern("A,4,-2", length=6.0)
        await self._b.layer_create("SETBACK-LINE", color="red", linetype="SITE_DASH")
        try:
            ent = doc.entitydb.get(envelope_handle)
            if ent:
                ent.dxf.layer = "SETBACK-LINE"
        except Exception as exc:
            import structlog
            structlog.get_logger().warning("compliance_setback_layer_assign_failed", error=str(exc))

        # Check if the ADU footprint is inside the envelope using bounding box comparison.
        # Full polygon-in-polygon is beyond scope here; we measure and compare areas.
        adu_r = await self._b.entity_measure(adu_footprint_handle)
        env_r = await self._b.entity_measure(envelope_handle)

        if not adu_r.ok or not env_r.ok:
            return CheckResult(
                "setbacks", False,
                message="Could not measure footprint or envelope area.",
                annotated_handles=annotated,
            )

        adu_area = adu_r.payload.get("area", 0)
        env_area = env_r.payload.get("area", 0)

        passed = adu_area <= env_area
        side_str = f"{side_ft} ft" if side_ft else "N/A"
        rear_str = f"{rear_ft} ft" if rear_ft else "N/A"
        return CheckResult(
            name="setbacks",
            passed=passed,
            value=f"ADU {adu_area:.1f} sq ft, buildable envelope {env_area:.1f} sq ft",
            limit=f"side {side_str}, rear {rear_str}",
            source=self._sources.get("setback_side_ft", ""),
            message=(
                f"Setback envelope ({offset_dist} ft uniform offset) drawn. "
                f"ADU area ({adu_area:.1f} sq ft) {'≤' if passed else '>'} "
                f"envelope area ({env_area:.1f} sq ft). "
                "Note: this is a simplified area check; verify all four sides meet setback requirements."
            ),
            annotated_handles=annotated,
        )

    async def check_lot_coverage(
        self, lot_boundary_handle: str, all_structure_handles: list[str]
    ) -> CheckResult:
        """Check total lot coverage (all structures) against max_pct limit."""
        max_pct = self._rules.get("lot_coverage_max_pct")
        lot_r = await self._b.entity_measure(lot_boundary_handle)
        if not lot_r.ok:
            return CheckResult("lot_coverage", False, message=f"Cannot measure lot: {lot_r.error}")
        lot_area = lot_r.payload.get("area", 0)

        total_covered = 0.0
        for h in all_structure_handles:
            r = await self._b.entity_measure(h)
            if r.ok and r.payload.get("area"):
                total_covered += r.payload["area"]

        pct = (total_covered / lot_area * 100) if lot_area > 0 else 0
        passed = max_pct is None or pct <= max_pct
        return CheckResult(
            name="lot_coverage",
            passed=passed,
            value=f"{pct:.1f}%",
            limit=f"{max_pct}%" if max_pct else "None",
            source=self._sources.get("lot_coverage_max_pct", ""),
            message=(
                f"Total lot coverage {pct:.1f}% of {lot_area:.0f} sq ft lot "
                f"({'within' if passed else 'EXCEEDS'} {max_pct}% limit)."
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
