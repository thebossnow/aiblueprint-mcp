"""AIBlueprint backend — enhanced ezdxf engine.

Based on autocad-mcp's ezdxf_backend (MIT), extended for site-plan drafting:
  - multi-document sessions (registry of named drawings)
  - entity_offset: parallel polyline offset (open and closed)
  - entity_fillet: fillet arc between two lines
  - entity_measure: area / perimeter / length takeoff
  - dimension style overrides (dimtxt, dimasz, dimlunit, ...)
  - solid-fill hatch support
  - RGB true-color layers
  - PNG/PDF/SVG rendering + LibreCAD preview

Configuration is resolved lazily via ``Config`` (see config.py), not at import
time, so hosts and tests can override the environment first.
"""

from __future__ import annotations

import base64
import functools
import io
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ezdxf
import structlog

from aiblueprint_mcp.config import Config
from aiblueprint_mcp.types import CommandError, CommandResult

log = structlog.get_logger()


def _op(func: Callable) -> Callable:
    """Wrap a backend coroutine so exceptions become CommandResult errors."""

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except CommandError as ce:
            return CommandResult(ok=False, error=str(ce))
        except Exception as ex:  # unexpected — log with op name for debugging
            log.error("backend_op_failed", op=func.__name__, error=str(ex))
            return CommandResult(ok=False, error=f"{func.__name__}: {ex}")

    return wrapper


@dataclass
class _DocState:
    """One open drawing in the session."""

    doc: Any
    msp: Any
    save_path: str | None = None
    counter: int = 0
    name: str = "untitled"


