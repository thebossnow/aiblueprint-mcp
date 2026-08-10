"""Minimal IFC4 export — site boundary plus building-footprint slabs/walls.

aiblueprint drawings are flat 2D DXF geometry (1 drawing unit = 1 ft, see
``parcel.py``) with no height data anywhere. This module can therefore only
honestly emit what the drawing implies: a site boundary curve, and — for
each polygon classified as a building footprint — an extruded ground slab
plus optional perimeter walls at a fixed default height. Doors, windows,
roofs, stairs, and columns are not modeled; nothing in the source drawing
implies them, so they're left out rather than fabricated.

Footprint classification is layer-name driven, matching the conventions
``plan_generator`` and ``import_boundary`` already use:

- layer name containing "lot", "parcel", or "property" -> site boundary
- layer name containing "footprint", "building", "adu", or "structure"
  -> building footprint (slab + optional walls)

Closed polylines on any other layer (setbacks, hatch outlines, etc.) are
ignored. If no layer matches the site hints, the single largest closed ring
is treated as the site boundary and every other closed ring as a footprint.

All geometry is converted from drawing feet to IFC meters so the file's
declared unit (metre) matches every coordinate — see FEET_TO_METERS.
"""

from __future__ import annotations

from dataclasses import dataclass

FEET_TO_METERS = 0.3048

DEFAULT_SLAB_THICKNESS_M = 0.15
DEFAULT_WALL_HEIGHT_M = 3.0
DEFAULT_WALL_THICKNESS_M = 0.2

_SITE_LAYER_HINTS = ("lot", "parcel", "property")
_FOOTPRINT_LAYER_HINTS = ("footprint", "building", "adu", "structure")


@dataclass
class _Ring:
    layer: str
    points: list[tuple[float, float]]  # feet, not closing duplicate


def _closed_rings(msp) -> list[_Ring]:
    rings = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points(format="xy")]
        if len(pts) >= 3:
            rings.append(_Ring(layer=e.dxf.get("layer", "0"), points=pts))
    return rings


def _signed_area(pts: list[tuple[float, float]]) -> float:
    total = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2


def _classify(rings: list[_Ring]) -> tuple[_Ring | None, list[_Ring]]:
    """Split closed rings into (site boundary, footprints).

    Layer-name hints decide first; a ring with no matching hint falls back
    to "largest unclassified ring is the site" once all hinted rings are
    assigned.
    """
    site: _Ring | None = None
    footprints: list[_Ring] = []
    unclassified: list[_Ring] = []
    for r in rings:
        layer_lc = r.layer.lower()
        if any(h in layer_lc for h in _SITE_LAYER_HINTS) and site is None:
            site = r
        elif any(h in layer_lc for h in _FOOTPRINT_LAYER_HINTS):
            footprints.append(r)
        else:
            unclassified.append(r)

    if site is None and unclassified:
        unclassified.sort(key=lambda r: abs(_signed_area(r.points)), reverse=True)
        site = unclassified.pop(0)

    footprints.extend(unclassified)
    return site, footprints


def _to_meters(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(x * FEET_TO_METERS, y * FEET_TO_METERS) for x, y in pts]


def build_ifc_bytes(
    msp,
    project_name: str = "aiblueprint export",
    slab_thickness_m: float = DEFAULT_SLAB_THICKNESS_M,
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M,
    wall_thickness_m: float = DEFAULT_WALL_THICKNESS_M,
    include_walls: bool = True,
) -> bytes:
    """Build a minimal IFC4 file from modelspace entities.

    Returns raw IFC-SPF bytes. Raises ``RuntimeError`` if the ``ifcopenshell``
    dependency is not installed (it's optional — only needed for this export
    format).
    """
    from aiblueprint_mcp.types import CommandError

    try:
        import ifcopenshell
        import ifcopenshell.api
    except ImportError as exc:
        raise CommandError(
            "IFC export requires the 'ifcopenshell' package. "
            "Install it with: uv sync --extra ifc"
        ) from exc

    run = ifcopenshell.api.run
    f = ifcopenshell.file(schema="IFC4")

    project = run("root.create_entity", f, ifc_class="IfcProject", name=project_name)
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    model_ctx = run("context.add_context", f, context_type="Model")
    body_ctx = run(
        "context.add_context", f, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=model_ctx,
    )

    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Ground Floor")
    run("aggregate.assign_object", f, relating_object=project, products=[site])
    run("aggregate.assign_object", f, relating_object=site, products=[building])
    run("aggregate.assign_object", f, relating_object=building, products=[storey])

    site_ring, footprint_rings = _classify(_closed_rings(msp))

    if site_ring is not None:
        ring_m = _to_meters(site_ring.points)
        closed = ring_m + [ring_m[0]]
        pts = [f.createIfcCartesianPoint(p) for p in closed]
        site_rep = run(
            "geometry.add_footprint_representation", f, context=body_ctx,
            curves=[f.createIfcPolyline(pts)],
        )
        run("geometry.assign_representation", f, product=site, representation=site_rep)

    for idx, ring in enumerate(footprint_rings, start=1):
        ring_m = _to_meters(ring.points)
        closed = ring_m + [ring_m[0]]
        profile_pts = [f.createIfcCartesianPoint(p) for p in closed]
        profile = f.createIfcArbitraryClosedProfileDef(
            "AREA", None, f.createIfcPolyline(profile_pts)
        )
        slab = run(
            "root.create_entity", f, ifc_class="IfcSlab", predefined_type="BASESLAB",
            name=f"Footprint {idx} slab",
        )
        slab_rep = run(
            "geometry.add_profile_representation", f, context=body_ctx, profile=profile,
            depth=slab_thickness_m, cardinal_point=None,
            placement_zx_axes=((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
        )
        run("geometry.assign_representation", f, product=slab, representation=slab_rep)
        run("spatial.assign_container", f, products=[slab], relating_structure=storey)

        if include_walls:
            n = len(ring_m)
            for i in range(n):
                p1, p2 = ring_m[i], ring_m[(i + 1) % n]
                wall = run(
                    "root.create_entity", f, ifc_class="IfcWall",
                    name=f"Footprint {idx} wall {i + 1}",
                )
                wall_rep = run(
                    "geometry.create_2pt_wall", f, element=wall, context=body_ctx,
                    p1=p1, p2=p2, elevation=0.0, height=wall_height_m,
                    thickness=wall_thickness_m,
                )
                run("geometry.assign_representation", f, product=wall, representation=wall_rep)
                run("spatial.assign_container", f, products=[wall], relating_structure=storey)

    return f.to_string().encode("utf-8")
