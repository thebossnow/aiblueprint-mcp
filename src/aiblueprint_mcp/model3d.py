"""Build a simple 3D model from a finished 2D DXF plan.

``view.to_3d`` (and ``view.export`` with format gltf/obj) reads wall geometry
and openings from the current drawing, then writes a glTF and/or OBJ file
plus an optional Blender Python script. The MCP server does not need Blender,
a GPU, or any paid service.

Method (not code) follows Nyxera0x/blueprint (MIT): write a numeric
wall-and-opening model in real units first, then build each lintel, sill,
jamb, and wall segment as its own box. All geometry here is generated from
DXF entities; none of Nyxera's source is copied.

Drawing units are decimal feet (1 unit = 1 ft), matching the rest of
aiblueprint-mcp. glTF and OBJ are written in metres, Y-up. The Blender
script is written in metres, Z-up, with XY matching the plan.
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from aiblueprint_mcp.config import Config
from aiblueprint_mcp.ifc_export import FEET_TO_METERS
from aiblueprint_mcp.types import CommandError

# US residential defaults for Tricity bid views.
DEFAULT_WALL_HEIGHT_FT = 8.0
DEFAULT_WALL_THICKNESS_FT = 0.5  # 6"
DEFAULT_DOOR_HEIGHT_FT = 6.0 + 8.0 / 12.0  # 6'-8"
DEFAULT_WINDOW_SILL_FT = 3.0
DEFAULT_WINDOW_HEAD_FT = 6.0 + 8.0 / 12.0
DEFAULT_CASEWORK_HEIGHT_FT = 3.0
DEFAULT_SLAB_THICKNESS_FT = 4.0 / 12.0  # 4"
DEFAULT_INFERRED_WIDTH_FT = 3.0
DEFAULT_SLIDING_WIDTH_FT = 6.0
DEFAULT_FORMATS = ("gltf", "blender.py")

_IGNORE_DEFAULT = ("DIMENSION", "ANNOTATION", "TITLE-BLOCK", "NORTH-ARROW", "SETBACK-LINE")
_FOOTPRINT_TOKENS = frozenset({"FOOTPRINT", "BUILDING", "ADU", "STRUCTURE"})
_SITE_TOKENS = frozenset({"LOT", "PARCEL", "PROPERTY"})
_OPENING_TOKENS = frozenset({"OPENING", "DOOR", "WINDOW", "WINDOWS"})
_CASEWORK_TOKENS = frozenset({"CABINET", "ISLAND", "APPLIANCE"})

_SNAP_FT = 0.15
_JOIN_FT = 0.05
_TEXT_NEAR_FT = 4.0
# Text on these layers is sheet graphics, not a door or window label.
_TEXT_SKIP_LAYERS = frozenset({"TITLE-BLOCK", "DIMENSION", "NORTH-ARROW", "SETBACK-LINE"})
_KIND_WORD = re.compile(r"\b(WINDOWS?|DOORS?|OPENINGS?)\b", re.IGNORECASE)
# Notes that point at a schedule or another sheet are not opening labels.
_REF_NOTE = re.compile(
    r"\b(SEE|REFER(?:RED)?(?:\s+TO)?|SCHEDULE|PER)\b",
    re.IGNORECASE,
)

ThicknessSide = Literal["inward", "outward", "center"]
Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


def _sub(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] + b[0], a[1] + b[1])


def _mul(a: Vec2, s: float) -> Vec2:
    return (a[0] * s, a[1] * s)


def _dot(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _length(a: Vec2) -> float:
    return math.hypot(a[0], a[1])


def _norm(a: Vec2) -> Vec2:
    n = _length(a)
    if n < 1e-12:
        return (0.0, 0.0)
    return (a[0] / n, a[1] / n)


def _perp_right(a: Vec2) -> Vec2:
    """Rotate (dx, dy) 90 degrees clockwise: (dy, -dx)."""
    return (a[1], -a[0])


def _signed_area(pts: Sequence[Vec2]) -> float:
    total = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[-_\s]+", name.upper()) if t}


def _sanitize(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return cleaned or "part"


def _closest_on_segment(p: Vec2, a: Vec2, b: Vec2) -> tuple[float, float, Vec2]:
    """Return (distance, t in [0, 1], closest point) from p to segment ab."""
    ab = _sub(b, a)
    length_sq = _dot(ab, ab)
    if length_sq < 1e-18:
        return _length(_sub(p, a)), 0.0, a
    t = max(0.0, min(1.0, _dot(_sub(p, a), ab) / length_sq))
    q = _add(a, _mul(ab, t))
    return _length(_sub(p, q)), t, q


def _segment_intersection(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> Vec2 | None:
    """Interior or endpoint intersection of segments ab and cd, else None."""
    ab = _sub(b, a)
    cd = _sub(d, c)
    den = ab[0] * cd[1] - ab[1] * cd[0]
    ac = _sub(c, a)
    if abs(den) < 1e-14:
        return None
    t = (ac[0] * cd[1] - ac[1] * cd[0]) / den
    u = (ac[0] * ab[1] - ac[1] * ab[0]) / den
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return _add(a, _mul(ab, max(0.0, min(1.0, t))))
    return None


@dataclass
class WallSeg:
    id: str
    layer: str
    start: Vec2
    end: Vec2
    thickness: float
    height: float
    thickness_side: ThicknessSide
    outward: Vec2
    ring_id: str | None = None


@dataclass
class Opening:
    id: str
    kind: str
    wall_id: str
    t0: float
    t1: float
    sill: float
    head: float
    source: str
    name: str | None = None


@dataclass
class Casework:
    id: str
    layer: str
    points: list[Vec2]
    height: float


@dataclass
class Part:
    name: str
    kind: str
    vertices: list[Vec3]
    faces: list[tuple[int, int, int]]


@dataclass
class Model3d:
    walls: list[WallSeg] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    casework: list[Casework] = field(default_factory=list)
    floors: list[tuple[str, list[Vec2]]] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    defaults: dict[str, float] = field(default_factory=dict)

    @property
    def wall_count(self) -> int:
        return len(self.walls)

    @property
    def opening_count(self) -> int:
        return len(self.openings)

    @property
    def casework_count(self) -> int:
        return len(self.casework)


@dataclass
class To3dOptions:
    wall_height: float = DEFAULT_WALL_HEIGHT_FT
    wall_thickness: float = DEFAULT_WALL_THICKNESS_FT
    door_height: float = DEFAULT_DOOR_HEIGHT_FT
    window_sill: float = DEFAULT_WINDOW_SILL_FT
    window_head: float = DEFAULT_WINDOW_HEAD_FT
    thickness_side: ThicknessSide | None = None
    wall_layers: list[str] | None = None
    opening_layers: list[str] | None = None
    ignore_layers: list[str] | None = None
    ignore_layers_mode: Literal["add", "replace"] = "add"
    include_casework: bool = True
    openings: list[dict[str, Any]] | None = None


def _ignored(layer: str, ignore: Sequence[str]) -> bool:
    upper = {i.upper() for i in ignore}
    if layer.upper() in upper:
        return True
    return bool(_tokens(layer) & {i.upper() for i in ignore if i})


def _ignore_list(opts: To3dOptions) -> list[str]:
    """Return the effective ignore list.

    By default ``ignore_layers`` is *added* to the built-in list (DIMENSION,
    ANNOTATION, TITLE-BLOCK, NORTH-ARROW, SETBACK-LINE). Pass
    ``ignore_layers_mode="replace"`` to use only the provided list.
    """
    extra = list(opts.ignore_layers or [])
    if opts.ignore_layers_mode == "replace":
        return extra
    seen: list[str] = []
    names = list(_IGNORE_DEFAULT) + extra
    for name in names:
        if name.upper() not in {s.upper() for s in seen}:
            seen.append(name)
    return seen


def _is_wall_layer(layer: str, opts: To3dOptions) -> bool:
    if _ignored(layer, _ignore_list(opts)):
        return False
    if opts.wall_layers:
        return layer.upper() in {n.upper() for n in opts.wall_layers}
    tokens = _tokens(layer)
    if tokens & _SITE_TOKENS:
        return False
    return "WALL" in tokens or bool(tokens & _FOOTPRINT_TOKENS)


def _is_opening_layer(layer: str, opts: To3dOptions) -> bool:
    if _ignored(layer, _ignore_list(opts)):
        return False
    if opts.opening_layers:
        return layer.upper() in {n.upper() for n in opts.opening_layers}
    return bool(_tokens(layer) & _OPENING_TOKENS)


def _is_casework_layer(layer: str, opts: To3dOptions) -> bool:
    if _ignored(layer, _ignore_list(opts)):
        return False
    return bool(_tokens(layer) & _CASEWORK_TOKENS)


def _default_side(layer: str, closed: bool, override: ThicknessSide | None) -> ThicknessSide:
    if override:
        return override
    if not closed:
        return "center"
    tokens = _tokens(layer)
    if tokens & _FOOTPRINT_TOKENS:
        return "inward"
    return "outward"


def _outward_for_edge(start: Vec2, end: Vec2, area: float) -> Vec2:
    direction = _norm(_sub(end, start))
    right = _perp_right(direction)
    # Positive shoelace (CCW): interior is to the left, outward is to the right.
    if area >= 0:
        return right
    return _mul(right, -1.0)


def _dedupe_ring(pts: list[Vec2]) -> list[Vec2]:
    out: list[Vec2] = []
    for p in pts:
        if not out or _length(_sub(p, out[-1])) > 1e-9:
            out.append(p)
    if len(out) >= 2 and _length(_sub(out[0], out[-1])) < 1e-9:
        out.pop()
    return out


def _rings_from_segments(
    segments: list[tuple[str, Vec2, Vec2]],
) -> list[tuple[str, list[Vec2]]]:
    """Join loose wall lines whose endpoints meet into closed rings."""
    unused = list(range(len(segments)))
    rings: list[tuple[str, list[Vec2]]] = []

    def take_nearest(point: Vec2) -> tuple[int, Vec2, Vec2] | None:
        best = None
        best_d = _JOIN_FT
        for i in unused:
            _, a, b = segments[i]
            da, db = _length(_sub(a, point)), _length(_sub(b, point))
            if da <= best_d:
                best, best_d = (i, a, b), da
            if db <= best_d:
                best, best_d = (i, b, a), db
        return best

    while unused:
        i0 = unused.pop(0)
        layer, a, b = segments[i0]
        pts = [a, b]
        closed = False
        while True:
            nxt = take_nearest(pts[-1])
            if nxt is None:
                break
            idx, start, end = nxt
            unused.remove(idx)
            if _length(_sub(start, pts[-1])) > 1e-9:
                pts.append(start)
            pts.append(end)
            if len(pts) >= 4 and _length(_sub(pts[-1], pts[0])) <= _JOIN_FT:
                ring = _dedupe_ring(pts)
                if len(ring) >= 3:
                    rings.append((layer, ring))
                closed = True
                break
        if not closed:
            # Leave these as loose segments; _segments_not_in_rings keeps them.
            pass
    return rings


def _segments_not_in_rings(
    segments: list[tuple[str, Vec2, Vec2]],
    rings: list[tuple[str, list[Vec2]]],
) -> list[tuple[str, Vec2, Vec2]]:
    edges: list[tuple[Vec2, Vec2]] = []
    for _, pts in rings:
        n = len(pts)
        for i in range(n):
            edges.append((pts[i], pts[(i + 1) % n]))

    def same(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> bool:
        return (
            (_length(_sub(a, c)) <= _JOIN_FT and _length(_sub(b, d)) <= _JOIN_FT)
            or (_length(_sub(a, d)) <= _JOIN_FT and _length(_sub(b, c)) <= _JOIN_FT)
        )

    leftover = []
    for layer, a, b in segments:
        if any(same(a, b, c, d) for c, d in edges):
            continue
        leftover.append((layer, a, b))
    return leftover


def _collect_lines_and_rings(
    msp, opts: To3dOptions
) -> tuple[list[tuple[str, Vec2, Vec2]], list[tuple[str, list[Vec2]]]]:
    segments: list[tuple[str, Vec2, Vec2]] = []
    rings: list[tuple[str, list[Vec2]]] = []
    for e in msp:
        t = e.dxftype()
        layer = e.dxf.get("layer", "0")
        if not _is_wall_layer(layer, opts):
            continue
        if t == "LINE":
            start = (float(e.dxf.start[0]), float(e.dxf.start[1]))
            end = (float(e.dxf.end[0]), float(e.dxf.end[1]))
            if _length(_sub(end, start)) > 1e-9:
                segments.append((layer, start, end))
        elif t == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points(format="xy")]
            if e.closed and len(pts) >= 3:
                rings.append((layer, _dedupe_ring(pts)))
            else:
                for i in range(len(pts) - 1):
                    if _length(_sub(pts[i + 1], pts[i])) > 1e-9:
                        segments.append((layer, pts[i], pts[i + 1]))
    rings.extend(_rings_from_segments(segments))
    leftover = _segments_not_in_rings(segments, rings)
    return leftover, rings


def _build_walls(
    opts: To3dOptions,
    loose: list[tuple[str, Vec2, Vec2]],
    rings: list[tuple[str, list[Vec2]]],
) -> tuple[list[WallSeg], list[tuple[str, list[Vec2]]]]:
    walls: list[WallSeg] = []
    floors: list[tuple[str, list[Vec2]]] = []
    n = 0
    for i, (layer, pts) in enumerate(rings, start=1):
        ring_id = f"ring-{i}"
        area = _signed_area(pts)
        side = _default_side(layer, True, opts.thickness_side)
        floors.append((ring_id, list(pts)))
        for j in range(len(pts)):
            start, end = pts[j], pts[(j + 1) % len(pts)]
            if _length(_sub(end, start)) < 1e-9:
                continue
            n += 1
            walls.append(
                WallSeg(
                    id=f"wall-{n}",
                    layer=layer,
                    start=start,
                    end=end,
                    thickness=opts.wall_thickness,
                    height=opts.wall_height,
                    thickness_side=side,
                    outward=_outward_for_edge(start, end, area),
                    ring_id=ring_id,
                )
            )
    for layer, start, end in loose:
        n += 1
        side = _default_side(layer, False, opts.thickness_side)
        direction = _norm(_sub(end, start))
        walls.append(
            WallSeg(
                id=f"wall-{n}",
                layer=layer,
                start=start,
                end=end,
                thickness=opts.wall_thickness,
                height=opts.wall_height,
                thickness_side=side,
                outward=_perp_right(direction),
                ring_id=None,
            )
        )
    return walls, floors


def _kind_from_text(text: str) -> str | None:
    """Return a kind only when WINDOW, DOOR, or OPENING is the main term.

    Substrings such as OUTDOOR must not count as a door. Notes that
    reference another document (SEE WINDOW SCHEDULE, DOOR SCHEDULE,
    PER A-401) are ignored. An OPENING note is a doorless passage, not
    a door with a panel.
    """
    raw = text or ""
    if _REF_NOTE.search(raw):
        return None
    words = [m.group(1).upper() for m in _KIND_WORD.finditer(raw)]
    if any(w.startswith("WINDOW") for w in words):
        return "window"
    if any(w.startswith("DOOR") for w in words):
        return "door"
    if any(w.startswith("OPENING") for w in words):
        return "opening"
    return None


def _kind_from_layer(layer: str) -> str | None:
    """Return a kind implied by the entity's layer, if any.

    WINDOW/WINDOWS -> window, DOOR -> door. The generic OPENING layer is
    left unclassified so room-plan ticks can still be doors when a nearby
    label says so (or default to a door).
    """
    tokens = _tokens(layer)
    if "WINDOW" in tokens or "WINDOWS" in tokens:
        return "window"
    if "DOOR" in tokens:
        return "door"
    return None


def _kind_from_block(name: str, attribs: dict[str, str]) -> str | None:
    """Kind from a block name and attributes, never from the INSERT layer.

    The OPENING layer is a generic mark layer, so a door block named D36
    (or any door-like name) on that layer must still default to a door
    with a panel. Only a name or attribute that clearly says opening or
    passage becomes a doorless opening.
    """
    blob = f"{name} {' '.join(attribs.values())}".strip()
    kind = _kind_from_text(blob)
    if kind:
        return kind
    if _REF_NOTE.search(blob):
        return None
    if re.search(r"\bPASSAGES?\b", blob, re.IGNORECASE):
        return "opening"
    return None


def _resolve_opening_kind(text_kind: str | None, *layers: str) -> str:
    """Prefer a WINDOW layer over a nearby DOOR/WINDOW note."""
    layer_kind = None
    for layer in layers:
        layer_kind = _kind_from_layer(layer)
        if layer_kind:
            break
    if layer_kind == "window":
        return "window"
    return text_kind or layer_kind or "door"


def _is_sliding(text: str) -> bool:
    return bool(re.search(r"\bSLIDING\b", text or "", re.IGNORECASE))


def _project_on_wall(wall: WallSeg, p: Vec2) -> tuple[float, float, Vec2]:
    return _closest_on_segment(p, wall.start, wall.end)


def _nearest_wall(walls: list[WallSeg], p: Vec2) -> tuple[WallSeg, float, float, Vec2] | None:
    best = None
    for w in walls:
        dist, t, q = _project_on_wall(w, p)
        if best is None or dist < best[1]:
            best = (w, dist, t, q)
    return best


def _skip_text_layer(layer: str, opts: To3dOptions) -> bool:
    """Skip title-block and ignored layers when reading door/window notes.

    ANNOTATION is in the default ignore list so it is not extruded, but it is
    the usual layer for labels, so notes there are still read unless the
    caller replaced the ignore list and omitted it, or listed it explicitly.
    """
    upper = layer.upper()
    if upper in _TEXT_SKIP_LAYERS or "TITLE" in _tokens(layer):
        return True
    extra = {n.upper() for n in (opts.ignore_layers or [])}
    if upper in extra:
        return True
    return False


def _collect_texts(msp, opts: To3dOptions) -> list[tuple[str, Vec2]]:
    items: list[tuple[str, Vec2]] = []
    for e in msp:
        t = e.dxftype()
        if t not in ("TEXT", "MTEXT"):
            continue
        layer = e.dxf.get("layer", "0")
        if _skip_text_layer(layer, opts):
            continue
        if t == "TEXT":
            items.append((str(e.dxf.text or ""), (float(e.dxf.insert[0]), float(e.dxf.insert[1]))))
        else:
            items.append((str(e.text or ""), (float(e.dxf.insert[0]), float(e.dxf.insert[1]))))
    return items


def _text_kind_near(texts: list[tuple[str, Vec2]], point: Vec2) -> tuple[str | None, str]:
    best: tuple[float, str, str] | None = None
    for text, pos in texts:
        kind = _kind_from_text(text)
        if not kind:
            continue
        d = _length(_sub(pos, point))
        if d <= _TEXT_NEAR_FT and (best is None or d < best[0]):
            best = (d, kind, text)
    if best is None:
        return None, ""
    return best[1], best[2]


def _add_opening(
    model: Model3d,
    wall: WallSeg,
    t0: float,
    t1: float,
    kind: str,
    source: str,
    opts: To3dOptions,
    name: str | None = None,
    sill: float | None = None,
    head: float | None = None,
) -> Opening:
    if t1 < t0:
        t0, t1 = t1, t0
    wall_len = _length(_sub(wall.end, wall.start))
    width = (t1 - t0) * wall_len
    if wall_len > 1e-9 and width > wall_len + 1e-9:
        model.warnings.append(
            f"Opening {name or kind} is {width:g} ft wide, wider than its wall "
            f"({wall_len:g} ft); it will be clamped to the wall length."
        )
    t0 = max(0.0, min(1.0, t0))
    t1 = max(0.0, min(1.0, t1))
    if kind == "window":
        z0 = opts.window_sill if sill is None else sill
        z1 = opts.window_head if head is None else head
    else:
        z0 = 0.0 if sill is None else sill
        z1 = opts.door_height if head is None else head
    z1 = min(z1, wall.height)
    z0 = max(0.0, min(z0, z1))
    oid = f"opening-{len(model.openings) + 1}"
    op = Opening(
        id=oid, kind=kind, wall_id=wall.id, t0=t0, t1=t1,
        sill=z0, head=z1, source=source, name=name,
    )
    model.openings.append(op)
    return op


def _openings_overlap(a: Opening, b: Opening) -> bool:
    if a.wall_id != b.wall_id:
        return False
    return not (a.t1 <= b.t0 + 1e-6 or b.t1 <= a.t0 + 1e-6)


def _infer_ticks(msp, model: Model3d, opts: To3dOptions, texts: list[tuple[str, Vec2]]) -> None:
    ticks: list[tuple[Vec2, Vec2, str]] = []
    spans: list[tuple[Vec2, Vec2, str]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        layer = e.dxf.get("layer", "0")
        if not _is_opening_layer(layer, opts):
            continue
        a = (float(e.dxf.start[0]), float(e.dxf.start[1]))
        b = (float(e.dxf.end[0]), float(e.dxf.end[1]))
        if _length(_sub(b, a)) < 1e-9:
            continue
        used = False
        for wall in model.walls:
            wdir = _norm(_sub(wall.end, wall.start))
            ldir = _norm(_sub(b, a))
            align = abs(_dot(wdir, ldir))
            mid = _mul(_add(a, b), 0.5)
            dist, _, _ = _project_on_wall(wall, mid)
            if align > 0.85 and dist <= max(_SNAP_FT, wall.thickness):
                spans.append((a, b, layer))
                used = True
                break
            hit = _segment_intersection(a, b, wall.start, wall.end)
            if hit is None and align < 0.5:
                da, _, _ = _closest_on_segment(a, wall.start, wall.end)
                db, _, _ = _closest_on_segment(b, wall.start, wall.end)
                if min(da, db) <= max(_SNAP_FT, wall.thickness):
                    hit = mid
            if hit is not None and align < 0.85:
                ticks.append((a, b, layer))
                used = True
                break
        if not used:
            model.warnings.append(
                f"Opening-layer line from ({a[0]:.2f}, {a[1]:.2f}) to "
                f"({b[0]:.2f}, {b[1]:.2f}) did not meet a wall."
            )

    by_wall: dict[str, list[tuple[float, str]]] = {w.id: [] for w in model.walls}
    wall_by_id = {w.id: w for w in model.walls}
    unmatched = 0
    for a, b, layer in ticks:
        mid = _mul(_add(a, b), 0.5)
        best: tuple[WallSeg, float, float] | None = None
        for wall in model.walls:
            hit = _segment_intersection(a, b, wall.start, wall.end)
            if hit is not None:
                _, t, _ = _project_on_wall(wall, hit)
                best = (wall, 0.0, t)
                break
            dist, t, _ = _project_on_wall(wall, mid)
            if best is None or dist < best[1]:
                best = (wall, dist, t)
        if best is None or best[1] > max(_SNAP_FT, best[0].thickness):
            unmatched += 1
            continue
        by_wall[best[0].id].append((best[2], layer))
    if unmatched:
        model.warnings.append(f"{unmatched} opening tick(s) could not be paired to a wall.")

    for wall_id, ts in by_wall.items():
        ts.sort(key=lambda item: item[0])
        wall = wall_by_id[wall_id]
        i = 0
        leftover = None
        while i + 1 < len(ts):
            t0, layer0 = ts[i]
            t1, layer1 = ts[i + 1]
            mid_pt = _add(wall.start, _mul(_sub(wall.end, wall.start), (t0 + t1) / 2))
            kind, text = _text_kind_near(texts, mid_pt)
            kind = _resolve_opening_kind(kind, layer0, layer1)
            _add_opening(
                model, wall, t0, t1, kind,
                source="ticks", opts=opts, name=text or None,
            )
            if kind == "door" and not text and not _kind_from_layer(layer0):
                model.warnings.append(
                    f"Opening on {wall.id} inferred from tick marks; treated as a door."
                )
            i += 2
        if i < len(ts):
            leftover = ts[i][0]
        if leftover is not None:
            model.warnings.append(
                f"Unmatched opening tick on {wall.id} at t={leftover:.3f}."
            )

    for a, b, layer in spans:
        mid = _mul(_add(a, b), 0.5)
        found = _nearest_wall(model.walls, mid)
        if found is None:
            continue
        wall, dist, _, _ = found
        if dist > max(_SNAP_FT, wall.thickness):
            model.warnings.append("Opening-layer span did not lie on a wall.")
            continue
        _, t0, _ = _project_on_wall(wall, a)
        _, t1, _ = _project_on_wall(wall, b)
        mid_pt = _add(wall.start, _mul(_sub(wall.end, wall.start), (t0 + t1) / 2))
        kind, text = _text_kind_near(texts, mid_pt)
        kind = _resolve_opening_kind(kind, layer)
        _add_opening(
            model, wall, t0, t1, kind, source="span", opts=opts, name=text or None,
        )


def _infer_text_only(model: Model3d, opts: To3dOptions, texts: list[tuple[str, Vec2]]) -> None:
    """Place a default-width opening for a WINDOW/DOOR note that has no ticks."""
    for text, pos in texts:
        kind = _kind_from_text(text)
        if not kind:
            continue
        found = _nearest_wall(model.walls, pos)
        if found is None:
            continue
        wall, dist, t, _q = found
        if dist > _TEXT_NEAR_FT:
            continue
        width = DEFAULT_SLIDING_WIDTH_FT if _is_sliding(text) else DEFAULT_INFERRED_WIDTH_FT
        wall_len = _length(_sub(wall.end, wall.start))
        if wall_len < 1e-9:
            continue
        half = (width / wall_len) / 2.0
        t0, t1 = t - half, t + half
        if t1 < t0:
            t0, t1 = t1, t0
        candidate = Opening(
            id="candidate",
            kind=kind,
            wall_id=wall.id,
            t0=max(0.0, min(1.0, t0)),
            t1=max(0.0, min(1.0, t1)),
            sill=0.0,
            head=opts.door_height,
            source="text",
            name=text,
        )
        if any(_openings_overlap(candidate, o) for o in model.openings):
            continue
        _add_opening(
            model, wall, t0, t1, kind,
            source="text", opts=opts, name=text,
        )
        model.warnings.append(
            f"Inferred {kind} {text!r} on {wall.id} from annotation text; "
            f"default width {width:g} ft used."
        )


def _apply_explicit(model: Model3d, opts: To3dOptions) -> None:
    for spec in opts.openings or []:
        kind = str(spec.get("kind") or "door")
        a = (float(spec["x1"]), float(spec["y1"]))
        b = (float(spec["x2"]), float(spec["y2"]))
        mid = _mul(_add(a, b), 0.5)
        found = _nearest_wall(model.walls, mid)
        if found is None:
            model.warnings.append("Explicit opening could not be placed: no walls.")
            continue
        wall, dist, _, _ = found
        if dist > max(1.0, wall.thickness * 2):
            model.warnings.append(
                f"Explicit opening at ({mid[0]:.2f}, {mid[1]:.2f}) is far from any wall."
            )
        _, t0, _ = _project_on_wall(wall, a)
        _, t1, _ = _project_on_wall(wall, b)
        width = spec.get("width")
        if width:
            wall_len = _length(_sub(wall.end, wall.start))
            if wall_len > 1e-9:
                mid_t = (t0 + t1) / 2.0
                half = (float(width) / wall_len) / 2.0
                t0, t1 = mid_t - half, mid_t + half
        explicit = _add_opening(
            model, wall, t0, t1, kind, source="explicit", opts=opts,
            name=spec.get("name"),
            sill=spec.get("sill"),
            head=spec.get("head"),
        )
        model.openings = [
            o for o in model.openings
            if o is explicit or o.source == "explicit" or not _openings_overlap(o, explicit)
        ]


def _insert_attribs(entity) -> dict[str, str]:
    try:
        return {a.dxf.tag.upper(): str(a.dxf.text) for a in entity.attribs}
    except Exception:
        return {}


def _parse_attr_length(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from aiblueprint_mcp.units import parse_length
        return parse_length(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return None


def _infer_inserts(msp, model: Model3d, opts: To3dOptions) -> None:
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        layer = e.dxf.get("layer", "0")
        name = str(e.dxf.get("name", "") or "")
        attribs = _insert_attribs(e)
        kind = None
        block_kind = _kind_from_block(name, attribs)
        if _is_opening_layer(layer, opts) or block_kind:
            # Use the layer only for WINDOW/DOOR, never for the generic
            # OPENING layer (that would turn a D36 door block into a
            # doorless passage).
            kind = block_kind or _kind_from_layer(layer) or "door"
        if not kind:
            continue
        insert = (float(e.dxf.insert[0]), float(e.dxf.insert[1]))
        found = _nearest_wall(model.walls, insert)
        if found is None:
            continue
        wall, dist, t, _ = found
        if dist > max(1.0, wall.thickness * 2):
            model.warnings.append(f"Block '{name}' was not close enough to a wall.")
            continue
        width = _parse_attr_length(attribs.get("WIDTH")) or DEFAULT_INFERRED_WIDTH_FT
        height = _parse_attr_length(attribs.get("HEIGHT"))
        sill = _parse_attr_length(attribs.get("SILL"))
        head = None
        if height is not None:
            if kind == "window":
                base_sill = opts.window_sill if sill is None else sill
            else:
                base_sill = 0.0 if sill is None else sill
            head = base_sill + height
            sill = base_sill
        wall_len = _length(_sub(wall.end, wall.start))
        if wall_len < 1e-9:
            continue
        half = (width / wall_len) / 2.0
        _add_opening(
            model, wall, t - half, t + half, kind, source="block",
            opts=opts, name=name or None, sill=sill, head=head,
        )


def _collect_casework(msp, opts: To3dOptions) -> list[Casework]:
    items: list[Casework] = []
    n = 0
    if not opts.include_casework:
        return items
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        layer = e.dxf.get("layer", "0")
        if not _is_casework_layer(layer, opts):
            continue
        pts = _dedupe_ring([(float(p[0]), float(p[1])) for p in e.get_points(format="xy")])
        if len(pts) < 3:
            continue
        n += 1
        items.append(Casework(
            id=f"casework-{n}", layer=layer, points=pts,
            height=DEFAULT_CASEWORK_HEIGHT_FT,
        ))
    return items


def _prism(name: str, kind: str, origin: Vec3, u: Vec3, v: Vec3, w: Vec3) -> Part:
    """Build a triangular mesh for the parallelepiped at origin with edges u,v,w."""
    def add(a: Vec3, b: Vec3) -> Vec3:
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    corners = [
        origin,
        add(origin, u),
        add(add(origin, u), v),
        add(origin, v),
        add(origin, w),
        add(add(origin, u), w),
        add(add(add(origin, u), v), w),
        add(add(origin, v), w),
    ]
    quads = (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    )
    # Reverse winding when (u, v, w) is a left-handed frame (common for
    # outward walls on a counterclockwise room outline) so faces point out.
    cx = v[1] * w[2] - v[2] * w[1]
    cy = v[2] * w[0] - v[0] * w[2]
    cz = v[0] * w[1] - v[1] * w[0]
    det = u[0] * cx + u[1] * cy + u[2] * cz
    # Unique vertices per face so averaged normals stay flat (one normal per face).
    verts: list[Vec3] = []
    faces: list[tuple[int, int, int]] = []
    for a, b, c, d in quads:
        seq = (a, b, c, d) if det >= 0 else (a, d, c, b)
        base = len(verts)
        verts.extend(corners[i] for i in seq)
        faces.append((base, base + 1, base + 2))
        faces.append((base, base + 2, base + 3))
    return Part(name=name, kind=kind, vertices=verts, faces=faces)


def _wall_frame(wall: WallSeg, t0: float, t1: float) -> tuple[Vec2, Vec2, Vec2]:
    along_full = _sub(wall.end, wall.start)
    p0 = _add(wall.start, _mul(along_full, t0))
    p1 = _add(wall.start, _mul(along_full, t1))
    along = _sub(p1, p0)
    n = wall.outward
    t = wall.thickness
    if wall.thickness_side == "outward":
        origin2 = p0
        thick = _mul(n, t)
    elif wall.thickness_side == "inward":
        origin2 = _add(p0, _mul(n, -t))
        thick = _mul(n, t)
    else:
        origin2 = _add(p0, _mul(n, -t / 2.0))
        thick = _mul(n, t)
    return origin2, along, thick


def _wall_slice(name: str, kind: str, wall: WallSeg, t0: float, t1: float, z0: float, z1: float) -> Part | None:
    if t1 - t0 < 1e-9 or z1 - z0 < 1e-9:
        return None
    origin2, along, thick = _wall_frame(wall, t0, t1)
    origin = (origin2[0], origin2[1], z0)
    u = (along[0], along[1], 0.0)
    v = (thick[0], thick[1], 0.0)
    w = (0.0, 0.0, z1 - z0)
    return _prism(name, kind, origin, u, v, w)


def _thin_fill(name: str, kind: str, wall: WallSeg, t0: float, t1: float, z0: float, z1: float) -> Part | None:
    if t1 - t0 < 1e-9 or z1 - z0 < 1e-9:
        return None
    origin2, along, thick = _wall_frame(wall, t0, t1)
    leaf = 1.5 / 12.0
    n = _norm((thick[0], thick[1]))
    thick_len = _length((thick[0], thick[1]))
    inset = max(0.0, (thick_len - leaf) / 2.0)
    origin2 = _add(origin2, _mul(n, inset))
    thick = _mul(n, min(leaf, thick_len))
    origin = (origin2[0], origin2[1], z0)
    u = (along[0], along[1], 0.0)
    v = (thick[0], thick[1], 0.0)
    w = (0.0, 0.0, z1 - z0)
    return _prism(name, kind, origin, u, v, w)


def _corner_is_convex(a: WallSeg, b: WallSeg, ring_area: float) -> bool:
    """True when the turn from a into b is a convex corner of the ring.

    The cross of the two directions has the same sign as the ring's signed
    area at a convex turn, and the opposite sign at a reflex (inside) turn.
    """
    d0 = _sub(a.end, a.start)
    d1 = _sub(b.end, b.start)
    cross = d0[0] * d1[1] - d0[1] * d1[0]
    return cross * ring_area > 1e-12


def _corner_posts(model: Model3d) -> list[Part]:
    """Fill the thickness x thickness gap at each ring corner.

    At a convex corner, outward walls meet at the interior vertex and leave
    a post-sized gap on the outside; inward walls already overlap, so no
    post is added. At a reflex (inside) corner the opposite is true: inward
    walls leave an interior notch that a post must fill, and outward walls
    already overlap. Centered walls have a half-thickness gap on the outside
    of a convex corner and on the inside of a reflex corner.
    """
    posts: list[Part] = []
    by_ring: dict[str, list[WallSeg]] = {}
    for wall in model.walls:
        if wall.ring_id:
            by_ring.setdefault(wall.ring_id, []).append(wall)
    for ring_id, walls in by_ring.items():
        n = len(walls)
        if n < 3:
            continue
        ring_area = _signed_area([w.start for w in walls])
        if abs(ring_area) < 1e-12:
            continue
        for i in range(n):
            a, b = walls[i], walls[(i + 1) % n]
            if a.thickness_side != b.thickness_side:
                continue
            side = a.thickness_side
            convex = _corner_is_convex(a, b, ring_area)
            if convex:
                if side == "inward":
                    continue
                scale_a = a.thickness if side == "outward" else a.thickness / 2.0
                scale_b = b.thickness if side == "outward" else b.thickness / 2.0
                ua = _mul(a.outward, scale_a)
                vb = _mul(b.outward, scale_b)
            else:
                if side == "outward":
                    continue
                scale_a = a.thickness if side == "inward" else a.thickness / 2.0
                scale_b = b.thickness if side == "inward" else b.thickness / 2.0
                ua = _mul(a.outward, -scale_a)
                vb = _mul(b.outward, -scale_b)
            if abs(ua[0] * vb[1] - ua[1] * vb[0]) < 1e-12:
                continue
            origin = (a.end[0], a.end[1], 0.0)
            post = _prism(
                f"{ring_id}_corner_{i + 1}",
                "wall",
                origin,
                (ua[0], ua[1], 0.0),
                (vb[0], vb[1], 0.0),
                (0.0, 0.0, a.height),
            )
            posts.append(post)
    return posts


def _build_parts(model: Model3d) -> None:
    parts: list[Part] = []
    for wall in model.walls:
        ops = sorted([o for o in model.openings if o.wall_id == wall.id], key=lambda o: o.t0)
        cursor = 0.0
        for i, op in enumerate(ops):
            t0, t1 = max(cursor, op.t0), min(1.0, op.t1)
            if t0 > cursor + 1e-9:
                p = _wall_slice(f"{wall.id}_seg_{i}", "wall", wall, cursor, t0, 0.0, wall.height)
                if p:
                    parts.append(p)
            if t1 <= t0:
                continue
            lintel = _wall_slice(
                f"{op.id}_lintel", "lintel", wall, t0, t1, op.head, wall.height,
            )
            if lintel:
                parts.append(lintel)
            # Wall under a sill for any kind; glass/leaf depends only on kind.
            # kind "opening" is a doorless passage (lintel only, plus sill wall).
            if op.sill > 1e-9:
                sill = _wall_slice(
                    f"{op.id}_sill", "sill", wall, t0, t1, 0.0, op.sill,
                )
                if sill:
                    parts.append(sill)
            if op.kind == "window":
                glass = _thin_fill(f"{op.id}_glass", "glass", wall, t0, t1, op.sill, op.head)
                if glass:
                    parts.append(glass)
            elif op.kind == "door":
                leaf = _thin_fill(f"{op.id}_leaf", "door_leaf", wall, t0, t1, op.sill, op.head)
                if leaf:
                    parts.append(leaf)
            cursor = max(cursor, t1)
        if cursor < 1.0 - 1e-9:
            p = _wall_slice(f"{wall.id}_seg_end", "wall", wall, cursor, 1.0, 0.0, wall.height)
            if p:
                parts.append(p)

    parts.extend(_corner_posts(model))

    for ring_id, pts in model.floors:
        slab = _extrude_polygon(f"{ring_id}_slab", "slab", pts, -DEFAULT_SLAB_THICKNESS_FT, 0.0)
        if slab:
            parts.append(slab)

    for cw in model.casework:
        box = _extrude_polygon(cw.id, "casework", cw.points, 0.0, cw.height)
        if box:
            parts.append(box)

    model.parts = parts


def _ear_clip(pts: list[Vec2]) -> list[tuple[int, int, int]]:
    n = len(pts)
    if n < 3:
        return []
    area = _signed_area(pts)
    idx = list(range(n))
    faces: list[tuple[int, int, int]] = []

    def is_ear(i0: int, i1: int, i2: int) -> bool:
        a, b, c = pts[i0], pts[i1], pts[i2]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if area >= 0 and cross <= 1e-14:
            return False
        if area < 0 and cross >= -1e-14:
            return False
        for i in idx:
            if i in (i0, i1, i2):
                continue
            if _in_triangle(pts[i], a, b, c):
                return False
        return True

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        clipped = False
        for j in range(len(idx)):
            i0, i1, i2 = idx[j - 1], idx[j], idx[(j + 1) % len(idx)]
            if is_ear(i0, i1, i2):
                faces.append((i0, i1, i2) if area >= 0 else (i0, i2, i1))
                idx.pop(j)
                clipped = True
                break
        if not clipped:
            break
    if len(idx) == 3:
        i0, i1, i2 = idx
        faces.append((i0, i1, i2) if area >= 0 else (i0, i2, i1))
    return faces


def _in_triangle(p: Vec2, a: Vec2, b: Vec2, c: Vec2) -> bool:
    def sign(p1: Vec2, p2: Vec2, p3: Vec2) -> float:
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

    d1, d2, d3 = sign(p, a, b), sign(p, b, c), sign(p, c, a)
    has_neg = (d1 < -1e-14) or (d2 < -1e-14) or (d3 < -1e-14)
    has_pos = (d1 > 1e-14) or (d2 > 1e-14) or (d3 > 1e-14)
    return not (has_neg and has_pos)


def _extrude_polygon(name: str, kind: str, pts: list[Vec2], z0: float, z1: float) -> Part | None:
    ring = _dedupe_ring(pts)
    if len(ring) < 3 or abs(z1 - z0) < 1e-9:
        return None
    # Walk counter-clockwise so side faces point outward (materials are not
    # double-sided). Clockwise slab, cabinet, and island outlines would
    # otherwise get inward-facing sides.
    if _signed_area(ring) < 0:
        ring = list(reversed(ring))
    tris = _ear_clip(ring)
    if not tris:
        return None
    verts: list[Vec3] = []
    faces: list[tuple[int, int, int]] = []

    def add_tri(p: Vec3, q: Vec3, r: Vec3) -> None:
        i = len(verts)
        verts.extend((p, q, r))
        faces.append((i, i + 1, i + 2))

    for a, b, c in tris:
        add_tri(
            (ring[a][0], ring[a][1], z0),
            (ring[c][0], ring[c][1], z0),
            (ring[b][0], ring[b][1], z0),
        )
    for a, b, c in tris:
        add_tri(
            (ring[a][0], ring[a][1], z1),
            (ring[b][0], ring[b][1], z1),
            (ring[c][0], ring[c][1], z1),
        )
    n = len(ring)
    for i in range(n):
        j = (i + 1) % n
        b0 = (ring[i][0], ring[i][1], z0)
        b1 = (ring[j][0], ring[j][1], z0)
        t0 = (ring[i][0], ring[i][1], z1)
        t1 = (ring[j][0], ring[j][1], z1)
        add_tri(b0, b1, t1)
        add_tri(b0, t1, t0)
    return Part(name=name, kind=kind, vertices=verts, faces=faces)


def _bbox(verts: Sequence[Vec3]) -> dict[str, list[float]]:
    xs, ys, zs = zip(*verts, strict=True)
    return {
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)],
    }


def model_to_ir(model: Model3d) -> dict[str, Any]:
    return {
        "version": 1,
        "units": "feet",
        "defaults": model.defaults,
        "walls": [
            {
                "id": w.id,
                "layer": w.layer,
                "start": [round(w.start[0], 6), round(w.start[1], 6)],
                "end": [round(w.end[0], 6), round(w.end[1], 6)],
                "thickness": w.thickness,
                "height": w.height,
                "thickness_side": w.thickness_side,
                "ring_id": w.ring_id,
            }
            for w in model.walls
        ],
        "openings": [
            {
                "id": o.id,
                "kind": o.kind,
                "wall_id": o.wall_id,
                "t0": round(o.t0, 6),
                "t1": round(o.t1, 6),
                "sill": o.sill,
                "head": o.head,
                "source": o.source,
                "name": o.name,
            }
            for o in model.openings
        ],
        "casework": [
            {
                "id": c.id,
                "layer": c.layer,
                "points": [[round(x, 6), round(y, 6)] for x, y in c.points],
                "height": c.height,
            }
            for c in model.casework
        ],
        "parts": [
            {
                "name": p.name,
                "kind": p.kind,
                "vertex_count": len(p.vertices),
                "bbox": _bbox(p.vertices),
            }
            for p in model.parts
        ],
        "warnings": list(model.warnings),
    }


def _to_meters_yup(v: Vec3) -> Vec3:
    """Feet, Z-up -> metres, Y-up (x, z, -y)."""
    return (v[0] * FEET_TO_METERS, v[2] * FEET_TO_METERS, -v[1] * FEET_TO_METERS)


def _to_meters_zup(v: Vec3) -> Vec3:
    return (v[0] * FEET_TO_METERS, v[1] * FEET_TO_METERS, v[2] * FEET_TO_METERS)


_MATERIALS = {
    "wall": (0.78, 0.76, 0.72),
    "lintel": (0.78, 0.76, 0.72),
    "sill": (0.78, 0.76, 0.72),
    "door_leaf": (0.55, 0.38, 0.22),
    "glass": (0.55, 0.72, 0.85),
    "slab": (0.55, 0.55, 0.52),
    "casework": (0.82, 0.72, 0.45),
}


def _vertex_normals(verts: list[Vec3], faces: list[tuple[int, int, int]]) -> list[Vec3]:
    acc = [(0.0, 0.0, 0.0) for _ in verts]
    for a, b, c in faces:
        va, vb, vc = verts[a], verts[b], verts[c]
        ux, uy, uz = vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]
        vx, vy, vz = vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for i in (a, b, c):
            ax, ay, az = acc[i]
            acc[i] = (ax + nx, ay + ny, az + nz)
    out: list[Vec3] = []
    for x, y, z in acc:
        n = math.sqrt(x * x + y * y + z * z)
        out.append((0.0, 1.0, 0.0) if n < 1e-12 else (x / n, y / n, z / n))
    return out


def _gltf_document_and_blob(parts: list[Part]) -> tuple[dict[str, Any], bytes]:
    """Build a glTF 2.0 document and its BIN payload (no buffer URI yet)."""
    blob = bytearray()
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    mat_index: dict[str, int] = {}

    def add_material(kind: str) -> int:
        if kind in mat_index:
            return mat_index[kind]
        rgb = _MATERIALS.get(kind, (0.7, 0.7, 0.7))
        extra: dict[str, Any] = {}
        if kind == "glass":
            extra["alphaMode"] = "BLEND"
            color: list[float] = [rgb[0], rgb[1], rgb[2], 0.45]
        else:
            color = [rgb[0], rgb[1], rgb[2], 1.0]
        materials.append({
            "name": kind,
            "pbrMetallicRoughness": {
                "baseColorFactor": color,
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8 if kind != "glass" else 0.1,
            },
            **extra,
        })
        mat_index[kind] = len(materials) - 1
        return mat_index[kind]

    def align4() -> None:
        while len(blob) % 4:
            blob.append(0)

    for part in parts:
        if not part.vertices or not part.faces:
            continue
        verts = [_to_meters_yup(v) for v in part.vertices]
        idx = [i for tri in part.faces for i in tri]

        align4()
        pos_off = len(blob)
        pos_bytes = b"".join(struct.pack("<fff", *v) for v in verts)
        blob.extend(pos_bytes)
        mins = [min(v[i] for v in verts) for i in range(3)]
        maxs = [max(v[i] for v in verts) for i in range(3)]
        views.append({"buffer": 0, "byteOffset": pos_off, "byteLength": len(pos_bytes), "target": 34962})
        pos_acc = len(accessors)
        accessors.append({
            "bufferView": len(views) - 1, "componentType": 5126, "count": len(verts),
            "type": "VEC3", "min": mins, "max": maxs,
        })

        normals = _vertex_normals(verts, part.faces)
        align4()
        nrm_off = len(blob)
        nrm_bytes = b"".join(struct.pack("<fff", *v) for v in normals)
        blob.extend(nrm_bytes)
        views.append({"buffer": 0, "byteOffset": nrm_off, "byteLength": len(nrm_bytes), "target": 34962})
        nrm_acc = len(accessors)
        accessors.append({
            "bufferView": len(views) - 1, "componentType": 5126, "count": len(normals),
            "type": "VEC3",
        })

        align4()
        idx_off = len(blob)
        if len(verts) > 65535:
            idx_bytes = b"".join(struct.pack("<I", i) for i in idx)
            ctype = 5125
        else:
            idx_bytes = b"".join(struct.pack("<H", i) for i in idx)
            ctype = 5123
        blob.extend(idx_bytes)
        views.append({"buffer": 0, "byteOffset": idx_off, "byteLength": len(idx_bytes), "target": 34963})
        idx_acc = len(accessors)
        accessors.append({
            "bufferView": len(views) - 1, "componentType": ctype, "count": len(idx),
            "type": "SCALAR",
        })

        meshes.append({
            "name": part.name,
            "primitives": [{
                "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc},
                "indices": idx_acc,
                "material": add_material(part.kind),
            }],
        })
        nodes.append({"name": part.name, "mesh": len(meshes) - 1})

    doc = {
        "asset": {"version": "2.0", "generator": "aiblueprint-mcp"},
        "scene": 0,
        "scenes": [{"name": "Scene", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    return doc, bytes(blob)


def write_gltf(parts: list[Part], gltf_path: Path) -> Path:
    """Write a glTF 2.0 file and a sibling .bin buffer. Returns the .gltf path."""
    doc, blob = _gltf_document_and_blob(parts)
    bin_path = gltf_path.with_suffix(".bin")
    doc["buffers"] = [{"uri": bin_path.name, "byteLength": len(blob)}]
    gltf_path.write_text(json.dumps(doc, indent=2))
    bin_path.write_bytes(blob)
    return gltf_path


def write_glb(parts: list[Part], glb_path: Path) -> Path:
    """Write a binary glTF (GLB) file. Returns the .glb path."""
    doc, blob = _gltf_document_and_blob(parts)
    doc["buffers"] = [{"byteLength": len(blob)}]
    json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "
    bin_blob = bytearray(blob)
    while len(bin_blob) % 4:
        bin_blob.append(0)
    json_len = len(json_bytes)
    bin_len = len(bin_blob)
    total = 12 + 8 + json_len + 8 + bin_len
    payload = b"".join((
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", json_len, b"JSON"),
        json_bytes,
        struct.pack("<I4s", bin_len, b"BIN\x00"),
        bytes(bin_blob),
    ))
    glb_path.write_bytes(payload)
    return glb_path


def write_obj(parts: list[Part], obj_path: Path) -> Path:
    lines = ["# Generated by aiblueprint-mcp view.to_3d", "# Units: metres, Y-up"]
    v_off = 1
    for part in parts:
        lines.append(f"o {_sanitize(part.name)}")
        verts = [_to_meters_yup(v) for v in part.vertices]
        normals = _vertex_normals(verts, part.faces)
        for v in verts:
            lines.append(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
        for nrm in normals:
            lines.append(f"vn {nrm[0]:.6f} {nrm[1]:.6f} {nrm[2]:.6f}")
        for a, b, c in part.faces:
            ia, ib, ic = a + v_off, b + v_off, c + v_off
            lines.append(f"f {ia}//{ia} {ib}//{ib} {ic}//{ic}")
        v_off += len(verts)
    obj_path.write_text("\n".join(lines) + "\n")
    return obj_path


def write_blender_script(parts: list[Part], script_path: Path) -> Path:
    payload = []
    for part in parts:
        payload.append({
            "name": _sanitize(part.name),
            "kind": part.kind,
            "vertices": [list(_to_meters_zup(v)) for v in part.vertices],
            "faces": [list(f) for f in part.faces],
            "color": list(_MATERIALS.get(part.kind, (0.7, 0.7, 0.7))),
        })
    data = json.dumps(payload)
    lines = [
        "# Generated by aiblueprint-mcp view.to_3d.",
        "# Units: metres. Z is up. XY matches the DXF plan.",
        f"# Run: blender --factory-startup -b -P {script_path.name}",
        "# Blender is optional; this script is only needed if you want a .blend or a render.",
        "",
        "import bpy",
        "import json",
        "",
        f"PARTS = json.loads({data!r})",
        "",
        "",
        "def main():",
        '    coll = bpy.data.collections.new("AIBlueprint")',
        "    bpy.context.scene.collection.children.link(coll)",
        "    mats = {}",
        "    for part in PARTS:",
        '        mesh = bpy.data.meshes.new(part["name"])',
        '        mesh.from_pydata([tuple(v) for v in part["vertices"]], [], [tuple(f) for f in part["faces"]])',
        "        mesh.update()",
        '        obj = bpy.data.objects.new(part["name"], mesh)',
        "        coll.objects.link(obj)",
        '        kind = part["kind"]',
        "        if kind not in mats:",
        "            mat = bpy.data.materials.new(kind)",
        "            mat.use_nodes = True",
        '            bsdf = mat.node_tree.nodes.get("Principled BSDF")',
        "            if bsdf is not None:",
        '                bsdf.inputs["Base Color"].default_value = (*part["color"], 1.0)',
        "            mats[kind] = mat",
        "        obj.data.materials.append(mats[kind])",
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]
    script_path.write_text("\n".join(lines))
    return script_path


def validate_to_3d_heights(
    wall_height: float,
    door_height: float,
    window_sill: float,
    window_head: float,
) -> None:
    """Reject combinations that would produce a zero-height window or no lintel."""
    if window_head <= window_sill:
        raise CommandError(
            f"window_head ({window_head:g} ft) must be greater than "
            f"window_sill ({window_sill:g} ft)."
        )
    if window_head > wall_height:
        raise CommandError(
            f"window_head ({window_head:g} ft) must be at most "
            f"wall_height ({wall_height:g} ft)."
        )
    if door_height >= wall_height:
        raise CommandError(
            f"door_height ({door_height:g} ft) must be less than "
            f"wall_height ({wall_height:g} ft) so a lintel can fit."
        )


def build_model(msp, opts: To3dOptions) -> Model3d:
    validate_to_3d_heights(
        opts.wall_height, opts.door_height, opts.window_sill, opts.window_head,
    )
    model = Model3d(
        defaults={
            "wall_height_ft": opts.wall_height,
            "wall_thickness_ft": opts.wall_thickness,
            "door_height_ft": opts.door_height,
            "window_sill_ft": opts.window_sill,
            "window_head_ft": opts.window_head,
        }
    )
    loose, rings = _collect_lines_and_rings(msp, opts)
    model.walls, model.floors = _build_walls(opts, loose, rings)
    if not model.walls:
        raise CommandError(
            "No wall geometry found. Draw a closed polyline or lines on a WALL "
            "layer (or a FOOTPRINT/BUILDING/ADU/STRUCTURE layer), or pass "
            "wall_layers to view.to_3d."
        )
    texts = _collect_texts(msp, opts)
    _infer_ticks(msp, model, opts, texts)
    _infer_inserts(msp, model, opts)
    _infer_text_only(model, opts, texts)
    _apply_explicit(model, opts)
    model.casework = _collect_casework(msp, opts)
    _build_parts(model)
    return model


def export_to_3d(
    msp,
    *,
    config: Config,
    drawing_name: str = "untitled",
    path: str | None = None,
    formats: Iterable[str] | None = None,
    wall_height: float = DEFAULT_WALL_HEIGHT_FT,
    wall_thickness: float = DEFAULT_WALL_THICKNESS_FT,
    door_height: float = DEFAULT_DOOR_HEIGHT_FT,
    window_sill: float = DEFAULT_WINDOW_SILL_FT,
    window_head: float = DEFAULT_WINDOW_HEAD_FT,
    thickness_side: ThicknessSide | None = None,
    wall_layers: list[str] | None = None,
    opening_layers: list[str] | None = None,
    ignore_layers: list[str] | None = None,
    ignore_layers_mode: Literal["add", "replace"] = "add",
    include_casework: bool = True,
    openings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the model and write requested formats inside the workspace."""
    fmts = [f.lower() for f in (list(formats) if formats is not None else list(DEFAULT_FORMATS))]
    allowed = {"gltf", "glb", "obj", "blender.py"}
    bad = [f for f in fmts if f not in allowed]
    if bad:
        raise CommandError(f"Unknown 3D format {bad[0]!r}. Use gltf, glb, obj, or blender.py.")
    if not fmts:
        raise CommandError("formats must include at least one of gltf, glb, obj, blender.py.")

    default_ext = "gltf"
    if "glb" in fmts and "gltf" not in fmts:
        default_ext = "glb"
    elif "gltf" not in fmts and "glb" not in fmts:
        default_ext = "obj" if "obj" in fmts else "blender.py"
    target = path or f"{drawing_name}.{default_ext}"
    try:
        resolved = config.resolve_path(target)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    suffix = resolved.suffix.lower()
    if resolved.name.endswith(".blender.py"):
        stem = resolved.with_name(resolved.name[: -len(".blender.py")])
    elif suffix in {".gltf", ".glb", ".obj", ".py", ".json"}:
        stem = resolved.with_suffix("")
    else:
        stem = resolved
    # A .glb path writes a real binary GLB (not a misnamed .gltf).
    if suffix == ".glb" and "glb" not in fmts:
        fmts = ["glb" if f == "gltf" else f for f in fmts]
        if "glb" not in fmts:
            fmts.insert(0, "glb")

    opts = To3dOptions(
        wall_height=wall_height,
        wall_thickness=wall_thickness,
        door_height=door_height,
        window_sill=window_sill,
        window_head=window_head,
        thickness_side=thickness_side,
        wall_layers=wall_layers,
        opening_layers=opening_layers,
        ignore_layers=ignore_layers,
        ignore_layers_mode=ignore_layers_mode,
        include_casework=include_casework,
        openings=openings,
    )
    model = build_model(msp, opts)
    ir = model_to_ir(model)
    ir_path = stem.with_name(stem.name + ".model3d.json")
    ir_path.write_text(json.dumps(ir, indent=2))

    files: dict[str, str] = {"ir": str(ir_path)}
    if "gltf" in fmts:
        gp = write_gltf(model.parts, stem.with_suffix(".gltf"))
        files["gltf"] = str(gp)
        files["gltf_bin"] = str(gp.with_suffix(".bin"))
    if "glb" in fmts:
        files["glb"] = str(write_glb(model.parts, stem.with_suffix(".glb")))
    if "obj" in fmts:
        files["obj"] = str(write_obj(model.parts, stem.with_suffix(".obj")))
    if "blender.py" in fmts:
        files["blender_py"] = str(write_blender_script(model.parts, stem.with_name(stem.name + ".blender.py")))

    if "glb" in fmts and suffix == ".glb":
        primary, primary_fmt = files["glb"], "glb"
    elif "gltf" in fmts:
        primary, primary_fmt = files["gltf"], "gltf"
    elif "glb" in fmts:
        primary, primary_fmt = files["glb"], "glb"
    elif "obj" in fmts:
        primary, primary_fmt = files["obj"], "obj"
    else:
        primary, primary_fmt = files["blender_py"], "blender.py"

    return {
        "path": primary,
        "format": primary_fmt,
        "files": files,
        "wall_count": model.wall_count,
        "opening_count": model.opening_count,
        "casework_count": model.casework_count,
        "part_count": len(model.parts),
        "ir_path": str(ir_path),
        "units": "feet in the JSON model; metres in glTF/OBJ/Blender",
        "warnings": list(model.warnings),
    }
