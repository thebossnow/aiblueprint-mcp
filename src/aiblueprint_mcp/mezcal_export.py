"""Mezcal bundle export — one JSON file merging the site boundary, setback
envelope, building footprints, resolved zoning requirements, and compliance
report, for the Pascal editor plugin (thebossnow/mezcal_adapter_plugin) to
import as a native `mezcal:site-plan` scene node.

Boundary-vs-footprint classification reuses the exact layer-name convention
``ifc_export.py`` already established (``_closed_rings``/``_classify``), so
this export and the IFC export never disagree about which ring is the site
and which are structures.

Everything here stays in feet (1 drawing unit = 1 ft, matching every other
aiblueprint-mcp export) — the Pascal plugin converts to meters at its own
render boundary, not this one.
"""

from __future__ import annotations

import json
from typing import Any

from aiblueprint_mcp.compliance_engine import ComplianceEngine
from aiblueprint_mcp.ifc_export import _classify, _closed_rings
from aiblueprint_mcp.parcel import inward_distance
from aiblueprint_mcp.types import CommandError, CommandResult

DEFAULT_FOOTPRINT_HEIGHT_FT = 12.0

_ADU_LAYER_HINTS = ("adu",)
_EXISTING_LAYER_HINTS = ("existing", "main", "primary", "house")

# aiblueprint-mcp's CheckResult.name -> the plugin schema's compliance key.
# "lot_coverage" is the only name that doesn't match 1:1 (schema.ts calls it
# "coverage" to match the plugin's shorter `maxCoveragePct` field).
_CHECK_NAME_TO_MEZCAL_KEY = {
    "area": "area",
    "setbacks": "setbacks",
    "lot_coverage": "coverage",
    "height": "height",
}


def _footprint_kind(layer: str) -> str:
    lc = layer.lower()
    if any(h in lc for h in _ADU_LAYER_HINTS):
        return "adu"
    if any(h in lc for h in _EXISTING_LAYER_HINTS):
        return "existing"
    return "proposed"


def _footprint_label(layer: str) -> str:
    return layer.replace("_", " ").replace("-", " ").title() or "Structure"


