"""Skill extraction: turn game results into *candidate* skills (not yet admitted).

Two deliberate constraints:

* **Extraction sees statistics plus a small contrast set of replays.** The prompt
  gets the stage's objectives, a failure profile (which objectives fail how
  often, round distribution), and at most two compact representative traces per
  stage: one close loss and one 3★ win. Full decision logs are never sent; the
  traces are ``skillloop.replay.compact_trace`` lines (action exchange + reactions
  + scoreboard), so the model can locate *where* a principle broke without
  overfitting to one lucky or unlucky game. The paired gate in ``skillloop.gate``
  still decides whether a candidate actually helps.
* **Candidates are inert.** They enter the library with ``status="candidate"`` and are
  never retrieved by the agent; only the gate can promote them to ``active``.

INNOVATION: failure-profile + contrast-replay driven extraction, candidate quarantine.
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
    "你是《七圣召唤》PvE 的策略教练。你接下来写的内容会**原样进入未来对局的系统提示**，"
    "直接改变模型的动作选择。你会看到某个关卡的失败统计和代表局回放"
    "（同一关卡的失败局与三星局对照）。"
    "请基于这些回放，写出你认为最能让自己下次打对的提示词："
    "可以是指令、判断条件、优先级、具体出手序列、反例、自查清单，任何形式都行；"
    "越具体、越可执行越好（可以写长）。"
    "不要复述关卡目标，不要写空泛的「要重视 X」。"
    "允许你指出上一版技能里哪条是错的/有害的，并给出替代写法。"
    "回放只用于定位问题，不要编造数据。"
    "只输出 JSON 数组，元素形如："
    '{"skill_name": "...", "description": "（将来原样注入的提示词正文）", '
    '"conditions": {"objective_types": ["..."], "round_max": 3}, '
    '"layer": "strategy" 或 "stage_specific"}'
)


@dataclass(frozen=True)
class ReplaySample:
    """One compact representative game, already rendered as prompt lines."""

    label: str               # "失败" or "三星"
    seed: int | None
    rounds: int
    missing: tuple[str, ...]
    lines: tuple[str, ...]

    def as_text(self, index: int) -> str:
        letter = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[index] if 0 <= index < 26 else str(index + 1)
        if self.missing:
            detail = "缺 " + "、".join(self.missing)
        elif self.label == "三星":
            detail = "目标全达成"
        else:
            detail = "缺目标（未记录）"
        seed = f"，seed={self.seed}" if self.seed is not None else ""
        header = (
            f"代表局 {letter}（{self.label}，{self.rounds} 回合，{detail}{seed}）:"
        )
        return header + "\n" + "\n".join(self.lines)


@dataclass
class StageProfile:
    stage: str
    n: int
    three_star_rate: float
    mean_rounds: float
    failed_objectives: dict[str, int]
    objectives: tuple[str, ...] = ()
    replays: list[ReplaySample] = field(default_factory=list)

    def as_text(self) -> str:
        failed = "，".join(f"{key} 失败 {count}/{self.n}" for key, count in
                           sorted(self.failed_objectives.items(), key=lambda kv: -kv[1]))
        return (
            f"关卡 {self.stage}：三星率 {self.three_star_rate:.2f}（n={self.n}），"
            f"平均回合 {self.mean_rounds:.1f}；未达成目标：{failed or '无'}；"
            f"关卡目标：{'；'.join(self.objectives) or '（未知）'}"
        )


def _row_rounds(item: dict[str, Any]) -> float:
    try:
        return float(item.get("rounds") or 0)
    except (TypeError, ValueError):
        return 0.0


def _row_seed(item: dict[str, Any]) -> int | None:
    value = item.get("seed")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _row_trace(item: dict[str, Any]) -> tuple[str, ...] | None:
    """Return a JSONL-safe trace, or ``None`` when the row has no usable trace."""
    raw = item.get("trace")
    if not isinstance(raw, (list, tuple)):
        return None
    lines = tuple(str(line) for line in raw if str(line).strip())
    return lines or None


def _missing_objectives(item: dict[str, Any]) -> tuple[str, ...]:
    """Names of unmet objectives, with a count-only fallback for old rows."""
    objectives = item.get("objectives")
    if isinstance(objectives, dict) and objectives:
        return tuple(str(name) for name, achieved in objectives.items() if not achieved)
    try:
        missing = int(item.get("n_objectives") or 0) - int(item.get("stars") or 0)
    except (TypeError, ValueError):
        missing = 0
    return (f"{max(0, missing)} 个目标",) if missing > 0 else ()


def _replay(
    item: dict[str, Any],
    lines: tuple[str, ...],
    *,
    label: str,
    missing: tuple[str, ...] = (),
) -> ReplaySample:
    rounds = int(round(_row_rounds(item)))
    return ReplaySample(
        label=label,
        seed=_row_seed(item),
        rounds=rounds,
        missing=missing,
        lines=lines,
    )


def _pick_replays(
    items: Sequence[dict[str, Any]],
    *,
    max_replays: int,
    round_cap: int | None,
) -> list[ReplaySample]:
    """One close failure + one fast 3★ game, both only if the row has a trace."""
    limit = min(2, max(0, int(max_replays)))
    if limit == 0:
        return []

    failures: list[tuple[tuple[Any, ...], dict[str, Any], tuple[str, ...], tuple[str, ...]]] = []
    wins: list[tuple[tuple[Any, ...], dict[str, Any], tuple[str, ...], tuple[str, ...]]] = []
    for index, item in enumerate(items):
        trace = _row_trace(item)
        if trace is None:
            continue
        missing = _missing_objectives(item)
        rounds = _row_rounds(item)
        over_cap = max(0.0, rounds - float(round_cap)) if round_cap is not None else 0.0
        seed = _row_seed(item)
        key = (_row_seed(item) if seed is not None else -1, index)
        if item.get("three_star"):
            # Fast wins are the cleanest contrast: fewer rounds, then seed order.
            wins.append(((rounds, key), item, missing, trace))
        else:
            # "Close" = fewest unmet objectives, then fewest rounds over the cap.
            failures.append(((len(missing), over_cap, rounds, key), item, missing, trace))

    picks: list[ReplaySample] = []
    if failures:
        _, item, missing, trace = min(failures, key=lambda entry: entry[0])
        picks.append(_replay(item, trace, label="失败", missing=missing))
    if wins and len(picks) < limit:
        _, item, _, trace = min(wins, key=lambda entry: entry[0])
        picks.append(_replay(item, trace, label="三星"))
    return picks[:limit]


def failure_profiles(
    rows: Iterable[dict[str, Any]],
    *,
    objectives_by_stage: dict[str, Sequence[str]] | None = None,
    round_caps_by_stage: dict[str, int] | None = None,
    max_replays: int = 2,
) -> list[StageProfile]:
    """Aggregate per-game rows into one failure profile per stage.

    ``max_replays`` is an upper bound (clamped to 2): at most one loss and one
    3★ win per stage. Rows without a ``trace`` still contribute to the
    statistics; that stage simply gets no replay block.
    """
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
                mean_rounds=sum(_row_rounds(item) for item in items) / n,
                failed_objectives=dict(failed),
                objectives=tuple((objectives_by_stage or {}).get(stage, ())),
                replays=_pick_replays(
                    items,
                    max_replays=max_replays,
                    round_cap=(round_caps_by_stage or {}).get(stage),
                ),
            )
        )
    return profiles


def _slug(text: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return ascii_part or "skill"


def build_prompt(profile: StageProfile, *, rules_note: str = "") -> list[dict[str, str]]:
    parts = [profile.as_text()]
    if profile.replays:
        blocks = ["代表局回放（用于定位失误；不要复述单局操作）："]
        blocks.extend(
            replay.as_text(index) for index, replay in enumerate(profile.replays)
        )
        parts.append("\n\n".join(blocks))
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
    stay cheap (``effort="none"``). Reflection is where quality compounds.
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
