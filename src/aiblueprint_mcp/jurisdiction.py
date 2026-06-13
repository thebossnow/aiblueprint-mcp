"""Jurisdiction rule loader and hierarchical merger.

Rules cascade:  CA state → county → city → HOA/CC&Rs (private)
Merger invariant: lower layers can only tighten (more restrictive), never relax.

Loading:
  JurisdictionLoader.load_state() → dict
  JurisdictionLoader.resolve_stack(state, county, city) → [state_data, county_data, city_data]

Merging:
  merge_layers(layers, adu_type, hoa_data?) → EffectiveRequirements
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DATA_ROOT = Path(__file__).parent / "data" / "jurisdictions"


# ── Loader ─────────────────────────────────────────────────────────────

class JurisdictionLoader:
    """Load jurisdiction JSON data files by name."""

    def __init__(self, state: str = "ca"):
        self._root = _DATA_ROOT / state.lower()
        self._index: dict[str, Any] | None = None

    def _get_index(self) -> dict[str, Any]:
        if self._index is None:
            idx_path = self._root / "index.json"
            if not idx_path.exists():
                raise FileNotFoundError(f"Jurisdiction index not found: {idx_path}")
            self._index = json.loads(idx_path.read_text())
        return self._index

    def known_counties(self) -> list[str]:
        return sorted(self._get_index().get("counties", {}).keys())

    def known_cities(self) -> list[str]:
        return sorted(self._get_index().get("cities", {}).keys())

    def load_state(self) -> dict[str, Any]:
        return json.loads((self._root / "state.json").read_text())

    def load_county(self, name: str) -> dict[str, Any] | None:
        key = name.strip().lower()
        rel = self._get_index().get("counties", {}).get(key)
        if rel is None:
            return None
        path = self._root / rel
        return json.loads(path.read_text()) if path.exists() else None

    def load_city(self, name: str) -> dict[str, Any] | None:
        key = name.strip().lower()
        rel = self._get_index().get("cities", {}).get(key)
        if rel is None:
            return None
        path = self._root / rel
        return json.loads(path.read_text()) if path.exists() else None

    def resolve_stack(
        self, county: str | None, city: str | None
    ) -> list[dict[str, Any]]:
        """Return ordered list [state, county?, city?] of loaded data."""
        stack = [self.load_state()]
        if county and county.lower() != "unincorporated":
            c = self.load_county(county)
            if c:
                stack.append(c)
        if city and city.lower() != "unincorporated":
            ct = self.load_city(city)
            if ct:
                stack.append(ct)
        return stack


# ── Merge logic ────────────────────────────────────────────────────────

@dataclass
class EffectiveRequirements:
    """Merged, most-restrictive rule set for a specific project."""

    adu_type: str
    rules: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    disclaimers: list[str] = field(default_factory=list)
    residential: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "adu_type": self.adu_type,
            "effective_rules": self.rules,
            "sources": self.sources,
            "notes": self.notes,
            "warnings": self.warnings,
            "disclaimers": self.disclaimers,
            "residential_standards": self.residential,
        }


# How to pick the most restrictive value for each numeric rule.
# "min" → smaller value is more restrictive (height limit, max sq ft)
# "max" → larger value is more restrictive (setback distance)
# "any_true" → True if any layer requires it
# "all_false" → False (i.e. NOT required) only if all layers say so

_NUMERIC_MERGE: dict[str, str] = {
    "max_sqft": "min",
    "max_height_ft": "min",
    "setback_side_ft": "max",
    "setback_rear_ft": "max",
    "setback_front_ft": "max",
    "lot_coverage_max_pct": "min",
    "impact_fees_waived_under_sqft": "min",
    "max_per_lot": "min",
}

_BOOL_MERGE: dict[str, str] = {
    "parking_required": "any_true",
    "fire_sprinklers": "any_true",
    "solar_required": "any_true",
    "owner_occupancy_required": "any_true",
    "design_review_required": "any_true",
    "ministerial_approval": "all_true",
    "replacement_parking_required": "any_true",
}


def merge_layers(
    layers: list[dict[str, Any]],
    adu_type: str,
    hoa_data: dict[str, Any] | None = None,
) -> EffectiveRequirements:
    """Merge jurisdiction layers + optional HOA data into effective requirements."""
    req = EffectiveRequirements(adu_type=adu_type)

    # Collect disclaimer/notes from each layer
    for layer in layers:
        if d := layer.get("disclaimer"):
            req.disclaimers.append(f"[{layer.get('name', '?')}] {d}")

    # Extract the adu sub-section for the requested type from each layer
    adu_layers: list[tuple[str, dict[str, Any]]] = []
    for layer in layers:
        adu_section = layer.get("adu", {})
        type_section = adu_section.get(adu_type, {})
        if type_section:
            adu_layers.append((layer.get("name", "?"), type_section))

    # Apply HOA as an additional layer
    if hoa_data:
        adu_layers.append(("HOA / CC&Rs", hoa_data))

    # Merge each numeric rule
    for param, strategy in _NUMERIC_MERGE.items():
        values: list[tuple[str, Any]] = [
            (name, section[param])
            for name, section in adu_layers
            if param in section and section[param] is not None
        ]
        if not values:
            continue
        if strategy == "min":
            chosen_name, chosen_val = min(values, key=lambda t: t[1])
        else:  # max
            chosen_name, chosen_val = max(values, key=lambda t: t[1])
        req.rules[param] = chosen_val
        # Track which layer imposed the most-restrictive value
        req.sources[param] = _source_citation(adu_layers, param, chosen_name)

    # Merge boolean rules
    for param, strategy in _BOOL_MERGE.items():
        values = [(name, _coerce_bool(section.get(param))) for name, section in adu_layers if param in section]
        if not values:
            continue
        if strategy == "any_true":
            effective = any(v for _, v in values)
            imposing = [n for n, v in values if v]
        else:  # all_true
            effective = all(v for _, v in values)
            imposing = [n for n, v in values if not v]
        req.rules[param] = effective
        req.sources[param] = ", ".join(imposing) if imposing else "all layers"

    # Collect notes from all adu type sections
    for _, section in adu_layers:
        req.notes.extend(section.get("notes", []))

    # Add state residential standards (smoke/CO, egress, ceiling height)
    for layer in layers:
        if res := layer.get("residential"):
            req.residential.update(res)

    # Warnings for things needing professional verification
    _add_warnings(req, layers, hoa_data)

    return req


def _source_citation(
    adu_layers: list[tuple[str, dict[str, Any]]], param: str, imposing_name: str
) -> str:
    for name, section in adu_layers:
        if name == imposing_name and param in (section.get("citations") or {}):
            return section["citations"][param]
    return imposing_name


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "always", "if_primary_requires")
    return bool(value)


def _add_warnings(
    req: EffectiveRequirements,
    layers: list[dict[str, Any]],
    hoa_data: dict[str, Any] | None,
) -> None:
    """Flag items that need professional verification."""
    warnings = []

    # Coastal zone
    for layer in layers:
        if layer.get("coastal", {}).get("cdp_required") or layer.get("coastal", {}).get("cdp_may_be_required"):
            warnings.append(
                f"[{layer.get('name','?')}] Coastal Development Permit (CDP) may be required. "
                "Verify with the CA Coastal Commission or local planning department."
            )
        if layer.get("coastal", {}).get("entirely_in_coastal_zone"):
            warnings.append(
                f"[{layer.get('name','?')}] Jurisdiction is entirely within the CA Coastal Zone — "
                "a CDP is required in addition to standard permits."
            )

    # Fire / WUI
    for layer in layers:
        fire = layer.get("fire", {})
        if fire.get("wui_construction_required") or fire.get("vhfhsz_applies"):
            warnings.append(
                f"[{layer.get('name','?')}] Property may be in a Wildland-Urban Interface (WUI) "
                "or Very High Fire Hazard Severity Zone. "
                "Verify fire zone designation and Chapter 7A/7B construction requirements."
            )

    # HOA
    if hoa_data:
        warnings.append(
            "HOA/CC&R restrictions are private and may impose design standards beyond code. "
            "Obtain HOA architectural approval BEFORE submitting for permit. "
            "Note: CA Civil Code §4751 prohibits HOAs from banning ADUs."
        )

    # Structural / geotechnical
    if req.rules.get("setback_side_ft") is None and req.rules.get("setback_rear_ft") is None:
        warnings.append("Setback data unavailable for this jurisdiction. Verify with building department.")

    warnings.append(
        "These rules are best-effort data and may not reflect the most current ordinance. "
        "Always verify final requirements with the applicable building department before permit submission. "
        "Structural drawings and calculations must be stamped by a licensed engineer."
    )

    req.warnings = warnings
