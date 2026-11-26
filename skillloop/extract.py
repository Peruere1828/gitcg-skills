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
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Iterable, Sequence

from common.llm import extract_json_object
from skillloop.prompt_v2 import GAME_RULES_V2, objective_message
from skillloop.schema import Skill, SkillConditions

# The reflection (skill-extraction) system message is now isomorphic to the
# decision prompt: coach role → game rules → stage special rules → card knowledge
# (+ glossary) → win objectives → output contract. The role / output-schema halves
# are kept as separate constants so ``SYSTEM`` stays byte-identical for the legacy
# (``task=None``) path while the new path can interleave the shared v2 blocks.
COACH_ROLE = (
    "你是《七圣召唤》PvE 的策略教练。你接下来写的内容会**原样进入未来对局的系统提示**，"
    "直接改变模型的动作选择。你会看到某个关卡的失败统计、固定牌组（角色/卡牌）、"
    "关卡特殊规则、当前技能库中与本关相关或通用的技能，以及失败局与三星局的代表回放。"
    "请基于这些证据，写出你认为最能让自己下次打对的提示词："
    "每关最多 2 条；每条必须是「触发条件 → 具体动作/优先级」的可执行规则，1–3 句。"
    "可以是指令、判断条件、优先级、具体出手序列、反例、自查清单，任何形式都行；"
    "越具体、越可执行越好。"
    "禁止空泛的「要重视/注意」；不要复述关卡目标，也不要复述回放中的单局操作。"
    "必须结合固定牌组的角色/卡牌和关卡特殊规则来写，可以引用角色/卡牌名。"
    "如果当前技能库中的某条技能错误或有害，写一条清晰可执行的替代，"
    "并把它在库里的 skill_id 填进 replaces（目标必须是库内同层的技能，"
    "stage_specific 还要求同关；不满足时留空），"
    "不要另加一条与它矛盾的新技能；"
    "skill_name 必须唯一、具体。"
    "conditions 只填能从本关匹配的 objective_types/enemies/round_max；"
    "enemies 填敌人英文 id（如 fatuipyroagent），写中文名也会被映射；"
    "objective_types 只用本关真实存在的类型，其他类型会被剔除。"
    "layer 固定填 stage_specific（本关专用）：抽取只写本关技能，跨关 strategy "
    "由人工维护，写了也会被降级。"
    "复盘时必须做四类量化分析并据此写技能："
    "①骰子利用率：对照「本关量化」的可用/支付，指出哪些回合骰子被浪费"
    "（支付数远低于可用数），给出下一局的具体用法；"
    "②手牌利用率：说明打出、调和、留在手里各多少，哪些牌该早打、哪些该留；"
    "③元素反应：我方触发了哪些反应、是否用于提高伤害；没触发时缺哪一步元素附着；"
    "④对方规律：对方最常用的技能与节奏（例如每轮固定先手某技能），"
    "给出针对性应对（留骰、换人、抢结束）。"
    "候选中至少有一条要引用上述量化数字（例如「R1 可用 8 骰只支付 3」）。"
    "回放只用于定位问题，不要编造数据。"
)
OUTPUT_SCHEMA = (
    "只输出 JSON 数组，不要输出 JSON 以外的内容；元素形如："
    '{"skill_name": "...", "description": "（将来原样注入的提示词正文）", '
    '"conditions": {"objective_types": ["win_within_rounds"], "round_max": 5}, '
    '"layer": "stage_specific", '
    '"replaces": ""}'
    "。示例只是格式示意：layer 固定 stage_specific（strategy 会被降级）；"
    "replaces 只在要替换库内同层、同关的 skill_id 时填写，"
    "否则留空字符串。"
)
GAME_RULES_HEADER = "【游戏规则】"
SPECIAL_RULES_HEADER = "【关卡特殊规则】"
OUTPUT_HEADER = "【输出要求】"

#: Objective wording that hides a precondition. ``npc/objectives.py`` scores
#: ``defeated_less_than`` as ``won and defeated < k`` — only a *won* game can
#: satisfy it — while the shared ``prompt_v2`` line ("我方被击倒的角色次数必须小于
#: K") reads as an unconditional check. Extraction is where the model writes rules
#: about that objective, so the judgement is spelled out here (``prompt_v2`` is
#: left byte-identical for the decision prompt).
_OBJECTIVE_CLARIFICATIONS = {
    "defeated_less_than": (
        "defeated_less_than（少被击倒）的判定是「胜利时，我方被击倒的角色次数必须小于 "
        "{k}」：只有赢下的那一局才检查；失败或超时一律不算达成。"
    ),
}


