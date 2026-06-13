"""ProjectSession — ties the questionnaire to a resolved project profile.

One ProjectSession lives in the server alongside the drawing backend.
It owns the Questionnaire and resolves the final EffectiveRequirements
once all questions are answered.

Usage (from server.py):
    session = ProjectSession()
    q = session.next_question()        # → Question or None
    session.answer("county", "San Diego")
    profile = session.resolved_profile()  # → dict (when complete)
"""

from __future__ import annotations

from typing import Any

from aiblueprint_mcp.jurisdiction import EffectiveRequirements, JurisdictionLoader, merge_layers
from aiblueprint_mcp.questionnaire import ADU_TYPE_MAP, Question, Questionnaire


class ProjectSession:
    """Manages questionnaire state and builds the resolved requirements profile."""

    def __init__(self):
        self._q = Questionnaire()
        self._resolved: EffectiveRequirements | None = None
        self._loader = JurisdictionLoader("ca")

    # ── Questionnaire interface ────────────────────────────────────────

    @property
    def complete(self) -> bool:
        return self._q.complete

    def next_question(self) -> Question | None:
        q = self._q.current_question()
        if q is None and not self._q.complete:
            self._q.state.complete = True
        return q

    def answer(self, question_id: str, value: Any) -> Question | None:
        """Record an answer; invalidate cached profile; return next question."""
        self._resolved = None
        return self._q.answer(question_id, value)

    def progress(self) -> dict[str, Any]:
        return self._q.progress()

    def all_answers(self) -> dict[str, Any]:
        return dict(self._q.answers)

    def reset(self):
        self._q.reset()
        self._resolved = None

    # ── Profile resolution ─────────────────────────────────────────────

    def resolved_profile(self) -> dict[str, Any]:
        """Build and return the full project profile with effective requirements."""
        if self._resolved is None:
            self._resolved = self._build()
        return self._format()

    def _adu_type(self) -> str:
        raw = self._q.answers.get("project_type", "ADU — Detached")
        return ADU_TYPE_MAP.get(raw, "detached")

    def _build(self) -> EffectiveRequirements:
        answers = self._q.answers
        county = answers.get("county", "")
        city = answers.get("city", "")
        if city in ("Unincorporated", "Other — Not Listed"):
            city = ""

        layers = self._loader.resolve_stack(county, city)
        hoa_data = self._q.build_hoa_data()
        adu_type = self._adu_type()
        return merge_layers(layers, adu_type, hoa_data)

    def _format(self) -> dict[str, Any]:
        r = self._resolved
        if r is None:
            return {}
        answers = self._q.answers

        # Derive project-specific derived values
        derived: dict[str, Any] = {}
        rules = dict(r.rules)

        # Attached ADU: cap at 50% of primary sq ft
        if self._adu_type() == "attached":
            primary = answers.get("existing_primary_sqft")
            if primary:
                cap = float(primary) * 0.5
                if "max_sqft" in rules and rules["max_sqft"] is not None:
                    rules["max_sqft"] = min(rules["max_sqft"], cap)
                else:
                    rules["max_sqft"] = cap
                derived["max_sqft_note"] = (
                    f"Attached ADU capped at 50% of primary ({float(primary):.0f} sq ft) "
                    f"= {cap:.0f} sq ft, or state max 1,200 sq ft — whichever is less."
                )

        # Flag if target sq ft exceeds max
        target = answers.get("adu_target_sqft")
        max_sqft = rules.get("max_sqft")
        if target and max_sqft and float(target) > float(max_sqft):
            derived["target_sqft_warning"] = (
                f"Target {target} sq ft exceeds the effective maximum of {max_sqft} sq ft. "
                "Reduce the design or verify with the local building department."
            )

        return {
            "project": {
                "type": answers.get("project_type", ""),
                "adu_type": self._adu_type(),
                "target_sqft": target,
                "bedrooms": answers.get("adu_bedrooms"),
            },
            "location": {
                "state": "California",
                "county": answers.get("county"),
                "city": answers.get("city"),
                "fire_zone": answers.get("fire_zone"),
                "coastal_zone": answers.get("coastal_zone"),
            },
            "site": {
                "lot_size_sqft": answers.get("lot_size_sqft"),
                "existing_primary_sqft": answers.get("existing_primary_sqft"),
                "existing_year_built": answers.get("existing_year_built"),
            },
            "hoa": {
                "exists": bool(answers.get("hoa_exists")),
                "arch_review_required": bool(answers.get("hoa_arch_review_required")),
                "notes": answers.get("hoa_notes"),
            },
            "requirements": {
                "effective": rules,
                "derived": derived,
                "sources": r.sources,
            },
            "residential_standards": r.residential,
            "notes": r.notes,
            "warnings": r.warnings,
            "disclaimers": r.disclaimers,
        }
