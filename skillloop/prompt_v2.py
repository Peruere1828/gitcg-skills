"""Prompt **v2** content blocks: fixed game rules, objective explanations, glossary.

The v2 system message has a fixed six-part order (see ``skillloop.rich_render``)::

    1. SYSTEM_ROLE            (skillloop.render, unchanged)
    2. GAME_RULES_V2          (this module; replaces rules_text() + _RULES_33)
    3. card knowledge         (our/opponent characters + our deck + glossary)
    4. objectives             (task.describe() + per-type explanation)
    5. learned skills         (unchanged block)
    6. OUTPUT_CONTRACT        (skillloop.render, unchanged)

Why a constant instead of the library's rule skills: the 17 ``rule_*`` skills stay in
``data/skills/*`` because the gate and historical runs need them, but a v2 prompt
reads one authoritative rules text. Two rule sources in one prompt (17 skill lines
plus ``_RULES_33``) repeated themselves and drifted apart on edits.

The glossary exists because character skills and card effects name their own states
(「鼓舞领域」「潜行」「流焰磨刃」) without explaining them. Terms and explanations are
copied verbatim from the runtime asset dump; nothing here is hand-written.

INNOVATION: prompt v2 = one canonical rules text + asset-derived glossary, so the
cached system prefix explains every state name it mentions.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# Bumped whenever the prompt structure or the wikitext-derived blocks change, so a
# run's meta can be attributed to the prompt generation it was produced with.
# 2026-11-25: the decision user message gained the 对局历史 segment.
PROMPT_VERSION = "2026-11-25"

# -- section headers (one source for the v2 layout) -------------------------
CARD_INFO_HEADER = "当前对局的卡组信息："
OBJECTIVE_HEADER = "当前对局的获胜目标："
SKILLS_HEADER = "可参考的技能（来自历史经验，优先级低于关卡目标）："
STAGE_RULES_HEADER = "【关卡特殊规则】"
# Fixed per-game note (so it lives in the cached system prefix): the candidate's
# 「预计」 comes from the engine's settlement, which already includes element
# infusion, equipment, shields and reactions, while a skill/card description only
# knows its base numbers. Stating the precedence once beats repeating a caveat on
# every candidate line.
PREVIEW_PRECEDENCE_NOTE = (
    "候选行的「预计」是引擎结算预测（已计附魔/装备/护盾/元素反应），"
    "与技能/卡牌描述里的基础数值冲突时，以「预计」为准。"
)
STAGE_RULES_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "npc" / "special_rules.json"
)


def stage_special_rules_text(stage_name: str) -> str:
    """Human-readable special rules for ``stage_name`` (``""`` when none).

    Delegates to ``scripts.extract_skills.stage_special_rules_text`` so the
    decision prompt and the reflection prompt quote the exact same wording. The
    import is lazy: ``scripts`` depends on ``skillloop``, so a module-level import
    would invert the layering. Any failure degrades to the raw ``rule_text`` field
    of ``configs/npc/special_rules.json`` and never raises.
    """
    stage = str(stage_name or "").strip()
    if not stage:
        return ""
    try:
        from scripts.extract_skills import stage_special_rules_text as _rules_text

        text = str(_rules_text(stage) or "").strip()
        if text:
            return text
    except Exception:
        pass
    try:
        payload = json.loads(STAGE_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for entry in payload.get("stages", ()):
        if not isinstance(entry, dict) or str(entry.get("stage") or "") != stage:
            continue
        raw = str(entry.get("rule_text") or "").strip()
        return "" if raw in ("", "无", "无。") else raw
    return ""


# -- segment 2: the game rules ---------------------------------------------
# Verbatim from the official rule page (wiki), in the page's own section order, so
# the model reads the rules the way a player does. Only edit allowed: the wiki
# repeats 「扩散」/「结晶」 four times (once per element); they are merged into one
# line each. No card-pool version label is carried: the pool is pinned per run
# (``configs/engine.lock``), so a "3.3"-style tag in the text would silently rot.
GAME_RULES_V2 = """【游戏准备】
- 双方牌手各抓5张起始手牌。然后，双方各有一次机会替换任意数量的手牌。
- 双方各自选择一张自己的「角色牌」作为「出战角色」。

