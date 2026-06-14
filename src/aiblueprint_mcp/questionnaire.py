"""Branching questionnaire state machine for project intake.

The questionnaire collects the answers needed to resolve a jurisdiction rule
stack and build a ProjectProfile. Each ``next()`` call returns the next
Question (or None when complete). Answers are stored in an Answers dict and
passed to the JurisdictionLoader + merger to build the profile.

Usage:
    q = Questionnaire()
    while not q.complete:
        question = q.current_question()
        # … show question to user, collect answer …
        q.answer(question.id, user_answer)
    profile = q.build_profile()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiblueprint_mcp.jurisdiction import JurisdictionLoader

# ── Question definition ────────────────────────────────────────────────

@dataclass
class Question:
    id: str
    text: str
    help: str
    type: str               # "select" | "number" | "text" | "boolean" | "multi_text"
    options: list[str] | None = None
    unit: str | None = None
    required: bool = True
    step: int = 0
    total_steps: int = 0


@dataclass
class QuestionnaireState:
    answers: dict[str, Any] = field(default_factory=dict)
    step_index: int = 0
    complete: bool = False


# ── Question flow ──────────────────────────────────────────────────────
# Each entry: (id, produces a Question given current answers)
# The flow is linear but some questions are conditionally skipped.

_LOADER = JurisdictionLoader("ca")
_ALL_CA_COUNTIES = [c.title() for c in _LOADER.known_counties()]
_ALL_CA_CITIES = [c.title() for c in _LOADER.known_cities()]

PROJECT_TYPES = [
    "ADU — Detached",
    "ADU — Attached",
    "ADU — Garage / Structure Conversion",
    "ADU — Junior (JADU, within primary dwelling)",
    "Addition to existing home",
    "Remodel (no sq ft change)",
    "New Construction — Single Family",
    "New Construction — Multi-Family",
]

ADU_TYPE_MAP = {
    "ADU — Detached": "detached",
    "ADU — Attached": "attached",
    "ADU — Garage / Structure Conversion": "garage_conversion",
    "ADU — Junior (JADU, within primary dwelling)": "jadu",
}

FIRE_ZONES = [
    "SRA — State Responsibility Area",
    "LRA — Local Responsibility Area (urban/suburban)",
    "VHFHSZ — Very High Fire Hazard Severity Zone (city-designated)",
    "Non-WUI — No special fire designation",
    "Unknown — Need to verify",
]


def _as_positive_float(value: Any) -> float | None:
    """Return value as a float if it's numeric and > 0, else None.

    Non-numeric answers (e.g. "N/A", "") are treated as "not provided" rather
    than crashing profile resolution.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _make_questions(answers: dict[str, Any]) -> list[Question]:
    """Return the full ordered question list, adjusted for current answers."""
    questions: list[Question] = []

    questions.append(Question(
        id="project_type",
        text="What type of project is this?",
        help="This determines which building codes, permit process, and checklist apply.",
        type="select",
        options=PROJECT_TYPES,
    ))

    questions.append(Question(
        id="county",
        text="Which California county is the property in?",
        help=(
            "Select the county. If you are in an incorporated city, the city rules will "
            "layer on top of the county. If you are in an unincorporated area, select the "
            "county and enter 'Unincorporated' for the city."
        ),
        type="select",
        options=sorted({c.title() for c in _LOADER.known_counties()} | {"Other — Not Listed"}),
    ))

    questions.append(Question(
        id="city",
        text="Which city or municipality is the property in?",
        help=(
            "Enter the city name. Type 'Unincorporated' if the property is in an unincorporated "
            "county area (no city jurisdiction). If your city is not in our database, select "
            "'Other — Not Listed' and you can enter key restrictions manually."
        ),
        type="select",
        options=sorted({c.title() for c in _LOADER.known_cities()} | {"Unincorporated", "Other — Not Listed"}),
    ))

    questions.append(Question(
        id="hoa_exists",
        text="Is the property subject to an HOA or CC&R restrictions?",
        help=(
            "Homeowners Associations and Covenants, Conditions & Restrictions (CC&Rs) are private "
            "rules that layer on top of government codes. CA law prohibits HOAs from banning ADUs "
            "(CA Civ Code §4751), but they can impose design standards."
        ),
        type="boolean",
        options=["Yes", "No"],
    ))

    if answers.get("hoa_exists") in (True, "Yes", "yes"):
        questions.append(Question(
            id="hoa_max_height_ft",
            text="What is the HOA's maximum allowed building height (feet)?",
            help="Check your CC&Rs or HOA architectural guidelines. Leave blank / enter 0 if not specified.",
            type="number",
            unit="ft",
            required=False,
        ))
        questions.append(Question(
            id="hoa_additional_setback_side_ft",
            text="What is the HOA's required side-yard setback (total feet from property line)?",
            help=(
                "Enter the TOTAL side setback your HOA requires, not the additional amount. "
                "Example: if code requires 4 ft and your HOA requires 7 ft, enter 7. "
                "Enter 0 if your HOA has no side setback requirement beyond code."
            ),
            type="number",
            unit="ft",
            required=False,
        ))
        questions.append(Question(
            id="hoa_additional_setback_rear_ft",
            text="What is the HOA's required rear-yard setback (total feet from property line)?",
            help=(
                "Enter the TOTAL rear setback your HOA requires, not the additional amount. "
                "Example: if code requires 4 ft and your HOA requires 6 ft, enter 6. "
                "Enter 0 if your HOA has no rear setback requirement beyond code."
            ),
            type="number",
            unit="ft",
            required=False,
        ))
        questions.append(Question(
            id="hoa_arch_review_required",
            text="Does the HOA require architectural/design review approval before permitting?",
            help="Most HOAs with CC&Rs require ARC (Architectural Review Committee) approval.",
            type="boolean",
            options=["Yes", "No"],
        ))
        questions.append(Question(
            id="hoa_notes",
            text="Any other HOA restrictions to note? (materials, colors, style requirements, etc.)",
            help="Enter free text. This will appear in the project notes and code-analysis sheet.",
            type="text",
            required=False,
        ))

    questions.append(Question(
        id="lot_size_sqft",
        text="What is the lot size (square feet)?",
        help="Found on your property tax bill, title report, or county assessor website (assessor.COUNTY.ca.gov).",
        type="number",
        unit="sq ft",
    ))

    # Existing primary dwelling info — skip for pure new construction
    project_type = answers.get("project_type", "")
    is_new_construction = "New Construction" in project_type
    if not is_new_construction:
        questions.append(Question(
            id="existing_primary_sqft",
            text="What is the square footage of the existing primary dwelling?",
            help="Gross living area (GLA) in square feet. Used to calculate attached ADU max size (50% of primary).",
            type="number",
            unit="sq ft",
        ))
        questions.append(Question(
            id="existing_year_built",
            text="What year was the primary dwelling built?",
            help="Used to determine smoke detector, CO detector, and egress window upgrade requirements.",
            type="number",
            unit="year",
        ))

    questions.append(Question(
        id="fire_zone",
        text="What fire hazard zone is the property in?",
        help=(
            "Look up at CAL FIRE FHSZ viewer: https://egis.fire.ca.gov/FHSZ or your county fire dept. "
            "SRA = state-managed wildlands. LRA = local (city/county) responsibility. "
            "VHFHSZ = highest hazard designation."
        ),
        type="select",
        options=FIRE_ZONES,
        required=False,
    ))

    questions.append(Question(
        id="coastal_zone",
        text="Is the property within the California Coastal Zone?",
        help=(
            "Properties in the Coastal Zone require a Coastal Development Permit (CDP) in addition "
            "to standard building permits. Check at CA Coastal Commission: https://www.coastal.ca.gov"
        ),
        type="boolean",
        options=["Yes", "No", "Unknown"],
        required=False,
    ))

    # ADU-specific questions
    is_adu = project_type in ADU_TYPE_MAP or "ADU" in project_type
    if is_adu or not project_type:
        questions.append(Question(
            id="adu_target_sqft",
            text="What is your target ADU square footage?",
            help=(
                "Detached ADU: up to 1,200 sq ft statewide. "
                "JADU: up to 500 sq ft. "
                "Attached ADU: up to 50% of primary or 1,200 sq ft, whichever is less."
            ),
            type="number",
            unit="sq ft",
        ))
        questions.append(Question(
            id="adu_bedrooms",
            text="How many bedrooms will the ADU have?",
            help="Studio = 0, up to 3 bedrooms typical for 1,200 sq ft ADU.",
            type="select",
            options=["Studio (0 BR)", "1 Bedroom", "2 Bedrooms", "3 Bedrooms"],
        ))

    # Set step numbers
    total = len(questions)
    for i, q in enumerate(questions):
        q.step = i + 1
        q.total_steps = total

    return questions


