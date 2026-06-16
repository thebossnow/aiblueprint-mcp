# AIBlueprint MCP — Project Handoff

**Last updated:** June 15, 2026
**Prepared by:** Hermes (AI coding agent, via Brittan Anderson / thebossnow)
**Repo:** [thebossnow/aiblueprint-mcp](https://github.com/thebossnow/aiblueprint-mcp)
**License:** MIT

---

## What This Is

AIBlueprint MCP is an MCP (Model Context Protocol) server that lets AI coding agents (Claude, DeepSeek, Hermes, etc.) generate architectural DXF drawings programmatically via `ezdxf` and preview them through LibreCAD's `dxf2png`. It's the first MCP server purpose-built for the LibreCAD ecosystem — a zero-cost, cross-platform alternative to AutoCAD-based MCP servers.

**The pitch:** AutoCAD MCP servers exist (top one had 272+ stars as of June 2026) but require Windows + a $600/yr license. LibreCAD is free and open-source but has no scripting API — its "API" is the DXF file format. AIBlueprint bridges the gap: same tool interface LLMs already know from autocad-mcp, zero software cost, runs on Linux/WSL/macOS.

---

## Current State

> Counts below verified June 16, 2026 via `uv run pytest --co` (101 collected),
> `ls src/aiblueprint_mcp/data/jurisdictions/ca/{counties,cities}`, and the
> `@mcp.tool()` decorators in `server.py`. GitHub stats are point-in-time and
> rot fast — re-check before quoting.

| Metric | Value |
|--------|-------|
| Version | 0.1.0 |
| Stars | 3 (as of June 2026) |
| Forks | 2 (as of June 2026) |
| Production code | ~4,200 lines (11 modules) |
| Tests | 101 — all passing (~1.8s), incl. stdio e2e integration suite |
| Lint | Ruff — zero issues |
| CI | GitHub Actions: ruff + pytest on Python 3.10/3.11/3.12, ~20s runs, all green |
| MCP tools | 8 (drawing, entity, layer, block, annotation, view, project, compliance) |
| Jurisdiction data | California only: 10 counties, 15 cities bundled as JSON |
| Dependencies | 6 production (mcp, ezdxf, matplotlib, Pillow, structlog, pydantic) + 3 dev |
| Python support | 3.10+ |

### What Works Today

- **8-tool MCP server** (drawing, entity, layer, block, annotation, view, project, compliance) with all standard CAD operations
- **entity_offset** — parallel polyline offset (deck bands, setbacks) with correct normal-vector math
- **entity_fillet** — fillet arc between two lines with auto-trim, correct tangent/arc computation
- **Dimension style overrides** (dimtxt, dimasz, dimlunit, dimclrd, dimclre, dimclrt, dimtxsty)
- **Solid-fill and pattern hatches** (SOLID for water, ANSI31/ANSI37 for hardscape/concrete)
- **LibreCAD preview** via dxf2png + matplotlib screenshot fallback
- **Pydantic input validation** on all 40+ operation paths with LLM-friendly error messages
- **Workspace path confinement** — rejects `../` traversal and absolute-path escapes
- **Auto site-plan generator** (`project.generate_site_plan`) — produces complete DXF from lot dimensions + jurisdiction profile
- **Jurisdiction-aware compliance engine** — branching questionnaire → resolved rule stack (CA state → county → city → HOA) → compliance checks (area, setbacks, coverage, height, full report)
- **Multi-document sessions** — drawing create/list/switch with handles
- **Undo/redo** — per-document snapshot stack (drawing undo/redo); `backend.batch()` groups a multi-op sequence into one checkpoint
- **Irregular parcel import** (`entity.import_boundary`) — closed boundary polyline from survey points or GeoJSON (Polygon/Feature/FeatureCollection); returns area/perimeter/bbox and plugs into the compliance engine
- **Entity measure** — area/perimeter/length quantity takeoff
- **Block operations** — define, insert, insert_with_attributes, get/update attributes
- **View export** to PNG/PDF/SVG and GeoJSON (`FeatureCollection` in drawing coordinates)
- **CI pipeline** — GitHub Actions, ruff lint + pytest matrix, all passing

### What's Not Yet Done (Known Gaps)

Each gap below is tracked as a GitHub issue — the [issue tracker](https://github.com/thebossnow/aiblueprint-mcp/issues) is the source of truth for "what's left."

- No Docker/container support (friction for new users who need LibreCAD) — [#9](https://github.com/thebossnow/aiblueprint-mcp/issues/9)
- No coverage reporting in CI — [#10](https://github.com/thebossnow/aiblueprint-mcp/issues/10)
- No PyPI publication / versioned releases / changelog — [#11](https://github.com/thebossnow/aiblueprint-mcp/issues/11)
- California-only jurisdiction data (format is extensible, just needs data entry) — [#12](https://github.com/thebossnow/aiblueprint-mcp/issues/12)
- Auto site-plan generator (`generate_site_plan`) still assumes a rectangular lot. Irregular parcels can be imported (`entity.import_boundary`) and run through compliance, but auto ADU placement/directional setbacks on non-rectangular lots is not yet implemented (no issue filed yet)
- Live LibreCAD backend (bivex TCP bridge) not yet implemented — [#1](https://github.com/thebossnow/aiblueprint-mcp/issues/1)

---

## Architecture

```
aiblueprint-mcp/
├── src/aiblueprint_mcp/
│   ├── __init__.py              # __version__ = "0.1.0"
│   ├── __main__.py              # Entry point: asyncio.run(server.main())
│   ├── server.py                # MCP tool dispatch (8 tools, ~545 lines)
│   ├── backend.py               # ezdxf engine (~1,400 lines)
│   ├── validation.py            # Pydantic models for all operations (328 lines)
│   ├── config.py                # Lazy env-var config + workspace confinement (78 lines)
│   ├── types.py                 # CommandResult dataclass
│   ├── plan_generator.py        # Auto site-plan generator (335 lines)
│   ├── questionnaire.py         # Branching intake state machine (331 lines)
│   ├── compliance_engine.py     # Post-draw compliance checks (243 lines)
│   ├── jurisdiction.py          # JSON loader + rule merger (273 lines)
│   ├── project_state.py         # ProjectSession: answers → profile (147 lines)
│   └── data/jurisdictions/      # CA state, 10 counties, 15 cities (JSON)
├── tests/
│   ├── conftest.py              # Fixtures: isolated workspace, fresh backend
│   ├── test_backend.py          # Geometry, sessions, errors (16 tests)
│   ├── test_compliance.py       # Area, setbacks, coverage, height, report (10 tests)
│   ├── test_config.py           # Workspace path confinement (4 tests)
│   ├── test_jurisdiction.py     # Loader, merge stack, HOA overrides (18 tests)
│   ├── test_plan_generator.py   # Auto-size, positions, HOA respect (11 tests)
│   ├── test_questionnaire.py    # State machine, branching, profile (16 tests)
│   ├── test_server.py           # JSON contract, validation, Image objects (6 tests)
│   ├── test_validation.py       # Pydantic models: required, negative, extra (8 tests)
│   └── test_integration.py      # Stdio e2e: full workflow + MCP dispatch (12 tests)
├── .github/workflows/ci.yml     # ruff + pytest on Python 3.10/3.11/3.12
├── pyproject.toml               # Hatchling build, uv deps, ruff config
├── README.md                    # Full API reference + examples
└── LICENSE                      # MIT
```

### Key Design Decisions

1. **8-tool consolidated surface, not 40+ individual tools.** Each tool (entity, drawing, etc.) takes an `operation` string + `data` dict. This keeps the MCP tool list small while supporting 40+ operations. Validation maps `tool.operation` → Pydantic model via a registry.

2. **Pydantic with `extra=forbid`.** Every operation gets a typed schema. Unknown fields are rejected. Field constraints (`gt=0`, `min_length=2`) catch bad LLM output before it hits the backend. Errors are formatted for readability by the calling LLM.

3. **Workspace path confinement.** `config.py:resolve_path()` rejects absolute paths and `..` traversal that escape `AIBLUEPRINT_WORKSPACE`. This prevents an LLM from reading/writing arbitrary files through the MCP server.

4. **Lazy env-var resolution.** `Config.from_env()` is called at backend init time, not import time. Tests can monkeypatch `AIBLUEPRINT_WORKSPACE` before constructing the backend.

5. **ezdxf 1.4+ dimension API.** Dimension overrides go on `dim.dimstyle.dxf` (not `dim.dxf`). The `_apply_overrides` helper handles all 7 supported dim vars.

6. **Offset direction:** Positive = outward (CCW winding), negative = inward. For a clockwise rectangle, negative offsets go inward. The math computes edge normals by rotating 90° CCW, then finds intersection points of offset edges.

7. **Fillet direction vectors:** When lines share a corner point, the far-endpoint approach avoids zero-length vectors. Direction is computed from the intersection toward the FAR endpoint, then negated to point toward the corner where the fillet cuts in.

---

## Development Workflow

### Local Setup

```bash
git clone https://github.com/thebossnow/aiblueprint-mcp.git
cd aiblueprint-mcp
uv sync --extra dev
```

### Run Tests

```bash
uv run pytest -v
# 101 tests, all passing
```

### Run Lint

```bash
uv run ruff check src tests
# All checks passed!
```

### Run the MCP Server

```bash
# Without LibreCAD (no PNG previews, matplotlib screenshot still works):
uv run aiblueprint-mcp

# With LibreCAD previews:
export AIBLUEPRINT_LIBRECAD_BIN=/path/to/librecad
export AIBLUEPRINT_WORKSPACE=/path/to/workspace
uv run aiblueprint-mcp
```

### MCP Client Configuration

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

### CI Pipeline

- **Trigger:** Push to `main`, pull requests
- **Matrix:** Python 3.10, 3.11, 3.12
- **Steps:** Checkout → install uv → install Python → `uv sync --extra dev` → `ruff check` → `pytest -q`
- **Runtime:** ~20 seconds
- **Dependabot:** Active for `uv` dependency graph updates

---

## Key Files Reference

| File | Purpose | Lines |
|------|---------|-------|
| `src/aiblueprint_mcp/backend.py` | ezdxf engine: drawing lifecycle, entity CRUD, offset/fillet geometry, hatches, blocks, preview/screenshot/export | ~1,400 |
| `src/aiblueprint_mcp/server.py` | MCP tool dispatch — 8 tools, validation integration, JSON serialization | 545 |
| `src/aiblueprint_mcp/validation.py` | 40+ Pydantic models, registry mapping `tool.operation` → model | 328 |
| `src/aiblueprint_mcp/plan_generator.py` | Auto site-plan: ADU sizing (4:5 ratio), setback/annotation/dimension/title-block generation | 335 |
| `src/aiblueprint_mcp/questionnaire.py` | Branching intake: project type → location → HOA → site facts → ADU goals → resolved profile | 331 |
| `src/aiblueprint_mcp/compliance_engine.py` | Post-draw checks: area, setbacks, coverage, height, full report with source citations | 243 |
| `src/aiblueprint_mcp/jurisdiction.py` | JSON loader + rule merger: state → county → city → HOA, "most restrictive wins" | 273 |
| `src/aiblueprint_mcp/project_state.py` | ProjectSession: collects answers, builds resolved profile | 147 |
| `src/aiblueprint_mcp/config.py` | Lazy env-var config, workspace path confinement | 78 |
| `src/aiblueprint_mcp/types.py` | CommandResult dataclass + CommandError | ~30 |

---

## External Dependencies & Relationships

### Upstream

- **[autocad-mcp](https://github.com/puran-water/autocad-mcp)** (MIT) — The ezdxf backend architecture, MCP tool dispatch pattern, and command result types are adapted from autocad-mcp v3.1 by Puran Water LLC. Offset, fillet, dimension overrides, solid fills, and LibreCAD preview are original additions to aiblueprint.

- **[LibreCAD](https://librecad.org/)** (GPLv2) — The open-source 2D CAD engine. AIBlueprint uses `dxf2png` for preview rendering.

### Community & Collaboration

- **LibreCAD Issue #2577:** [MCP Server for AI-driven DXF generation](https://github.com/LibreCAD/LibreCAD/issues/2577) — Filed by thebossnow to introduce aiblueprint-mcp to the LibreCAD project. Active conversation.
- **bivex's TCP bridge:** bivex has a fork of LibreCAD that adds `mcp_bridge` — a TCP plugin for live manipulation of the internal `Document` object. Tracking issue: [aiblueprint-mcp#1](https://github.com/thebossnow/aiblueprint-mcp/issues/1).
  - **Open question (unconfirmed):** bivex commented (paraphrased) that there's a "need bridge between Plugin and MCP server for internal drawing." The exact ask isn't yet pinned down — it likely means aiblueprint's MCP server should talk to the LibreCAD plugin over the TCP bridge so edits land on the live in-memory `Document` rather than round-tripping through a DXF file on disk. **Next maintainer: confirm the intent with bivex on issue #1 before scoping** — this drives whether item #15 below (the `LiveLibreCADBackend`) is a thin TCP client or a deeper protocol design.
- **Hermes AI Agent** — Co-author. Designed and implemented entity offset, fillet, dimension overrides, solid fills, and LibreCAD preview integration.
- **DeepSeek V4 Pro** — The model that powered the initial development.

### Related Skills (Hermes Agent)

- `aiblueprint` — The Hermes skill for generating DXF site plans using ezdxf and LibreCAD
- `aiblueprint/references/aiblueprint-mcp-reference.md` — Full MCP tool reference
- `aiblueprint/references/offset-fillet-algorithms.md` — Detailed geometry notes
- `aiblueprint/references/autocad-mcp-evaluation.md` — Assessment of the upstream project
- `cad-dxf-generation` — General DXF generation skill

---

## Recommendations

### Immediate (Low Effort, High Impact)

| # | Item | Effort |
|---|------|--------|
| 1 | Add coverage reporting to CI (`pytest-cov`, `--cov=aiblueprint_mcp`) | 30 min |
| 2 | Add a CI / test-count badge to README | 15 min |
| 3 | Write CHANGELOG.md covering v0.1.0 surface | 1 hr |
| 4 | Ship `mcp.json.example` in repo root | 10 min |
| 5 | Add `.gitattributes` for `data/` directory | 5 min |
| 6 | Add `aiblueprint` CLI alias alongside `aiblueprint-mcp` | 5 min |

### Short-Term (Medium Effort)

| # | Item | Effort |
|---|------|--------|
| 8 | Dockerize the server (bundle LibreCAD + MCP server, publish to ghcr.io) | 2-3 hrs |
| 9 | Add stdio e2e integration test (spawn server, JSON-RPC handshake, tool calls) | 2 hrs |
| 10 | Add `--version` flag and health check endpoint (`drawing(health)`) | 1 hr |
| 11 | Publish to PyPI via CI on tagged releases | 1 hr + CI |
| 12 | Add undo/redo command stack to backend | 3-4 hrs |
| 13 | Better error messages: include install hints when LibreCAD not found | 15 min |
| 14 | Add PDF, SVG, and GeoJSON export | 3-4 hrs |

### Strategic (Higher Effort, 1-3 Months)

| # | Item | Effort |
|---|------|--------|
| 15 | Integrate bivex's TCP live bridge (`LiveLibreCADBackend`) | Days |
| 16 | Add non-CA jurisdiction data (TX, FL, NY seed + contributor guide) | Days |
| 17 | Property boundary import from GeoJSON/survey points (irregular parcels) | 4-6 hrs |
| 18 | Add type hints / overloads to server.py tool functions | 1 hr |

---

## Gotchas & Pitfalls

### ezdxf Dimension API (1.4+)

Do NOT set overrides on `dim.dxf` or call `dim.dxf.override()`. Use `dim.dimstyle.dxf.dimtxt = value`. The `override={}` kwarg on `add_aligned_dim()` does not exist in newer ezdxf. This is documented in the aiblueprint skill but easy to get wrong.

### Fillet Direction Vectors

When two lines share a corner point, the direction from the intersection toward the NEAR endpoint can be zero-length. Always compute direction from intersection toward the FAR endpoint, then negate. See `references/offset-fillet-algorithms.md` for the full walkthrough.

### Offset Direction

Positive = outward (counter-clockwise winding), negative = inward. For a clockwise rectangle, negative offsets go inward. Test with small values before scaling up.

### dxf2png Path Handling

`dxf2png` doubles the path if you pass an absolute path for `-o`. Use relative paths from the workspace directory instead, or pass the output to a temp directory as the `preview()` method does.

### Workspace Path Confinement

The `config.resolve_path()` rejects absolute paths and `..` traversal. When integrating with a file manager or external tool, ensure paths stay within `AIBLUEPRINT_WORKSPACE`. In tests, monkeypatch `AIBLUEPRINT_WORKSPACE` to a tmp_path.

### Hatch Pattern Names

Use `hatch.set_solid_fill()` for solid fills, NOT `set_pattern_fill("SOLID", ...)`. The "SOLID" pattern name is not a standard ANSI pattern — `set_solid_fill()` is the correct API.

### LibreCAD Binary Discovery

The `_find_librecad()` function checks common paths:
1. `AIBLUEPRINT_LIBRECAD_BIN` env var (highest priority)
2. `~/workspace/LibreCAD/unix/librecad`
3. `/usr/bin/librecad`
4. `/usr/local/bin/librecad`
5. `~/LibreCAD/unix/librecad`

If none exist, it returns the first candidate and the caller surfaces a clear error. For WSL, the build-from-source path at `~/workspace/LibreCAD/unix/librecad` is the most common.

---

## Links

- **Repository:** https://github.com/thebossnow/aiblueprint-mcp
- **CI Dashboard:** https://github.com/thebossnow/aiblueprint-mcp/actions
- **LibreCAD Upstream Issue:** https://github.com/LibreCAD/LibreCAD/issues/2577
- **TCP Live Bridge Tracking:** https://github.com/thebossnow/aiblueprint-mcp/issues/1
- **Upstream autocad-mcp:** https://github.com/puran-water/autocad-mcp
- **LibreCAD CLI Docs:** https://docs.librecad.org/en/latest/guides/cmdline.html
- **LibreCAD Build Docs:** https://docs.librecad.org/en/latest/appx/build.html
- **ezdxf Docs:** https://ezdxf.readthedocs.io/

---

## Notes for the Next Maintainer

1. **The `main` branch is the source of truth.** The other branches (`feat/text-align`, `feature/auto-plan-generator`, `fix/drawing-layout`, `fix/review-issues`, `test/integration`, `claude/repo-overview-jqvdbe`) represent the PR workflow that was squashed into main. They're preserved for history but all code lives on main. (The exact branch list drifts as stale branches are pruned — don't treat it as a fixed count.)

2. **The local clone at `~/workspace/aiblueprint-mcp` was stale** as of June 15, 2026 — it was on the initial commit. Running `git pull` brought it current. Confirm the working copy is up to date before making changes.

3. **The Hermes `aiblueprint` skill** at `~/.hermes/skills/aiblueprint/SKILL.md` and its `references/` directory contain additional context, worked examples, and the full MCP tool reference. These are the primary consumer of aiblueprint-mcp and should be kept in sync.

4. **The Agent Control Room** at `~/agent-control-room/` manages the broader agent ecosystem. The `webdev` and `maintenance` agents may use aiblueprint-mcp for site-plan generation tasks.

5. **Testing philosophy:** Every new backend method needs both a unit test (in `test_backend.py`) and a validation model test (in `test_validation.py`). Server dispatch changes need tests in `test_server.py`. Compliance/jurisdiction changes go in their respective test files.

6. **Breaking changes to the tool surface** require updating: the README tool reference table, the Hermes skill reference docs, the Pydantic validation models, and the server dispatch function. Four places to touch — don't miss any.
