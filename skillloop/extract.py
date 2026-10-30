"""Skill extraction: turn game results into *candidate* skills (not yet admitted).

Two deliberate constraints:

* **Extraction sees statistics, not single games.** The prompt gets the stage's
  objectives and a failure profile (which objectives fail how often, round
  distribution). A single unlucky game is exactly the kind of "experience" that
  produces skills nobody can replicate; the paired gate in ``skillloop.gate`` then
  decides whether a candidate actually helps.
* **Candidates are inert.** They enter the library with ``status="candidate"`` and are
  never retrieved by the agent; only the gate can promote them to ``active``.

INNOVATION: failure-profile driven extraction + candidate quarantine.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from common.llm import extract_json_object
from skillloop.schema import Skill, SkillConditions

SYSTEM = (
    "你是《七圣召唤》PvE 的策略教练。你会看到某个关卡的**失败统计**（不是单局回放），"
    "请提出 1-2 条**可复用**的打法原则，能跨种子、跨同类型关卡使用。"
    "不要写具体某一手的操作，不要编造数据。"
    "只输出 JSON 数组，元素形如："
    '{"skill_name": "...", "description": "...", '
    '"conditions": {"objective_types": ["..."], "round_max": 3}, '
    '"layer": "strategy" 或 "stage_specific"}'
)


@dataclass
class StageProfile:
    stage: str
    n: int
    three_star_rate: float
    mean_rounds: float
    failed_objectives: dict[str, int]
    objectives: tuple[str, ...] = ()

    def as_text(self) -> str:
        failed = "，".join(f"{key} 失败 {count}/{self.n}" for key, count in
                           sorted(self.failed_objectives.items(), key=lambda kv: -kv[1]))
        return (
            f"关卡 {self.stage}：三星率 {self.three_star_rate:.2f}（n={self.n}），"
            f"平均回合 {self.mean_rounds:.1f}；未达成目标：{failed or '无'}；"
            f"关卡目标：{'；'.join(self.objectives) or '（未知）'}"
        )


def failure_profiles(
    rows: Iterable[dict[str, Any]],
    *,
    objectives_by_stage: dict[str, Sequence[str]] | None = None,
) -> list[StageProfile]:
    """Aggregate per-game rows into one failure profile per stage."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("error") or row.get("truncated"):
            continue
        buckets[str(row.get("stage"))].append(row)

    profiles: list[StageProfile] = []
    for stage, items in sorted(buckets.items()):
        n = len(items)
        failed: Counter[str] = Counter()
        for item in items:
            for objective, achieved in (item.get("objectives") or {}).items():
                if not achieved:
                    failed[str(objective)] += 1
        profiles.append(
            StageProfile(
                stage=stage,
                n=n,
                three_star_rate=sum(1 for item in items if item.get("three_star")) / n,
                mean_rounds=sum(float(item.get("rounds") or 0) for item in items) / n,
                failed_objectives=dict(failed),
                objectives=tuple((objectives_by_stage or {}).get(stage, ())),
            )
        )
    return profiles


def _slug(text: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return ascii_part or "skill"


def build_prompt(profile: StageProfile, *, rules_note: str = "") -> list[dict[str, str]]:
    parts = [profile.as_text()]
    if rules_note:
        parts.append("已知规则：\n" + rules_note)
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def parse_skills(raw: str | None, *, stage: str) -> list[Skill]:
    """Parse the model's JSON array into candidate skills (invalid entries dropped)."""
    if not raw:
        return []
    payload: Any = None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # tolerate a wrapper object / code fences
        start, end = raw.find("["), raw.rfind("]")
        if start >= 0 and end > start:
            try:
                payload = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                payload = None
    if payload is None:
        payload = extract_json_object(raw)
    if isinstance(payload, dict):
        payload = payload.get("skills")
    if not isinstance(payload, list):
        return []

    out: list[Skill] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        name = str(item.get("skill_name") or "").strip()
        desc = str(item.get("description") or "").strip()
        if not name or not desc:
            continue
        conditions = SkillConditions.from_dict(item.get("conditions") or {})
        layer = str(item.get("layer") or "strategy")
        if layer not in ("strategy", "stage_specific"):
            layer = "strategy"
        skill_id = f"{_slug(name)}_{stage}"
        out.append(
            Skill(
                skill_id=skill_id,
                skill_name=name,
                description=desc,
                conditions=conditions,
                layer=layer,
                status="candidate",
                source_games=(f"{stage}:profile",),
            )
        )
    return out


def propose_candidates(
    client: Any,
    profiles: Sequence[StageProfile],
    *,
    model: str | None = None,
    rules_note: str = "",
    max_tokens: int = 8192,
    effort: str | None = "high",
    min_failure_rate: float = 0.15,
) -> list[Skill]:
    """One LLM call per stage that actually loses stars; returns candidate skills.

    Extraction runs **once per evolution round**, not per decision, so it gets the
    expensive setting (``effort="high"``, 8k output budget) while in-game decisions
    stay cheap (``effort="low"``). Reflection is where quality compounds.
    """
    candidates: list[Skill] = []
    for profile in profiles:
        if profile.three_star_rate >= 1.0:
            continue
        if not profile.failed_objectives:
            continue
        worst = max(profile.failed_objectives.values()) / max(1, profile.n)
        if worst < min_failure_rate:
            continue
        raw = client.chat(
            build_prompt(profile, rules_note=rules_note),
            max_tokens=max_tokens,
            temperature=0.0,
            model=model,
            reasoning_effort=effort,
        )
        candidates.extend(parse_skills(raw, stage=profile.stage))
    # de-duplicate while keeping the first occurrence
    seen: set[str] = set()
    unique: list[Skill] = []
    for skill in candidates:
        if skill.skill_id in seen:
            continue
        seen.add(skill.skill_id)
        unique.append(skill)
    return unique
