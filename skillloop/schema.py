"""Skill schema: the smallest representation that satisfies the course requirement
(``skill_id`` / ``skill_name`` / ``description``) plus what our experiments need
(conditions for retrieval, evidence for gating, version for lifecycle).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

LAYERS = ("rule", "strategy", "stage_specific")
STATUSES = ("candidate", "active", "degraded", "retired")


@dataclass(frozen=True)
class SkillConditions:
    """When a skill is *relevant*. Unspecified fields are wildcards.

    These conditions are used for (a) retrieval filtering and (b) defining what
    "the skill was activated" means in the logs. They never gate execution.
    """

    stages: tuple[str, ...] = ()          # e.g. ("wagner", "marjorie")
    objective_types: tuple[str, ...] = ()  # e.g. ("win_within_rounds",)
    enemies: tuple[str, ...] = ()          # enemy slugs
    round_min: int | None = None
    round_max: int | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.stages:
            out["stages"] = list(self.stages)
        if self.objective_types:
            out["objective_types"] = list(self.objective_types)
        if self.enemies:
            out["enemies"] = list(self.enemies)
        if self.round_min is not None:
            out["round_min"] = self.round_min
        if self.round_max is not None:
            out["round_max"] = self.round_max
        return out

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SkillConditions":
        payload = payload or {}
        return cls(
            stages=tuple(payload.get("stages") or ()),
            objective_types=tuple(payload.get("objective_types") or ()),
            enemies=tuple(payload.get("enemies") or ()),
            round_min=payload.get("round_min"),
            round_max=payload.get("round_max"),
        )


@dataclass(frozen=True)
class SkillEvidence:
    """What we know about the skill's measured effect (filled by ``skillloop.gate``)."""

    library_version: int | None = None
    games: int = 0
    delta_three_star: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {"games": self.games}
        for key in ("library_version", "delta_three_star", "ci_low", "ci_high"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SkillEvidence":
        payload = payload or {}
        return cls(
            library_version=payload.get("library_version"),
            games=int(payload.get("games") or 0),
            delta_three_star=payload.get("delta_three_star"),
            ci_low=payload.get("ci_low"),
            ci_high=payload.get("ci_high"),
            note=str(payload.get("note") or ""),
        )


@dataclass
class Skill:
    skill_id: str
    skill_name: str
    description: str
    conditions: SkillConditions = field(default_factory=SkillConditions)
    layer: str = "strategy"
    evidence: SkillEvidence = field(default_factory=SkillEvidence)
    version: int = 1
    status: str = "active"
    source_games: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ValueError(f"unknown layer {self.layer!r}; expected one of {LAYERS}")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}; expected one of {STATUSES}")
        if not self.skill_id or not self.skill_name or not self.description:
            raise ValueError("skill_id / skill_name / description are required")

    # -- serialisation ------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        payload = {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "description": self.description,
            "conditions": self.conditions.as_dict(),
            "layer": self.layer,
            "evidence": self.evidence.as_dict(),
            "version": self.version,
            "status": self.status,
        }
        if self.source_games:
            payload["source_games"] = list(self.source_games)
        if self.created_at:
            payload["created_at"] = self.created_at
        if self.updated_at:
            payload["updated_at"] = self.updated_at
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Skill":
        return cls(
            skill_id=str(payload["skill_id"]),
            skill_name=str(payload["skill_name"]),
            description=str(payload["description"]),
            conditions=SkillConditions.from_dict(payload.get("conditions")),
            layer=str(payload.get("layer") or "strategy"),
            evidence=SkillEvidence.from_dict(payload.get("evidence")),
            version=int(payload.get("version") or 1),
            status=str(payload.get("status") or "active"),
            source_games=tuple(payload.get("source_games") or ()),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )

    def prompt_block(self) -> str:
        """The text injected into the LLM context (stable wording = cache friendly)."""
        return f"- [{self.skill_id}] {self.skill_name}: {self.description}"

    def asdict(self) -> dict[str, Any]:  # convenience for tests / reports
        return asdict(self)