class AIBlueprintBackend:
    """Pure-Python DXF generation via ezdxf — multi-document, extended."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.from_env()
        self._docs: dict[str, _DocState] = {}
        self._current: str | None = None
        self._handle_seq = 0

    @property
    def name(self) -> str:
        return "aiblueprint"

    # ── Current-document accessors ─────────────────────────────────────

    def _state(self) -> _DocState:
        if self._current is None or self._current not in self._docs:
            raise CommandError("No document open. Create or open a drawing first.")
        return self._docs[self._current]

    @property
    def _doc(self):
        return self._state().doc

    @property
    def _msp(self):
        return self._state().msp

    def _new_handle(self) -> str:
        self._handle_seq += 1
        return f"dwg_{self._handle_seq}"

    def _get_entity(self, entity_id: str):
        """Resolve an entity by handle, supporting the alias 'last'."""
        st = self._state()
        if entity_id == "last":
            entities = list(st.msp)
            if not entities:
                raise CommandError("No entities to reference with 'last'")
            return entities[-1]
        e = st.doc.entitydb.get(entity_id)
        if e is None:
            raise CommandError(f"Entity {entity_id} not found")
        return e

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def initialize(self) -> CommandResult:
        await self.drawing_create(None)
        return CommandResult(
            ok=True, payload={"backend": "aiblueprint", "version": ezdxf.__version__}
        )

    @_op
    async def status(self) -> CommandResult:
        has_doc = self._current is not None
        entity_count = len(self._msp) if has_doc else 0
        layers = [layer.dxf.name for layer in self._doc.layers] if has_doc else []
        return CommandResult(
            ok=True,
            payload={
                "backend": "aiblueprint",
                "version": ezdxf.__version__,
                "has_document": has_doc,
                "documents": list(self._docs),
                "current": self._current,
                "entity_count": entity_count,
                "layers": layers,
            },
        )

    def _ensure_layer(self, layer: str | None):
        if layer and layer not in self._doc.layers:
            self._doc.layers.add(layer)

    # ── Color helpers ──────────────────────────────────────────────────

    @staticmethod
    def _color_to_int(color: str | int) -> int:
        if isinstance(color, int):
            return color
        color_map = {
            "red": 1, "yellow": 2, "green": 3, "cyan": 4,
            "blue": 5, "magenta": 6, "white": 7, "grey": 8, "gray": 8,
            "darkgrey": 8, "lightgrey": 9, "lightgray": 9,
        }
        return color_map.get(str(color).lower(), 7)

    # ── Drawing / session management ───────────────────────────────────

    @_op
    async def drawing_create(self, name: str | None = None) -> CommandResult:
        doc = ezdxf.new("R2013")
        handle = self._new_handle()
        self._docs[handle] = _DocState(
            doc=doc,
            msp=doc.modelspace(),
            save_path=f"{name}.dxf" if name else None,
            name=name or "untitled",
        )
        self._current = handle
        return CommandResult(ok=True, payload={"handle": handle, "name": name or "untitled"})

    @_op
    async def drawing_list(self) -> CommandResult:
        docs = [
            {"handle": h, "name": st.name, "save_path": st.save_path,
             "entity_count": len(st.msp), "current": h == self._current}
            for h, st in self._docs.items()
        ]
        return CommandResult(ok=True, payload={"documents": docs, "current": self._current})

    @_op
    async def drawing_switch(self, handle: str) -> CommandResult:
        if handle not in self._docs:
            raise CommandError(f"No document with handle '{handle}'")
        self._current = handle
        return CommandResult(ok=True, payload={"current": handle, "name": self._docs[handle].name})

    @_op
    async def drawing_info(self) -> CommandResult:
        st = self._state()
        layers = [layer.dxf.name for layer in st.doc.layers]
        blocks = [b.name for b in st.doc.blocks if not b.name.startswith("*")]
        return CommandResult(ok=True, payload={
            "handle": self._current,
            "name": st.name,
            "entity_count": len(st.msp),
            "layers": layers,
            "blocks": blocks,
            "dxf_version": st.doc.dxfversion,
            "save_path": st.save_path,
        })

    @_op
    async def drawing_save(self, path: str | None = None) -> CommandResult:
        st = self._state()
        target = path or st.save_path
        if not target:
            raise CommandError("No save path specified")
        resolved = self.config.resolve_path(target)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        st.doc.saveas(str(resolved))
        st.save_path = str(resolved)
        return CommandResult(ok=True, payload={"path": str(resolved)})

    @_op
    async def drawing_open(self, path: str) -> CommandResult:
        resolved = self.config.resolve_path(path)
        if not resolved.exists():
            raise CommandError(f"File not found: {resolved}")
        doc = ezdxf.readfile(str(resolved))
        handle = self._new_handle()
        self._docs[handle] = _DocState(
            doc=doc, msp=doc.modelspace(),
            save_path=str(resolved), name=resolved.stem,
        )
        self._current = handle
        return CommandResult(ok=True, payload={"handle": handle, "path": str(resolved)})

    # ── Entity creation ────────────────────────────────────────────────

    @_op
    async def create_line(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "LINE", "handle": e.dxf.handle})

    @_op
    async def create_circle(self, cx, cy, radius, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_circle((cx, cy), radius, dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "CIRCLE", "handle": e.dxf.handle})

    @_op
    async def create_polyline(self, points, closed=False, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        pts = [(p[0], p[1]) for p in points]
        e = self._msp.add_lwpolyline(pts, close=closed, dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "LWPOLYLINE", "handle": e.dxf.handle})

    async def create_rectangle(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        return await self.create_polyline(pts, closed=True, layer=layer)

    @_op
    async def create_arc(self, cx, cy, radius, start_angle, end_angle, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_arc((cx, cy), radius, start_angle, end_angle,
                              dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "ARC", "handle": e.dxf.handle})

    @_op
    async def create_text(self, x, y, text, height=2.5, rotation=0.0, layer=None,
                          align=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_text(text, dxfattribs={
            "height": height, "rotation": rotation, "layer": layer or "0",
        })
        if align:
            from ezdxf.enums import TextEntityAlignment
            e.set_placement((x, y), align=TextEntityAlignment[align.upper()])
        else:
            e.dxf.insert = (x, y)
        return CommandResult(ok=True, payload={"entity_type": "TEXT", "handle": e.dxf.handle})

    @_op
    async def create_mtext(self, x, y, width, text, height=2.5, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_mtext(text, dxfattribs={
            "insert": (x, y), "char_height": height, "width": width, "layer": layer or "0",
        })
        return CommandResult(ok=True, payload={"entity_type": "MTEXT", "handle": e.dxf.handle})

    # ── Entity query ───────────────────────────────────────────────────

    @_op
    async def entity_list(self, layer=None) -> CommandResult:
        entities = []
        for e in self._msp:
            if layer and e.dxf.get("layer", "0") != layer:
                continue
            entities.append({
                "type": e.dxftype(), "handle": e.dxf.handle,
                "layer": e.dxf.get("layer", "0"),
            })
        return CommandResult(ok=True, payload={"entities": entities, "count": len(entities)})

    @_op
    async def entity_get(self, entity_id) -> CommandResult:
        e = self._get_entity(entity_id)
        t = e.dxftype()
        info: dict[str, Any] = {"type": t, "handle": e.dxf.handle, "layer": e.dxf.get("layer", "0")}
        if t == "LINE":
            info["start"] = list(e.dxf.start)[:2]
            info["end"] = list(e.dxf.end)[:2]
        elif t == "CIRCLE":
            info["center"] = list(e.dxf.center)[:2]
            info["radius"] = e.dxf.radius
        elif t == "ARC":
            info["center"] = list(e.dxf.center)[:2]
            info["radius"] = e.dxf.radius
            info["start_angle"] = e.dxf.start_angle
            info["end_angle"] = e.dxf.end_angle
        elif t == "LWPOLYLINE":
            info["points"] = [[float(p[0]), float(p[1])] for p in e.get_points(format="xy")]
            info["closed"] = bool(e.closed)
        elif t == "TEXT":
            info["text"] = e.dxf.text
            info["insert"] = list(e.dxf.insert)[:2]
            info["height"] = e.dxf.height
        elif t == "MTEXT":
            info["text"] = e.text
            info["insert"] = list(e.dxf.insert)[:2]
            info["height"] = e.dxf.char_height
        elif t == "INSERT":
            info["block"] = e.dxf.name
            info["insert"] = list(e.dxf.insert)[:2]
            info["rotation"] = e.dxf.get("rotation", 0.0)
        elif t == "HATCH":
            info["pattern"] = e.dxf.get("pattern_name", "SOLID")
        return CommandResult(ok=True, payload=info)

    @_op
    async def entity_measure(self, entity_id) -> CommandResult:
        """Return area / perimeter / length for an entity (quantity takeoff)."""
        e = self._get_entity(entity_id)
        t = e.dxftype()
        if t == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points(format="xy")]
            perim = _polyline_length(pts, bool(e.closed))
            payload = {"type": t, "perimeter": float(perim)}
            if e.closed:
                payload["area"] = float(abs(_polygon_area(pts)))
            return CommandResult(ok=True, payload=payload)
        if t == "CIRCLE":
            r = e.dxf.radius
            return CommandResult(ok=True, payload={
                "type": t, "area": math.pi * r * r, "circumference": 2 * math.pi * r,
            })
        if t == "LINE":
            length = math.dist(e.dxf.start[:2], e.dxf.end[:2])
            return CommandResult(ok=True, payload={"type": t, "length": length})
        if t == "ARC":
            r = e.dxf.radius
            sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360
            return CommandResult(ok=True, payload={
                "type": t, "length": math.radians(sweep) * r, "sweep_degrees": sweep,
            })
        raise CommandError(f"Cannot measure entity type {t}")

    # ── Entity modification ────────────────────────────────────────────

    @_op
    async def entity_erase(self, entity_id) -> CommandResult:
        e = self._get_entity(entity_id)
        self._msp.delete_entity(e)
        return CommandResult(ok=True, payload={"erased": entity_id})

    @_op
    async def entity_copy(self, entity_id, dx, dy) -> CommandResult:
        e = self._get_entity(entity_id)
        copy = e.copy()
        self._msp.add_entity(copy)
        copy.translate(dx, dy, 0)
        return CommandResult(ok=True, payload={"handle": copy.dxf.handle})

    @_op
    async def entity_move(self, entity_id, dx, dy) -> CommandResult:
        e = self._get_entity(entity_id)
        e.translate(dx, dy, 0)
        return CommandResult(ok=True, payload={"moved": entity_id})

    @_op
    async def entity_rotate(self, entity_id, cx, cy, angle) -> CommandResult:
        from ezdxf.math import Matrix44
        e = self._get_entity(entity_id)
        m = Matrix44.z_rotate(math.radians(angle))
        e.translate(-cx, -cy, 0)
        e.transform(m)
        e.translate(cx, cy, 0)
        return CommandResult(ok=True, payload={"rotated": entity_id})

    @_op
    async def entity_scale(self, entity_id, cx, cy, factor) -> CommandResult:
        from ezdxf.math import Matrix44
        e = self._get_entity(entity_id)
        m = Matrix44.scale(factor, factor, factor)
        e.translate(-cx, -cy, 0)
        e.transform(m)
        e.translate(cx, cy, 0)
        return CommandResult(ok=True, payload={"scaled": entity_id})

    @_op
    async def entity_mirror(self, entity_id, x1, y1, x2, y2) -> CommandResult:
        from ezdxf.math import Matrix44
        e = self._get_entity(entity_id)
        dx, dy = x2 - x1, y2 - y1
        if dx * dx + dy * dy == 0:
            raise CommandError("Mirror line has zero length")
        copy = e.copy()
        self._msp.add_entity(copy)
        a = math.atan2(dy, dx)
        cos2a, sin2a = math.cos(2 * a), math.sin(2 * a)
        m = Matrix44([
            cos2a, sin2a, 0, 0,
            sin2a, -cos2a, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        ])
        copy.translate(-x1, -y1, 0)
        copy.transform(m)
        copy.translate(x1, y1, 0)
        return CommandResult(ok=True, payload={"handle": copy.dxf.handle})

    @_op
    async def entity_array(self, entity_id, rows, cols, row_dist, col_dist) -> CommandResult:
        e = self._get_entity(entity_id)
        handles = []
        for r in range(rows):
            for c in range(cols):
                if r == 0 and c == 0:
                    continue
                copy = e.copy()
                self._msp.add_entity(copy)
                copy.translate(c * col_dist, r * row_dist, 0)
                handles.append(copy.dxf.handle)
        return CommandResult(ok=True, payload={"copies": len(handles), "handles": handles})

    @_op
    async def entity_offset(self, entity_id, distance) -> CommandResult:
        """Offset an LWPOLYLINE by ``distance`` (open or closed)."""
        e = self._get_entity(entity_id)
        if e.dxftype() != "LWPOLYLINE":
            raise CommandError("Offset only supports LWPOLYLINE entities")

        points = list(e.get_points(format="xy"))
        if len(points) < 2:
            raise CommandError("Need at least 2 points for offset")

        is_closed = bool(e.closed)
        offset_pts = _offset_polyline(points, distance, is_closed)
        e_new = self._msp.add_lwpolyline(
            offset_pts, close=is_closed,
            dxfattribs={"layer": e.dxf.get("layer", "0")},
        )
        return CommandResult(ok=True, payload={
            "entity_type": "LWPOLYLINE", "handle": e_new.dxf.handle,
            "offset": distance, "points": len(offset_pts),
        })

    @_op
    async def entity_fillet(self, entity_id1, entity_id2, radius) -> CommandResult:
        """Fillet two LINE entities with an arc of the given radius."""
        e1 = self._get_entity(entity_id1)
        e2 = self._get_entity(entity_id2)
        if e1.dxftype() != "LINE" or e2.dxftype() != "LINE":
            raise CommandError("Fillet requires two LINE entities")

        l1_start = (e1.dxf.start.x, e1.dxf.start.y)
        l1_end = (e1.dxf.end.x, e1.dxf.end.y)
        l2_start = (e2.dxf.start.x, e2.dxf.start.y)
        l2_end = (e2.dxf.end.x, e2.dxf.end.y)

        inter = _line_intersection(l1_start, l1_end, l2_start, l2_end)
        if not inter:
            raise CommandError("Lines are parallel — cannot fillet")

        d1a = math.dist(l1_start, inter)
        d1b = math.dist(l1_end, inter)
        keep1 = l1_start if d1a > d1b else l1_end

        d2a = math.dist(l2_start, inter)
        d2b = math.dist(l2_end, inter)
        keep2 = l2_start if d2a > d2b else l2_end

        v1_far = (keep1[0] - inter[0], keep1[1] - inter[1])
        v2_far = (keep2[0] - inter[0], keep2[1] - inter[1])
        len1 = math.hypot(*v1_far)
        len2 = math.hypot(*v2_far)
        if len1 == 0 or len2 == 0:
            raise CommandError("Zero-length line segment")

        u1 = (-v1_far[0] / len1, -v1_far[1] / len1)
        u2 = (-v2_far[0] / len2, -v2_far[1] / len2)

        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        half_angle = math.acos(dot) / 2
        if abs(math.sin(half_angle)) < 1e-10:
            raise CommandError("Lines are nearly parallel")

        tan_dist = radius / math.tan(half_angle)
        t1 = (inter[0] + u1[0] * tan_dist, inter[1] + u1[1] * tan_dist)
        t2 = (inter[0] + u2[0] * tan_dist, inter[1] + u2[1] * tan_dist)

        perp1 = (-u1[1], u1[0])
        center = (t1[0] + perp1[0] * radius, t1[1] + perp1[1] * radius)
        if math.dist(center, inter) > math.dist(t1, inter):
            center = (t1[0] - perp1[0] * radius, t1[1] - perp1[1] * radius)

        start_angle = math.degrees(math.atan2(t1[1] - center[1], t1[0] - center[0]))
        end_angle = math.degrees(math.atan2(t2[1] - center[1], t2[0] - center[0]))
        arc = self._msp.add_arc(center, radius, start_angle, end_angle,
                                dxfattribs={"layer": e1.dxf.get("layer", "0")})

        e1.dxf.start = keep1
        e1.dxf.end = t1
        e2.dxf.start = keep2
        e2.dxf.end = t2
        return CommandResult(ok=True, payload={
            "entity_type": "ARC", "handle": arc.dxf.handle,
            "radius": radius, "tangent_points": [list(t1), list(t2)],
        })

    # ── Layer operations ───────────────────────────────────────────────

    @_op
    async def layer_list(self) -> CommandResult:
        layers = []
        for layer in self._doc.layers:
            entry = {
                "name": layer.dxf.name,
                "color": layer.dxf.get("color", 7),
                "linetype": layer.dxf.get("linetype", "Continuous"),
                "is_frozen": layer.is_frozen(),
                "is_locked": layer.is_locked(),
            }
            if layer.dxf.hasattr("true_color"):
                entry["true_color"] = list(layer.rgb) if layer.rgb else None
            layers.append(entry)
        return CommandResult(ok=True, payload={"layers": layers})

    @_op
    async def layer_create(self, name, color="white", linetype="CONTINUOUS",
                           true_color=None) -> CommandResult:
        if name in self._doc.layers:
            return CommandResult(ok=True, payload={"name": name, "existed": True})
        color_int = self._color_to_int(color)
        layer = self._doc.layers.add(name, color=color_int, linetype=linetype)
        if true_color:
            layer.rgb = tuple(true_color)
        return CommandResult(ok=True, payload={"name": name, "color": color_int})

    @_op
    async def layer_set_current(self, name) -> CommandResult:
        if name not in self._doc.layers:
            raise CommandError(f"Layer '{name}' does not exist")
        self._doc.header["$CLAYER"] = name
        return CommandResult(ok=True, payload={"current_layer": name})

    @_op
    async def layer_set_properties(self, name, color=None, linetype=None,
                                   true_color=None) -> CommandResult:
        if name not in self._doc.layers:
            raise CommandError(f"Layer '{name}' does not exist")
        layer = self._doc.layers.get(name)
        if color is not None:
            layer.color = self._color_to_int(color)
        if linetype is not None:
            layer.dxf.linetype = linetype
        if true_color is not None:
            layer.rgb = tuple(true_color)
        return CommandResult(ok=True, payload={"name": name})

    @_op
    async def layer_freeze(self, name) -> CommandResult:
        self._require_layer(name).freeze()
        return CommandResult(ok=True, payload={"name": name, "frozen": True})

    @_op
    async def layer_thaw(self, name) -> CommandResult:
        self._require_layer(name).thaw()
        return CommandResult(ok=True, payload={"name": name, "frozen": False})

    @_op
    async def layer_lock(self, name) -> CommandResult:
        self._require_layer(name).lock()
        return CommandResult(ok=True, payload={"name": name, "locked": True})

    @_op
    async def layer_unlock(self, name) -> CommandResult:
        self._require_layer(name).unlock()
        return CommandResult(ok=True, payload={"name": name, "locked": False})

    def _require_layer(self, name: str):
        if name not in self._doc.layers:
            raise CommandError(f"Layer '{name}' does not exist")
        return self._doc.layers.get(name)

    # ── Annotation (with dimension overrides) ──────────────────────────

    @_op
    async def create_dimension_aligned(self, x1, y1, x2, y2, offset,
                                       dim_overrides=None) -> CommandResult:
        dim = self._msp.add_aligned_dim(p1=(x1, y1), p2=(x2, y2), distance=offset)
        applied = self._apply_overrides(dim, dim_overrides)
        dim.render()
        return CommandResult(ok=True, payload={"entity_type": "DIMENSION", "overrides_applied": applied})

    @_op
    async def create_dimension_linear(self, x1, y1, x2, y2, dim_x, dim_y,
                                      dim_overrides=None) -> CommandResult:
        dim = self._msp.add_linear_dim(base=(dim_x, dim_y), p1=(x1, y1), p2=(x2, y2))
        applied = self._apply_overrides(dim, dim_overrides)
        dim.render()
        return CommandResult(ok=True, payload={"entity_type": "DIMENSION", "overrides_applied": applied})

    @_op
    async def create_dimension_angular(self, cx, cy, x1, y1, x2, y2,
                                       dim_overrides=None) -> CommandResult:
        a1 = math.atan2(y1 - cy, x1 - cx)
        a2 = math.atan2(y2 - cy, x2 - cx)
        r = max(math.hypot(x1 - cx, y1 - cy), math.hypot(x2 - cx, y2 - cy)) * 0.7
        dim = self._msp.add_angular_dim_cra(
            center=(cx, cy), radius=r,
            start_angle=math.degrees(a1), end_angle=math.degrees(a2), distance=r * 1.2,
        )
        applied = self._apply_overrides(dim, dim_overrides)
        dim.render()
        return CommandResult(ok=True, payload={"entity_type": "DIMENSION", "overrides_applied": applied})

    @_op
    async def create_dimension_radius(self, cx, cy, radius, angle,
                                      dim_overrides=None) -> CommandResult:
        rad = math.radians(angle)
        px = cx + radius * math.cos(rad)
        py = cy + radius * math.sin(rad)
        dim = self._msp.add_radius_dim(center=(cx, cy), mpoint=(px, py))
        applied = self._apply_overrides(dim, dim_overrides)
        dim.render()
        return CommandResult(ok=True, payload={"entity_type": "DIMENSION", "overrides_applied": applied})

    @_op
    async def create_leader(self, points, text) -> CommandResult:
        pts = [(p[0], p[1]) for p in points]
        self._msp.add_leader(pts)
        last = pts[-1]
        self._msp.add_mtext(text, dxfattribs={
            "insert": (last[0] + 2, last[1]), "char_height": 2.5, "width": 30,
        })
        return CommandResult(ok=True, payload={"entity_type": "LEADER"})

    @staticmethod
    def _apply_overrides(dim, dim_overrides) -> list[str]:
        if not dim_overrides:
            return []
        applied = []
        for attr in ["dimtxt", "dimasz", "dimlunit", "dimclrd", "dimclre", "dimclrt", "dimtxsty"]:
            if attr in dim_overrides:
                setattr(dim.dimstyle.dxf, attr, dim_overrides[attr])
                applied.append(attr)
        return applied

    # ── Hatch ──────────────────────────────────────────────────────────

    @_op
    async def create_hatch(self, entity_id, pattern="ANSI31", scale=1.0) -> CommandResult:
        e = self._get_entity(entity_id)
        if e.dxftype() == "LWPOLYLINE":
            boundary_pts = [(p[0], p[1]) for p in e.get_points(format="xy")]
        elif e.dxftype() == "CIRCLE":
            cx, cy = e.dxf.center.x, e.dxf.center.y
            r = e.dxf.radius
            n = 36
            boundary_pts = [
                (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
                for i in range(n)
            ]
        else:
            raise CommandError(f"Cannot hatch entity type {e.dxftype()}")

        hatch = self._msp.add_hatch()
        if pattern.upper() == "SOLID":
            hatch.set_solid_fill()
        else:
            hatch.set_pattern_fill(pattern, scale=scale)
        hatch.paths.add_polyline_path(boundary_pts, is_closed=True)
        return CommandResult(ok=True, payload={
            "entity_type": "HATCH", "handle": hatch.dxf.handle,
            "pattern": pattern, "is_solid": pattern.upper() == "SOLID",
        })

    # ── Block operations ───────────────────────────────────────────────

    @_op
    async def block_define(self, name, entities) -> CommandResult:
        block = self._doc.blocks.new(name=name)
        for d in entities:
            etype = d.get("type", "LINE")
            if etype == "LINE":
                block.add_line((d.get("x1", 0), d.get("y1", 0)), (d.get("x2", 0), d.get("y2", 0)))
            elif etype == "CIRCLE":
                block.add_circle((d.get("cx", 0), d.get("cy", 0)), d.get("radius", 1))
            elif etype == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in d.get("points", [])]
                block.add_lwpolyline(pts, close=d.get("closed", False))
            elif etype == "ATTDEF":
                block.add_attdef(d.get("tag", "TAG"), (d.get("x", 0), d.get("y", 0)),
                                 dxfattribs={"height": d.get("height", 2.5)})
        return CommandResult(ok=True, payload={"block": name, "entity_count": len(entities)})

    @_op
    async def block_list(self) -> CommandResult:
        blocks = [b.name for b in self._doc.blocks if not b.name.startswith("*")]
        return CommandResult(ok=True, payload={"blocks": blocks})

    @_op
    async def block_insert(self, name, x, y, scale=1.0, rotation=0.0) -> CommandResult:
        if name not in self._doc.blocks:
            raise CommandError(f"Block '{name}' not defined")
        e = self._msp.add_blockref(name, (x, y), dxfattribs={
            "xscale": scale, "yscale": scale, "zscale": scale, "rotation": rotation,
        })
        return CommandResult(ok=True, payload={"entity_type": "INSERT", "handle": e.dxf.handle})

    @_op
    async def block_insert_with_attributes(self, name, x, y, scale=1.0, rotation=0.0,
                                           attributes=None) -> CommandResult:
        if name not in self._doc.blocks:
            raise CommandError(f"Block '{name}' not defined")
        e = self._msp.add_blockref(name, (x, y), dxfattribs={
            "xscale": scale, "yscale": scale, "zscale": scale, "rotation": rotation,
        })
        if attributes:
            try:
                e.add_auto_attribs(attributes)
            except Exception:
                for tag, value in attributes.items():
                    try:
                        e.add_attrib(tag, value, (x, y))
                    except Exception:
                        pass
        return CommandResult(ok=True, payload={"entity_type": "INSERT", "handle": e.dxf.handle})

    @_op
    async def block_get_attributes(self, entity_id) -> CommandResult:
        e = self._get_entity(entity_id)
        if e.dxftype() != "INSERT":
            raise CommandError("Not an INSERT entity")
        attribs = {a.dxf.tag: a.dxf.text for a in e.attribs}
        return CommandResult(ok=True, payload={"attributes": attribs})

    @_op
    async def block_update_attribute(self, entity_id, tag, value) -> CommandResult:
        e = self._get_entity(entity_id)
        if e.dxftype() != "INSERT":
            raise CommandError("Not an INSERT entity")
        for attrib in e.attribs:
            if attrib.dxf.tag.upper() == tag.upper():
                attrib.dxf.text = value
                return CommandResult(ok=True, payload={"tag": tag, "value": value})
        raise CommandError(f"Attribute '{tag}' not found")

    # ── Rendering ──────────────────────────────────────────────────────

    def _render_bytes(self, fmt: str = "png") -> bytes:
        """Render the current modelspace to image bytes via matplotlib."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        fig, ax = plt.subplots(figsize=(16, 10), dpi=150)
        try:
            ax.set_aspect("equal")
            Frontend(RenderContext(self._doc), MatplotlibBackend(ax)).draw_layout(self._msp)
            buf = io.BytesIO()
            fig.savefig(buf, format=fmt, bbox_inches="tight", pad_inches=0.1)
        finally:
            plt.close(fig)
        return buf.getvalue()

    @_op
    async def get_screenshot(self) -> CommandResult:
        """Render the current drawing to a base64 PNG."""
        self._state()  # ensure a doc is open
        data = base64.b64encode(self._render_bytes("png")).decode("ascii")
        return CommandResult(ok=True, payload={"image_base64": data, "format": "png"})

    @_op
    async def export(self, fmt: str = "pdf", path: str | None = None) -> CommandResult:
        """Export the current drawing to PNG/PDF/SVG in the workspace."""
        fmt = fmt.lower()
        if fmt not in ("pdf", "png", "svg"):
            raise CommandError("export format must be pdf, png, or svg")
        st = self._state()
        target = path or f"{st.name}.{fmt}"
        resolved = self.config.resolve_path(target)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(self._render_bytes(fmt))
        return CommandResult(ok=True, payload={"path": str(resolved), "format": fmt})

    @_op
    async def preview(self, save_first: bool = True) -> CommandResult:
        """Save DXF and render a PNG preview via LibreCAD dxf2png."""
        st = self._state()
        tmp = self.config.ensure_workspace()
        dxf_path = tmp / "aiblueprint_preview.dxf"
        png_path = tmp / "aiblueprint_preview.png"
        st.doc.saveas(str(dxf_path))

        librecad = self.config.librecad_bin
        if not librecad.exists():
            raise CommandError(
                f"LibreCAD not found at {librecad}. "
                "Set AIBLUEPRINT_LIBRECAD_BIN to your librecad path, "
                "or build from source: https://docs.librecad.org/en/latest/appx/build.html"
            )

        import os
        try:
            result = subprocess.run(
                [str(librecad), "dxf2png", "-o", str(png_path), str(dxf_path)],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "DISPLAY": self.config.display},
                cwd=str(self.config.workspace),
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandError("dxf2png timed out") from exc
        if result.returncode != 0:
            raise CommandError(result.stderr.strip() or "dxf2png failed")
        return CommandResult(ok=True, payload={
            "dxf_path": str(dxf_path), "png_path": str(png_path),
            "entity_count": len(st.msp),
        })


