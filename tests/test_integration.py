"""End-to-end integration tests.

These tests exercise the complete chain — questionnaire → rule resolution →
site plan generation → visual output → compliance — rather than individual
functions in isolation.

They also smoke-test the MCP tool dispatch layer (server.py) by calling the
tool functions directly as async Python, verifying the JSON responses that an
MCP client would actually receive.
"""

from __future__ import annotations

import base64
import json

import pytest

from aiblueprint_mcp.compliance_engine import ComplianceEngine
from aiblueprint_mcp.plan_generator import SitePlanConfig, SitePlanGenerator
from aiblueprint_mcp.project_state import ProjectSession

# ── Helpers ────────────────────────────────────────────────────────────

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _answered_session(answers: list[tuple]) -> ProjectSession:
    s = ProjectSession()
    for qid, val in answers:
        s.answer(qid, val)
    return s


_ENCINITAS = [
    ("project_type", "ADU — Detached"),
    ("county", "San Diego"),
    ("city", "Encinitas"),
    ("hoa_exists", False),
    ("lot_size_sqft", 9000),
    ("fire_zone", "Standard"),
    ("coastal_zone", False),
    ("adu_target_sqft", 500),
    ("adu_bedrooms", 1),
]

_LA_HOA = [
    ("project_type", "ADU — Detached"),
    ("county", "Los Angeles"),
    ("city", "Other — Not Listed"),
    ("hoa_exists", True),
    ("hoa_max_height_ft", 14),
    ("hoa_additional_setback_side_ft", 7),
    ("hoa_additional_setback_rear_ft", 6),
    ("hoa_arch_review_required", True),
    ("hoa_notes", ""),
    ("lot_size_sqft", 10000),
    ("fire_zone", "Standard"),
    ("coastal_zone", False),
    ("adu_target_sqft", 750),
    ("adu_bedrooms", 2),
]


# ═══════════════════════════════════════════════════════════════════════
# Full workflow tests
# ═══════════════════════════════════════════════════════════════════════

async def test_full_workflow_encinitas(workspace, backend):
    """
    Complete chain for a standard Encinitas ADU:
    questionnaire → rules → generate plan → screenshot → compliance.
    """
    session = _answered_session(_ENCINITAS)

    # 1. Rules resolution
    profile = session.resolved_profile()
    assert profile["location"]["city"] == "Encinitas"
    rules = profile["requirements"]["effective"]
    assert rules["max_sqft"] == 1200
    assert rules["setback_side_ft"] == 4
    assert rules["setback_rear_ft"] == 4
    assert rules["lot_coverage_max_pct"] == 50  # Encinitas city rule

    # 2. Site plan generation
    gen = SitePlanGenerator(backend, session)
    r = await gen.generate(SitePlanConfig(lot_width=60, lot_depth=120))
    assert r.ok, r.error
    p = r.payload
    assert p["adu_area_sqft"] <= rules["max_sqft"]
    assert p["side_setback_ft"] == 4.0
    assert p["rear_setback_ft"] == 4.0
    assert p["jurisdiction"] == "Encinitas, San Diego, CA"
    assert p["lot_handle"]
    assert p["adu_handle"]

    # 3. Screenshot — must be a valid, non-trivial PNG
    scr = await backend.get_screenshot()
    assert scr.ok, scr.error
    img = base64.b64decode(scr.payload["image_base64"])
    assert img[:8] == _PNG_MAGIC, "Screenshot is not a valid PNG"
    assert len(img) > 20_000, f"Screenshot suspiciously small: {len(img)} bytes"

    # 4. Compliance checks on generated entities
    engine = ComplianceEngine(backend, profile)

    area = await engine.check_area(p["adu_handle"])
    assert area.passed, area.message

    coverage = await engine.check_lot_coverage(p["lot_handle"], [p["adu_handle"]])
    assert coverage.passed, coverage.message


async def test_full_workflow_la_hoa(workspace, backend):
    """
    Complete chain with HOA restrictions (LA county).
    Verifies HOA setbacks tighten the effective requirements and the generated
    plan reflects them.
    """
    session = _answered_session(_LA_HOA)
    profile = session.resolved_profile()
    rules = profile["requirements"]["effective"]

    # HOA overrides win (7 > 4 state minimum, 6 > 4 state minimum)
    assert rules["setback_side_ft"] == 7.0
    assert rules["setback_rear_ft"] == 6.0
    assert rules["max_height_ft"] == 14.0  # HOA 14 < state 16
    assert profile["hoa"]["arch_review_required"] is True

    gen = SitePlanGenerator(backend, session)
    r = await gen.generate(SitePlanConfig(lot_width=80, lot_depth=130))
    assert r.ok, r.error
    assert r.payload["side_setback_ft"] == 7.0
    assert r.payload["rear_setback_ft"] == 6.0


