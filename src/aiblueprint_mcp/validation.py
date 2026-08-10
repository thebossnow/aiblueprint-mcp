"""Input validation models for aiblueprint-mcp tools.

Each tool operation that takes structured input has a Pydantic model here.
``validate(operation, data)`` returns a validated dict (with defaults applied)
or raises ``ValidationError`` with a clear, LLM-readable message.

This keeps the 8-tool consolidated surface while giving every operation a real
schema and friendly errors instead of raw ``KeyError``s.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = ["validate", "ValidationError"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── drawing ────────────────────────────────────────────────────────────
class DrawingCreate(_Base):
    name: str | None = None


class DrawingOpen(_Base):
    path: str


class DrawingSave(_Base):
    path: str | None = None


class DrawingSwitch(_Base):
    handle: str


# ── entity ─────────────────────────────────────────────────────────────
class CreateLine(_Base):
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str | None = None


class CreateCircle(_Base):
    cx: float
    cy: float
    radius: float = Field(gt=0)
    layer: str | None = None


class CreatePolyline(_Base):
    points: list[list[float]] = Field(min_length=2)
    closed: bool = False
    layer: str | None = None


class CreateRectangle(_Base):
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str | None = None


class CreateArc(_Base):
    cx: float
    cy: float
    radius: float = Field(gt=0)
    start_angle: float
    end_angle: float
    layer: str | None = None


class CreateText(_Base):
    x: float
    y: float
    text: str
    height: float = Field(default=2.5, gt=0)
    rotation: float = 0.0
    layer: str | None = None
    align: str | None = None  # e.g. "LEFT" (default), "CENTER", "MIDDLE_CENTER"


class CreateMText(_Base):
    x: float
    y: float
    width: float = Field(gt=0)
    text: str
    height: float = Field(default=2.5, gt=0)
    layer: str | None = None


class CreateHatch(_Base):
    entity_id: str
    pattern: str = "ANSI31"
    scale: float = Field(default=1.0, gt=0)


class EntityList(_Base):
    layer: str | None = None


class EntityRef(_Base):
    entity_id: str


class EntityTranslate(_Base):
    entity_id: str
    dx: float
    dy: float


class EntityRotate(_Base):
    entity_id: str
    cx: float
    cy: float
    angle: float


class EntityScale(_Base):
    entity_id: str
    cx: float
    cy: float
    factor: float = Field(gt=0)


class EntityMirror(_Base):
    entity_id: str
    x1: float
    y1: float
    x2: float
    y2: float


class EntityOffset(_Base):
    entity_id: str
    distance: float


class EntityArray(_Base):
    entity_id: str
    rows: int = Field(ge=1)
    cols: int = Field(ge=1)
    row_dist: float
    col_dist: float


class EntityFillet(_Base):
    id1: str
    id2: str
    radius: float = Field(gt=0)


class EntityMeasure(_Base):
    entity_id: str


# ── layer ──────────────────────────────────────────────────────────────
class LayerCreate(_Base):
    name: str
    color: str | int = "white"
    linetype: str = "CONTINUOUS"
    true_color: list[int] | None = Field(default=None, min_length=3, max_length=3)


class LayerName(_Base):
    name: str


class LayerSetProperties(_Base):
    name: str
    color: str | int | None = None
    linetype: str | None = None
    true_color: list[int] | None = Field(default=None, min_length=3, max_length=3)


# ── block ──────────────────────────────────────────────────────────────
class BlockDefine(_Base):
    name: str
    entities: list[dict[str, Any]] = []


class BlockInsert(_Base):
    name: str
    x: float
    y: float
    scale: float = Field(default=1.0, gt=0)
    rotation: float = 0.0


class BlockInsertAttribs(BlockInsert):
    attributes: dict[str, str] | None = None


class BlockGetAttribs(_Base):
    entity_id: str


class BlockUpdateAttrib(_Base):
    entity_id: str
    tag: str
    value: str


# ── annotation ─────────────────────────────────────────────────────────
class DimAligned(_Base):
    x1: float
    y1: float
    x2: float
    y2: float
    offset: float
    dim_overrides: dict[str, Any] | None = None


class DimLinear(_Base):
    x1: float
    y1: float
    x2: float
    y2: float
    dim_x: float
    dim_y: float
    dim_overrides: dict[str, Any] | None = None


class DimAngular(_Base):
    cx: float
    cy: float
    x1: float
    y1: float
    x2: float
    y2: float
    dim_overrides: dict[str, Any] | None = None


class DimRadius(_Base):
    cx: float
    cy: float
    radius: float = Field(gt=0)
    angle: float
    dim_overrides: dict[str, Any] | None = None


class Leader(_Base):
    points: list[list[float]] = Field(min_length=2)
    text: str


# ── project ────────────────────────────────────────────────────────────
class GenerateSitePlan(_Base):
    lot_width: float = Field(gt=0)
    lot_depth: float = Field(gt=0)
    adu_width: float | None = Field(default=None, gt=0)
    adu_depth: float | None = Field(default=None, gt=0)
    adu_position: Literal["rear_center", "rear_left", "rear_right"] = "rear_center"
    draw_name: str | None = None


class ImportBoundary(_Base):
    points: list[Any] | None = None
    geojson: dict[str, Any] | None = None
    layer: str | None = "LOT-LINE"


# ── view ───────────────────────────────────────────────────────────────
class ViewExport(_Base):
    format: Literal["pdf", "png", "svg", "geojson", "ifc"] = "pdf"
    path: str | None = None


# ── registry ───────────────────────────────────────────────────────────
_MODELS: dict[str, type[_Base]] = {
    # drawing
    "drawing.create": DrawingCreate,
    "drawing.open": DrawingOpen,
    "drawing.save": DrawingSave,
    "drawing.switch": DrawingSwitch,
    # entity
    "entity.create_line": CreateLine,
    "entity.create_circle": CreateCircle,
    "entity.create_polyline": CreatePolyline,
    "entity.create_rectangle": CreateRectangle,
    "entity.import_boundary": ImportBoundary,
    "entity.create_arc": CreateArc,
    "entity.create_text": CreateText,
    "entity.create_mtext": CreateMText,
    "entity.create_hatch": CreateHatch,
    "entity.list": EntityList,
    "entity.get": EntityRef,
    "entity.copy": EntityTranslate,
    "entity.move": EntityTranslate,
    "entity.rotate": EntityRotate,
    "entity.scale": EntityScale,
    "entity.mirror": EntityMirror,
    "entity.offset": EntityOffset,
    "entity.array": EntityArray,
    "entity.fillet": EntityFillet,
    "entity.erase": EntityRef,
    "entity.measure": EntityMeasure,
    # layer
    "layer.create": LayerCreate,
    "layer.set_current": LayerName,
    "layer.set_properties": LayerSetProperties,
    "layer.freeze": LayerName,
    "layer.thaw": LayerName,
    "layer.lock": LayerName,
    "layer.unlock": LayerName,
    # block
    "block.define": BlockDefine,
    "block.insert": BlockInsert,
    "block.insert_with_attributes": BlockInsertAttribs,
    "block.get_attributes": BlockGetAttribs,
    "block.update_attribute": BlockUpdateAttrib,
    # project
    "project.generate_site_plan": GenerateSitePlan,
    # view
    "view.export": ViewExport,
    # annotation
    "annotation.create_text": CreateText,
    "annotation.create_dimension_aligned": DimAligned,
    "annotation.create_dimension_linear": DimLinear,
    "annotation.create_dimension_angular": DimAngular,
    "annotation.create_dimension_radius": DimRadius,
    "annotation.create_leader": Leader,
}


def validate(key: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate ``data`` for the given ``tool.operation`` key.

    Returns the validated dict (defaults applied). Raises ``ValidationError``
    if a model exists for the key and the data is invalid. If no model is
    registered for the key, the data is returned unchanged.
    """
    model = _MODELS.get(key)
    if model is None:
        return data
    return model.model_validate(data).model_dump()
