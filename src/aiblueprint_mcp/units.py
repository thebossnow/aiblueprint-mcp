"""Architectural length parsing.

Drawing units are decimal feet everywhere in aiblueprint-mcp. ``parse_length``
lets tool inputs also be written the way drafters write them — ``5'-6"``,
``5' 6 1/2"``, ``66"``, ``12 ft``, ``6in`` — and normalizes to decimal feet.

Bare numbers stay feet (``12`` -> 12.0), so existing callers are unaffected.
A bare number string with a space between two numbers is read as feet and
inches (``"5 5"`` -> 5 ft 5 in), matching common CAD keyboard entry.
"""

from __future__ import annotations

import re
from fractions import Fraction

__all__ = ["parse_length"]

_NUM = r"(?:\d+(?:\.\d+)?|\.\d+)"
# A quantity: integer/decimal, a fraction ("1/2"), or a mixed number ("6 1/2").
_QTY = rf"(?:{_NUM}(?:\s+\d+/\d+)?|\d+/\d+)"

_FEET_INCHES = re.compile(
    rf"""^\s*
    (?:(?P<ft>{_QTY})\s*(?:'|ft|feet|foot)\s*-?\s*)?
    (?:(?P<in>{_QTY})\s*(?:"|in|inch|inches)?\s*)?
    $""",
    re.IGNORECASE | re.VERBOSE,
)
_SPACE_PAIR = re.compile(rf"^\s*(?P<ft>{_NUM})\s+(?P<in>{_QTY})\s*$")


def _qty(text: str) -> float:
    total = Fraction(0)
    for part in text.split():
        total += Fraction(part)
    return float(total)


def parse_length(value: float | int | str) -> float:
    """Return ``value`` as decimal feet.

    Accepts numbers (feet) or strings in feet-and-inches notation. Raises
    ``ValueError`` with an example-bearing message on anything unparseable.
    """
    if isinstance(value, bool):
        raise ValueError("length must be a number or string, not a boolean")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"length must be a number or string, got {type(value).__name__}")

    text = value.strip()
    negative = text.startswith("-")
    if negative:
        text = text[1:].strip()

    bad = ValueError(
        f"Cannot parse length {value!r}. Use decimal feet (12.5) or feet-inches "
        "like 12'-6\", 5' 6 1/2\", 66\", or '5 5'."
    )
    if not text:
        raise bad

    try:
        if re.fullmatch(_QTY, text):
            feet = _qty(text)
        elif m := _SPACE_PAIR.match(text):
            feet = _qty(m["ft"]) + _qty(m["in"]) / 12
        elif m := _FEET_INCHES.match(text):
            ft, inch = m["ft"], m["in"]
            if ft is None and inch is None:
                raise bad
            if ft is None and not re.search(r'"|in', text, re.IGNORECASE):
                raise bad
            feet = (_qty(ft) if ft else 0.0) + (_qty(inch) / 12 if inch else 0.0)
        else:
            raise bad
    except ZeroDivisionError:
        raise bad from None
    return -feet if negative else feet