【对局流程】
每个回合都会按照以下顺序进行：
- （1）「投掷阶段」双方重新获得8个元素骰子，进行投掷。
- （2）「行动阶段」双方轮流进行行动，直到双方都宣布回合结束。
- （3）「结束阶段」双方各抓2张牌。

【投掷阶段】
- 每回合的投掷阶段中，会投掷8个正八面体「元素骰」。
- 投掷完成后，还有1次机会选择任意数量元素骰进行「重投」。
- 每个「元素骰」都可以投出冰、水、火、雷、风、岩、草7种元素，以及可以当作任何元素使用的「万能元素」。
- 「元素骰」的数量上限为16个，达到此上限后将无法获得更多「元素骰」。

【行动阶段】
- 从「先手牌手」开始，双方「轮流进行行动」。可以进行的行动有：
- 【使用技能】支付所需费用，使用「出战角色」的技能。
- 【切换角色】支付1个任意元素骰，切换「出战角色」。
- 【打出手牌】支付所需费用，打出手牌。
- 【元素调和】丢弃一张手牌，转换一个元素骰的类型。
- 【宣布结束】结束本回合的行动。每回合先宣布回合结束的牌手成为「先手牌手」。

【结束阶段】
- 在行动阶段中所有牌手都「宣布回合结束」后，进入「结束阶段」。
- 1、从「先手牌手」开始，处理在「结束阶段」触发的卡牌效果。
- 2、每位牌手从自己的牌堆中抓2张牌。
- 3、开始新的回合！

【行动：战斗行动与快速行动】
- 【战斗行动】完成一个战斗行动后，就会轮到对方行动。「使用技能」「切换角色」「宣布结束」是战斗行动。
- 【快速行动】完成一个快速行动后，我方可以继续行动。「打出手牌」和「元素调和」是快速行动。

【战斗行动·使用技能】
- 用元素骰支付所需的费用，使我方「出战角色」使用技能攻击对方的「出战角色」。
- 角色每次使用「普通攻击」或「元素战技」后，会获得1点「充能」。
- 角色「充能」达到上限后，可以花费所有「充能」来使用威力强大的「元素爆发」！

【战斗行动·切换角色】
- 支付1个任意类型的元素骰，将一个我方的「后台角色」切换为「出战角色」。

【快速行动·打出手牌】
- 用元素骰支付所需的费用，打出一张手牌。手牌分为以下类型：
- 【装备牌】装备在一个角色身上，提供效果强化这个角色。
- 【支援牌】放置在我方支援区，持续地发挥支援效果。
- 【事件牌】打出后，一次性地结算所有效果。

【快速行动·元素调和】
- 丢弃一张手牌，将一个元素骰转换成我方当前「出战角色」的元素类型。

【战斗行动·宣布结束】
- 放弃本回合的行动，「宣布回合结束」。
- 先「宣布回合结束」的牌手会成为「先手牌手」，并在下回合的行动阶段优先行动。
- 这之后，未「宣布回合结束」的牌手可以继续行动，直到「宣布回合结束」。
- 双方都「宣布回合结束」后，行动阶段结束，进入本回合的「结束阶段」。

【游戏区域·角色区】
- 游戏开始后，牌组中所有「角色牌」都会被放置到角色区。
- （1）角色牌的左上角会显示角色「生命值」。「生命值」会在角色受到伤害后扣减，角色的「生命值」为0后将被「击倒」。
- （2）角色牌的右边会显示角色「充能」。角色「充能」达到上限后，就能使用威力强大的「元素爆发」！
- （3）每位牌手都会有一名「出战角色」。只有「出战角色」才能使用技能，同时只有「出战角色」会受到对方的技能攻击。成为「出战角色」的角色牌，会通过向前摆放一段距离来表示。
- （4）其他未出战的角色被称为「后台角色」。可以通过「切换角色」行动来将他们切换为「出战角色」。
- （5）如果「出战角色」被击倒，牌手就需要选择一个「后台角色」成为新的「出战角色」。

