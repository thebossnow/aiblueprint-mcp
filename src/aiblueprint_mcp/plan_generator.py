"""Auto site-plan generator — builds a code-compliant DXF from project answers.

Given lot dimensions and a resolved ProjectSession, generates:
  LOT-LINE      — property boundary
  SETBACK-LINE  — dashed setback lines (side, rear, front-reference)
  ADU-FOOTPRINT — ADU footprint with cross-hatch fill
  DIMENSION     — aligned dimensions (lot, ADU, setbacks)
  ANNOTATION    — text labels and street note
  NORTH-ARROW   — simple north arrow outside the lot
  TITLE-BLOCK   — jurisdiction summary and permit disclaimer
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from .backend import AIBlueprintBackend
from .project_state import ProjectSession
from .types import CommandError, CommandResult


@dataclass
class SitePlanConfig:
    # Rectangular lot source: both lot_width and lot_depth.
    lot_width: float | None = None
    lot_depth: float | None = None
    # Irregular lot source (pick exactly one, instead of lot_width/lot_depth):
    # an explicit ring of [x, y] vertices, or the handle of a boundary already
    # drawn via entity.import_boundary.
    lot_boundary: list[list[float]] | None = None
    lot_boundary_handle: str | None = None
    adu_width: float | None = None
    adu_depth: float | None = None
    adu_position: Literal["rear_center", "rear_left", "rear_right"] = "rear_center"
    draw_name: str | None = None


@dataclass
class _Dims:
    lot_w: float
    lot_d: float
    adu_w: float
    adu_d: float
    side_sb: float
    rear_sb: float
    front_sb: float
    adu_x: float
    adu_y: float

    @property
    def adu_x2(self) -> float:
        return self.adu_x + self.adu_w

    @property
    def adu_y2(self) -> float:
        return self.adu_y + self.adu_d

    @property
    def adu_cx(self) -> float:
        return self.adu_x + self.adu_w / 2

    @property
    def adu_cy(self) -> float:
        return self.adu_y + self.adu_d / 2

    @property
    def adu_area(self) -> float:
        return self.adu_w * self.adu_d

    @property
    def lot_area(self) -> float:
        return self.lot_w * self.lot_d


class SitePlanGenerator:
    _DIM = {"dimtxt": 1.5, "dimasz": 1.0, "dimlunit": 2}

    def __init__(self, backend: AIBlueprintBackend, session: ProjectSession):
        self._b = backend
        self._s = session

    async def generate(self, cfg: SitePlanConfig) -> CommandResult:
        try:
            if cfg.adu_position not in ("rear_center", "rear_left", "rear_right"):
                raise CommandError(
                    f"adu_position must be one of rear_center, rear_left, rear_right "
                    f"(got {cfg.adu_position!r})."
                )
            ring = await self._resolve_irregular_boundary(cfg)
            if ring is not None:
                return await self._generate_irregular(cfg, ring)
            return await self._generate_rectangular(cfg)
        except CommandError as ce:
            return CommandResult(ok=False, error=str(ce))
        except Exception as ex:
            return CommandResult(ok=False, error=f"Plan generation failed: {ex}")

    async def _resolve_irregular_boundary(self, cfg: SitePlanConfig) -> list[tuple[float, float]] | None:
        """Resolve an irregular lot source into a ring, or None for the
        rectangular (lot_width/lot_depth) path.

        Exactly one lot source must be given: (lot_width and lot_depth),
        lot_boundary, or lot_boundary_handle.
        """
        try:
            validate_lot_source(cfg.lot_width, cfg.lot_depth, cfg.lot_boundary, cfg.lot_boundary_handle)
        except ValueError as ex:
            raise CommandError(str(ex)) from None

        from .parcel import boundary_ring

        if cfg.lot_boundary is not None:
            try:
                return boundary_ring(points=cfg.lot_boundary)
            except ValueError as ex:
                raise CommandError(str(ex)) from None
        if cfg.lot_boundary_handle is not None:
            r = await self._b.entity_get(cfg.lot_boundary_handle)
            if not r.ok:
                raise CommandError(f"Could not resolve lot_boundary_handle: {r.error}")
            if r.payload.get("type") != "LWPOLYLINE" or not r.payload.get("closed"):
                raise CommandError("lot_boundary_handle must reference a closed LWPOLYLINE boundary.")
            try:
                return boundary_ring(points=r.payload["points"])
            except ValueError as ex:
                raise CommandError(str(ex)) from None
        return None

    async def _generate_rectangular(self, cfg: SitePlanConfig) -> CommandResult:
        profile = self._s.resolved_profile()
        rules = _get_rules(profile)

        side_sb = float(rules.get("setback_side_ft") or 4.0)
        rear_sb = float(rules.get("setback_rear_ft") or 4.0)
        front_sb = 20.0
        max_sqft = float(rules.get("max_sqft") or 1200.0)

        target = float((profile.get("project") or {}).get("target_sqft") or 500.0)
        target = min(target, max_sqft)

        adu_w, adu_d = _compute_adu_size(target, cfg, cfg.lot_width, side_sb)
        adu_x = _compute_adu_x(cfg.adu_position, cfg.lot_width, adu_w, side_sb)
        adu_y = cfg.lot_depth - rear_sb - adu_d

        dims = _Dims(
            lot_w=cfg.lot_width, lot_d=cfg.lot_depth,
            adu_w=adu_w, adu_d=adu_d,
            side_sb=side_sb, rear_sb=rear_sb, front_sb=front_sb,
            adu_x=adu_x, adu_y=adu_y,
        )

        buildable_w = cfg.lot_width - 2 * side_sb
        if adu_w > buildable_w:
            raise CommandError(
                f"ADU width {adu_w:.1f} ft exceeds buildable width "
                f"{buildable_w:.1f} ft (lot {cfg.lot_width} ft minus "
                f"{side_sb} ft setbacks each side)."
            )
        if adu_y < 0:
            raise CommandError(
                f"ADU depth {adu_d:.1f} ft + rear setback {rear_sb:.1f} ft "
                f"exceeds lot depth {cfg.lot_depth:.1f} ft."
            )

        r = await self._b.drawing_create(cfg.draw_name or "site_plan")
        if not r.ok:
            raise CommandError(f"Could not create drawing: {r.error}")
        handle = r.payload["handle"]

        # Group the whole plan into one undo checkpoint instead of one per entity.
        with self._b.batch():
            await self._setup_layers()
            lot_handle = await self._draw_lot(dims)
            await self._draw_setback_lines(dims)
            adu_handle = await self._draw_adu(dims)
            await self._add_dimensions(dims)
            await self._add_annotations(dims, profile)
            await self._add_north_arrow(dims)
            await self._add_title_block(dims, profile)

        loc = profile.get("location") or {}
        city = loc.get("city") or ""
        county = loc.get("county") or ""
        jurisdiction = ", ".join(p for p in [city, county, "CA"] if p)

        return CommandResult(ok=True, payload={
            "handle": handle,
            "drawing_name": cfg.draw_name or "site_plan",
            "lot_shape": "rectangular",
            "lot_handle": lot_handle,
            "adu_handle": adu_handle,
            "lot_width_ft": dims.lot_w,
            "lot_depth_ft": dims.lot_d,
            "lot_area_sqft": round(dims.lot_area),
            "adu_width_ft": round(dims.adu_w, 1),
            "adu_depth_ft": round(dims.adu_d, 1),
            "adu_area_sqft": round(dims.adu_area, 1),
            "side_setback_ft": side_sb,
            "rear_setback_ft": rear_sb,
            "adu_position": cfg.adu_position,
            "jurisdiction": jurisdiction,
            "effective_max_sqft": max_sqft,
            "warnings": list(profile.get("warnings") or []),
        })

    async def _generate_irregular(
        self, cfg: SitePlanConfig, ring: list[tuple[float, float]]
    ) -> CommandResult:
        from .irregular_lot import buildable_envelope, place_adu, ring_bounds
        from .parcel import ring_area

        profile = self._s.resolved_profile()
        rules = _get_rules(profile)

        side_sb = float(rules.get("setback_side_ft") or 4.0)
        rear_sb = float(rules.get("setback_rear_ft") or 4.0)
        front_sb = 20.0
        max_sqft = float(rules.get("max_sqft") or 1200.0)

        target = float((profile.get("project") or {}).get("target_sqft") or 500.0)
        target = min(target, max_sqft)

        # Only side/rear setbacks constrain ADU placement (front_sb=0), matching
        # the rectangular generator: front setback is a primary-structure
        # reference only, since ADUs are placed at the rear.
        adu_envelope = buildable_envelope(ring, front_sb=0.0, rear_sb=rear_sb, side_sb=side_sb)
        if adu_envelope.is_empty:
            raise CommandError(
                f"Side ({side_sb:.1f} ft) and rear ({rear_sb:.1f} ft) setbacks leave no "
                "buildable area on this lot shape."
            )
        env_minx, env_miny, env_maxx, env_maxy = adu_envelope.bounds
        adu_w, adu_d = _compute_adu_size(target, cfg, env_maxx - env_minx, 0.0)

        placement = place_adu(adu_envelope, adu_w, adu_d, cfg.adu_position)
        if placement is None:
            raise CommandError(
                f"No placement for a {adu_w:.1f}' × {adu_d:.1f}' ADU fits within the "
                f"buildable envelope for this lot shape after setbacks "
                f"({side_sb:.1f} ft side / {rear_sb:.1f} ft rear). Try a smaller "
                "target size or a different adu_position."
            )
        adu_x, adu_y = placement
        adu_x2, adu_y2 = adu_x + adu_w, adu_y + adu_d

        # Full setback zone (including front) for the drawn reference boundary —
        # a single closed polygon rather than the rectangular path's 4
        # disconnected lines, since an irregular lot's setback zone generally
        # isn't a rectangle either.
        setback_zone = buildable_envelope(ring, front_sb=front_sb, rear_sb=rear_sb, side_sb=side_sb)

        lot_bbox = ring_bounds(ring)
        lot_area = ring_area(ring)

        r = await self._b.drawing_create(cfg.draw_name or "site_plan")
        if not r.ok:
            raise CommandError(f"Could not create drawing: {r.error}")
        handle = r.payload["handle"]

        with self._b.batch():
            await self._setup_layers()
            lot_handle = await self._draw_lot_polygon(ring)
            await self._draw_setback_polygon(setback_zone)
            adu_handle = await self._draw_adu_polygon(adu_x, adu_y, adu_x2, adu_y2)
            await self._add_dimensions_irregular(lot_bbox, adu_x, adu_y, adu_x2, adu_y2)
            await self._add_annotations_irregular(
                lot_bbox, adu_x, adu_y, adu_x2, adu_y2, adu_w, adu_d, side_sb, rear_sb, front_sb
            )
            await self._add_north_arrow_bbox(lot_bbox)
            await self._add_title_block_irregular(lot_bbox, lot_area, adu_w, adu_d, profile, side_sb, rear_sb)

        loc = profile.get("location") or {}
        city = loc.get("city") or ""
        county = loc.get("county") or ""
        jurisdiction = ", ".join(p for p in [city, county, "CA"] if p)

        return CommandResult(ok=True, payload={
            "handle": handle,
            "drawing_name": cfg.draw_name or "site_plan",
            "lot_shape": "irregular",
            "lot_handle": lot_handle,
            "adu_handle": adu_handle,
            "lot_bbox_width_ft": round(lot_bbox[2] - lot_bbox[0], 1),
            "lot_bbox_depth_ft": round(lot_bbox[3] - lot_bbox[1], 1),
            "lot_area_sqft": round(lot_area),
            "adu_width_ft": round(adu_w, 1),
            "adu_depth_ft": round(adu_d, 1),
            "adu_area_sqft": round(adu_w * adu_d, 1),
            "adu_x_ft": round(adu_x, 1),
            "adu_y_ft": round(adu_y, 1),
            "side_setback_ft": side_sb,
            "rear_setback_ft": rear_sb,
            "adu_position": cfg.adu_position,
            "jurisdiction": jurisdiction,
            "effective_max_sqft": max_sqft,
            "warnings": list(profile.get("warnings") or []),
        })

    async def _draw_lot_polygon(self, ring: list[tuple[float, float]]) -> str:
        r = await self._b.create_polyline(ring, closed=True, layer="LOT-LINE")
        return r.payload.get("handle", "") if r.ok else ""

    async def _draw_setback_polygon(self, setback_zone) -> None:
        if setback_zone.is_empty:
            return
        # A concave envelope from an L-shaped lot may come back as a
        # MultiPolygon; draw each piece. Tight/degenerate setback combinations
        # can also collapse the intersection to a LineString or Point (zero
        # area) — nothing meaningful to draw as a setback boundary, so skip it
        # rather than crashing on the missing .exterior.
        geoms = getattr(setback_zone, "geoms", [setback_zone])
        for geom in geoms:
            if geom.is_empty or geom.geom_type != "Polygon":
                continue
            coords = list(geom.exterior.coords)[:-1]  # drop shapely's repeated closing vertex
            await self._b.create_polyline(coords, closed=True, layer="SETBACK-LINE")

    async def _draw_adu_polygon(self, x1: float, y1: float, x2: float, y2: float) -> str:
        r = await self._b.create_rectangle(x1, y1, x2, y2, "ADU-FOOTPRINT")
        eid = r.payload.get("handle", "") if r.ok else ""
        if eid:
            await self._b.create_hatch(eid, "ANSI37", scale=3.0)
        return eid

    async def _add_dimensions_irregular(
        self, bbox: tuple[float, float, float, float],
        adu_x: float, adu_y: float, adu_x2: float, adu_y2: float,
    ) -> None:
        ov = self._DIM
        minx, miny, maxx, maxy = bbox
        m = 8.0

        # Overall bounding-box width/depth, same convention as the rectangular
        # path (width below, depth to the right).
        await self._b.create_dimension_aligned(minx, miny, maxx, miny, -m, ov)
        await self._b.create_dimension_aligned(maxx, miny, maxx, maxy, m, ov)
        await self._b.create_dimension_aligned(adu_x, adu_y, adu_x2, adu_y, -6.0, ov)
        await self._b.create_dimension_aligned(adu_x2, adu_y, adu_x2, adu_y2, 4.0, ov)

    async def _add_annotations_irregular(
        self, bbox: tuple[float, float, float, float],
        adu_x: float, adu_y: float, adu_x2: float, adu_y2: float,
        adu_w: float, adu_d: float, side_sb: float, rear_sb: float, front_sb: float,
    ) -> None:
        t = self._b.create_text
        C = "MIDDLE_CENTER"
        minx, miny, maxx, maxy = bbox
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

        await t(cx, cy + 4, "EXISTING LOT (IRREGULAR)", height=2.5, layer="ANNOTATION", align=C)

        adu_cx, adu_cy = (adu_x + adu_x2) / 2, (adu_y + adu_y2) / 2
        await t(adu_cx, adu_cy + 1.5, "PROPOSED ADU", height=2.0, layer="ANNOTATION", align=C)
        await t(adu_cx, adu_cy - 1.5, f"{adu_w * adu_d:.0f} SF", height=1.75, layer="ANNOTATION", align=C)

        await t(cx, miny - 4.5, "STREET / FRONT", height=2.0, layer="ANNOTATION", align=C)
        await t(minx - 8.0, cy, f"{side_sb:.0f}' SIDE SETBACK (TYP.)", height=1.5, layer="ANNOTATION", align=C)
        await t(cx, maxy + 3.0, f"{rear_sb:.0f}' REAR SETBACK", height=1.5, layer="ANNOTATION", align=C)
        await t(
            cx, miny + front_sb / 2,
            "20' FRONT SETBACK (PRIMARY)", height=1.25, layer="ANNOTATION", align=C,
        )

    async def _add_north_arrow_bbox(self, bbox: tuple[float, float, float, float]) -> None:
        minx, miny, maxx, maxy = bbox
        cx = maxx + 16
        cy = maxy - 6
        shaft = 5.0
        head = 1.5
        t, ln, ci = self._b.create_text, self._b.create_line, self._b.create_circle
        await ln(cx, cy - shaft / 2, cx, cy + shaft / 2, "NORTH-ARROW")
        await ln(cx, cy + shaft / 2, cx - head, cy + shaft / 2 - head * 1.5, "NORTH-ARROW")
        await ln(cx, cy + shaft / 2, cx + head, cy + shaft / 2 - head * 1.5, "NORTH-ARROW")
        await ci(cx, cy, shaft / 2 + 1.5, "NORTH-ARROW")
        await t(cx, cy + shaft / 2 + 2.5, "N", height=3.0, layer="NORTH-ARROW", align="MIDDLE_CENTER")

    async def _add_title_block_irregular(
        self, bbox: tuple[float, float, float, float], lot_area: float,
        adu_w: float, adu_d: float, profile: dict[str, Any],
        side_sb: float, rear_sb: float,
    ) -> None:
        minx, miny, maxx, maxy = bbox
        loc = profile.get("location") or {}
        proj = profile.get("project") or {}
        rules = _get_rules(profile)

        city = loc.get("city") or "—"
        county = loc.get("county") or "—"
        jurisdiction = f"{city}, {county}, California"

        adu_type = (proj.get("adu_type") or "detached").replace("_", " ").title()
        max_sqft = rules.get("max_sqft", "1200")
        max_h = rules.get("max_height_ft", "16")
        today = date.today().strftime("%B %d, %Y")

        tx, ty, lh = minx, miny - 12.0, 3.5

        lines: list[tuple[str, float]] = [
            ("SITE PLAN — PROPOSED ADU (IRREGULAR LOT)", 4.0),
            (f"JURISDICTION: {jurisdiction.upper()}", 2.5),
            (
                f"ADU TYPE: {adu_type.upper()}    "
                f"TARGET: {proj.get('target_sqft', '—')} SF    "
                f"MAX ALLOWED: {max_sqft} SF",
                2.0,
            ),
            (
                f"MAX HEIGHT: {max_h} ft    "
                f"SIDE SETBACK: {side_sb} ft    "
                f"REAR SETBACK: {rear_sb} ft",
                2.0,
            ),
            (
                f"LOT AREA: {lot_area:.0f} SF (BBOX {maxx - minx:.0f}' × {maxy - miny:.0f}')    "
                f"ADU: {adu_w:.0f}' × {adu_d:.0f}' = {adu_w * adu_d:.0f} SF",
                2.0,
            ),
            (f"DATE: {today}    SCALE: 1\" = 1'-0\"    SHEET: SP-1", 2.0),
            ("PRELIMINARY — FOR PERMIT REVIEW ONLY — NOT FOR CONSTRUCTION", 2.0),
            ("VERIFY ALL SETBACKS AND REQUIREMENTS WITH LOCAL BUILDING DEPARTMENT.", 1.75),
        ]

        y = ty
        for text, height in lines:
            await self._b.create_text(tx, y, text, height=height, layer="TITLE-BLOCK")
            y -= height + lh

        await self._b.create_rectangle(minx - 10, ty + 3, maxx + 22, y + 1.5, "TITLE-BLOCK")

    async def _setup_layers(self) -> None:
        doc = self._b._doc
        if "SITE_DASH" not in doc.linetypes:
            ltype = doc.linetypes.new("SITE_DASH")
            ltype.setup_pattern("A,4,-2", length=6.0)

        specs: list[tuple[str, str, str]] = [
            ("LOT-LINE", "white", "CONTINUOUS"),
            ("SETBACK-LINE", "red", "SITE_DASH"),
            ("ADU-FOOTPRINT", "magenta", "CONTINUOUS"),
            ("DIMENSION", "cyan", "CONTINUOUS"),
            ("ANNOTATION", "yellow", "CONTINUOUS"),
            ("TITLE-BLOCK", "white", "CONTINUOUS"),
            ("NORTH-ARROW", "white", "CONTINUOUS"),
        ]
        for name, color, lt in specs:
            await self._b.layer_create(name, color, lt)

    async def _draw_lot(self, d: _Dims) -> str:
        r = await self._b.create_rectangle(0, 0, d.lot_w, d.lot_d, "LOT-LINE")
        return r.payload.get("handle", "") if r.ok else ""

    async def _draw_setback_lines(self, d: _Dims) -> None:
        # Side setback lines (vertical)
        await self._b.create_line(d.side_sb, 0, d.side_sb, d.lot_d, "SETBACK-LINE")
        await self._b.create_line(d.lot_w - d.side_sb, 0, d.lot_w - d.side_sb, d.lot_d, "SETBACK-LINE")
        # Rear setback line (horizontal, near top of lot)
        await self._b.create_line(0, d.lot_d - d.rear_sb, d.lot_w, d.lot_d - d.rear_sb, "SETBACK-LINE")
        # Front setback reference for primary structure (near bottom of lot)
        await self._b.create_line(0, d.front_sb, d.lot_w, d.front_sb, "SETBACK-LINE")

    async def _draw_adu(self, d: _Dims) -> str:
        r = await self._b.create_rectangle(d.adu_x, d.adu_y, d.adu_x2, d.adu_y2, "ADU-FOOTPRINT")
        eid = r.payload.get("handle", "") if r.ok else ""
        if eid:
            await self._b.create_hatch(eid, "ANSI37", scale=3.0)
        return eid

    async def _add_dimensions(self, d: _Dims) -> None:
        ov = self._DIM
        m = 8.0

        # Lot overall: width below lot, depth to the right of lot
        await self._b.create_dimension_aligned(0, 0, d.lot_w, 0, -m, ov)
        await self._b.create_dimension_aligned(d.lot_w, 0, d.lot_w, d.lot_d, m, ov)
        # ADU: width 6 ft below ADU bottom edge, depth 4 ft right of ADU right edge.
        # Setback zones (4 ft typical) are too narrow for clean dimension lines —
        # those values are communicated by the annotation text callouts instead.
        await self._b.create_dimension_aligned(d.adu_x, d.adu_y, d.adu_x2, d.adu_y, -6.0, ov)
        await self._b.create_dimension_aligned(d.adu_x2, d.adu_y, d.adu_x2, d.adu_y2, 4.0, ov)

    async def _add_annotations(self, d: _Dims, profile: dict[str, Any]) -> None:
        t = self._b.create_text

        C = "MIDDLE_CENTER"

        # Lot label — centered on the lot centroid
        await t(d.lot_w / 2, d.lot_d / 2 + 4, "EXISTING LOT", height=2.5, layer="ANNOTATION", align=C)
        await t(d.lot_w / 2, d.lot_d / 2, f"{d.lot_w:.0f}' × {d.lot_d:.0f}'", height=2.0, layer="ANNOTATION", align=C)

        # ADU label — centered on ADU footprint
        await t(d.adu_cx, d.adu_cy + 1.5, "PROPOSED ADU", height=2.0, layer="ANNOTATION", align=C)
        await t(d.adu_cx, d.adu_cy - 1.5, f"{d.adu_area:.0f} SF", height=1.75, layer="ANNOTATION", align=C)

        # Setback callout labels — placed OUTSIDE the lot so they don't crowd the
        # 4 ft setback zones.
        mid_y = d.adu_y + d.adu_d / 2
        # Left side: outside the lot to the left, centered on their column
        await t(-8.0, mid_y + 2.0, f"{d.side_sb:.0f}' SIDE", height=1.5, layer="ANNOTATION", align=C)
        await t(-8.0, mid_y - 0.5, "SETBACK", height=1.5, layer="ANNOTATION", align=C)
        # Right side: outside the lot to the right (past the depth dim)
        await t(d.lot_w + 14, mid_y + 2.0, f"{d.side_sb:.0f}' SIDE", height=1.5, layer="ANNOTATION", align=C)
        await t(d.lot_w + 14, mid_y - 0.5, "SETBACK", height=1.5, layer="ANNOTATION", align=C)
        # Rear setback: centered above the rear setback line
        await t(d.lot_w / 2, d.lot_d - d.rear_sb / 2 + 0.5,
                f"{d.rear_sb:.0f}' REAR", height=1.5, layer="ANNOTATION", align=C)
        # Front setback reference — centered on lot width
        await t(d.lot_w / 2, d.front_sb / 2,
                "20' FRONT SETBACK (PRIMARY)", height=1.25, layer="ANNOTATION", align=C)
        await t(d.lot_w / 2, -4.5, "STREET / FRONT", height=2.0, layer="ANNOTATION", align=C)

    async def _add_north_arrow(self, d: _Dims) -> None:
        cx = d.lot_w + 16
        cy = d.lot_d - 6
        shaft = 5.0
        head = 1.5
        t, ln, ci = self._b.create_text, self._b.create_line, self._b.create_circle
        await ln(cx, cy - shaft / 2, cx, cy + shaft / 2, "NORTH-ARROW")
        await ln(cx, cy + shaft / 2, cx - head, cy + shaft / 2 - head * 1.5, "NORTH-ARROW")
        await ln(cx, cy + shaft / 2, cx + head, cy + shaft / 2 - head * 1.5, "NORTH-ARROW")
        await ci(cx, cy, shaft / 2 + 1.5, "NORTH-ARROW")
        await t(cx, cy + shaft / 2 + 2.5, "N", height=3.0, layer="NORTH-ARROW", align="MIDDLE_CENTER")

    async def _add_title_block(self, d: _Dims, profile: dict[str, Any]) -> None:
        loc = profile.get("location") or {}
        proj = profile.get("project") or {}
        rules = _get_rules(profile)

        city = loc.get("city") or "—"
        county = loc.get("county") or "—"
        jurisdiction = f"{city}, {county}, California"

        adu_type = (proj.get("adu_type") or "detached").replace("_", " ").title()
        max_sqft = rules.get("max_sqft", "1200")
        max_h = rules.get("max_height_ft", "16")
        today = date.today().strftime("%B %d, %Y")

        tx, ty, lh = 0.0, -12.0, 3.5

        lines: list[tuple[str, float]] = [
            ("SITE PLAN — PROPOSED ADU", 4.0),
            (f"JURISDICTION: {jurisdiction.upper()}", 2.5),
            (
                f"ADU TYPE: {adu_type.upper()}    "
                f"TARGET: {proj.get('target_sqft', '—')} SF    "
                f"MAX ALLOWED: {max_sqft} SF",
                2.0,
            ),
            (
                f"MAX HEIGHT: {max_h} ft    "
                f"SIDE SETBACK: {d.side_sb} ft    "
                f"REAR SETBACK: {d.rear_sb} ft",
                2.0,
            ),
            (
                f"LOT: {d.lot_w:.0f}' × {d.lot_d:.0f}' = {d.lot_area:.0f} SF    "
                f"ADU: {d.adu_w:.0f}' × {d.adu_d:.0f}' = {d.adu_area:.0f} SF",
                2.0,
            ),
            (f"DATE: {today}    SCALE: 1\" = 1'-0\"    SHEET: SP-1", 2.0),
            ("PRELIMINARY — FOR PERMIT REVIEW ONLY — NOT FOR CONSTRUCTION", 2.0),
            ("VERIFY ALL SETBACKS AND REQUIREMENTS WITH LOCAL BUILDING DEPARTMENT.", 1.75),
        ]

        y = ty
        for text, height in lines:
            await self._b.create_text(tx, y, text, height=height, layer="TITLE-BLOCK")
            y -= height + lh

        # Border around title block (starts at x=-2 to align with left side callouts)
        await self._b.create_rectangle(-10, ty + 3, d.lot_w + 22, y + 1.5, "TITLE-BLOCK")


# ── Module-level helpers ───────────────────────────────────────────────

def validate_lot_source(
    lot_width: float | None,
    lot_depth: float | None,
    lot_boundary: list | None,
    lot_boundary_handle: str | None,
) -> None:
    """Raise ValueError unless exactly one lot source is given.

    Shared by SitePlanConfig's own resolution (direct construction) and
    validation.GenerateSitePlan's pydantic model_validator (the MCP tool
    path), so the two entry points can't silently drift on what's accepted.
    """
    sources = [
        lot_width is not None or lot_depth is not None,
        lot_boundary is not None,
        lot_boundary_handle is not None,
    ]
    if sum(sources) != 1:
        raise ValueError(
            "Provide exactly one lot source: (lot_width and lot_depth), "
            "lot_boundary, or lot_boundary_handle."
        )
    if (lot_width is None) != (lot_depth is None):
        raise ValueError("lot_width and lot_depth must both be set together.")


def _get_rules(profile: dict[str, Any]) -> dict[str, Any]:
    return (profile.get("requirements") or {}).get("effective") or {}


def _compute_adu_size(
    target_sqft: float,
    cfg: SitePlanConfig,
    lot_width: float,
    side_sb: float,
) -> tuple[float, float]:
    if cfg.adu_width is not None and cfg.adu_depth is not None:
        return float(cfg.adu_width), float(cfg.adu_depth)
    # Auto-size: 4:5 aspect ratio (width:depth), rounded to nearest 0.5 ft
    w = math.sqrt(target_sqft * 0.8)
    d = target_sqft / w
    w = round(w * 2) / 2
    d = round(d * 2) / 2
    max_w = lot_width - 2 * side_sb
    if w > max_w:
        w = max(1.0, max_w)
        d = math.ceil(target_sqft / w)
    return w, d


def _compute_adu_x(pos: str, lot_width: float, adu_width: float, side_sb: float) -> float:
    if pos == "rear_left":
        return side_sb
    if pos == "rear_right":
        return lot_width - side_sb - adu_width
    return (lot_width - adu_width) / 2  # rear_center