# ── Questionnaire class ────────────────────────────────────────────────

class Questionnaire:
    """Stateful questionnaire that yields Questions and accepts answers."""

    def __init__(self):
        self.state = QuestionnaireState()

    @property
    def complete(self) -> bool:
        return self.state.complete

    @property
    def answers(self) -> dict[str, Any]:
        return self.state.answers

    def current_question(self) -> Question | None:
        """Return the current unanswered question, or None if complete."""
        if self.state.complete:
            return None
        questions = _make_questions(self.state.answers)
        answered = set(self.state.answers.keys())
        for q in questions:
            if q.id not in answered:
                return q
        self.state.complete = True
        return None

    def answer(self, question_id: str, value: Any) -> Question | None:
        """Record an answer and return the next question (or None if done)."""
        # Normalise booleans
        if isinstance(value, str) and value.lower() in ("yes", "true"):
            value = True
        elif isinstance(value, str) and value.lower() in ("no", "false"):
            value = False
        self.state.answers[question_id] = value
        return self.current_question()

    def progress(self) -> dict[str, Any]:
        """Return current progress status."""
        questions = _make_questions(self.state.answers)
        answered = sum(1 for q in questions if q.id in self.state.answers)
        total = len(questions)
        return {
            "answered": answered,
            "total": total,
            "percent": round(answered / total * 100) if total else 0,
            "complete": self.state.complete,
        }

    def reset(self):
        self.state = QuestionnaireState()

    def build_hoa_data(self) -> dict[str, Any] | None:
        """Extract HOA rules from answers into a rules dict for the merger."""
        if not self.state.answers.get("hoa_exists"):
            return None
        hoa: dict[str, Any] = {}
        if (h := _as_positive_float(self.state.answers.get("hoa_max_height_ft"))) is not None:
            hoa["max_height_ft"] = h
        if (s := _as_positive_float(self.state.answers.get("hoa_additional_setback_side_ft"))) is not None:
            hoa["setback_side_ft"] = s
        if (r := _as_positive_float(self.state.answers.get("hoa_additional_setback_rear_ft"))) is not None:
            hoa["setback_rear_ft"] = r
        if self.state.answers.get("hoa_arch_review_required"):
            hoa["design_review_required"] = True
        if n := self.state.answers.get("hoa_notes"):
            hoa["notes"] = [n]
        return hoa if hoa else None