【游戏区域·角色附属】
- 每张「角色牌」，都可以附属「装备牌」「状态」「出战状态」。
- （1）「装备牌」打出给角色后，会附属在角色牌上。每个角色最多装备1张「武器」、1张「圣遗物」，和任意张其他类型的装备。
- （2）一些角色技能会生成「状态」，并附属在角色牌上。
- （3）一些角色技能会生成「出战状态」，并附属在出战的角色上。出战角色被切换为后台角色时，「出战状态」会转移到新的出战角色上。

【游戏区域·支援区】
- 「支援牌」打出后，会放置在支援区。
- 支援区最多放置4张「支援牌」。占满后打出新的支援牌时，需要先选择一张支援区内的支援牌弃置。
- （同时结算多个支援牌效果时，会按照支援区的排列顺序依次结算。）

【游戏区域·召唤物区】
- 一些角色技能会在召唤物区生成「召唤物」。
- 召唤物区最多放置4个「召唤物」。占满后，无法生成新的「召唤物」。
- （同时结算多个召唤物效果时，会按照召唤物区的排列顺序依次结算。）

【游戏区域·牌堆】
- 游戏开始前，将牌组中的所有「行动牌」洗混，形成「牌堆」。
- 进行「抓牌」时，会从「牌堆」顶部抓取「行动牌」加入手牌。此时如果「牌堆」中已经没有卡牌，则牌手不会获得手牌。

【游戏区域·手牌】
- 游戏开始后，从「牌堆」中抓5张牌，作为初始的「手牌」。
- 每个回合结束时，双方各抓2张牌。
- 只有「手牌」中的行动牌可以被打出和用于元素调和。
- 「手牌」上限为10张，达到上限后抓到的牌会被自动弃置。

【元素附着】
- 角色在受到冰、水、火、雷、草的「元素伤害」时，会附着对应类型的元素。（风元素伤害和岩元素伤害，无法进行元素附着。）
- 此外，一些特殊环境/条件也会对角色附着元素。

【元素反应·规则】
对已经具有元素附着的目标：
- （1）造成特定类型的元素伤害时，会引发元素反应。
- （2）以非伤害的形式施加特定类型的元素附着时也会引发元素反应。结算这类元素反应效果时，会忽略所有伤害类效果。

【元素反应·效果】
- 融化：本伤害+2
- 蒸发：本伤害+2
- 超载：本伤害+2；如果目标是「出战角色」，就强制切换到下一个角色（如果超载反应击倒了角色，则不进行选择，强制选择下一个角色作为新的出战角色）。
- 超导：本伤害+1，对目标以外的所有敌方角色造成1点穿透伤害（不会受到其他效果的伤害加成，但是也不会被护盾等效果抵消）。
- 感电：本伤害+1，对目标以外的所有敌方角色造成1点穿透伤害。
- 冻结：本伤害+1，使目标角色附属「冻结」状态：角色无法使用技能（状态持续到回合结束为止）；角色受到火元素伤害或物理伤害时伤害值+2，并移除此状态。
- 扩散：对目标以外的所有敌方角色造成1点冰/水/火/雷元素伤害（四种扩散分别对应附着元素）。
- 结晶：本伤害+1，提供1点护盾，保护我方出战角色（护盾值可叠加，最多2点）。
- 燃烧：本伤害+1，生成「燃烧烈焰」：结束阶段：造成1点火元素伤害。（可用次数1，最多叠加到2）
- 绽放：本伤害+1，生成「草原核」：我方对敌方出战角色造成火元素伤害或雷元素伤害时，伤害值+2。（可用次数1）
- 原激化：本伤害+1，生成「激化领域」：我方对敌方出战角色造成雷元素伤害或草元素伤害时，伤害值+1。（可用次数2）

