"""Skill library: storage, retrieval, lifecycle, and activation accounting.

INNOVATION: activation accounting + evidence-gated lifecycle.

Three behaviours matter for the course project and are implemented here:

1. **Layered retrieval** — a `stage_specific` skill outranks a `strategy` skill that
   outranks a `rule` skill when both are applicable. The rules layer is always a
   candidate (the agent must know how cards work); strategy layers compete for the
   remaining context budget.
2. **Activation accounting** — every retrieval is counted per skill, so we can later
   ask "did this skill actually earn its context?" (`usage.json`). A skill that is
   never retrieved/used cannot be defended and is retired by `skillloop.extract`.
3. **Versioned snapshots** — every admission batch bumps `library_version` and writes
   `history/v{n}.json`, so the paired A/B in `skillloop.gate` compares two immutable
   library states rather than a moving target.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .schema import Skill, SkillConditions

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")

LAYER_BONUS = {"stage_specific": 1.5, "strategy": 0.8, "rule": 0.0}


def tokenize(text: str) -> list[str]:
    """Cheap bilingual tokenizer: ASCII words + CJK character bigrams.

    A dedicated Chinese segmenter (jieba) would be more accurate, but the skill
    descriptions are short and the retrieval only has to *rank* a few dozen items;
    bigrams keep the dependency list empty and the behaviour reproducible.
    """
    text = (text or "").lower()
    tokens = _ASCII_WORD.findall(text)
    cjk = _CJK.findall(text)
    tokens.extend(cjk)
    tokens.extend("".join(pair) for pair in zip(cjk, cjk[1:]))
    return tokens


def match_context(conditions: SkillConditions, context: dict[str, Any]) -> bool:
    """True when every specified condition holds for ``context``.

    ``context`` keys: ``stage``, ``objective_types`` (iterable), ``enemies``
    (iterable), ``round`` (int). Unspecified conditions are wildcards, so a skill
    with ``SkillConditions()`` applies everywhere (the rules layer uses that).
    """
    if conditions.stages:
        if str(context.get("stage") or "") not in conditions.stages:
            return False
    if conditions.objective_types:
        have = set(context.get("objective_types") or ())
        if not have & set(conditions.objective_types):
            return False
    if conditions.enemies:
        have = set(context.get("enemies") or ())
        if not have & set(conditions.enemies):
            return False
    rnd = context.get("round")
    if conditions.round_min is not None:
        if rnd is None or int(rnd) < conditions.round_min:
            return False
    if conditions.round_max is not None:
        if rnd is None or int(rnd) > conditions.round_max:
            return False
    return True


class _BM25:
    def __init__(self, docs: dict[str, list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs = docs
        self.lengths = {key: len(tokens) for key, tokens in docs.items()}
        self.avg = (sum(self.lengths.values()) / len(self.lengths)) if self.lengths else 0.0
        self.tf = {key: Counter(tokens) for key, tokens in docs.items()}
        df: Counter[str] = Counter()
        for tokens in docs.values():
            df.update(set(tokens))
        n = len(docs) or 1
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def score(self, query: Sequence[str], key: str) -> float:
        if not query or not self.avg:
            return 0.0
        tf = self.tf[key]
        length = self.lengths[key] or 1
        total = 0.0
        for term in query:
            freq = tf.get(term)
            if not freq:
                continue
            total += self.idf.get(term, 0.0) * freq * (self.k1 + 1) / (
                freq + self.k1 * (1 - self.b + self.b * length / self.avg)
            )
        return total


@dataclass
class SkillLibrary:
    """A versioned, retrievable skill collection."""

    skills: dict[str, Skill] = field(default_factory=dict)
    version: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    # -- construction -------------------------------------------------------
    @classmethod
    def from_skills(cls, skills: Iterable[Skill], *, version: int = 1, meta: dict | None = None) -> "SkillLibrary":
        lib = cls(skills={skill.skill_id: skill for skill in skills}, version=version, meta=dict(meta or {}))
        return lib

    def __len__(self) -> int:
        return len(self.skills)

    def __iter__(self):
        return iter(sorted(self.skills.values(), key=lambda s: s.skill_id))

    def get(self, skill_id: str) -> Skill | None:
        return self.skills.get(skill_id)

    def active(self) -> list[Skill]:
        return [skill for skill in self if skill.status == "active"]

    def rules_text(self, *, exclude_ids: Iterable[str] = ()) -> str:
        """The always-on rules layer, as a stable (sorted) prompt block.

        ``exclude_ids`` drops rule skills that are already injected elsewhere (e.g.
        a frozen loadout pins ``rule_burst``: it stays in the pinned block, so the
        same block must not appear a second time under the rules note).
        """
        excluded = set(exclude_ids)
        rules = [
            skill for skill in self.active()
            if skill.layer == "rule" and skill.skill_id not in excluded
        ]
        return "\n".join(skill.prompt_block() for skill in sorted(rules, key=lambda s: s.skill_id))

    def by_layer(self) -> dict[str, int]:
        counts = Counter(skill.layer for skill in self.skills.values())
        return {layer: counts.get(layer, 0) for layer in ("rule", "strategy", "stage_specific")}

    # -- mutation -----------------------------------------------------------
    def add(self, skill: Skill, *, now: str | None = None) -> Skill:
        if skill.skill_id in self.skills:
            raise KeyError(f"skill {skill.skill_id!r} already exists; use update()")
        stamp = now or _now()
        skill.created_at = skill.created_at or stamp
        skill.updated_at = stamp
        self.skills[skill.skill_id] = skill
        return skill

    def update(self, skill_id: str, **changes: Any) -> Skill:
        """Apply a change and bump the skill's version (history stays in snapshots)."""
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"unknown skill {skill_id!r}")
        for key, value in changes.items():
            if not hasattr(skill, key):
                raise KeyError(f"skill has no field {key!r}")
            setattr(skill, key, value)
        skill.version += 1
        skill.updated_at = _now()
        return skill

    def retire(self, skill_id: str, *, reason: str) -> Skill:
        skill = self.update(skill_id, status="retired")
        note = (skill.evidence.note + " | " if skill.evidence.note else "") + f"retired: {reason}"
        skill.evidence = replace(skill.evidence, note=note)   # SkillEvidence is frozen
        return skill

    # -- retrieval ----------------------------------------------------------
    def applicable(self, context: dict[str, Any]) -> list[Skill]:
        return [skill for skill in self.active() if match_context(skill.conditions, context)]

    def retrieve(
        self,
        query: str,
        context: dict[str, Any],
        k: int = 3,
        *,
        layers: tuple[str, ...] | None = ("strategy", "stage_specific"),
    ) -> list[Skill]:
        """Top-k applicable skills for ``query`` under ``context``.

        Rules-layer skills are always *applicable* but score with no layer bonus, so
        a task-specific skill wins the slot when the query matches. Ties break on
        ``skill_id`` to keep prompts byte-stable (prompt caching).
        """
        if k <= 0:
            return []
        pool = self.applicable(context)
        if layers is not None:
            pool = [skill for skill in pool if skill.layer in layers]
        if not pool:
            return []
        docs = {skill.skill_id: tokenize(f"{skill.skill_name} {skill.description}") for skill in pool}
        bm25 = _BM25(docs)
        query_tokens = tokenize(query)
        scored = []
        for skill in pool:
            score = bm25.score(query_tokens, skill.skill_id) + LAYER_BONUS.get(skill.layer, 0.0)
            scored.append((score, skill.skill_id, skill))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [skill for _score, _id, skill in scored[:k]]

    # -- persistence --------------------------------------------------------
    def save(self, root: str | Path, *, snapshot: bool = False) -> Path:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "library_version": self.version,
            "meta": self.meta,
            "skills": [skill.as_dict() for skill in self],
        }
        target = root / "library.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if snapshot:
            history = root / "history"
            history.mkdir(exist_ok=True)
            (history / f"v{self.version}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return target

    @classmethod
    def load(cls, root: str | Path) -> "SkillLibrary":
        path = Path(root) / "library.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        skills = [Skill.from_dict(item) for item in payload.get("skills") or []]
        return cls(
            skills={skill.skill_id: skill for skill in skills},
            version=int(payload.get("library_version") or 0),
            meta=dict(payload.get("meta") or {}),
        )

    def bump_version(self, *, note: str, snapshot_root: str | Path | None = None) -> int:
        self.version += 1
        self.meta.setdefault("history", []).append({"version": self.version, "note": note, "at": _now()})
        if snapshot_root is not None:
            self.save(snapshot_root, snapshot=True)
        return self.version


@dataclass
class UsageLog:
    """Activation accounting: how often each skill was retrieved / cited by the LLM."""

    retrieved: Counter = field(default_factory=Counter)
    cited: Counter = field(default_factory=Counter)
    decisions: int = 0

    def record(self, retrieved: Sequence[str], cited: Sequence[str] = ()) -> None:
        self.decisions += 1
        self.retrieved.update(retrieved)
        self.cited.update(cited)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions": self.decisions,
            "retrieved": dict(self.retrieved),
            "cited": dict(self.cited),
        }

    def save(self, root: str | Path) -> Path:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "usage.json"
        target.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, root: str | Path) -> "UsageLog":
        path = Path(root) / "usage.json"
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            retrieved=Counter(payload.get("retrieved") or {}),
            cited=Counter(payload.get("cited") or {}),
            decisions=int(payload.get("decisions") or 0),
        )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