# ── Geometry helpers ───────────────────────────────────────────────────


def _line_intersection(p1, p2, p3, p4):
    """Intersection of infinite lines through (p1,p2) and (p3,p4), or None."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-10:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _polygon_area(pts) -> float:
    """Signed area of a polygon via the shoelace formula."""
    n = len(pts)
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2


def _polyline_length(pts, closed: bool) -> float:
    total = 0.0
    for i in range(len(pts) - 1):
        total += math.dist(pts[i], pts[i + 1])
    if closed and len(pts) > 1:
        total += math.dist(pts[-1], pts[0])
    return total


def _offset_polyline(points, distance: float, closed: bool):
    """Offset a polyline by ``distance`` using edge-normal intersection.

    Positive distance offsets to the left of each directed edge. Works for
    open and closed polylines; endpoints of open polylines are offset along
    their single adjacent edge normal.
    """
    n = len(points)
    result = []
    for i in range(n):
        has_prev = closed or i > 0
        has_next = closed or i < n - 1
        p_curr = points[i]
        p_prev = points[(i - 1) % n] if has_prev else None
        p_next = points[(i + 1) % n] if has_next else None

        def edge_normal(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length == 0:
                return None
            return (-dy / length, dx / length)

        n1 = edge_normal(p_prev, p_curr) if has_prev else None
        n2 = edge_normal(p_curr, p_next) if has_next else None

        if n1 and n2:
            e1s = (p_prev[0] + n1[0] * distance, p_prev[1] + n1[1] * distance)
            e1e = (p_curr[0] + n1[0] * distance, p_curr[1] + n1[1] * distance)
            e2s = (p_curr[0] + n2[0] * distance, p_curr[1] + n2[1] * distance)
            e2e = (p_next[0] + n2[0] * distance, p_next[1] + n2[1] * distance)
            inter = _line_intersection(e1s, e1e, e2s, e2e)
            result.append(inter if inter else e1e)
        elif n2:  # first vertex of an open polyline
            result.append((p_curr[0] + n2[0] * distance, p_curr[1] + n2[1] * distance))
        elif n1:  # last vertex of an open polyline
            result.append((p_curr[0] + n1[0] * distance, p_curr[1] + n1[1] * distance))
        else:
            result.append(p_curr)
    return result