【其他规则·相同元素】
- 打出需要费用的「行动牌」时，需要支付指定数量的、相同类型的元素骰。
- （万能元素被视为和任何元素相同。）

【其他规则·无色元素】
- 打出需要费用的「行动牌」时，需要支付指定数量的元素骰，不限制元素骰的类型。

【其他规则·击倒】
- 当角色的生命值被降为0，则在技能或效果结算完成后，角色就会被「击倒」。
- 此时，角色身上所附属的装备和状态都会被弃置，充能会被清空。

【其他规则·免于被击倒】
- 一些效果会使角色在生命值被降为0的时候「免于被击倒」，并治疗该角色到一定生命值。
- 此时，角色不视为被「击倒」过。（因此，角色所附属的装备和状态不会被弃置，充能也不会被清空。）

【其他规则·结算顺序】
- 当多个卡牌效果需要在同一个时间点结算时，会按照以下规则决定执行的顺序：
- 1、先结算当前牌手的所有卡牌效果，再结算对方牌手的卡牌效果。
- 2、同属于同一位牌手的卡牌效果，会按照以下顺序进行结算：角色牌、角色装备和状态、阵营出战状态、召唤物、支援牌。
- 3、同属于同一位牌手的同类型卡牌效果，会按照进场的先后顺序进行结算。

【其他规则·转换形态】
- 部分角色可以通过一些效果「转换形态」。转换为其他形态后，角色的名称、元素类型、标签、技能等都有可能发生改变。

【其他规则·对局回合上限】
- 第15轮结束时，如果仍未分出胜负，则双方都会输掉这场对局。

【特殊词条·护盾】
- 一些效果会提供「护盾」，来保护特定角色。
- 「护盾」所保护的角色受到伤害时，将会消耗「护盾值」来抵消伤害。

【特殊词条·可用次数】
- 一些卡牌或状态会具有「可用次数」限制。
- 每次触发这张牌的效果，都会消耗1次「可用次数」。
- 「可用次数」耗尽后，会立刻弃置这张牌。

【特殊词条·持续回合】
- 一些卡牌或状态会具有「持续回合」限制。
- 每个结束阶段，都会使「持续回合」-1。
- 「持续回合」耗尽后，会立刻弃置这张牌。

【特殊词条·穿透伤害】
- 一些效果会造成「穿透伤害」。
- 「穿透伤害」不会受到其他效果的伤害加成，但是也不会被护盾等效果抵消。

【特殊词条·战斗行动（行动牌）】
- 一些「行动牌」会具有「战斗行动」规则。
- 打出具有此规则的手牌，将被视为进行战斗行动，而非通常的快速行动。

【特殊词条·重击】
- 我方行动开始前，如果元素骰总数为偶数，则进行的「普通攻击」将被视为「重击」。