def _objective_k(task: Any, kind: str) -> str:
    """The stage's ``k`` for ``kind``, or a literal ``K`` when it is not configured."""
    for detail in getattr(task, "objective_details", ()) or ():
        if str(getattr(detail, "type", "") or "") != kind:
            continue
        value = getattr(detail, "k", None)
        if value is None:
            value = getattr(detail, "count", None)
        if value is not None:
            try:
                return str(int(value))
            except (TypeError, ValueError):
                break
    return "K"


def objective_clarification(task: Any) -> str:
    """Extra judgement wording for objectives whose line hides a precondition."""
    kinds = [str(kind) for kind in (getattr(task, "objective_types", ()) or ())]
    for detail in getattr(task, "objective_details", ()) or ():
        kinds.append(str(getattr(detail, "type", "") or ""))
    lines = [
        _OBJECTIVE_CLARIFICATIONS[kind].format(k=_objective_k(task, kind))
        for kind in dict.fromkeys(kinds)
        if kind in _OBJECTIVE_CLARIFICATIONS
    ]
    return "\n".join(lines)


def _objective_block(task: Any) -> str:
    """``objective_message`` plus the clarifications of :func:`objective_clarification`."""
    text = objective_message(task)
    extra = objective_clarification(task)
    return f"{text}\n{extra}" if extra else text


#: Legacy single-block prompt (``build_prompt(profile)`` without a task).
SYSTEM = COACH_ROLE + OUTPUT_SCHEMA

# Deck facts / special-rules blocks are rendered into the same statistics line;
# keep them bounded so one verbose deck cannot crowd out the replay contrast.
DECK_FACTS_LIMIT = 360
SPECIAL_RULES_LIMIT = 400

#: Extraction replies are JSON arrays written *after* hidden reasoning, and both
#: share one completion budget. 8192 was routinely eaten entirely by reasoning
#: (``finish_reason="length"``, empty/partial content, 0 candidates), so the
#: default is 32768 and one truncation retry may double it up to this ceiling.
DEFAULT_MAX_TOKENS = 32768
MAX_TRUNCATION_RETRY_TOKENS = 65536


@lru_cache(maxsize=1)
def _render_assets() -> Any:
    """The renderer's asset catalog, loaded at most once per process.

    Reflection runs offline in tests, so a missing cache returns ``None`` instead
    of triggering a network fetch; the caller then degrades to rules + objectives
    only. Mirrors ``skillloop.replay._catalog``.
    """
    try:
        from agents.scripted.assets import _cache_path, load_assets

        if not _cache_path().exists():
            return None
        return load_assets()
    except Exception:
        return None


def _knowledge_block(task: Any) -> str:
    """Deck knowledge (our/opponent sheets + deck + glossary) via the decision renderer.

    Same renderer and section header as ``RichStateRenderer.system_parts`` so the
    reflection system prefix uses one source of truth for card facts. Any failure
    (missing assets, unexpected task, unknown ids) yields ``""`` rather than
    breaking extraction.
    """
    assets = _render_assets()
    if assets is None:
        return ""
    try:
        from skillloop.rich_render import RichStateRenderer

        return RichStateRenderer(assets).knowledge_message(task)
    except Exception:
        return ""


def system_parts(
    task: Any, *, special_rules: str = ""
) -> list[tuple[str, str]]:
    """Labelled segments of the new reflection system message, in prompt order.

    Single source for both :func:`build_system` and the rendered sample report, so
    a report can never claim a layout the prompt does not have. ``task=None``
    keeps the rules / special-rules / output parts and skips card knowledge and
    objectives.
    """
    parts: list[tuple[str, str]] = [
        ("教练角色", COACH_ROLE),
        ("游戏规则（GAME_RULES_V2）", f"{GAME_RULES_HEADER}\n{GAME_RULES_V2}"),
    ]
    rules = " ".join(str(special_rules or "").split())
    if rules:
        parts.append(("关卡特殊规则", f"{SPECIAL_RULES_HEADER}\n{rules}"))
    if task is not None:
        knowledge = _knowledge_block(task)
        if knowledge:
            parts.append(("卡组信息 + 名词解释", knowledge))
        parts.append(("获胜目标 + 目标判定", _objective_block(task)))
    parts.append(("输出要求", f"{OUTPUT_HEADER}\n{OUTPUT_SCHEMA}"))
    return parts


def build_system(task: Any, *, special_rules: str = "") -> str:
    """The reflection system message, structured like the decision prompt.

    Order: coach role → game rules (``GAME_RULES_V2``, same constant as the
    decision prompt) → stage special rules (omitted when empty) → card knowledge
    + glossary → objectives → output schema.
    """
    return "\n".join(text for _name, text in system_parts(task, special_rules=special_rules))