def _round_points(points: list[tuple[float, float]]) -> list[list[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in points]


async def build_mezcal_export(backend, profile: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble the ``MezcalExportV1`` bundle from the current drawing, plus
    an optional resolved project profile (``ProjectSession.resolved_profile()``).

    Pure data assembly plus the same read-only ``entity_offset`` /
    ``ComplianceEngine`` calls the ``compliance`` tool already makes — this
    function creates no new *design* geometry, only (when a profile is
    available) the same setback-envelope entity ``compliance.check_setbacks``
    would create, so re-running the export is idempotent w.r.t. the drawing's
    intent even though it does add one polyline per call.

    Raises ``CommandError`` if the drawing has no closed boundary ring —
    callers should surface that as a normal tool error, not a crash.
    """
    msp = backend._msp
    site_ring, footprint_rings = _classify(_closed_rings(msp))
    if site_ring is None:
        raise CommandError(
            "No closed boundary polyline found (layer name containing "
            "'lot'/'parcel'/'property', or — failing that — the largest "
            "closed ring on the drawing). Draw or entity.import_boundary a "
            "lot boundary first."
        )

    bundle: dict[str, Any] = {
        "version": 1,
        "meta": {"generatedBy": "aiblueprint-mcp"},
        "boundary": _round_points(site_ring.points),
        "footprints": [
            {
                "id": ring.handle or f"footprint-{i}",
                "label": _footprint_label(ring.layer),
                "kind": _footprint_kind(ring.layer),
                "points": _round_points(ring.points),
                "heightFt": DEFAULT_FOOTPRINT_HEIGHT_FT,
            }
            for i, ring in enumerate(footprint_rings, start=1)
        ],
    }

    if not profile:
        return bundle

    rules = profile.get("requirements", {}).get("effective", {})
    requirements = {
        k: v
        for k, v in {
            "setbackFrontFt": rules.get("setback_front_ft"),
            "setbackRearFt": rules.get("setback_rear_ft"),
            "setbackSideFt": rules.get("setback_side_ft"),
            "maxHeightFt": rules.get("max_height_ft"),
            "maxCoveragePct": rules.get("lot_coverage_max_pct"),
            "maxSqft": rules.get("max_sqft"),
        }.items()
        if v is not None
    }
    if not requirements:
        # No county/city ever resolved (questionnaire never run, or run with
        # no jurisdiction match) — every rule is None. Computing a setback
        # envelope or compliance report against an empty rule set would make
        # `check_setbacks`/`check_area`/etc. vacuously return `passed=True`
        # ("no data available"), which would render as a false "PASS" in the
        # plugin. Stop here rather than manufacture that.
        return bundle
    bundle["requirements"] = requirements

    # Nothing in a flat 2D DXF records real building height (see
    # ifc_export.py's docstring) — an ADU footprint gets the jurisdiction's
    # max allowed height as an honest planning default, not a measurement.
    max_height_ft = rules.get("max_height_ft")
    if max_height_ft:
        for footprint in bundle["footprints"]:
            if footprint["kind"] == "adu":
                footprint["heightFt"] = max_height_ft

    side_ft = rules.get("setback_side_ft")
    rear_ft = rules.get("setback_rear_ft")
    offset_candidates = [v for v in (side_ft, rear_ft) if v is not None]
    if offset_candidates and site_ring.handle:
        # Uniform-offset simplification (smaller of side/rear) — same one
        # compliance.check_setbacks uses. `inward_distance` picks the correct
        # signed distance from the boundary's actual winding — entity_offset's
        # positive/negative distance is only "inward" for a CCW-wound ring
        # (see backend.py's `_offset_polyline` docstring), and nothing
        # upstream guarantees CCW (create_rectangle always is; import_boundary
        # / GeoJSON boundaries can be either — see parcel.py).
        signed_offset = inward_distance(site_ring.points, min(offset_candidates))
        envelope_result = await backend.entity_offset(site_ring.handle, signed_offset)
        if envelope_result.ok:
            envelope_entity = backend._doc.entitydb.get(envelope_result.payload["handle"])
            if envelope_entity is not None:
                bundle["setbackEnvelope"] = _round_points(
                    [(float(p[0]), float(p[1])) for p in envelope_entity.get_points(format="xy")]
                )

    adu_ring = next((r for r in footprint_rings if _footprint_kind(r.layer) == "adu"), None)
    if site_ring.handle and adu_ring is not None and adu_ring.handle:
        engine = ComplianceEngine(backend, profile)
        report = await engine.full_report(
            property_boundary_handle=site_ring.handle,
            adu_footprint_handle=adu_ring.handle,
            all_structure_handles=[r.handle for r in footprint_rings if r.handle],
        )
        compliance: dict[str, Any] = {"overall": "pass" if report["passed"] else "fail"}
        for check in report["checks"]:
            key = _CHECK_NAME_TO_MEZCAL_KEY.get(check["check"])
            if key:
                compliance[key] = {"ok": check["passed"], "message": check["message"]}
        bundle["compliance"] = compliance

    warnings = profile.get("warnings", [])
    notes = profile.get("notes", [])
    if warnings:
        bundle["warnings"] = warnings
    if notes:
        bundle["notes"] = notes

    return bundle


async def export_mezcal(backend, profile: dict[str, Any] | None, path: str | None = None) -> CommandResult:
    """Build the bundle and write it into the workspace — the `project`
    tool's `export_mezcal` operation. Mirrors `SitePlanGenerator.generate()`/
    `RoomFloorPlanGenerator.generate()`: takes the backend (+ here, the
    resolved profile) and returns a `CommandResult`, so `server.py`'s
    handler is a two-line `_result(await export_mezcal(...))` like theirs.
    """
    try:
        bundle = await build_mezcal_export(backend, profile)
    except CommandError as ce:
        return CommandResult(ok=False, error=str(ce))

    st = backend._state()
    target = path or f"{st.name}.mezcal.json"
    resolved = backend.config.resolve_path(target)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(bundle, indent=2))
    return CommandResult(
        ok=True,
        payload={
            "path": str(resolved),
            "footprint_count": len(bundle["footprints"]),
            "has_requirements": "requirements" in bundle,
            "has_compliance": "compliance" in bundle,
        },
    )