【特殊词条·下落攻击】
- 角色被切换为「出战角色」后，本回合内的下一个战斗行动若为「普通攻击」，则被视为「下落攻击」。"""


# -- segment 4: objective types --------------------------------------------
# ``{n}`` / ``{k}`` are filled from the stage's ``ObjectiveDetail``; a literal N/K
# stays when the stage does not carry the number (never guessed).
_OBJECTIVE_LINES = {
    "win": "赢得胜利——击倒对方全部角色。",
    "win_within_rounds": "回合上限——在第{n}轮结束前取胜，拖过上限即该目标失败。",
    "defeated_less_than": "少被击倒——胜利时，我方被击倒的角色次数必须小于{k}。",
    "use_elemental_burst": "元素爆发——实际释放{n}次元素爆发。",
    "trigger_elemental_reactions": "元素反应——触发{n}次元素反应。",
    "play_event_cards": "事件牌——打出{n}张事件牌。",
    "play_equipment_card": "装备牌——打出{n}张武器或圣遗物牌。",
}


def _first_number(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        try:
            return str(int(value))
        except (TypeError, ValueError):
            continue
    return "N"


def _objective_line(kind: str, detail: Any, task: Any) -> str:
    """One ``- <type>：<explanation>`` line; unknown types fall back to the text."""
    template = _OBJECTIVE_LINES.get(kind)
    if template is None:
        text = str(getattr(detail, "text", "") or "")
        return f"- {kind}：{text}" if kind else ""
    n = _first_number(
        getattr(detail, "n", None) if kind == "win_within_rounds" else None,
        getattr(task, "max_rounds", None) if kind == "win_within_rounds" else None,
        getattr(detail, "count", None),
    )
    k = _first_number(getattr(detail, "k", None))
    return f"- {kind}：" + template.format(n=n, k=k)


def objective_lines(task: Any) -> list[str]:
    """Per-type explanation lines for the types **this stage** actually uses.

    Order follows ``task.objective_types`` (the machine-readable list the runner
    already logs), so the block is stable for a given stage. Duplicate types are
    emitted once; when the stage has no type list the detail types are used.
    """
    details = tuple(getattr(task, "objective_details", ()) or ())
    by_type: dict[str, Any] = {}
    for detail in details:
        by_type.setdefault(str(getattr(detail, "type", "") or ""), detail)
    kinds: list[str] = [
        str(kind) for kind in (getattr(task, "objective_types", ()) or ())
    ]
    if not kinds:
        kinds = list(by_type)
    lines: list[str] = []
    for kind in dict.fromkeys(kinds):
        line = _objective_line(kind, by_type.get(kind), task)
        if line:
            lines.append(line)
    return lines


def objective_message(task: Any) -> str:
    """Segment 4: the human task line plus the per-type win/condition explanation."""
    parts = [OBJECTIVE_HEADER]
    try:
        parts.append(task.describe())
    except Exception:
        pass
    lines = objective_lines(task)
    if lines:
        parts.append("目标判定：")
        parts.extend(lines)
    return "\n".join(parts)


# -- segment 3 appendix: glossary of state / summon names -------------------
GLOSSARY_HEADER = "名词解释（上文出现的状态/出战状态/召唤物）："
# Only entities the engine *shows as an effect* on the board. Equipment/talent cards
# (``GCG_CARD_MODIFY``) are excluded on purpose: the deck block above already prints
# their description, so explaining them again would spend the 12 slots on duplicates.
GLOSSARY_TYPES = frozenset({
    "GCG_CARD_STATE",
    "GCG_CARD_ONSTAGE",
    "GCG_CARD_SUMMON",
    "GCG_CARD_ATTACHMENT",
})
GLOSSARY_TYPE_LABELS = {
    "GCG_CARD_STATE": "状态",
    "GCG_CARD_ONSTAGE": "出战状态",
    "GCG_CARD_SUMMON": "召唤物",
    "GCG_CARD_ATTACHMENT": "附着",
}
# Generic words that some asset entry happens to use as a name. Explaining them adds
# nothing (they are not a concrete status a player can act on), so they never enter
# the glossary.
GLOSSARY_STOPWORDS = frozenset({
    "目标", "效果", "状态", "伤害", "回合", "角色",
    "手牌", "骰子", "元素", "技能", "卡牌", "牌组",
})
GLOSSARY_MAX_TERMS = 12
GLOSSARY_MIN_NAME = 2
# One line per *version* of a term. The asset dump sometimes carries several
# entries under one name (different rule versions, e.g. 潜行「可用次数：2」and
# 「可用次数：3」): the first two **distinct** descriptions are printed, each with
# the card/skill that mentions the term as its source label. Identical descriptions
# still collapse to one line. 200 chars only guards against a pathological entry.
GLOSSARY_MAX_VERSIONS = 2
GLOSSARY_DESC_LIMIT = 200
ASSET_PATH = Path(__file__).resolve().parents[1] / "data" / "assets" / "expert_system_assets_chs_latest.json"


def _collapse(text: str) -> str:
    return " ".join(str(text or "").split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@lru_cache(maxsize=4)
def _load_entries(path: str) -> tuple[dict[str, Any], ...]:
    """Asset entries from ``path`` (``()`` on any failure).

    Keyed by the path string, so tests that point ``ASSET_PATH`` at a fixture get
    their own entry list instead of a cached parse of the real dump.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return ()
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, dict))