def _compact(text: str, limit: int) -> str:
    """Flatten whitespace and truncate a prompt-bound fact block."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


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
class StageMetrics:
    """Quantitative evidence parsed from a stage's compact replay traces.

    These are the four things the coach is asked to reason about (dice, hand,
    reactions, opponent habits), aggregated over every game of the stage that
    was recorded with a ``trace``. Every counter is a plain int / dict so the
    dataclass round-trips through JSON alongside :class:`StageProfile`.
    """

    games: int = 0
    #: Sum over rounds of *our* dice pool after the reroll (the last
    #: ``我方骰子`` line of the round, or the reroll's ``→ [...]`` pool).
    dice_available: int = 0
    #: Elements in every ``自动支付[...]`` on a P0 action line (dice actually spent).
    dice_spent: int = 0
    #: P0 actions whose label is ``调和`` (elemental tuning).
    tuning: int = 0
    #: P0 ``打牌`` actions (tuning excluded; it is its own kind).
    cards_played: int = 0
    #: Card names after P0 ``抽牌：`` (opening hand included).
    cards_drawn: int = 0
    #: Reactions triggered by P0, name -> occurrences.
    reactions: dict[str, int] = field(default_factory=dict)
    #: NPC skills used, name (without the effect text) -> occurrences, top 5.
    opponent_skills: dict[str, int] = field(default_factory=dict)

    def as_text(self) -> str:
        """One prompt line, or ``""`` when no trace produced any evidence."""
        parts: list[str] = []
        if self.dice_available > 0:
            percent = int(self.dice_spent * 100 / self.dice_available + 0.5)
            parts.append(
                f"骰子利用率 {percent}%"
                f"（可用 {self.dice_available} / 支付 {self.dice_spent}）"
            )
        if self.tuning:
            parts.append(f"调和 {self.tuning} 次")
        if self.cards_played:
            parts.append(f"出牌 {self.cards_played} 张")
        if self.cards_drawn:
            parts.append(f"抽牌 {self.cards_drawn} 张")
        if self.reactions:
            items = sorted(self.reactions.items(), key=lambda kv: (-kv[1], kv[0]))
            parts.append("我方触发的反应：" + "、".join(f"{name} {count}" for name, count in items))
        if self.opponent_skills:
            items = sorted(
                self.opponent_skills.items(), key=lambda kv: (-kv[1], kv[0])
            )
            parts.append("对方最常用：" + "、".join(f"{name} {count}" for name, count in items))
        if not parts:
            return ""
        return f"本关量化（{self.games} 局）：" + "；".join(parts) + "。"


@dataclass
class StageProfile:
    stage: str
    n: int
    three_star_rate: float
    mean_rounds: float
    failed_objectives: dict[str, int]
    objectives: tuple[str, ...] = ()
    replays: list[ReplaySample] = field(default_factory=list)
    # Facts about the fixed contestant deck for this stage (rendered by the
    # caller that has the deck registry; extraction itself stays offline).
    deck: str = ""
    deck_facts: str = ""
    # Human-readable stage special rules (initial supports / action-phase
    # effects), omitted from the prompt when empty.
    special_rules: str = ""
    # Already rendered lines ``[id] name: description`` for the relevant learned
    # skills in the base library (rules are the fixed system text, never extracted).
    existing_skills: tuple[str, ...] = ()
    # Quantitative evidence parsed from the stage's replay traces. ``None`` for
    # profiles built by hand or from rows without traces, so the prompt is
    # byte-identical to the pre-metrics layout in that case.
    metrics: StageMetrics | None = None

    def as_text(self, *, include_context: bool = True) -> str:
        """Statistics line; ``include_context=False`` drops deck/special-rule facts.

        The legacy prompt kept the deck and special rules on this line. The new
        (``task``) prompt carries both in the system message instead, so the user
        block asks for ``include_context=False`` to avoid repeating them.

        The objective texts are **not** repeated here either: the system message
        already renders ``task.describe()`` plus the per-objective judgement rules
        (H11), and a third copy on the user line only crowded out evidence.
        """
        failed = "，".join(f"{key} 失败 {count}/{self.n}" for key, count in
                           sorted(self.failed_objectives.items(), key=lambda kv: -kv[1]))
        text = (
            f"关卡 {self.stage}：三星率 {self.three_star_rate:.2f}（n={self.n}），"
            f"平均回合 {self.mean_rounds:.1f}；未达成目标：{failed or '无'}"
        )
        if not include_context:
            return text
        if self.deck or self.deck_facts:
            deck_line = f"固定牌组：{self.deck or '（未知）'}"
            if self.deck_facts:
                deck_line += f"；角色/卡牌：{_compact(self.deck_facts, DECK_FACTS_LIMIT)}"
            text += "；" + deck_line
        if self.special_rules:
            text += "；关卡特殊规则：" + _compact(self.special_rules, SPECIAL_RULES_LIMIT)
        return text


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


# -- quantitative metrics from a compact trace --------------------------------
#: ``R<n>`` prefix; only these lines are counted, so scoreboard / truncation
#: notes pass through untouched.
_ROUND_LINE = re.compile(r"^R(\d+)\s")
_MY_DICE = re.compile(r"^R\d+\s+我方骰子：(?P<body>.*)$")
_DICE_COUNT = re.compile(r"（(\d+)个）")
_REROLL_ARROW = re.compile(r"^R\d+\s+P0\s+重掷\b.*?→\s*\[(?P<pool>[^\]]*)\]")
_P0_LINE = re.compile(r"^R\d+\s+P0(?:\s|$)")
_AUTOPAY = re.compile(r"自动支付\[([^\]]*)\]")
_P0_TUNING = re.compile(r"^R\d+\s+P0\s+调和(?:\s|$)")
_P0_PLAY = re.compile(r"^R\d+\s+P0\s+打牌(?:\s|$)")
_P0_DRAW = re.compile(r"^R\d+\s+P0\s+抽牌：(?P<body>.*)$")
_CARD_NAME = re.compile(r"「([^」]*)」")
_REACTION = re.compile(
    r"^R\d+\s+反应\s+(?P<name>\S+)(?:\s+(?P<side>P0|P1\(NPC\)))?(?:\s+x(?P<count>\d+))?$"
)
_OPPONENT_SKILL = re.compile(r"^R\d+\s+P1\(NPC\)\s+技能\s+(?P<name>.+)$")


def _element_count(pool: str) -> int:
    """Comma-separated element symbols -> count (``""`` -> 0)."""
    return len([part for part in str(pool).split(",") if part.strip()])


def _opponent_skill_name(raw: str) -> str:
    """Skill name without the ``（...）`` effect text or the paid-dice suffix."""
    name = str(raw).split("（", 1)[0]
    name = name.split(" 自动支付", 1)[0]
    return name.strip()


def parse_metrics(traces: Iterable[Sequence[str]]) -> StageMetrics:
    """Aggregate dice/hand/reaction/opponent evidence from compact traces.

    ``traces`` is one sequence of rendered lines per game (the ``trace`` field
    of a JSONL row). Any line that does not match a known shape is skipped
    silently: old runs predate several of these rows and must not raise.

    Counting rules:

    * ``dice_available``: for each ``R<n> 我方骰子：[...]（N个）`` line keep ``N``
      for that round; a later ``R<n> P0 重掷 ... → [...]`` replaces it with the
      size of the post-reroll pool. Rounds are then summed. The last update of a
      round wins, so a repeated snapshot never double-counts.
    * ``dice_spent``: summed element counts of every ``自动支付[...]`` on a P0
      line (tuning and card plays included: they cost dice too).
    * ``reactions``: only ``R<n> 反应 <name> P0`` (the contestant's triggers).
      The trace coalesces consecutive duplicates as `` xK``; ``K`` is honoured
      so the count is occurrences, not rendered lines.
    * ``opponent_skills``: ``R<n> P1(NPC) 技能 <name>`` grouped by name, top 5.
    """
    games = 0
    dice_available = 0
    dice_spent = 0
    tuning = 0
    cards_played = 0
    cards_drawn = 0
    reactions: Counter[str] = Counter()
    opponent: Counter[str] = Counter()

    for lines in traces:
        games += 1
        round_dice: dict[int, int] = {}
        for line in lines:
            text = str(line).strip()
            match = _ROUND_LINE.match(text)
            if match is None:
                continue
            round_number = int(match.group(1))

            dice = _MY_DICE.match(text)
            if dice:
                body = dice.group("body")
                counted = _DICE_COUNT.search(body)
                round_dice[round_number] = int(counted.group(1)) if counted else 0
                continue

            reroll = _REROLL_ARROW.match(text)
            if reroll:
                round_dice[round_number] = _element_count(reroll.group("pool"))
                continue

            reaction = _REACTION.match(text)
            if reaction and reaction.group("side") == "P0":
                count = int(reaction.group("count") or 1)
                reactions[reaction.group("name")] += count
                continue

            opponent_skill = _OPPONENT_SKILL.match(text)
            if opponent_skill:
                name = _opponent_skill_name(opponent_skill.group("name"))
                if name:
                    opponent[name] += 1
                continue

            if not _P0_LINE.match(text):
                continue
            for pool in _AUTOPAY.findall(text):
                dice_spent += _element_count(pool)
            if _P0_TUNING.match(text):
                tuning += 1
            if _P0_PLAY.match(text):
                cards_played += 1
            draw = _P0_DRAW.match(text)
            if draw:
                cards_drawn += len(_CARD_NAME.findall(draw.group("body")))

        dice_available += sum(round_dice.values())

    top_skills = dict(
        sorted(opponent.items(), key=lambda item: (-item[1], item[0]))[:5]
    )
    return StageMetrics(
        games=games,
        dice_available=dice_available,
        dice_spent=dice_spent,
        tuning=tuning,
        cards_played=cards_played,
        cards_drawn=cards_drawn,
        reactions=dict(reactions),
        opponent_skills=top_skills,
    )


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
    failed_counts: dict[str, int] | None = None,
) -> list[ReplaySample]:
    """At most one loss **per failure objective** (dominant first) + one fast 3★.

    B4: ranking losses only by "fewest unmet objectives" picked games that missed
    the *rare* objective (chefmao seed 1 missed only ``defeated_less_than``, 1/5)
    while the dominant failure (``win_within_rounds``, 2/5) got zero replay
    evidence. Losses are now grouped by objective in descending failure count:
    the first slot goes to the best game that actually *misses the most-failed
    objective*, so the replay and the statistics line agree. Within one objective
    the old closeness ranking still applies (fewest unmet objectives, then fewest
    rounds over the cap, then fewest rounds, then seed). Extra objectives get
    their own slot when ``max_replays`` allows it; a single 3★ game is the
    contrast and keeps the last slot whenever one exists.
    """
    limit = min(2, max(0, int(max_replays)))
    if limit == 0:
        return []

    failures: list[
        tuple[dict[str, Any], tuple[str, ...], tuple[str, ...], float, float, tuple[Any, ...]]
    ] = []
    wins: list[tuple[tuple[Any, ...], dict[str, Any], tuple[str, ...]]] = []
    for index, item in enumerate(items):
        trace = _row_trace(item)
        if trace is None:
            continue
        missing = _missing_objectives(item)
        rounds = _row_rounds(item)
        over_cap = max(0.0, rounds - float(round_cap)) if round_cap is not None else 0.0
        seed = _row_seed(item)
        key = (seed if seed is not None else -1, index)
        if item.get("three_star"):
            # Fast wins are the cleanest contrast: fewer rounds, then seed order.
            wins.append(((rounds, key), item, trace))
        else:
            failures.append((item, missing, trace, rounds, over_cap, key))

    # Objective priority: the most-failed objective first; ties by name so the
    # same rows always produce the same replay and prompt.
    priority = [
        str(name)
        for name, _count in sorted(
            (failed_counts or {}).items(), key=lambda item: (-int(item[1]), str(item[0]))
        )
    ]
    failure_slots = max(0, limit - (1 if wins else 0))
    used: set[int] = set()

    def best_loss(objective: str | None) -> int | None:
        """Index of the closest loss missing ``objective`` (any loss if ``None``)."""
        ranked = [
            (len(missing), over_cap, rounds, key, position)
            for position, (item, missing, _trace, rounds, over_cap, key) in enumerate(failures)
            if position not in used and (objective is None or objective in missing)
        ]
        return min(ranked)[-1] if ranked else None

    picks: list[ReplaySample] = []
    for objective in priority:
        if len(picks) >= failure_slots:
            break
        position = best_loss(objective)
        if position is None:
            continue
        used.add(position)
        item, missing, trace, *_ = failures[position]
        picks.append(_replay(item, trace, label="失败", missing=missing))
    while len(picks) < failure_slots:
        position = best_loss(None)
        if position is None:
            break
        used.add(position)
        item, missing, trace, *_ = failures[position]
        picks.append(_replay(item, trace, label="失败", missing=missing))
    if wins and len(picks) < limit:
        _, item, trace = min(wins, key=lambda entry: entry[0])
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

    ``max_replays`` is an upper bound (clamped to 2): one loss *for the stage's
    most-failed objective* (then one 3★ win) per stage. Rows without a ``trace``
    still contribute to the statistics; that stage simply gets no replay block.
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
                    failed_counts=dict(failed),
                ),
                metrics=parse_metrics(
                    trace
                    for item in items
                    if (trace := _row_trace(item)) is not None
                ),
            )
        )
    return profiles


def _slug(text: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return ascii_part or "skill"


#: Extraction contract: at most two injected prompts per stage (H14). ``COACH_ROLE``
#: states it; the parser enforces it so an over-eager reply cannot crowd the
#: decision prompt with near-duplicates.
MAX_SKILLS_PER_STAGE = 2

_EFFECTIVE_LAYERS = ("strategy", "stage_specific")


def build_prompt(
    profile: StageProfile,
    *,
    task: Any = None,
    special_rules: str = "",
) -> list[dict[str, str]]:
    """Build the reflection prompt.

    With ``task`` the system message is the full decision-shaped prompt (rules,
    stage special rules, card knowledge, objectives, output schema) and the user
    message carries only the three non-duplicated blocks: statistics, replays and
    relevant existing skills. The always-on rules therefore no longer appear as a
    user-side ``已知规则`` block, and the statistics line drops the deck/special
    rules that moved into the system message.

    Without ``task`` the historical prompt is reproduced exactly (single big
    ``SYSTEM`` constant, deck/special-rule facts on the statistics line), so old
    rows/tests keep working.
    """
    if task is None:
        parts = [profile.as_text()]
        system = SYSTEM
    else:
        # ``special_rules`` is explicit in the new path; fall back to the value
        # ``enrich_profiles`` attached to the profile when the caller omits it.
        parts = [profile.as_text(include_context=False)]
        system = build_system(task, special_rules=special_rules or profile.special_rules)
    # Quantitative evidence sits between the statistics line and the replays:
    # the coach reads *how much* went wrong before seeing *where*.
    if profile.metrics is not None:
        metrics_text = profile.metrics.as_text()
        if metrics_text:
            parts.append(metrics_text)
    if profile.replays:
        blocks = [
            "代表局回放（用于定位失误；不要复述单局操作）：",
            # H10: the replay mixes engine mechanics with LLM/script decisions;
            # say which is which up front (see replay.py). The first sentence is
            # the historical wording kept for older prompts; the correction names
            # the sync runner's actual behaviour.
            "说明：重掷由脚本策略执行（只留万能与我方角色元素）；换牌/选人为 LLM 决策"
            "（驻留 runner 中三者均为脚本）；自动支付为引擎选骰；其余 P0 行为为 LLM 决策。",
        ]
        blocks.extend(
            replay.as_text(index) for index, replay in enumerate(profile.replays)
        )
        parts.append("\n\n".join(blocks))
    if profile.existing_skills:
        lines = ["当前技能库中与本关相关/通用的技能（可批判、可替换；不要重复）："]
        lines.extend(f"- {entry}" for entry in profile.existing_skills)
        parts.append("\n".join(lines))
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


@lru_cache(maxsize=None)
def _stage_vocabulary(stage: str) -> tuple[frozenset[str], dict[str, str]]:
    """``(objective types, enemy name -> id)`` for ``stage``.

    H12: the output schema asks for real values, and a Chinese enemy name (which
    models write naturally, and which older libraries stored) is mapped to the
    engine id the retrieval filters compare against. Loaded from ``configs/npc``
    through ``npc.spec``; any failure returns empty values so extraction still
    works in an environment without the stage configs.
    """
    try:
        from npc.spec import get_stage, load_enemies
    except Exception:
        return frozenset(), {}
    try:
        spec = get_stage(str(stage))
        types = frozenset(str(objective.type) for objective in spec.objectives)
        lineup_ids = {str(entry.id) for entry in (*spec.lineup, *spec.reserve)}
    except Exception:
        return frozenset(), {}
    names: dict[str, str] = {}
    try:
        for enemy in load_enemies().values():
            enemy_id = str(enemy.id)
            if lineup_ids and enemy_id not in lineup_ids:
                continue
            names[str(enemy.name)] = enemy_id
            if enemy.en:
                names[str(enemy.en).lower()] = enemy_id
    except Exception:
        return types, {}
    return types, names


def _conditions_from_payload(
    payload: dict[str, Any],
    *,
    stage: str,
    objective_types: Sequence[str] | None,
    enemy_names: dict[str, str] | None,
) -> SkillConditions:
    """Parse ``conditions`` with the stage's vocabulary applied (H12).

    Unknown keys stay ignored (``SkillConditions.from_dict`` only reads its own);
    ``objective_types`` entries the stage does not have are dropped, and enemy
    names resolve to engine ids. Everything else is passed through unchanged.
    """
    stage_types, stage_names = _stage_vocabulary(stage)
    allowed = frozenset(objective_types) if objective_types is not None else stage_types
    names = stage_names if enemy_names is None else dict(enemy_names)

    raw_types = payload.get("objective_types") or ()
    if isinstance(raw_types, (str, bytes)):
        raw_types = (raw_types,)
    kept: list[str] = []
    dropped: list[str] = []
    for value in raw_types:
        text = str(value).strip()
        if not text:
            continue
        if allowed and text not in allowed:
            dropped.append(text)
        else:
            kept.append(text)
    if dropped:
        print(
            f"[extract] {stage}: dropping objective_types {dropped} "
            f"(not on this stage: {sorted(allowed)})",
            file=sys.stderr,
        )

    raw_enemies = payload.get("enemies") or ()
    if isinstance(raw_enemies, (str, bytes)):
        raw_enemies = (raw_enemies,)
    enemies: list[str] = []
    for value in raw_enemies:
        text = str(value).strip()
        if not text:
            continue
        enemies.append(names.get(text) or names.get(text.lower()) or text)

    merged = dict(payload)
    merged["objective_types"] = kept
    merged["enemies"] = enemies
    return SkillConditions.from_dict(merged)


def parse_skills(
    raw: str | None,
    *,
    stage: str,
    existing_ids: Sequence[str] = (),
    objective_types: Sequence[str] | None = None,
    enemy_names: dict[str, str] | None = None,
) -> list[Skill]:
    """Parse the model's JSON array into candidate skills (invalid entries dropped).

    Entries without a usable ``skill_name``/``description`` are dropped loudly
    rather than repaired (H13). A missing or unknown ``layer`` no longer silently
    becomes ``strategy`` — that produced cross-stage skills nobody meant to write;
    it now degrades to ``stage_specific`` (pinned to this stage) with a stderr
    note. A declared ``strategy`` layer is treated the same way: extraction only
    sees one stage's failures, so its advice cannot be a global rule (v10 rewrote
    ``strat_burst_timing`` with ``blanche``/``irontonguetian``/``liusu`` text and
    polluted every stage). ``replaces`` is only kept when it names a skill in the current library
    (B6), so a hallucinated id cannot make the writer touch the wrong entry. At
    most :data:`MAX_SKILLS_PER_STAGE` candidates survive (H14).

    Two candidates in one reply may share ``skill_name`` (and therefore the
    slug-based id) while targeting different failures (e.g. ``win_within_rounds``
    vs ``defeated_less_than``). Only an *identical* name+description is a
    duplicate; a same-id / different-description entry gets ``_2``/``_3``
    appended instead of being dropped, with a stderr note (B7).
    """
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

    known_ids = {str(skill_id) for skill_id in existing_ids}
    out: list[Skill] = []
    #: ``skill_id -> (skill_name, description)`` for this reply. A repeated pair
    #: is a true duplicate; a shared id with *different* advice gets a ``_N``
    #: suffix so both failures keep a candidate (B7).
    seen: dict[str, tuple[str, str]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("skill_name") or "").strip()
        desc = str(item.get("description") or "").strip()
        if not name or not desc:
            continue
        payload_conditions = item.get("conditions")
        if not isinstance(payload_conditions, dict):
            payload_conditions = {}
        conditions = _conditions_from_payload(
            payload_conditions,
            stage=stage,
            objective_types=objective_types,
            enemy_names=enemy_names,
        )
        layer = str(item.get("layer") or "").strip()
        if layer not in _EFFECTIVE_LAYERS:
            # A missing/typo'd layer must never become a cross-stage ``strategy``
            # rule: that leaked one stage's advice into every stage. Candidates are
            # generated for this stage, so fall back to ``stage_specific`` (which
            # pins ``conditions.stages`` below) and say so on stderr.
            print(
                f"[extract] {stage}: {name!r}: layer missing/invalid "
                f"({layer!r} -> stage_specific)",
                file=sys.stderr,
            )
            layer = "stage_specific"
        if layer == "strategy":
            # Global strategy rules are maintained by hand (and have no stage pin,
            # so they are injected into every stage). Extraction only sees one
            # stage's failures; a "strategy" label here is one stage's lesson, not
            # a cross-stage rule. Downgrade and pin instead of trusting the label.
            print(
                f"[extract] {stage}: {name!r}: layer strategy is human-maintained; "
                "downgrading to stage_specific pinned to this stage",
                file=sys.stderr,
            )
            layer = "stage_specific"
        replaces = str(item.get("replaces") or "").strip()
        if replaces and replaces not in known_ids:
            print(
                f"[extract] {stage}: {name!r} replaces unknown skill "
                f"{replaces!r}; ignoring that field",
                file=sys.stderr,
            )
            replaces = ""
        if layer == "stage_specific":
            # A stage-specific skill must never leak into another stage, even if
            # the model wrote other/empty ``stages``. Keep the retrieval-relevant
            # objective/enemy/round conditions it did provide.
            conditions = replace(conditions, stages=(stage,))
        base_id = f"{_slug(name)}_{stage}"
        skill_id = base_id
        previous = seen.get(base_id)
        if previous == (name, desc):
            continue  # the same rule twice: one candidate, not a numbered twin
        if previous is not None:
            suffix = 2
            while f"{base_id}_{suffix}" in seen:
                suffix += 1
            skill_id = f"{base_id}_{suffix}"
            print(
                f"[extract] {stage}: duplicate id {base_id!r} from {name!r} with a "
                f"different description -> renamed to {skill_id!r}",
                file=sys.stderr,
            )
        seen[skill_id] = (name, desc)
        out.append(
            Skill(
                skill_id=skill_id,
                skill_name=name,
                description=desc,
                conditions=conditions,
                layer=layer,
                status="candidate",
                source_games=(f"{stage}:profile",),
                replaces=replaces,
            )
        )
    if len(out) > MAX_SKILLS_PER_STAGE:
        print(
            f"[extract] {stage}: reply has {len(out)} candidates; keeping the first "
            f"{MAX_SKILLS_PER_STAGE} and dropping {len(out) - MAX_SKILLS_PER_STAGE}",
            file=sys.stderr,
        )
        out = out[:MAX_SKILLS_PER_STAGE]
    return out


def propose_candidates(
    client: Any,
    profiles: Sequence[StageProfile],
    *,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = "high",
    min_failure_rate: float = 0.15,
    tasks: dict[str, Any] | None = None,
    special_rules_by_stage: dict[str, str] | None = None,
    existing_ids: Sequence[str] = (),
) -> list[Skill]:
    """One LLM call per stage that actually loses stars; returns candidate skills.

    Extraction runs **once per evolution round**, not per decision, so it gets the
    expensive setting (``effort="high"``, 24k output budget) while in-game decisions
    stay cheap (``effort="none"``). Reflection is where quality compounds.

    Hidden reasoning and the JSON body share that budget: when a call comes back
    with ``finish_reason == "length"`` (``LLMClient`` exposes it as
    ``last_finish_reason``), the same messages are retried **once** with the
    budget doubled (capped at :data:`MAX_TRUNCATION_RETRY_TOKENS`). A retry that
    is still truncated is reported on stderr and the stage is skipped, so a
    truncated reply is never silently read as "0 candidates".

    ``tasks`` / ``special_rules_by_stage`` inject the same per-stage
    :class:`~skillloop.render.TaskSpec` and special-rules text the decision prompt
    uses. When no task is available for a stage the call falls back to the legacy
    prompt rather than half-render a new system message.

    ``existing_ids`` are the current library's ids; a candidate whose ``replaces``
    names one of them is accepted as an in-place rewrite (B6), anything else is
    ignored by :func:`parse_skills`.
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
        task = (tasks or {}).get(profile.stage)
        special_rules = (
            (special_rules_by_stage or {}).get(profile.stage, "")
            if task is not None
            else ""
        )
        messages = build_prompt(
            profile,
            task=task,
            special_rules=special_rules,
        )
        budget = int(max_tokens)
        raw = client.chat(
            messages,
            max_tokens=budget,
            temperature=0.0,
            model=model,
            reasoning_effort=effort,
        )
        finish_reason = getattr(client, "last_finish_reason", None)
        if finish_reason == "length":
            retry_budget = min(budget * 2, MAX_TRUNCATION_RETRY_TOKENS)
            print(
                f"[extract] {profile.stage}: finish_reason=length at "
                f"max_tokens={budget}; retrying once at max_tokens={retry_budget}",
                file=sys.stderr,
            )
            raw = client.chat(
                messages,
                max_tokens=retry_budget,
                temperature=0.0,
                model=model,
                reasoning_effort=effort,
            )
            finish_reason = getattr(client, "last_finish_reason", None)
            if finish_reason == "length" or not str(raw or "").strip():
                print(
                    f"[extract] {profile.stage}: still truncated after retry "
                    f"(finish_reason={finish_reason}, max_tokens={retry_budget}); "
                    "skipping this stage",
                    file=sys.stderr,
                )
                continue
        parsed = parse_skills(
            raw, stage=profile.stage, existing_ids=existing_ids
        )
        if not parsed and str(raw or "").strip():
            print(
                f"[extract] {profile.stage}: reply parsed to 0 candidates "
                f"({len(str(raw))} chars); check the JSON output contract",
                file=sys.stderr,
            )
        candidates.extend(parsed)
    # Safety net only: ids are already unique per stage (``_2``/``_3`` on a
    # same-name collision), so this can only fire if a caller hand-built
    # candidates. Report it instead of dropping silently (B7).
    seen: set[str] = set()
    unique: list[Skill] = []
    for skill in candidates:
        if skill.skill_id in seen:
            print(
                f"[extract] duplicate candidate id {skill.skill_id!r} across stages; "
                "keeping the first",
                file=sys.stderr,
            )
            continue
        seen.add(skill.skill_id)
        unique.append(skill)
    return unique
