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
    lot_width: float
    lot_depth: float
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
            return await self._generate(cfg)
        except CommandError as ce:
            return CommandResult(ok=False, error=str(ce))
        except Exception as ex:
            return CommandResult(ok=False, error=f"Plan generation failed: {ex}")

    async def _generate(self, cfg: SitePlanConfig) -> CommandResult:
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

        # Lot overall: width below, depth to the right
        await self._b.create_dimension_aligned(0, 0, d.lot_w, 0, -m, ov)
        await self._b.create_dimension_aligned(d.lot_w, 0, d.lot_w, d.lot_d, m, ov)
        # ADU: width below ADU, depth to the right of ADU
        await self._b.create_dimension_aligned(d.adu_x, d.adu_y, d.adu_x2, d.adu_y, -4.0, ov)
        await self._b.create_dimension_aligned(d.adu_x2, d.adu_y, d.adu_x2, d.adu_y2, 4.0, ov)
        # Left side setback (horizontal, just above mid-height of ADU)
        mid_y = d.adu_y + d.adu_d / 2
        await self._b.create_dimension_aligned(0, mid_y, d.side_sb, mid_y, 2.5, ov)
        # Rear setback (vertical, offset to the right)
        await self._b.create_dimension_aligned(
            d.adu_cx, d.lot_d - d.rear_sb, d.adu_cx, d.lot_d, 3.0, ov
        )

    async def _add_annotations(self, d: _Dims, profile: dict[str, Any]) -> None:
        t = self._b.create_text

        # Lot label
        await t(d.lot_w / 2, d.lot_d / 2 + 4, "EXISTING LOT", height=2.5, layer="ANNOTATION")
        await t(d.lot_w / 2, d.lot_d / 2, f"{d.lot_w:.0f}' × {d.lot_d:.0f}'", height=2.0, layer="ANNOTATION")

        # ADU label
        await t(d.adu_cx, d.adu_cy + 1.5, "PROPOSED ADU", height=2.0, layer="ANNOTATION")
        await t(d.adu_cx, d.adu_cy - 1.5, f"{d.adu_area:.0f} SF", height=1.75, layer="ANNOTATION")

        # Setback annotations
        mid_y = d.adu_y + d.adu_d / 2
        await t(d.side_sb / 2, mid_y,
                f"{d.side_sb:.0f}'", height=1.5, rotation=90.0, layer="ANNOTATION")
        await t(d.lot_w - d.side_sb / 2, mid_y,
                f"{d.side_sb:.0f}'", height=1.5, rotation=90.0, layer="ANNOTATION")
        await t(d.adu_cx, d.lot_d - d.rear_sb / 2,
                f"{d.rear_sb:.0f}' REAR SETBACK", height=1.5, layer="ANNOTATION")
        await t(d.lot_w / 2, d.front_sb / 2,
                "20' FRONT SETBACK (PRIMARY)", height=1.25, layer="ANNOTATION")
        await t(d.lot_w / 2, -4.5, "STREET / FRONT", height=2.0, layer="ANNOTATION")

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
        await t(cx - 1.0, cy + shaft / 2 + 2.5, "N", height=3.0, layer="NORTH-ARROW")

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
                f"ADU TYPE: Detached {adu_type.upper()}    "
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

        # Border around title block
        await self._b.create_rectangle(tx - 2, ty + 3, d.lot_w + 22, y + 1.5, "TITLE-BLOCK")


# ── Module-level helpers ───────────────────────────────────────────────

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