@lru_cache(maxsize=1)
def glossary_variants() -> dict[str, tuple[tuple[str, str], ...]]:
    """``name -> ((type, description), ...)`` for the explainable types.

    Under one name the dump can carry several rule versions. Up to
    ``GLOSSARY_MAX_VERSIONS`` **distinct** descriptions are kept in ``data`` order
    (identical text deduped), so a term with one wording still renders one line.
    Module-level lazy load: nothing is read until a v2 prompt asks for the glossary,
    and any failure (missing dump, bad JSON, unexpected shape) yields ``{}`` so a
    prompt render can never fail because of the asset pack.
    """
    variants: dict[str, list[tuple[str, str]]] = {}
    for entry in _load_entries(str(ASSET_PATH)):
        entity_type = str(entry.get("type") or "")
        if entity_type not in GLOSSARY_TYPES:
            continue
        name = str(entry.get("name") or "").strip()
        description = _collapse(entry.get("description") or "")
        if len(name) < GLOSSARY_MIN_NAME or not description or name in GLOSSARY_STOPWORDS:
            continue
        bucket = variants.setdefault(name, [])
        if any(known == description for _type, known in bucket):
            continue
        if len(bucket) >= GLOSSARY_MAX_VERSIONS:
            continue
        bucket.append((entity_type, description))
    return {name: tuple(bucket) for name, bucket in variants.items()}


@lru_cache(maxsize=1)
def glossary_index() -> dict[str, tuple[str, str]]:
    """``name -> (type, first description)``; the shape ``glossary_type_of`` needs."""
    return {
        name: variants[0]
        for name, variants in glossary_variants().items()
        if variants
    }


# ``(path, term) -> source labels``; bounded by the number of rendered terms.
_source_cache: dict[tuple[str, str], tuple[str, ...]] = {}


def clear_glossary_cache() -> None:
    """Drop every cached asset read (tests point ``ASSET_PATH`` at fixtures)."""
    glossary_variants.cache_clear()
    glossary_index.cache_clear()
    _source_cache.clear()


def glossary_sources(term: str) -> tuple[str, ...]:
    """Names of the cards/skills that *mention* ``term``, in ``data`` order.

    Used as the per-version label of a multi-version term: the asset dump has no
    link from a state to the card that creates it, but a description that names the
    term is the closest verifiable source, so the label never guesses. Characters
    (``type`` empty) and the explainable entity types themselves are skipped.
    """
    name = str(term or "").strip()
    if not name:
        return ()
    key = (str(ASSET_PATH), name)
    cached = _source_cache.get(key)
    if cached is not None:
        return cached
    labels: list[str] = []
    for entry in _load_entries(str(ASSET_PATH)):
        entity_type = str(entry.get("type") or "")
        if not entity_type or entity_type in GLOSSARY_TYPES:
            continue
        source_name = str(entry.get("name") or "").strip()
        if len(source_name) < GLOSSARY_MIN_NAME or source_name == name:
            continue
        if name not in str(entry.get("description") or ""):
            continue
        if source_name in labels:
            continue
        labels.append(source_name)
        if len(labels) >= GLOSSARY_MAX_VERSIONS:
            break
    result = tuple(labels)
    _source_cache[key] = result
    return result


@lru_cache(maxsize=4)
def _name_pool(path: str) -> tuple[str, ...]:
    """Every asset display name from ``path``, longest first (see above)."""
    names = {
        str(entry.get("name") or "").strip()
        for entry in _load_entries(path)
    }
    pool = (name for name in names if len(name) >= GLOSSARY_MIN_NAME)
    # Longest first; the name tie-break keeps the scan deterministic across runs.
    return tuple(sorted(pool, key=lambda name: (-len(name), name)))


