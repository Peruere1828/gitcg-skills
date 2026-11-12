"""Per-(stage, library version) skill loadout planning.

The model is asked **once per stage per library version** which skills to load for
that stage; the answer is frozen for every seed of the stage. This replaces BM25
top-k for the LLM groups while staying cheap (one call per stage, not per game) and
reproducible (the file records library version, engine lock, model and prompt hash).

The deck is *not* chosen here: it is a fixed input (``configs/stage_decks.json``),
identical in every experiment arm. The deck facts are still shown because skill
choice depends on the deck's characters and card mix.

INNOVATION: stage-level loadout selection with a deterministic BM25 fallback and a
stale-loadout check, so a library/engine bump cannot silently reuse old skills.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from common.llm import extract_json_object
from common.paths import REPO_ROOT
from npc.spec import NpcObjective, NpcStage
from skillloop.cards import deck_facts
from skillloop.library import SkillLibrary
from skillloop.render import ObjectiveDetail, TaskSpec

DECKS_ROOT = REPO_ROOT / "configs" / "stage_decks.json"
ENGINE_LOCK = REPO_ROOT / "configs" / "engine.lock"

LOADOUT_SYSTEM = (
    "你是《七圣召唤》PvE 的技能编排助手。你要为一个关卡从给定技能库中挑选"
    "要装载进对局提示的技能；这些技能会原样加入系统提示，帮助模型逐决策选择动作。"
    "只能从清单里选 id，绝不能编造。只输出 JSON。"
)

LOADOUT_MAX_TOKENS = 256
DESCRIPTION_LIMIT = 60


@dataclass(frozen=True)
class Loadout:
    """The frozen answer for one stage under one library version."""

    stage: str
    deck: str
    skills: tuple[str, ...]
    rationale: str
    model: str
    prompt_hash: str
    fallback: bool = False
    # Provenance, written to disk so a stale file is detectable before a run.
    library_version: int = 0
    engine_lock: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "deck": self.deck,
            "skills": list(self.skills),
            "rationale": self.rationale,
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "fallback": bool(self.fallback),
            "library_version": int(self.library_version),
            "engine_lock": self.engine_lock,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Loadout":
        return cls(
            stage=str(payload["stage"]),
            deck=str(payload.get("deck") or ""),
            skills=tuple(str(item) for item in payload.get("skills") or ()),
            rationale=str(payload.get("rationale") or ""),
            model=str(payload.get("model") or ""),
            prompt_hash=str(payload.get("prompt_hash") or ""),
            fallback=bool(payload.get("fallback") or False),
            library_version=int(payload.get("library_version") or 0),
            engine_lock=str(payload.get("engine_lock") or ""),
        )

    def fingerprint(self) -> str:
        """Stable row-level hash; changes when any load-bearing field changes."""
        payload = {
            "stage": self.stage,
            "deck": self.deck,
            "skills": list(self.skills),
            "fallback": bool(self.fallback),
            "prompt_hash": self.prompt_hash,
            "library_version": int(self.library_version),
            "engine_lock": self.engine_lock,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha1(blob).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# shared metadata
# --------------------------------------------------------------------------- #
def engine_lock_hash() -> str:
    """12-hex digest of ``configs/engine.lock`` (``missing`` when absent)."""
    if not ENGINE_LOCK.exists():
        return "missing"
    return hashlib.sha1(ENGINE_LOCK.read_bytes()).hexdigest()[:12]


def stage_context(stage: NpcStage) -> dict[str, Any]:
    """Stage-level retrieval context (same shape as ``SkillAgentPolicy._context_dict``).

    ``round`` is the stage's own round cap (``None`` without one): loadout planning
    is per stage, so a ``round_max`` skill that fits the cap applies for the whole
    stage. In-game retrieval still passes the live round number.
    """
    max_rounds = next(
        (objective.n for objective in stage.objectives
         if objective.type == "win_within_rounds" and objective.n is not None),
        None,
    )
    return {
        "stage": stage.name,
        "objective_types": tuple(objective.type for objective in stage.objectives),
        "enemies": tuple(
            entry.id for entry in (*stage.lineup, *stage.reserve)
        ),
        "round": max_rounds,
    }


def applicable_skills(stage: NpcStage, library: SkillLibrary) -> list[Any]:
    """Active skills whose conditions match this stage (rule layer included)."""
    return library.applicable(stage_context(stage))


def _objective_detail(objective: NpcObjective) -> ObjectiveDetail:
    return ObjectiveDetail(
        type=objective.type,
        text=objective.text,
        count=objective.count,
        kind=objective.kind,
        n=objective.n,
        k=objective.k,
    )


def stage_task(stage: NpcStage, deck: str) -> TaskSpec:
    """The same TaskSpec the runner builds, shared here to keep fallback retrieval aligned."""
    details = tuple(_objective_detail(objective) for objective in stage.objectives)
    max_rounds = next(
        (objective.n for objective in stage.objectives
         if objective.type == "win_within_rounds" and objective.n is not None),
        None,
    )
    return TaskSpec(
        stage=stage.name,
        display=stage.display or stage.name,
        objectives=tuple(objective.text for objective in stage.objectives),
        deck=deck,
        objective_types=tuple(objective.type for objective in stage.objectives),
        max_rounds=max_rounds,
        objective_details=details,
    )


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
def _one_line(text: Any, limit: int = DESCRIPTION_LIMIT) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _objectives_block(stage: NpcStage) -> list[str]:
    lines: list[str] = []
    for objective in stage.objectives:
        line = f"- {objective.text or objective.type}"
        if objective.type == "win_within_rounds" and objective.n is not None:
            line += f"（回合上限 {objective.n}）"
        lines.append(line)
    return lines


def _enemies_block(stage: NpcStage) -> str:
    lineup = "、".join(entry.name for entry in stage.lineup) or "未知"
    if stage.reserve:
        reserve = "、".join(entry.name for entry in stage.reserve)
        return f"{lineup}（后备：{reserve}）"
    return lineup


def _deck_block(deck: str) -> list[str]:
    from skillloop.era import resolve_deck

    facts = deck_facts(resolve_deck(deck))
    characters = "、".join(facts["characters"]) or "未知"
    elements = "、".join(f"{label}x{count}" for label, count in facts["elements"].items()) or "未知"
    cards = facts["cards"]
    card_text = " | ".join(
        f"{label}x{cards.get(key, 0)}"
        for key, label in (
            ("weapon", "武器"),
            ("artifact", "圣遗物"),
            ("event", "事件"),
            ("support", "支援"),
            ("other", "其它"),
            ("unknown", "未知"),
        )
    )
    return [
        f"出战牌组：{deck}",
        f"- 角色：{characters}",
        f"- 元素：{elements}",
        f"- 卡牌：{card_text}",
    ]


def _skills_block(skills: Sequence[Any]) -> list[str]:
    lines = []
    for skill in skills:
        lines.append(
            f"- {skill.skill_id} | {skill.layer} | {skill.skill_name} | "
            f"{_one_line(skill.description)}"
        )
    return lines or ["- （本关无适用技能）"]


def build_messages(stage: NpcStage, library: SkillLibrary, deck: str, *, max_skills: int) -> tuple[dict[str, str], dict[str, str]]:
    """System + user prompt. Kept separate so ``prompt_hash`` can cover both.

    Only skills applicable to **this stage** are listed (rule layer included), so
    the model cannot pick a stage-specific skill that belongs to another stage.
    """
    applicable = applicable_skills(stage, library)
    lines = [
        f"关卡：{stage.display or stage.name}（{stage.name}）",
        "三星目标：",
        *_objectives_block(stage),
        f"敌方阵容：{_enemies_block(stage)}",
        *_deck_block(deck),
        f"技能库 v{library.version}（共{len(library)}条，当前关卡适用 "
        f"{len(applicable)} 条；最多选 {max_skills} 条；id 必须来自下列清单）：",
        *_skills_block(applicable),
        '输出严格 JSON：{"skills": ["<id>", ...], "rationale": "<80字以内>"}',
    ]
    system = {"role": "system", "content": LOADOUT_SYSTEM}
    user = {"role": "user", "content": "\n".join(lines)}
    return system, user


def _prompt_hash(messages: Sequence[dict[str, str]]) -> str:
    digest = hashlib.sha1()
    for message in messages:
        digest.update(str(message.get("content") or "").encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:12]


# --------------------------------------------------------------------------- #
# parse / validate
# --------------------------------------------------------------------------- #
def _parse_skills(
    payload: dict[str, Any] | None,
    library: SkillLibrary,
    max_skills: int,
    *,
    allowed: set[str] | None = None,
) -> tuple[tuple[str, ...], str, str | None]:
    """Return ``(skills, rationale, error)``; error is ``None`` when valid.

    Ids outside the prompt's allowed set (or unknown to the library) are dropped;
    if every id was dropped the reply is a validation failure so ``plan_loadout``
    can retry and ultimately fall back to BM25. Duplicates and the cap are
    normalisation.
    """
    if payload is None:
        return (), "", "unparseable"
    raw = payload.get("skills")
    if not isinstance(raw, list):
        return (), "", "skills_not_list"
    selected: list[str] = []
    discarded: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return (), "", "skill_id_not_string"
        if allowed is not None and item not in allowed:
            discarded.append(item)
            continue
        if item not in library.skills:
            discarded.append(item)
            continue
        if item not in selected:
            selected.append(item)
    if not selected and discarded:
        return (), "", f"out_of_scope:{discarded[0]}"
    rationale = payload.get("rationale")
    return tuple(selected[: max(0, int(max_skills))]), (rationale if isinstance(rationale, str) else ""), None


def _fallback_skills(stage: NpcStage, library: SkillLibrary, deck: str, max_skills: int) -> tuple[str, ...]:
    task = stage_task(stage, deck)
    picked = library.retrieve(task.describe(), stage_context(stage), k=max(0, int(max_skills)))
    return tuple(skill.skill_id for skill in picked)


def _model_name(client: Any, model: str | None) -> str:
    if model:
        return model
    config = getattr(client, "config", None)
    return str(getattr(config, "model", "") or "")


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def plan_loadout(
    stage: NpcStage,
    library: SkillLibrary,
    deck: str,
    client: Any,
    *,
    model: str | None = None,
    max_skills: int = 5,
    retries: int = 1,
) -> Loadout:
    """Ask the model once (plus ``retries``) for this stage's skills.

    Temperature is fixed at 0. On parse/validation failure after the retries, returns
    the library's BM25 top-k for the stage with ``fallback=True``. The fallback is
    deterministic, so a failed model call still produces a reproducible run.
    """
    allowed = {
        skill.skill_id for skill in applicable_skills(stage, library)
    }
    system, user = build_messages(stage, library, deck, max_skills=max_skills)
    messages = [system, user]
    prompt_hash = _prompt_hash(messages)
    model_name = _model_name(client, model)
    last_error = "empty_response"

    for _attempt in range(max(0, int(retries)) + 1):
        try:
            raw = client.chat(
                messages,
                max_tokens=LOADOUT_MAX_TOKENS,
                temperature=0.0,
                response_format_json=True,
                model=model,
                reasoning_effort="none",
            )
        except Exception as exc:  # a broken client must degrade, not abort planning
            raw = None
            last_error = f"client_error:{type(exc).__name__}"
        skills, rationale, error = _parse_skills(
            extract_json_object(raw), library, max_skills, allowed=allowed
        )
        if error is None:
            return Loadout(
                stage=stage.name,
                deck=deck,
                skills=skills,
                rationale=rationale,
                model=model_name,
                prompt_hash=prompt_hash,
                fallback=False,
                library_version=library.version,
                engine_lock=engine_lock_hash(),
            )
        last_error = error

    return Loadout(
        stage=stage.name,
        deck=deck,
        skills=_fallback_skills(stage, library, deck, max_skills),
        rationale=f"fallback: {last_error}",
        model=model_name,
        prompt_hash=prompt_hash,
        fallback=True,
        library_version=library.version,
        engine_lock=engine_lock_hash(),
    )


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #
def save_loadout(root: str | Path, loadout: Loadout) -> Path:
    target = Path(root) / f"v{int(loadout.library_version)}" / f"{loadout.stage}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(loadout.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_loadouts(root: str | Path) -> dict[str, Loadout]:
    """Load every loadout under ``root`` (``v*/stage.json`` or flat ``stage.json``).

    When the same stage appears under several versions the highest library version
    wins; the runner then rejects it if it does not match the run's library.
    """
    root = Path(root)
    if not root.exists():
        return {}
    found: list[tuple[int, str, Loadout]] = []
    for path in sorted(root.rglob("*.json")):
        if path.name in ("library.json", "usage.json"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "stage" not in payload or "skills" not in payload:
            continue
        try:
            loadout = Loadout.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid loadout file {path}: {exc}") from exc
        found.append((loadout.library_version, str(path), loadout))
    ordered = sorted(found, key=lambda item: (item[0], item[1]))
    return {loadout.stage: loadout for _version, _path, loadout in ordered}


def loadout_matches(loadout: Loadout, *, library_version: int, engine_lock: str) -> bool:
    """True when a saved loadout was built for the same library + engine lock."""
    return (
        int(loadout.library_version) == int(library_version)
        and loadout.engine_lock == engine_lock
    )


# --------------------------------------------------------------------------- #
# fixed stage -> deck config
# --------------------------------------------------------------------------- #
def load_stage_decks(path: str | Path) -> dict[str, str]:
    """Read ``{"stages": {stage: {"deck": name, ...}}}``; missing entries are absent.

    The loader is deliberately tolerant: a stage without an entry falls back to the
    runner's ``RunSpec.deck`` instead of failing the whole run.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    stages = payload.get("stages") if isinstance(payload, dict) else None
    if not isinstance(stages, dict):
        return {}
    out: dict[str, str] = {}
    for stage, entry in stages.items():
        if isinstance(entry, dict) and entry.get("deck"):
            out[str(stage)] = str(entry["deck"])
    return out
