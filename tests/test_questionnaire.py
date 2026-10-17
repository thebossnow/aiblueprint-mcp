"""Tests for the questionnaire state machine and project profile."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.project_state import ProjectSession
from aiblueprint_mcp.questionnaire import Questionnaire


def test_questionnaire_starts_with_project_type():
    q = Questionnaire()
    first = q.current_question()
    assert first is not None
    assert first.id == "project_type"


def test_questionnaire_answers_advance_to_next():
    q = Questionnaire()
    q.answer("project_type", "ADU — Detached")
    nxt = q.current_question()
    assert nxt is not None
    assert nxt.id == "county"


def test_questionnaire_hoa_yes_injects_hoa_questions():
    q = Questionnaire()
    q.answer("project_type", "ADU — Detached")
    q.answer("county", "San Diego")
    q.answer("city", "Encinitas")
    q.answer("hoa_exists", True)
    q_ids = [qn.id for qn in _drain_ids(q)]
    assert "hoa_max_height_ft" in q_ids
    assert "hoa_arch_review_required" in q_ids


def test_questionnaire_hoa_no_skips_hoa_questions():
    q = Questionnaire()
    q.answer("project_type", "ADU — Detached")
    q.answer("county", "San Diego")
    q.answer("city", "Encinitas")
    q.answer("hoa_exists", False)
    q_ids = [qn.id for qn in _drain_ids(q)]
    assert "hoa_max_height_ft" not in q_ids


def test_questionnaire_progress_increases():
    q = Questionnaire()
    p0 = q.progress()["answered"]
    q.answer("project_type", "ADU — Detached")
    p1 = q.progress()["answered"]
    assert p1 > p0


def test_questionnaire_reset_clears_state():
    q = Questionnaire()
    q.answer("project_type", "ADU — Detached")
    q.reset()
    assert q.current_question().id == "project_type"
    assert q.progress()["answered"] == 0


def test_new_construction_skips_existing_dwelling_questions():
    q = Questionnaire()
    q.answer("project_type", "New Construction — Single Family")
    q_ids = [qn.id for qn in _drain_ids(q)]
    assert "existing_primary_sqft" not in q_ids
    assert "existing_year_built" not in q_ids


def test_project_session_profile_basic():
    p = ProjectSession()
    _answer_minimal_adu(p)
    profile = p.resolved_profile()
    assert "requirements" in profile
    assert profile["location"]["county"] == "San Diego"
    assert profile["location"]["city"] == "Encinitas"


def test_project_session_profile_respects_hoa_height():
    p = ProjectSession()
    _answer_minimal_adu(p, hoa_height=14)
    profile = p.resolved_profile()
    eff = profile["requirements"]["effective"]
    assert eff.get("max_height_ft") == 14  # HOA is more restrictive than 16ft


def test_project_session_profile_no_hoa_uses_state():
    p = ProjectSession()
    _answer_minimal_adu(p, include_hoa=False)
    profile = p.resolved_profile()
    eff = profile["requirements"]["effective"]
    assert eff.get("max_height_ft") == 16
    assert eff.get("max_sqft") == 1200


def test_project_session_warns_if_target_exceeds_max():
    p = ProjectSession()
    _answer_minimal_adu(p, target_sqft=1500)
    profile = p.resolved_profile()
    derived = profile["requirements"].get("derived", {})
    assert "target_sqft_warning" in derived


def test_project_session_attached_adu_caps_at_50pct_primary():
    p = ProjectSession()
    _answer_minimal_adu(p, adu_type="ADU — Attached", primary_sqft=800, target_sqft=500)
    profile = p.resolved_profile()
    eff = profile["requirements"]["effective"]
    # 50% of 800 = 400, which is less than state max 1200
    assert eff.get("max_sqft") == pytest.approx(400.0)


def test_project_session_jadu_owner_occupancy():
    p = ProjectSession()
    _answer_minimal_adu(p, adu_type="ADU — Junior (JADU, within primary dwelling)", target_sqft=400)
    profile = p.resolved_profile()
    eff = profile["requirements"]["effective"]
    assert eff.get("owner_occupancy_required") is True
    assert eff.get("max_sqft") == 500


def test_project_session_profile_has_citations():
    p = ProjectSession()
    _answer_minimal_adu(p)
    profile = p.resolved_profile()
    sources = profile["requirements"].get("sources", {})
    assert len(sources) > 0


def test_project_session_profile_has_warnings():
    p = ProjectSession()
    _answer_minimal_adu(p)
    profile = p.resolved_profile()
    assert len(profile.get("warnings", [])) > 0


def test_hoa_non_numeric_answer_does_not_crash():
    """A non-numeric HOA numeric answer is ignored, not propagated into a crash."""
    p = ProjectSession()
    p.reset()
    p.answer("project_type", "ADU — Detached")
    p.answer("county", "San Diego")
    p.answer("city", "Encinitas")
    p.answer("hoa_exists", "Yes")
    p.answer("hoa_max_height_ft", "N/A")          # garbage instead of a number
    p.answer("hoa_additional_setback_side_ft", "")  # blank
    p.answer("hoa_additional_setback_rear_ft", "0")
    p.answer("hoa_arch_review_required", "Yes")
    p.answer("hoa_notes", "")
    p.answer("lot_size_sqft", "7500")
    p.answer("existing_primary_sqft", "1400")
    p.answer("existing_year_built", "1985")
    p.answer("fire_zone", "LRA — Local Responsibility Area (urban/suburban)")
    p.answer("coastal_zone", "No")
    p.answer("adu_target_sqft", "500")
    p.answer("adu_bedrooms", "1 Bedroom")

    profile = p.resolved_profile()  # must not raise
    eff = profile["requirements"]["effective"]
    # The garbage HOA height is ignored, so the state 16 ft height stands.
    assert eff.get("max_height_ft") == 16


def test_project_session_coastal_warning_santa_monica():
    p = ProjectSession()
    _answer_minimal_adu(p, county="Los Angeles", city="Santa Monica")
    profile = p.resolved_profile()
    coastal_w = [w for w in profile.get("warnings", []) if "Coastal" in w or "CDP" in w]
    assert len(coastal_w) > 0


# ── helpers ────────────────────────────────────────────────────────────

def _drain_ids(q: Questionnaire) -> list:
    questions = []
    while True:
        nxt = q.current_question()
        if nxt is None:
            break
        questions.append(nxt)
        # answer with a placeholder to advance
        q.answer(nxt.id, nxt.options[0] if nxt.options else "0")
    return questions


def _answer_minimal_adu(
    p: ProjectSession,
    adu_type: str = "ADU — Detached",
    county: str = "San Diego",
    city: str = "Encinitas",
    include_hoa: bool = True,
    hoa_height: float | None = None,
    primary_sqft: float = 1400,
    target_sqft: float = 500,
):
    p.reset()
    p.answer("project_type", adu_type)
    p.answer("county", county)
    p.answer("city", city)
    p.answer("hoa_exists", "Yes" if include_hoa else "No")
    if include_hoa:
        p.answer("hoa_max_height_ft", str(hoa_height) if hoa_height else "0")
        p.answer("hoa_additional_setback_side_ft", "0")
        p.answer("hoa_additional_setback_rear_ft", "0")
        p.answer("hoa_arch_review_required", "Yes")
        p.answer("hoa_notes", "")
    p.answer("lot_size_sqft", "7500")
    if "New Construction" not in adu_type:
        p.answer("existing_primary_sqft", str(primary_sqft))
        p.answer("existing_year_built", "1985")
    p.answer("fire_zone", "LRA — Local Responsibility Area (urban/suburban)")
    p.answer("coastal_zone", "No")
    p.answer("adu_target_sqft", str(target_sqft))
    p.answer("adu_bedrooms", "1 Bedroom")