def glossary_name_pool() -> tuple[str, ...]:
    """Every asset display name (longest first): the mask for the glossary scan.

    Not just the explainable types: a skill (「夜巡影翼」), a passive (「潜行大师」),
    a character/enemy or a card also owns its span in the text. Without these, the
    state 「夜巡」 would be read out of the skill name 夜巡影翼 -- the asset dump has
    no entity called 夜巡, only the state that happens to share those two characters.
    """
    return _name_pool(str(ASSET_PATH))


def _free_positions(text: str, names: Any) -> dict[str, list[int]]:
    """Occurrence starts per name, longest name first, spans never overlap.

    A longer known name consumes its span (``bytearray`` of 1s), so a shorter name
    is only a hit where no longer name covers the text. That is what separates
    「潜行」 (also a substring of the passive 潜行大师) from a pure substring hit:
    the standalone 潜行 in the description still gets its own span and is
    explained, while 夜巡 inside 夜巡影翼 never does.
    """
    taken = bytearray(len(text))
    positions: dict[str, list[int]] = {}
    for name in sorted(set(names), key=lambda value: (-len(value), value)):
        if not name:
            continue
        start = text.find(name)
        while start >= 0:
            end = start + len(name)
            if not any(taken[start:end]):
                taken[start:end] = b"\x01" * (end - start)
                positions.setdefault(name, []).append(start)
            start = text.find(name, start + 1)
    return positions


def glossary_type_of(name: str) -> str:
    """Human label for the indexed entity type of ``name`` (``""`` when unknown)."""
    item = glossary_index().get(name)
    return GLOSSARY_TYPE_LABELS.get(item[0], "") if item else ""


def glossary_terms(text: str, *, limit: int = GLOSSARY_MAX_TERMS) -> list[tuple[str, str]]:
    """``(name, description)`` for names in ``text``, first mention first.

    Matching is substring containment over *unclaimed* spans: every known asset name
    (character/skill/card/state, longest first) first takes the text it covers, so a
    name only counts where it is not merely a substring of a longer known name (see
    ``_free_positions``). Generic stopwords never match. One description per term
    (the first version), capped at ``GLOSSARY_DESC_LIMIT``; ``glossary_section``
    prints the extra versions of a multi-wording term.
    """
    if not text:
        return []
    variants = glossary_variants()
    if not variants:
        return []
    spans = _free_positions(text, glossary_name_pool() or tuple(variants))
    position = {
        name: min(starts) for name, starts in spans.items() if name in variants and starts
    }
    ordered = sorted(position, key=lambda name: (position[name], name))
    terms: list[tuple[str, str]] = []
    for name in ordered[: max(0, int(limit))]:
        bucket = variants.get(name)
        if not bucket:
            continue
        description = _truncate(bucket[0][1], GLOSSARY_DESC_LIMIT)
        if description:
            terms.append((name, description))
    return terms


def glossary_section(text: str, *, limit: int = GLOSSARY_MAX_TERMS) -> str:
    """The ``名词解释`` block, or ``""`` when nothing in ``text`` matches.

    A term the dump defines more than once is printed once per distinct description
    (at most ``GLOSSARY_MAX_VERSIONS``), each with the card/skill that mentions the
    term as its source label -- ``（来自「抵天雷罚」）`` -- or ``（另一版本）`` when the
    dump has no such mention. A single-version term keeps the plain one-line shape.
    """
    terms = glossary_terms(text, limit=limit)
    if not terms:
        return ""
    variants = glossary_variants()
    lines = [GLOSSARY_HEADER]
    for name, first in terms:
        bucket = variants.get(name) or (("", first),)
        descriptions = [_truncate(description, GLOSSARY_DESC_LIMIT) for _type, description in bucket]
        descriptions = [description for description in descriptions if description]
        if len(descriptions) <= 1:
            lines.append(f"- {name}：{descriptions[0] if descriptions else first}")
            continue
        sources = glossary_sources(name)
        for index, description in enumerate(descriptions):
            if index < len(sources):
                label = f"（来自「{sources[index]}」）"
            else:
                label = "（另一版本）"
            lines.append(f"- {name}：{description}{label}")
    return "\n".join(lines)
