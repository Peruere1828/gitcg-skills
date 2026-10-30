"""Skill library for the course project (skill self-evolution for an LLM agent).

INNOVATION: layered library + activation log + evidence-gated admission.

The library is deliberately *data*, not code: a skill is a natural-language
description (what the LLM reads) plus metadata (what retrieval and the statistics
use). Nothing here executes a skill; execution belongs to the LLM agent in
``skillloop.agent``. That split is what lets us measure a library version against
another one with the same seeds (``skillloop.gate``).
"""

from __future__ import annotations

from .library import SkillLibrary, match_context, tokenize
from .schema import Skill, SkillConditions, SkillEvidence

__all__ = [
    "Skill",
    "SkillConditions",
    "SkillEvidence",
    "SkillLibrary",
    "match_context",
    "tokenize",
]
