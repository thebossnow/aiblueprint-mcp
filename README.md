# AIBlueprint MCP

[![CI](https://github.com/thebossnow/aiblueprint-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/thebossnow/aiblueprint-mcp/actions/workflows/ci.yml)

> MCP server for headless DXF generation via ezdxf + LibreCAD preview — purpose-built for site plans, architectural drafting, and pool bid layouts.

Built on the ezdxf backend architecture from [autocad-mcp](https://github.com/puran-water/autocad-mcp) (MIT), extended with offset, fillet, dimension overrides, solid fills, and LibreCAD integration. No AutoCAD required — runs on Linux, macOS, WSL, or a Chromebook.

## Why This Exists

AutoCAD MCP servers exist, but they require Windows and an AutoCAD license ($600+/year). LibreCAD is free and open-source, but has no scripting API — its "API" is the DXF file format.

AIBlueprint bridges the gap: an MCP server that generates DXF via ezdxf, renders previews through LibreCAD's `dxf2png`, and exposes the same tool interface LLMs already know from autocad-mcp — all at $0 in software costs.

## Quick Start

```bash
git clone https://github.com/thebossnow/aiblueprint-mcp.git
cd aiblueprint-mcp
uv sync
uv run aiblueprint-mcp
```

### Configure LibreCAD (for previews)

```bash
# Set path to your librecad binary (required for previews)
export AIBLUEPRINT_LIBRECAD_BIN=/path/to/librecad

# Optional: working directory for preview renders
export AIBLUEPRINT_WORKSPACE=/path/to/workspace
```

**Don't have LibreCAD?** The server works without it — you just won't get PNG previews. Note that `dxf2png` (the console converter previews depend on) was only added in LibreCAD v2.2.1 — Debian/Ubuntu's packaged `librecad` predates it, so `apt install librecad` alone won't give you working previews. Grab an [official release AppImage](https://github.com/LibreCAD/LibreCAD/releases) (v2.2.1 or later) instead, or use the [Docker image](#docker) below, which bundles a working build.

### Docker

The published image bundles a working LibreCAD build alongside the server, so previews work out of the box with no local LibreCAD install:

```bash
docker run --rm -i \
  -v /path/to/workspace:/workspace \
  ghcr.io/thebossnow/aiblueprint-mcp:latest
```

MCP client config:

```json
{
  "mcpServers": {
    "aiblueprint-mcp": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/path/to/workspace:/workspace",
        "ghcr.io/thebossnow/aiblueprint-mcp:latest"
      ]
    }
  }
}
```

Mount a host directory at `/workspace` to access generated DXF/PNG files outside the container; omit `-v` to keep everything ephemeral inside the container's filesystem.

### MCP Client Configuration

Add to your MCP client (Claude Desktop, Hermes, etc.):

```json
{
  "mcpServers": {
    "aiblueprint-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/aiblueprint-mcp", "aiblueprint-mcp"],
      "env": {
        "AIBLUEPRINT_LIBRECAD_BIN": "/path/to/librecad",
        "AIBLUEPRINT_WORKSPACE": "/path/to/workspace"
      }
    }
  }
}
```

## Tools

### `drawing` — File + Session Management

| Operation | Description | Data |
|-----------|-------------|------|
| `create` | New empty drawing — returns a `handle` | `{name?}` |
| `open` | Open existing DXF (within workspace) | `{path}` |
| `info` | Layers, entity count, blocks | — |
| `save` | Save to path (within workspace) | `{path?}` |
| `list` | List all open drawings in the session | — |
| `switch` | Make another open drawing current | `{handle}` |
| `undo` | Revert the current drawing to the previous checkpoint | — |
| `redo` | Re-apply the most recently undone checkpoint | — |

Multiple drawings can be open at once. Each `create`/`open` returns a `handle`;
use `switch` to change which drawing subsequent operations target. File paths
for `open`/`save` are confined to `AIBLUEPRINT_WORKSPACE` — traversal outside it
(`../`, absolute escapes) is rejected.

### `entity` — Entity CRUD + Modification

**Create:**

| Operation | Parameters |
|-----------|------------|
| `create_line` | `x1, y1, x2, y2, layer?` |
| `create_circle` | `data: {cx, cy, radius}, layer?` |
| `create_polyline` | `points: [[x,y],...], data: {closed?}, layer?` |
| `create_rectangle` | `x1, y1, x2, y2, layer?` |
| `create_arc` | `data: {cx, cy, radius, start_angle, end_angle}, layer?` |
| `create_text` | `data: {x, y, text, height?, rotation?}, layer?` |
| `create_mtext` | `data: {x, y, width, text, height?}, layer?` |
| `create_hatch` | `entity_id, data: {pattern?, scale?}` |
| `import_boundary` | ⭐ Irregular parcel boundary. `data: {points: [[x,y],...]}` **or** `{geojson: {...}}`, `layer?` → returns handle + area/perimeter/bbox |

**Read:** `list` (by layer), `get` (full detail for LINE/CIRCLE/ARC/LWPOLYLINE/TEXT/MTEXT/INSERT/HATCH), `measure` (area / perimeter / length quantity takeoff by entity_id)

**Modify:**

| Operation | Notes |
|-----------|-------|
| `copy` / `move` / `rotate` / `scale` / `mirror` | Standard CAD transforms |
| `offset` | ⭐ Offset polylines (open or closed) — deck bands, setbacks |
| `fillet` | ⭐ Fillet two lines with a radius arc + auto-trim |
| `array` | Rectangular array (rows × cols) |
| `erase` | By entity_id or `"last"` |

### `layer` — Layer Management

`list`, `create`, `set_current`, `set_properties`, `freeze`, `thaw`, `lock`, `unlock`

Named colors: `red`, `yellow`, `green`, `cyan`, `blue`, `magenta`, `white`, `grey`, `lightgrey`. For arbitrary colors, pass `true_color: [r, g, b]` (0–255) to `create` or `set_properties`.

### `block` — Blocks + Attributes

`list`, `insert`, `insert_with_attributes`, `get_attributes`, `update_attribute`, `define`

### `annotation` — Dimensions, Text, Leaders

| Operation | Notes |
|-----------|-------|
| `create_text` | Single-line text with rotation |
| `create_dimension_aligned` | ⭐ With `dim_overrides` |
| `create_dimension_linear` | ⭐ With `dim_overrides` |
| `create_dimension_angular` | ⭐ With `dim_overrides` |
| `create_dimension_radius` | ⭐ With `dim_overrides` |
| `create_leader` | Leader line + mtext |

**Dimension overrides:** `dimtxt`, `dimasz`, `dimlunit`, `dimclrd`, `dimclre`, `dimclrt`, `dimtxsty`

Example:
```json
{
  "operation": "create_dimension_aligned",
  "data": {
    "x1": 0, "y1": 0, "x2": 100, "y2": 0, "offset": -5,
    "dim_overrides": {"dimtxt": 1.75, "dimasz": 1.25, "dimlunit": 2}
  }
}
```

### `view` — Previews + Screenshots

| Operation | Description |
|-----------|-------------|
| `preview` | Save DXF + render PNG via LibreCAD `dxf2png` — returns file paths |
| `screenshot` | Render the drawing via matplotlib and return it as a native MCP image (no LibreCAD needed) |
| `export` | Write a PNG/PDF/SVG raster/vector render, a GeoJSON `FeatureCollection`, or a minimal IFC4 model to the workspace. `data: {format?: "pdf"|"png"|"svg"|"geojson"|"ifc", path?}` |

**IFC export** is intentionally minimal: aiblueprint drawings are flat 2D with no
height data, so `ifc` only emits a site boundary plus, for each building-footprint
polygon, an extruded ground slab and perimeter walls at a fixed default height — no
doors, windows, roofs, or stairs are fabricated. Footprints are picked up by layer
name (`LOT`/`PARCEL`/`PROPERTY` → site; `FOOTPRINT`/`BUILDING`/`ADU`/`STRUCTURE` →
building), the same convention `import_boundary` and the site-plan generator already
use. Requires the optional `ifcopenshell` dependency: `uv sync --extra ifc`.

### `project` — Mezcal bundle export

The `project` tool (see its own docstring for the full intake/jurisdiction
operation list — `start`/`question`/`answer`/`profile`/`generate_site_plan`/etc.)
also has an `export_mezcal` operation:

| Operation | Description |
|-----------|-------------|
| `export_mezcal` | Write the current drawing's boundary + setback envelope + building footprints — merged with the resolved zoning requirements and a compliance report, when a project profile is available — as one JSON bundle. `data: {path?}` (defaults to `<drawing name>.mezcal.json` in the workspace) |

This is the format the [Pascal editor](https://editor.pascal.app)'s Mezcal
adapter plugin ([thebossnow/mezcal_adapter_plugin](https://github.com/thebossnow/mezcal_adapter_plugin))
imports to render a site plan as a native 3D/floor-plan scene node. Same
footprint-classification convention as the IFC export above; requires a
closed boundary polyline (draw one or `entity.import_boundary` first).
`requirements`/`compliance` are only included once `project.answer` has
resolved at least one real jurisdiction rule — an empty/unresolved profile
is omitted rather than reported as a false "pass".

## Hatch Patterns

| Pattern | Use |
|---------|-----|
| `SOLID` | Water features, colored surfaces |
| `ANSI31` | Single diagonal hatch |
| `ANSI37` | Dense cross-hatch — hardscape, concrete |
| `ANSI32` | Wide cross-hatch |
| `AR-CONC` | Concrete texture |
| `EARTH` | Earth/soil fill |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AIBLUEPRINT_LIBRECAD_BIN` | Auto-detects from common locations | Path to `librecad` executable |
| `AIBLUEPRINT_WORKSPACE` | `~/workspace` | Working directory for preview renders |
| `DISPLAY` | `:0` | X11 display (for WSLg / Linux GUI) |

## Python API

You can also use the backend directly without the MCP server:

```python
from aiblueprint_mcp.backend import AIBlueprintBackend
import asyncio

async def main():
    b = AIBlueprintBackend()
    await b.initialize()
    await b.drawing_create("my_plan")

    # Draw a 100×80 ft lot with pool deck
    await b.create_rectangle(0, 0, 100, 80, layer="LOT")
    deck = await b.create_rectangle(10, 20, 50, 60, layer="DECK")

    # Offset deck band inward 4 ft
    inner = await b.entity_offset(deck.payload["handle"], -4.0)

    # Add pool with solid blue fill
    pool = await b.create_rectangle(18, 28, 42, 52, layer="POOL")
    await b.create_hatch(pool.payload["handle"], "SOLID")

    # Cross-hatch the deck
    await b.create_hatch(inner.payload["handle"], "ANSI37", scale=12.0)

    # Fillet a corner
    l1 = await b.create_line(50, 60, 50, 20, layer="DECK")
    l2 = await b.create_line(50, 20, 10, 20, layer="DECK")
    await b.entity_fillet(l1.payload["handle"], l2.payload["handle"], 8.0)

    # Dimension with style overrides
    await b.create_dimension_aligned(0, 0, 100, 0, -5,
        dim_overrides={"dimtxt": 1.75, "dimasz": 1.25, "dimlunit": 2})

    # Save and preview
    await b.drawing_save("/tmp/my_plan.dxf")
    result = await b.preview()
    print(result.payload["png_path"])

asyncio.run(main())
```

## Input Validation

Every tool operation validates its input against a per-operation schema
(Pydantic) before touching the drawing. Missing required fields, wrong types,
out-of-range values (e.g. a negative radius), and unknown fields are rejected
with a clear message — `{"ok": false, "error": "Invalid input for entity.create_circle: ..."}` — instead of a raw traceback.

## Development

```bash
uv sync --extra dev      # install dev dependencies (pytest, ruff)
uv run pytest -q         # run the test suite (134 tests)
uv run ruff check src tests
```

CI runs lint + tests on Python 3.10–3.12 via GitHub Actions (`.github/workflows/ci.yml`).

See [CHANGELOG.md](CHANGELOG.md) for release history and [RELEASING.md](RELEASING.md) for how releases are cut.

## Roadmap / Known Limitations

Tracked in the [issue tracker](https://github.com/thebossnow/aiblueprint-mcp/issues) — contributions welcome:

- Docker image bundling LibreCAD ([#9](https://github.com/thebossnow/aiblueprint-mcp/issues/9))
- Coverage reporting in CI ([#10](https://github.com/thebossnow/aiblueprint-mcp/issues/10))
- PyPI publication, versioned releases, changelog ([#11](https://github.com/thebossnow/aiblueprint-mcp/issues/11))
- Jurisdiction data beyond California ([#12](https://github.com/thebossnow/aiblueprint-mcp/issues/12))
- Live LibreCAD backend via bivex's TCP bridge ([#1](https://github.com/thebossnow/aiblueprint-mcp/issues/1))

## License

MIT — see [LICENSE](LICENSE).

This project incorporates architecture and patterns from [autocad-mcp](https://github.com/puran-water/autocad-mcp) by Puran Water LLC, also MIT-licensed. The ezdxf backend, MCP tool dispatch pattern, and command result types are adapted from autocad-mcp v3.1. Entity offset, fillet, dimension overrides, solid fills, and LibreCAD preview are original additions.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- LibreCAD (optional, for PNG previews)
- 0 software licenses — ezdxf is MIT, LibreCAD is GPLv2

## Acknowledgments

- **Hermes** (AI coding agent) — co-author. Designed and implemented entity offset, fillet, dimension overrides, solid fills, and LibreCAD preview integration.
- **DeepSeek V4 Pro** — the model that powered every line of this project. Fast, precise, never hallucinated a dimension.
- **[Julian Goldie](https://www.youtube.com/@JulianGoldieSEO)** — for the relentless push to build in public and ship real tools, not just prompts. Join his [AI Profit Lab](https://www.skool.com/ai-profit-lab-7462/about?ref=77c45f8dcc5f49baad3210f88b3ad519) on Skool.
- **[Puran Water LLC](https://github.com/puran-water/autocad-mcp)** — upstream autocad-mcp project (MIT). The ezdxf backend architecture, MCP tool dispatch pattern, and command result types are adapted from their v3.1 release.
- **[LibreCAD](https://librecad.org/)** — the open-source 2D CAD engine that makes $0 drafting possible.