async def test_full_workflow_auto_size(workspace, backend):
    """
    Auto-size path: no explicit adu_width/adu_depth — sized from target_sqft.
    The generated area should be within 100 sq ft of the target.
    """
    session = _answered_session(_ENCINITAS)
    profile = session.resolved_profile()
    target = profile["project"]["target_sqft"]

    gen = SitePlanGenerator(backend, session)
    r = await gen.generate(SitePlanConfig(lot_width=60, lot_depth=120))
    assert r.ok, r.error
    assert abs(r.payload["adu_area_sqft"] - float(target)) <= 100


# ═══════════════════════════════════════════════════════════════════════
# MCP tool dispatch layer tests
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=False)
async def server_env(workspace, backend):
    """Wire the server module's global singletons to our test backend."""
    import aiblueprint_mcp.server as srv
    original_backend = srv._backend
    original_project = srv._project
    srv._backend = backend
    srv._project = None  # fresh project per test
    yield srv
    srv._backend = original_backend
    srv._project = original_project


async def test_server_drawing_create(server_env):
    """drawing('create') returns JSON with ok=True and a handle."""
    srv = server_env
    result = json.loads(await srv.drawing(operation="create", data={"name": "test_draw"}))
    assert result["ok"] is True
    assert result["handle"]


async def test_server_entity_create_line(server_env):
    """entity create_line returns a DXF entity handle."""
    srv = server_env
    await srv.drawing(operation="create")
    r = json.loads(await srv.entity(operation="create_line", x1=0, y1=0, x2=10, y2=0))
    assert r["ok"] is True
    assert r["entity_type"] == "LINE"
    assert r["handle"]


async def test_server_entity_create_rectangle(server_env):
    """entity create_rectangle returns an LWPOLYLINE handle."""
    srv = server_env
    await srv.drawing(operation="create")
    r = json.loads(await srv.entity(operation="create_rectangle", x1=0, y1=0, x2=50, y2=100))
    assert r["ok"] is True
    assert r["entity_type"] == "LWPOLYLINE"


async def test_server_project_questionnaire_flow(server_env):
    """project tool: start → question → answer loop → profile."""
    srv = server_env

    # Start
    r = json.loads(await srv.project(operation="start"))
    assert r["ok"] is True
    assert r["first_question"]["question_id"] == "project_type"

    # Answer all questions
    for qid, val in _ENCINITAS:
        r = json.loads(await srv.project(operation="answer", data={"question_id": qid, "value": val}))
        assert r["ok"] is True

    # Profile
    r = json.loads(await srv.project(operation="profile"))
    assert r["ok"] is True
    loc = r["profile"]["location"]
    assert loc["city"] == "Encinitas"
    assert loc["county"] == "San Diego"


async def test_server_project_generate_site_plan(server_env):
    """project generate_site_plan via MCP dispatch returns full payload."""
    srv = server_env
    await srv.project(operation="start")
    for qid, val in _ENCINITAS:
        await srv.project(operation="answer", data={"question_id": qid, "value": val})

    r = json.loads(await srv.project(
        operation="generate_site_plan",
        data={"lot_width": 60, "lot_depth": 120, "draw_name": "mcp_test"},
    ))
    assert r["ok"] is True, r.get("error")
    assert r["drawing_name"] == "mcp_test"
    assert r["adu_area_sqft"] > 0
    assert r["jurisdiction"] == "Encinitas, San Diego, CA"


async def test_server_invalid_operation(server_env):
    """Unknown operations return ok=False with a clear error message."""
    srv = server_env
    r = json.loads(await srv.drawing(operation="nonexistent"))
    assert r["ok"] is False
    assert "nonexistent" in r["error"].lower()


async def test_server_validation_rejects_bad_input(server_env):
    """Pydantic validation catches bad input before it reaches the backend."""
    srv = server_env
    await srv.drawing(operation="create")
    # Negative radius should fail
    r = json.loads(await srv.entity(operation="create_circle", data={"cx": 0, "cy": 0, "radius": -5}))
    assert r["ok"] is False
    assert "Invalid input" in r["error"]


async def test_server_compliance_requirements(server_env):
    """compliance('requirements') returns effective rules without needing geometry."""
    srv = server_env
    await srv.project(operation="start")
    for qid, val in _ENCINITAS:
        await srv.project(operation="answer", data={"question_id": qid, "value": val})

    r = json.loads(await srv.compliance(operation="requirements"))
    assert r["ok"] is True
    rules = r["effective_requirements"]["effective"]
    assert rules["max_sqft"] == 1200
    assert rules["setback_side_ft"] == 4


# ═══════════════════════════════════════════════════════════════════════
# Server startup smoke test
# ═══════════════════════════════════════════════════════════════════════

def test_server_tools_registered():
    """The FastMCP instance should expose exactly the 8 expected tools."""
    import aiblueprint_mcp.server as srv
    # FastMCP stores tools in _tool_manager or tools attribute
    tool_names = {t.name for t in srv.mcp._tool_manager._tools.values()}
    expected = {"drawing", "entity", "layer", "block", "annotation", "view", "project", "compliance"}
    assert expected == tool_names, f"Tool mismatch: {tool_names}"
