# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Test coverage reporting in CI (`pytest-cov`); Markdown coverage table written
  to the GitHub Actions job summary per Python version.
- Stdio end-to-end integration test suite (`tests/test_integration.py`) covering
  the full drawing workflow and the MCP dispatch layer.
- Tag-driven release workflow: publishes to PyPI via Trusted Publishing and
  creates a GitHub Release on `v*` tags.

## [0.1.0] — 2026-06-13

Initial release.

### Added
- **MCP server** exposing 8 consolidated tools (`drawing`, `entity`, `layer`,
  `block`, `annotation`, `view`, `project`, `compliance`), each dispatching an
  `operation` + `data` payload to the ezdxf backend.
- **ezdxf backend** with drawing lifecycle, multi-document sessions, entity CRUD,
  blocks with attributes, layers, dimensions/text/leaders, and hatches.
- **entity_offset** — parallel polyline offset with correct normal-vector math.
- **entity_fillet** — fillet arc between two lines with auto-trim.
- **Dimension style overrides** (dimtxt, dimasz, dimlunit, dimclrd, dimclre,
  dimclrt, dimtxsty) via the ezdxf 1.4+ dimstyle API.
- **Solid-fill and pattern hatches** (SOLID, ANSI31/ANSI37).
- **LibreCAD preview** via `dxf2png` with a matplotlib screenshot fallback.
- **Auto site-plan generator** (`project.generate_site_plan`) producing a complete
  DXF from lot dimensions + a jurisdiction profile.
- **Jurisdiction-aware compliance engine** — branching questionnaire → resolved
  rule stack (CA state → county → city → HOA) → compliance checks (area, setbacks,
  coverage, height, full report).
- **California jurisdiction data** — state, 10 counties, 15 cities as bundled JSON.
- **Pydantic input validation** on all operation paths (`extra=forbid`) with
  LLM-friendly error messages.
- **Workspace path confinement** — rejects `../` traversal and absolute-path escapes.
- **View export** to PNG/PDF/SVG.
- **CI** — GitHub Actions running ruff + pytest on Python 3.10/3.11/3.12.

[Unreleased]: https://github.com/thebossnow/aiblueprint-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thebossnow/aiblueprint-mcp/releases/tag/v0.1.0
