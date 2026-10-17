"""Interior room floor-plan generator — builds a clean DXF from measured geometry.

Supports kitchens, baths, laundry, and similar interior spaces.
Follows the same pattern as SitePlanGenerator.

Layers:
  WALL, CABINET, ISLAND, APPLIANCE, DIMENSION, ANNOTATION, TITLE-BLOCK, NORTH-ARROW
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from .backend import AIBlueprintBackend
from .types import CommandError, CommandResult

RoomType = Literal["kitchen", "bath", "laundry", "office", "generic"]


@dataclass
class RoomFloorPlanConfig:
    """Configuration for an interior room floor plan.

    All dimensions are in feet.
    Origin (0, 0) is the south-west corner of the room.
    North is +Y, East is +X.
    """

    room_width: float
    """East-west dimension of the room (feet)."""

    room_depth: float
    """North-south dimension of the room (feet)."""

    room_type: RoomType = "kitchen"

    # Island (optional)
    island_length: float | None = None
    """Long dimension of the island (feet)."""
    island_depth: float | None = None
    """Short dimension of the island (feet)."""
    island_orientation: Literal["NS", "EW"] = "NS"
    """NS = long edges run north-south (vertical on plan). EW = long edges east-west."""
    island_x: float | None = None
    """Left edge of island. If None, centered."""
    island_y: float | None = None
    """Bottom edge of island. If None, placed with reasonable clearance."""

    # Base cabinet depth (standard 2.0 ft)
    cabinet_depth: float = 2.0

    # North wall run (contains most appliances in a typical kitchen)
    north_run_start: float = 1.5
    north_run_length: float | None = None  # defaults to room_width - 3.0

    # East wall short return
    east_run_length: float = 3.5

    # Appliance positions (relative to room, optional overrides)
    include_sink: bool = True
    include_range: bool = True
    include_dishwasher: bool = True
    include_fridge: bool = True

    # Sliding door / large opening on west wall (optional)
    sliding_door_start: float | None = 1.8
    sliding_door_length: float | None = 6.0

    # Title / annotation
    title: str = "EXISTING ROOM FLOOR PLAN"
    address: str = "Example Address, City, CA"
    notes: list[str] = field(default_factory=list)

    draw_name: str | None = None


class RoomFloorPlanGenerator:
    """Generate a dimensioned interior room floor plan as DXF."""

    _DIM = {"dimtxt": 1.5, "dimasz": 1.0, "dimlunit": 2}

    def __init__(self, backend: AIBlueprintBackend):
        self._b = backend

    async def generate(self, cfg: RoomFloorPlanConfig) -> CommandResult:
        try:
            self._validate(cfg)
            return await self._generate(cfg)
        except CommandError as ce:
            return CommandResult(ok=False, error=str(ce))
        except Exception as ex:
            return CommandResult(ok=False, error=f"Room plan generation failed: {ex}")

    def _validate(self, cfg: RoomFloorPlanConfig) -> None:
        if cfg.room_width <= 0 or cfg.room_depth <= 0:
            raise CommandError("room_width and room_depth must be positive")
        if cfg.cabinet_depth <= 0:
            raise CommandError("cabinet_depth must be positive")
        if cfg.island_length is not None and cfg.island_length <= 0:
            raise CommandError("island_length must be positive")
        if cfg.island_depth is not None and cfg.island_depth <= 0:
            raise CommandError("island_depth must be positive")
        if (cfg.island_length is None) != (cfg.island_depth is None):
            raise CommandError("island_length and island_depth must both be set, or both left unset")
        if cfg.island_orientation not in ("NS", "EW"):
            raise CommandError("island_orientation must be 'NS' or 'EW'")

    def _validate_fit(self, cfg: RoomFloorPlanConfig, north_len: float, cab_d: float) -> None:
        """Check that derived geometry actually fits inside the room."""
        w, d = cfg.room_width, cfg.room_depth
        ns = cfg.north_run_start

        if cab_d >= d:
            raise CommandError(f"cabinet_depth {cab_d:.1f} ft exceeds room_depth {d:.1f} ft")
        if ns + north_len > w:
            raise CommandError(
                f"north cabinet run ({ns:.1f}..{ns + north_len:.1f} ft) exceeds room_width {w:.1f} ft"
            )
        if cfg.east_run_length > d:
            raise CommandError(f"east_run_length {cfg.east_run_length:.1f} ft exceeds room_depth {d:.1f} ft")

        # Fixed-size appliances must stay within the actual north cabinet run.
        appliance_east_edge = ns
        if cfg.include_dishwasher:
            appliance_east_edge = max(appliance_east_edge, ns + 2.3)
        if cfg.include_sink:
            appliance_east_edge = max(appliance_east_edge, ns + 5.4)
        if cfg.include_range:
            appliance_east_edge = max(appliance_east_edge, ns + 9.0)
        if appliance_east_edge > ns + north_len:
            raise CommandError(
                f"appliance layout needs a north run of at least {appliance_east_edge - ns:.1f} ft, "
                f"but north_run_length is {north_len:.1f} ft"
            )

        if cfg.include_fridge:
            fr_w, fr_y = 3.0, 2.2
            if fr_y + fr_w > d:
                raise CommandError(
                    f"fridge (y={fr_y:.1f}..{fr_y + fr_w:.1f}) exceeds room_depth {d:.1f} ft"
                )
            if cfg.east_run_length > 0:
                east_run_y0 = d - cfg.east_run_length
                if fr_y + fr_w > east_run_y0:
                    raise CommandError(
                        f"fridge (y={fr_y:.1f}..{fr_y + fr_w:.1f} ft) overlaps the east cabinet run "
                        f"(y={east_run_y0:.1f}..{d:.1f} ft) — increase room_depth or reduce east_run_length"
                    )

        if cfg.island_length is not None and cfg.island_depth is not None:
            iw, ih = self._island_dims(cfg)
            ix, iy = self._island_pos(cfg, iw)
            if ix < 0 or ix + iw > w or iy < 0 or iy + ih > d:
                raise CommandError(
                    f"island ({ix:.1f}, {iy:.1f}) to ({ix + iw:.1f}, {iy + ih:.1f}) "
                    f"doesn't fit within the room ({w:.1f} x {d:.1f} ft)"
                )

    def _island_dims(self, cfg: RoomFloorPlanConfig) -> tuple[float, float]:
        if cfg.island_orientation == "NS":
            return cfg.island_depth, cfg.island_length  # short E-W, long N-S
        return cfg.island_length, cfg.island_depth

    def _island_pos(self, cfg: RoomFloorPlanConfig, iw: float) -> tuple[float, float]:
        # Default placement: centered horizontally, with clearance from south wall
        ix = cfg.island_x if cfg.island_x is not None else (cfg.room_width - iw) / 2
        iy = cfg.island_y if cfg.island_y is not None else 3.0
        return ix, iy

    async def _generate(self, cfg: RoomFloorPlanConfig) -> CommandResult:
        # Defaults
        north_len = cfg.north_run_length if cfg.north_run_length is not None else max(1.0, cfg.room_width - 3.0)
        cab_d = cfg.cabinet_depth
        self._validate_fit(cfg, north_len, cab_d)

        r = await self._b.drawing_create(cfg.draw_name or f"{cfg.room_type}_plan")
        if not r.ok:
            raise CommandError(f"Could not create drawing: {r.error}")
        handle = r.payload["handle"]

        with self._b.batch():
            await self._setup_layers()
            await self._draw_walls(cfg)
            await self._draw_cabinets(cfg, north_len, cab_d)
            island_handle = await self._draw_island(cfg)
            await self._draw_appliances(cfg, north_len, cab_d)
            await self._add_dimensions(cfg, north_len, cab_d)
            await self._add_annotations(cfg)
            await self._add_north_arrow(cfg)
            await self._add_title_block(cfg)

        return CommandResult(ok=True, payload={
            "handle": handle,
            "drawing_name": cfg.draw_name or f"{cfg.room_type}_plan",
            "room_type": cfg.room_type,
            "room_width_ft": cfg.room_width,
            "room_depth_ft": cfg.room_depth,
            "island_handle": island_handle,
            "has_island": island_handle is not None,
        })

    async def _setup_layers(self) -> None:
        specs: list[tuple[str, str, str]] = [
            ("WALL", "white", "CONTINUOUS"),
            ("CABINET", "yellow", "CONTINUOUS"),
            ("ISLAND", "magenta", "CONTINUOUS"),
            ("APPLIANCE", "cyan", "CONTINUOUS"),
            ("DIMENSION", "cyan", "CONTINUOUS"),
            ("ANNOTATION", "yellow", "CONTINUOUS"),
            ("TITLE-BLOCK", "white", "CONTINUOUS"),
            ("NORTH-ARROW", "white", "CONTINUOUS"),
            ("OPENING", "green", "CONTINUOUS"),
        ]
        for name, color, lt in specs:
            await self._b.layer_create(name, color, lt)

    async def _draw_walls(self, cfg: RoomFloorPlanConfig) -> None:
        w, d = cfg.room_width, cfg.room_depth
        # Outer walls as a closed polyline
        await self._b.create_rectangle(0, 0, w, d, "WALL")

        # Sliding door opening on west wall (break the wall visually with an opening mark)
        if cfg.sliding_door_start is not None and cfg.sliding_door_length is not None:
            start = cfg.sliding_door_start
            length = cfg.sliding_door_length
            # Simple opening indication: two short ticks
            await self._b.create_line(-0.5, start, 0.5, start, "OPENING")
            await self._b.create_line(-0.5, start + length, 0.5, start + length, "OPENING")
            await self._b.create_text(
                -3.5, start + length / 2, "SLIDING DOOR",
                height=1.2, rotation=90, layer="ANNOTATION", align="MIDDLE_CENTER"
            )

    async def _draw_cabinets(self, cfg: RoomFloorPlanConfig, north_len: float, cab_d: float) -> None:
        # North run (along top wall, inside the room)
        ns = cfg.north_run_start
        await self._b.create_rectangle(
            ns, cfg.room_depth - cab_d,
            ns + north_len, cfg.room_depth,
            "CABINET"
        )
        # East short return
        if cfg.east_run_length > 0:
            await self._b.create_rectangle(
                cfg.room_width - cab_d, cfg.room_depth - cfg.east_run_length,
                cfg.room_width, cfg.room_depth,
                "CABINET"
            )

    async def _draw_island(self, cfg: RoomFloorPlanConfig) -> str | None:
        if cfg.island_length is None or cfg.island_depth is None:
            return None

        iw, ih = self._island_dims(cfg)
        ix, iy = self._island_pos(cfg, iw)

        r = await self._b.create_rectangle(ix, iy, ix + iw, iy + ih, "ISLAND")
        handle = r.payload.get("handle", "") if r.ok else ""
        if handle:
            await self._b.create_hatch(handle, "ANSI37", scale=4.0)
        return handle or None

    async def _draw_appliances(self, cfg: RoomFloorPlanConfig, north_len: float, cab_d: float) -> None:
        ns = cfg.north_run_start
        # Approximate positions along the north run
        # DW near west end, sink middle, range east end of north run
        if cfg.include_dishwasher:
            await self._b.create_rectangle(
                ns + 0.3, cfg.room_depth - cab_d,
                ns + 2.3, cfg.room_depth,
                "APPLIANCE"
            )
            await self._b.create_text(
                ns + 1.3, cfg.room_depth - cab_d / 2, "DW",
                height=1.2, layer="ANNOTATION", align="MIDDLE_CENTER"
            )

        if cfg.include_sink:
            await self._b.create_rectangle(
                ns + 3.0, cfg.room_depth - cab_d + 0.15,
                ns + 5.4, cfg.room_depth - 0.15,
                "APPLIANCE"
            )
            await self._b.create_text(
                ns + 4.2, cfg.room_depth - cab_d / 2, "SINK",
                height=1.2, layer="ANNOTATION", align="MIDDLE_CENTER"
            )
            # Window note above
            await self._b.create_text(
                ns + 4.2, cfg.room_depth + 1.5, "WINDOW",
                height=1.1, layer="ANNOTATION", align="MIDDLE_CENTER"
            )

        if cfg.include_range:
            await self._b.create_rectangle(
                ns + 6.2, cfg.room_depth - cab_d,
                ns + 9.0, cfg.room_depth,
                "APPLIANCE"
            )
            await self._b.create_text(
                ns + 7.6, cfg.room_depth - cab_d / 2, "RANGE",
                height=1.2, layer="ANNOTATION", align="MIDDLE_CENTER"
            )

        if cfg.include_fridge:
            # Fridge on east wall, south of the short return
            fr_d = 2.5
            fr_w = 3.0
            fr_y = 2.2
            await self._b.create_rectangle(
                cfg.room_width - fr_d, fr_y,
                cfg.room_width, fr_y + fr_w,
                "APPLIANCE"
            )
            await self._b.create_text(
                cfg.room_width - fr_d / 2, fr_y + fr_w / 2, "FRIDGE",
                height=1.2, layer="ANNOTATION", align="MIDDLE_CENTER"
            )

    async def _add_dimensions(self, cfg: RoomFloorPlanConfig, north_len: float, cab_d: float) -> None:
        ov = self._DIM
        w, d = cfg.room_width, cfg.room_depth
        m = 6.0

        # Overall room
        await self._b.create_dimension_aligned(0, 0, w, 0, -m, ov)
        await self._b.create_dimension_aligned(w, 0, w, d, m, ov)

        # Cabinet depth
        await self._b.create_dimension_aligned(
            w, d - cab_d, w, d, 3.5, ov
        )

        # Island dimensions if present
        if cfg.island_length is not None and cfg.island_depth is not None:
            iw, ih = self._island_dims(cfg)
            ix, iy = self._island_pos(cfg, iw)
            await self._b.create_dimension_aligned(ix, iy, ix + iw, iy, -3.5, ov)
            await self._b.create_dimension_aligned(ix, iy, ix, iy + ih, -3.5, ov)

    async def _add_annotations(self, cfg: RoomFloorPlanConfig) -> None:
        # Room type label
        label = cfg.room_type.upper()
        await self._b.create_text(
            cfg.room_width / 2, cfg.room_depth / 2 + 1.5,
            f"{label} FLOOR PLAN", height=2.0, layer="ANNOTATION", align="MIDDLE_CENTER"
        )

    async def _add_north_arrow(self, cfg: RoomFloorPlanConfig) -> None:
        cx = cfg.room_width + 12
        cy = cfg.room_depth - 5
        shaft = 4.0
        head = 1.2
        await self._b.create_line(cx, cy - shaft / 2, cx, cy + shaft / 2, "NORTH-ARROW")
        await self._b.create_line(cx, cy + shaft / 2, cx - head, cy + shaft / 2 - head * 1.4, "NORTH-ARROW")
        await self._b.create_line(cx, cy + shaft / 2, cx + head, cy + shaft / 2 - head * 1.4, "NORTH-ARROW")
        await self._b.create_circle(cx, cy, shaft / 2 + 1.2, "NORTH-ARROW")
        await self._b.create_text(cx, cy + shaft / 2 + 2.2, "N", height=2.5, layer="NORTH-ARROW", align="MIDDLE_CENTER")

    async def _add_title_block(self, cfg: RoomFloorPlanConfig) -> None:
        today = date.today().strftime("%B %d, %Y")
        tx, ty = 0.0, -10.0
        lh = 3.2

        lines: list[tuple[str, float]] = [
            (cfg.title, 3.5),
            (cfg.address.upper(), 2.2),
            (f"ROOM TYPE: {cfg.room_type.upper()}    SIZE: {cfg.room_width:.1f}' × {cfg.room_depth:.1f}'", 1.8),
            (f"DATE: {today}    SCALE: 1/4\" = 1'-0\"    SHEET: FP-1", 1.8),
            ("PRELIMINARY — FOR VISUALIZATION / REMODEL PLANNING ONLY", 1.6),
            ("NOT A CONSTRUCTION DOCUMENT — VERIFY ALL DIMENSIONS ON SITE", 1.5),
        ]

        for note in (cfg.notes or [])[:3]:
            lines.append((note, 1.4))

        y = ty
        for text, height in lines:
            await self._b.create_text(tx, y, text, height=height, layer="TITLE-BLOCK")
            y -= height + lh

        # Border
        await self._b.create_rectangle(-2, ty + 2, cfg.room_width + 18, y + 1, "TITLE-BLOCK")
