#!/usr/bin/env python3
# web/ai_rpc_server.py
#
# WS8/D18：浏览器引擎 ↔ Python 的 HTTP 桥。
#
# 浏览器侧 `@gi-tcg/standalone` 的 AI 座位把 `PlayerIO.rpc/notify` 转成对
# 本服务的 HTTP POST（见 engine-overlay/overlay/packages/standalone/src/ai-http-io.ts）：
#   POST /rpc       {"session","who","seq?","request"}  -> RpcResponse
#   POST /notify    {"session","who","seq","notification"} -> {"applied":bool}
#   POST /session   {"session","meta","resume?"}        -> {"ok","seq_base",...}
#   POST /choose-deck {"session","deck"}                -> C1 反选：按人的牌组挑应对牌组
#   POST /game-end  {"session","winner","rounds","aborted?","abort_reason?"} -> 终局关账
#   POST /game-log  {"session","log"}                   -> 引擎日志归档（含人类决策）
#   GET  /health    -> {"ok":true,...}
#   GET  /state?session=&who= -> 该座位最近一次（掩码后）notification.state
#   GET  /ai-deck   -> {"slug","characters","cards"}（T3：AI 座位牌组，供 UI 回填）
#   GET  /npc-decks -> C3：可当玩家牌组用的 NPC 阵容（角色 + 池 C 牌）
#   GET  /results   -> 已开局会话的汇总（C4 调试闭环）
#
# 三个隔离维度（T1/T2）：
#   * **座位**：引擎的 `exposeState(who, …)` 按座位掩码（自己手牌/骰子可见、
#     对手隐 id），所以每个 (session, who) 各存一份 `latest_state`；自对弈时两个
#     座位的 /notify 不再互相覆盖。
#   * **会话**：一局一个 `session`（浏览器每局生成），状态、策略实例、决策计数、
#     日志目录都按 session 隔离，多开标签页不串局。
#   * **通知序号（T2）**：`/notify` 带座位内单调递增的 `seq`，过期通知直接丢弃；
#     浏览器侧还会把 notify 串成队列、发 rpc 前先等队列排空，避免决策吃到陈旧状态。
#     **续局**（「从此处继续」）会在同一个 session 上开新的一局：浏览器侧 seq 从 1
#     重新数，所以 `/session` 带 `resume:true` 时会把每个座位的 seq 顶到该会话
#     上一局的末尾之后（`_RESUME_SEQ_GAP`）并把基线放进响应的 `seq_base`，浏览器
#     从 `seq_base+1` 接着数——否则新局的通知会被一律判成过期、决策吃的是被终止
#     那一局的残局状态。同一请求也清掉上一局的 `latest_state`。
#
# 决策逻辑有两种模式：
#   * 默认（不给 --policy）：纯标准库占位策略，合法动作里随机/贪心挑一个：
#       rerollDice   -> 不重掷
#       switchHands  -> 不换手
#       chooseActive -> 候选第一只
#       selectCard   -> 候选第一张
#       action       -> 优先选“合法且非 declareEnd”的动作；否则第一个合法；
#                       usedDice 用该动作的 autoSelectedDice（引擎提示的自动选骰）。
#   * `--policy SPEC`（C2 模型桥）：把五种请求都路由到仓库策略
#       `envs.policy.build_policy(SPEC, seed=SEED, role=…)` +
#       `reps.action_adapter.build_decision_context`。SPEC 支持
#       `heuristic`、`legal_random`、`expert:<slug>`、`random`，以及 LLM 技能
#       智能体 `llm:<library>`（library ∈ none | llm_empty | static | dynamic）。
#       LLM 策略**严格对齐 `skillloop.run.build_policy`**：关卡 TaskSpec、
#       RichStateRenderer（默认完整 prompt）、era/expert base、未过滤技能库、candidate_cap=16、
#       引擎原候选顺序。`none` / `llm_empty` 都映射到 runner 的 `llm_empty`
#       （LLM + **仅游戏规则** `data/skills/rules_v1`，17 条 rule；不含策略/关卡特化）；
#       `static` 用 `data/skills/static_v1`（17 rule + 10 strategy）；`dynamic`
#       用 `data/skills/dyn_v4`（17 rule + 23 strategy）。loadout 只对
#       static/dynamic 生效。
#       牌组优先级：loadout > `--stage-decks` > 网页上报的 AI deck。
#       LLM 需要仓库根 `.env` 里的 key/base_url/model；不可用时 AI 座位退回
#       `resolve_base_policy`（era/expert；未知牌组用 heuristic）并在 `/session`
#       的 `ai` 字段记录原因。`GET /ai` 报告全局设置与 `parity`、`POST
#       /ai-library {"library": ...}` 改后续新会话的库。
#       reps/torch 等依赖只在请求模型策略时才延迟导入，默认路径仍是零依赖。
#       任何加载/转换/选择失败都回退脚本/占位策略（回退写进决策日志的 `fallback`）。
#
# T3 牌组对齐：`--ai-deck <slug>`（或 `--policy expert:<slug>` 隐含）声明 AI 座位
# 应该用的牌组，`/ai-deck` 把它给前端回填；开局 `/session` 里浏览器上报实际牌组，
# 与声明不一致就 stderr 告警并记进会话（`deck_mismatch` + `seat_specs`，脚本专家
# 规则是按自家牌组写的，牌组不符会静默变蠢）。
#
# C1 反选（D19）：`POST /choose-deck` 拿人的牌组 → `web.counterpick` 用对位矩阵挑
# 一套应对牌组，结果记进会话；开局 `/session` 时若 base 策略是 deck-specific 的
# `expert:*`，就按**各座位实际牌组**改派 `expert:<slug>`（人的牌组若也是池内 20 套
# 之一，自对弈时同样派它自己的脚本）。base 是 `llm:*` 时只有 `meta.ai_seat` 那侧
# 用 LLM；`meta.ai_self_play` 且带 stage 时非 AI 座位派 `npc:<stage>` 的真实
# NpcPolicy（缺 stage 用 heuristic），否则仍按牌组派脚本。`neural:` 等用户显式选的
# 策略绝不被覆盖。
#
# C3 NPC 卡组（D18）：`GET /npc-decks` 给出可当玩家牌组用的 NPC 阵容
# （角色取自 `configs/npc/`，牌由 decklab 池 C 填充）。
#
# 跑：
#   conda run -n gitcg python web/ai_rpc_server.py --port 8770
#   conda run -n gitcg python web/ai_rpc_server.py --port 8770 \
#       --policy expert:superconduct_aggro --ai-deck superconduct_aggro
#   conda run -n gitcg python web/ai_rpc_server.py --port 8770 --policy llm:dynamic \
#       --stage-decks configs/stage_decks.json --loadout-root reports/loadouts
# 前端（standalone）里勾选「AI 对手（HTTP 桥）」并填 http://127.0.0.1:8770 即可。
# LLM 策略需要仓库根 `.env`（GITCG_LLM_* / DEEPSEEK_*）；`GET /ai` 自查是否可用。

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# 允许直接 `python web/ai_rpc_server.py` 运行：此时 sys.path[0] 是 web/，仓库根不在
# 路径上，`envs`/`reps`/`train` 无法导入（C2 的延迟导入会全部失败并回退占位）。
# 把仓库根补进 sys.path，使直接运行与 `python -m web.ai_rpc_server` 等价。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 引擎枚举：ActionValidity.VALID === 0（见 packages/typings/src/gen/rpc.ts）
VALID = 0

# 默认只允许来自本机的网页驱动本桥（见 Handler._cors）。
# 这是**有意识**的收敛：桥能替 AI 座位做决策，若允许任意 Origin，
# 用户浏览器里任意一个网页都能 POST /rpc 驱动本地桥。
_LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# 五种 RPC 方法名 -> gitcg `Request` oneof 的 proto 字段名（snake_case）。
_METHOD_TO_FIELD = {
    "rerollDice": "reroll_dice",
    "switchHands": "switch_hands",
    "chooseActive": "choose_active",
    "action": "action",
    "selectCard": "select_card",
}

# 模型策略 seed 派生用的角色前缀（build_policy 把 seed/role 折进策略 RNG）。
_WEB_ROLE = "web"

# 没带 session 的老客户端（或直接 curl 调试）统一落到这个默认会话。
DEFAULT_SESSION = "-"

# 决策日志根目录（`data/**` 已 gitignore，只提交 schema/清单，见 AGENTS 约定）。
DEFAULT_LOG_DIR = os.path.join(_REPO_ROOT, "data", "web")

# base 是 deck-specific 的 `expert:*` 时，非池内牌组（NPC 阵容/自改牌组）改派到
# deck-agnostic 的策略——硬套别人的脚本每步都会回退占位（见 assign_seat_specs）。
_NON_POOL_DECK_SPEC = "heuristic"

# CORS 预检缓存时长（秒）：浏览器据此缓存 OPTIONS 结果，避免跨域访问时
# 每个 /notify、/rpc 都先配一次预检请求。
_CORS_MAX_AGE_SECONDS = 600

# 续局（「从此处继续」）时把座位 seq 顶到上一局末尾之后多远：既让新局的序号不与
# 旧局重叠（旧局在途通知会被判过期丢掉），又不至于大到难以阅读。
_RESUME_SEQ_GAP = 1_000_000

# 掩码 state 的 phase 枚举在 proto-JSON 里的各种写法（Python MessageToDict 与
# protobuf-ts 的 toJson 都可能出现），用于识别终局。
_END_PHASES = {"PHASE_TYPE_GAME_END", "GAME_END", "gameEnd", 5}

# LLM 策略的四个技能库选项（`llm:<library>`）。none / llm_empty 都表示
# 「LLM + 仅游戏规则」（`data/skills/rules_v1`，17 条 rule）；static 是规则 +
# 手写静态策略（static_v1）；dynamic 是规则 + 动态策略（dyn_v4）。
LLM_LIBRARY_CHOICES = ("none", "llm_empty", "static", "dynamic")
_SKILLS_ROOT = os.path.join(_REPO_ROOT, "data", "skills")
_LLM_LIBRARY_LABELS = {
    "none": "none（仅游戏规则）",
    "llm_empty": "llm_empty（仅游戏规则）",
}

# 与 ``skillloop.run`` 严格对齐的评测配置。网页端只是调试接口：策略、提示词、
# 技能库、基础策略、候选顺序/上限全部由 ``skillloop.run.build_policy`` 决定。
DEFAULT_STAGE_DECKS = os.path.join(_REPO_ROOT, "configs", "stage_decks.json")
DEFAULT_ERA_DECKS = os.path.join(_REPO_ROOT, "configs", "era_decks.json")
_LIBRARY_GROUPS = ("llm_empty", "static", "dynamic")
_LOADOUT_GROUPS = ("static", "dynamic")
_PROTOCOL_NAME = "skillloop.run"


@dataclass(frozen=True)
class WebEvalConfig:
    """Runner knobs the web bridge must pass through unchanged."""

    stage_decks_root: str = DEFAULT_STAGE_DECKS
    loadout_root: str | None = None
    library_override: str | None = None
    card_version: str | None = None
    rotation: str | None = None
    stage_rules: bool = True
    # Optional in-game notebook, off by default. The web bridge only forwards it so
    # a debug session can match a `skillloop.run --scratchpad` A/B arm.
    scratchpad: bool = False
    # Provider reasoning effort for AI decisions ("none" = current baseline).
    reasoning: str = "none"
    # Full prompt is the default (matches skillloop.run); compact is opt-out.
    rich_render: bool = True


def _abs_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(_REPO_ROOT) / path


def _display_path(path: str | os.PathLike[str] | None) -> str | None:
    if not path:
        return None
    resolved = _abs_path(path)
    try:
        return os.path.relpath(resolved, _REPO_ROOT)
    except ValueError:
        return str(resolved)


def _stage_or_none(slug: Any) -> Any:
    """Resolve an NPC stage slug; unknown/missing slugs degrade to parity=false."""
    if not isinstance(slug, str) or not slug:
        return None
    try:
        from npc.spec import get_stage

        return get_stage(slug)
    except Exception as exc:  # noqa: BLE001
        _warn(f"无法解析关卡 {slug!r}（{type(exc).__name__}: {exc}），按非评价协议处理")
        return None


@lru_cache(maxsize=4)
def _stage_decks_map(path: str) -> dict[str, str]:
    from skillloop.loadout import load_stage_decks

    root = _abs_path(path)
    if not root.exists():
        _warn(f"--stage-decks 文件不存在：{root}（关卡牌组映射为空）")
        return {}
    return load_stage_decks(root)


@lru_cache(maxsize=1)
def _era_version() -> str | None:
    try:
        payload = json.loads(_abs_path(DEFAULT_ERA_DECKS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = payload.get("version")
    return str(version) if version else None


def _selection_group(selection: str | None) -> str | None:
    """Map the four web selections to the runner's group names."""
    if selection in ("none", "llm_empty"):
        return "llm_empty"
    if selection in _LIBRARY_GROUPS:
        return selection
    return None


def resolve_library_root(selection: str | None, override: str | None = None) -> str | None:
    """Library directory for a selection.

    ``none``/``llm_empty`` resolve to the fixed rules library; ``override`` is only
    honoured for static/dynamic (llm_empty must stay comparable across runs).
    """
    if selection in ("none", "llm_empty"):
        info = resolve_llm_library(selection)
        if info is None or not info.get("path"):
            raise RuntimeError(f"技能库 {selection!r} 未解析到目录")
        return str(_abs_path(info["path"]))
    if selection not in _LIBRARY_GROUPS:
        return None
    if override:
        return str(_abs_path(override))
    info = resolve_llm_library(selection)
    if info is None or not info.get("path"):
        raise RuntimeError(f"技能库 {selection!r} 未解析到目录")
    return str(_abs_path(info["path"]))



def parse_llm_spec(spec: str | None) -> tuple[str, str | None] | None:
    """解析 `llm:<library>[#deck=<slug>]`；非 llm spec 返回 None。

    `#deck=` 是桥内部的座位编码（由 `assign_seat_specs` 写进 seat spec），
    让 LLM 策略知道该用哪套牌组的机械兜底脚本。
    """
    if not isinstance(spec, str) or not spec.startswith("llm:"):
        return None
    body = spec[len("llm:"):]
    deck: str | None = None
    if "#" in body:
        body, fragment = body.split("#", 1)
        for part in fragment.split("&"):
            key, _, value = part.partition("=")
            if key == "deck" and value:
                deck = value
    return (body.strip() or "none"), deck


def _library_roots() -> list[Path]:
    root = Path(_SKILLS_ROOT)
    if not root.is_dir():
        return []
    return [path for path in sorted(root.iterdir()) if (path / "library.json").is_file()]


def _library_meta(path: Path) -> dict[str, Any]:
    """只读 library.json 的 meta/version（避免为一个 label 构造整个 SkillLibrary）。

    严格对齐后网页不再过滤技能库：``applied_skills`` 恒等于 ``skills``，
    ``filtered_out`` 恒为 0（字段保留给前端向后兼容）。
    """
    import json as _json

    try:
        payload = _json.loads((path / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    skills = [item for item in (payload.get("skills") or []) if isinstance(item, dict)]
    return {
        "version": int(payload.get("library_version") or 0),
        "skills": len(skills),
        "applied_skills": len(skills),
        "filtered_out": 0,
        "meta": dict(payload.get("meta") or {}),
    }


def _library_info(path: Path, selection: str) -> dict[str, Any]:
    payload = _library_meta(path)
    meta = payload.get("meta") or {}
    return {
        "selection": selection,
        "resolved": path.name,
        "path": os.path.relpath(path, _REPO_ROOT),
        "version": int(payload.get("version") or 0),
        "skills": int(payload.get("skills") or 0),
        "applied_skills": int(payload.get("applied_skills") or 0),
        "filtered_out": int(payload.get("filtered_out") or 0),
        "built_at": meta.get("built_at"),
        "kind": meta.get("kind"),
    }


def _newest_library(kind: str) -> Path | None:
    """meta.kind==kind 的库中 built_at 最新的一个（ISO 时间戳可直接字典序比）。"""
    candidates = []
    for path in _library_roots():
        meta = _library_meta(path).get("meta") or {}
        if meta.get("kind") != kind:
            continue
        candidates.append((str(meta.get("built_at") or ""), path.name, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def resolve_llm_library(selection: str) -> dict[str, Any] | None:
    """把库选择解析成可序列化的描述；未知选择返回 None。

    ``none``/``llm_empty`` = LLM + 仅游戏规则，固定使用 ``data/skills/rules_v1``
    （17 条 rule）；它们不是「无提示词」。
    """
    if selection in ("none", "llm_empty"):
        path = Path(_SKILLS_ROOT) / "rules_v1"
    elif selection == "static":
        preferred = Path(_SKILLS_ROOT) / "static_v1"
        path = preferred if (preferred / "library.json").is_file() else _newest_library("rules")
    elif selection == "dynamic":
        preferred = Path(_SKILLS_ROOT) / "dyn_v4"
        path = preferred if (preferred / "library.json").is_file() else _newest_library("evolved")
    else:
        _warn(f"未知 LLM 技能库选择 {selection!r}；可选：{list(LLM_LIBRARY_CHOICES)}")
        return None
    if path is None or not (path / "library.json").is_file():
        _warn(f"技能库选择 {selection!r} 未解析到目录")
        return None
    return _library_info(path, selection)


def llm_library_options() -> list[dict[str, str]]:
    """`/ai` 的 options：label 带解析到的目录名/条数，便于人直接看懂。"""
    options: list[dict[str, str]] = []
    for selection in LLM_LIBRARY_CHOICES:
        if selection in _LLM_LIBRARY_LABELS:
            label = _LLM_LIBRARY_LABELS[selection]
        else:
            info = resolve_llm_library(selection)
            if info is None:
                label = f"{selection}（未找到）"
            else:
                label = f"{selection}（{info['resolved']}，{info['skills']} 条）"
        options.append({"value": selection, "label": label})
    return options


def _loadout_for_stage(
    stage_slug: str,
    selection: str | None,
    config: WebEvalConfig,
) -> tuple[Any, str | None, str | None]:
    """Load the frozen loadout for ``stage`` (same vN/``loadout_matches`` rules).

    Unlike the runner this is a *debug* bridge: a missing or stale loadout warns
    and falls back to the stage deck instead of aborting the session.
    """
    if not config.loadout_root or selection not in _LOADOUT_GROUPS:
        return None, None, None
    try:
        from skillloop.library import SkillLibrary
        from skillloop.loadout import engine_lock_hash, load_loadouts, loadout_matches

        library_root = resolve_library_root(selection, config.library_override)
        if library_root is None:
            return None, None, None
        library = SkillLibrary.load(library_root)
        root = _abs_path(config.loadout_root)
        versioned = root / f"v{library.version}"
        source = versioned if versioned.is_dir() else root
        loadouts = load_loadouts(source)
        loadout = loadouts.get(stage_slug)
        if loadout is None:
            _warn(
                f"关卡 {stage_slug!r} 在 {source} 没有 loadout；"
                "按 --stage-decks 的关卡牌组继续（非严格 loadout 协议）"
            )
            return None, library_root, None
        if not loadout_matches(
            loadout,
            library_version=library.version,
            engine_lock=engine_lock_hash(),
        ):
            _warn(
                f"关卡 {stage_slug!r} 的 loadout 过期（文件 v{loadout.library_version} / "
                f"engine {loadout.engine_lock or 'missing'}，运行 v{library.version} / "
                f"engine {engine_lock_hash()}）；按 --stage-decks 继续"
            )
            return None, library_root, None
        candidates = [
            source / f"{stage_slug}.json",
            source / f"v{library.version}" / f"{stage_slug}.json",
            root / f"v{library.version}" / f"{stage_slug}.json",
            root / f"{stage_slug}.json",
        ]
        # ``reports/loadouts`` packs versions as ``v<run>/v<library>/stage.json``;
        # pick the file whose parent directory matches the loaded library version.
        candidates.extend(sorted(root.rglob(f"{stage_slug}.json")))
        version_dir = f"v{library.version}"
        path = next(
            (
                item
                for item in candidates
                if item.is_file() and item.parent.name == version_dir
            ),
            None,
        )
        if path is None:
            path = next((item for item in candidates if item.is_file()), source)
        return loadout, library_root, str(path)
    except Exception as exc:  # noqa: BLE001
        _warn(f"加载 loadout 失败（{stage_slug!r}）：{type(exc).__name__}: {exc}；退回 stage_decks")
        return None, None, None


def _resolve_stage_assignment(
    stage_slug: str,
    *,
    selection: str | None,
    config: WebEvalConfig,
) -> tuple[Any, str | None, Any, str | None, str | None]:
    """Return ``(stage, stage_deck, loadout, library_root, loadout_path)``."""
    stage = _stage_or_none(stage_slug)
    if stage is None:
        return None, None, None, None, None
    stage_deck = _stage_decks_map(config.stage_decks_root).get(stage_slug)
    loadout, library_root, loadout_path = _loadout_for_stage(stage_slug, selection, config)
    return stage, stage_deck, loadout, library_root, loadout_path


def _eval_target(
    stage_slug: str | None,
    *,
    selection: str | None,
    config: WebEvalConfig,
) -> dict[str, Any]:
    """The deck/rules an evaluation run would use for this stage."""
    info: dict[str, Any] = {
        "stage": stage_slug,
        "parity": False,
        "card_version": None,
        "deck": None,
        "characters": None,
        "cards": None,
        "rules": None,
        "loadout": None,
    }
    if not stage_slug:
        return info
    stage = _stage_or_none(stage_slug)
    if stage is None:
        return info
    info["parity"] = True
    _stage, stage_deck, loadout, _library_root, loadout_path = _resolve_stage_assignment(
        stage_slug, selection=selection, config=config
    )
    deck = loadout.deck if loadout is not None else stage_deck
    if loadout_path:
        info["loadout"] = _display_path(loadout_path)
    if deck:
        try:
            from skillloop.era import is_era, resolve_deck

            spec = resolve_deck(deck)
        except Exception as exc:  # noqa: BLE001
            _warn(f"解析关卡 {stage_slug!r} 的牌组 {deck!r} 失败：{type(exc).__name__}: {exc}")
        else:
            info["deck"] = deck
            info["characters"] = [int(c) for c in spec.characters]
            info["cards"] = [int(c) for c in spec.cards]
            info["card_version"] = _era_version() if is_era(deck) else None
    if config.stage_rules:
        try:
            from npc.stage_rules import stage_rules_json

            raw = stage_rules_json(stage_slug)
            info["rules"] = json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            _warn(f"解析关卡 {stage_slug!r} 的特殊规则失败：{type(exc).__name__}: {exc}")
    return info


def build_web_llm_policy(
    selection: str,
    reported_deck: str | None,
    *,
    stage_slug: str | None,
    config: WebEvalConfig,
    client: Any,
    seed: int,
) -> tuple[Any, dict[str, Any]]:
    """Build the exact ``skillloop.run.build_policy`` LLM policy for one stage.

    Deck priority matches the runner: frozen loadout > ``--stage-decks`` > the
    deck the browser reported for the AI seat. Missing stage falls back to a
    generic TaskSpec (``parity=False``), which the caller must surface as
    non-evaluation.
    """
    from npc.spec import NpcStage
    from skillloop.run import RunSpec, build_policy

    group = _selection_group(selection)
    if group is None:
        raise RuntimeError(f"未知 LLM 库选择 {selection!r}")
    library_root = resolve_library_root(selection, config.library_override)

    stage = _stage_or_none(stage_slug)
    parity = stage is not None
    stage_deck: str | None = None
    loadout = None
    loadout_path: str | None = None
    if stage is not None:
        stage, stage_deck, loadout, _root, loadout_path = _resolve_stage_assignment(
            stage.name, selection=selection, config=config
        )
    deck = (loadout.deck if loadout is not None else None) or stage_deck or reported_deck
    if not deck:
        raise RuntimeError("无法确定 AI 牌组（loadout/stage_decks/网页上报均缺失）")

    run_stage = stage
    if run_stage is None:
        run_stage = NpcStage(
            name="web",
            display="网页对局（非评价协议）",
            region="",
            lineup=(),
            reserve=(),
            special_rules=(),
            objectives=(),
        )
        _warn("meta.stage 缺失：本次会话按非评价协议（parity=false）运行")

    spec = RunSpec(
        group=group,
        stages=(run_stage.name,),
        seeds=(int(seed),),
        deck=deck,
        library_root=library_root,
        top_k=5,
        model=None,
        max_calls=80,
        stage_decks_root=config.stage_decks_root,
        loadout_root=config.loadout_root,
        card_version=config.card_version,
        scratchpad=config.scratchpad,
        reasoning=config.reasoning,
        rich_render=config.rich_render,
    )
    pinned = tuple(loadout.skills) if loadout is not None else ()
    policy = build_policy(
        spec,
        run_stage,
        client=client,
        deck=deck,
        pinned_skills=pinned,
    )
    info = {
        "stage": run_stage.name if parity else None,
        "parity": parity,
        "group": group,
        "library_root": _display_path(library_root),
        "deck": deck,
        "reported_deck": reported_deck,
        "loadout": _display_path(loadout_path),
        "card_version": config.card_version,
    }
    return policy, info


def _ai_seat_of(meta: dict[str, Any]) -> int:
    try:
        return int(meta.get("ai_seat") or 0)
    except (TypeError, ValueError):
        return 0


def _deck_matches(deck_slug: str, reported: Any) -> bool | None:
    """Compare a reported ``{characters, cards}`` deck with an eval deck slug.

    Returns ``None`` when the comparison cannot be made (unknown deck/slug or a
    malformed report), so callers do not turn a missing field into a mismatch.
    """
    if not isinstance(reported, dict):
        return None
    try:
        from skillloop.era import resolve_deck

        spec = resolve_deck(deck_slug)
    except Exception:  # noqa: BLE001
        return None
    expected_chars = [int(c) for c in spec.characters]
    expected_cards = sorted(int(c) for c in spec.cards)
    actual_chars = [int(c) for c in reported.get("characters") or []]
    actual_cards = sorted(int(c) for c in reported.get("cards") or [])
    return actual_chars == expected_chars and actual_cards == expected_cards


def _is_local_origin(origin: str) -> bool:
    """Origin 形如 scheme://host[:port]，判断其 host 是否为本机。"""
    try:
        host = urlparse(origin).hostname
    except ValueError:
        return False
    return host in _LOCAL_ORIGIN_HOSTS


def _warn(message: str) -> None:
    """桥内非致命错误一律写 stderr，绝不冒泡成 500。"""
    print(f"[ai-bridge] {message}", file=sys.stderr, flush=True)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _role_for(session: str, who: int) -> str:
    """策略 role：按座位区分 seed 流，再按会话区分（同一 session 才可复现）。"""
    role = f"{_WEB_ROLE}{who}"
    if session and session != DEFAULT_SESSION:
        role = f"{role}@{session}"
    return role


class Policy:
    """占位策略：合法动作里随机/贪心挑一个（不主动 declareEnd，除非只剩它）。"""

    def __init__(self, strategy: str = "greedy", seed: int | None = None) -> None:
        self.strategy = strategy
        self.name = f"placeholder:{strategy}"
        self.rng = random.Random(seed)
        # 决策日志用：占位策略本身即兜底，恒为 False。
        self.last_fallback = False
        self.last_error: str | None = None

    def decide(
        self, who: int, method: str, value: dict[str, Any], latest_state: Any = None
    ) -> dict[str, Any]:
        """把 RPC 方法分派到具体的占位决策（`who`/`latest_state` 占位不用）。"""
        if method == "rerollDice":
            return self.reroll_dice(value)
        if method == "switchHands":
            return self.switch_hands(value)
        if method == "chooseActive":
            return self.choose_active(value)
        if method == "selectCard":
            return self.select_card(value)
        if method == "action":
            return self.action(value)
        raise ValueError(f"unknown rpc method: {method!r}")

    def reroll_dice(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"diceToReroll": []}

    def switch_hands(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"removedHandIds": []}

    def choose_active(self, payload: dict[str, Any]) -> dict[str, Any]:
        cands = payload.get("candidateIds") or [0]
        return {"activeCharacterId": cands[0]}

    def select_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        cands = payload.get("candidateDefinitionIds") or [0]
        return {"selectedDefinitionId": cands[0]}

    def action(self, payload: dict[str, Any]) -> dict[str, Any]:
        actions = payload.get("action") or []
        if not actions:
            return {"chosenActionIndex": 0, "usedDice": []}
        valid = [i for i, a in enumerate(actions) if a.get("validity") == VALID]
        if not valid:
            idx = 0
        else:
            # 优先非 declareEnd，让牌局更“活”
            non_end = [
                i for i in valid
                if (actions[i].get("action") or {}).get("$case") != "declareEnd"
            ]
            pool = non_end or valid
            if self.strategy == "random":
                idx = self.rng.choice(pool)
            else:
                idx = pool[0]
        used = list(actions[idx].get("autoSelectedDice") or [])
        return {"chosenActionIndex": idx, "usedDice": used}


class RepoPolicy:
    """模型桥：把决策路由到 `envs.policy.build_policy` 产出的真实策略。

    只在 `--policy` 下构造。重型依赖（reps/train/torch/gitcg.proto）都在
    首次决策时延迟导入，默认占位路径保持纯标准库零依赖。任何一步失败
    （无 notification、proto 转换失败、`choose()` 抛错、选中非法动作码）
    都回退到占位策略并写 stderr 警告，保证 handler 永不 500/挂死。

    `last_fallback` / `last_error` 供决策日志标注「这一步是不是模型真的说的」。
    """

    def __init__(
        self,
        spec: str,
        *,
        seed: int | None,
        role: str,
        fallback: Policy,
        policy: Any = None,
    ) -> None:
        self.spec = spec
        self.name = spec
        self.seed = 0 if seed is None else int(seed)
        self.role = role
        self._fallback = fallback
        # Pre-built policy (era/expert base, NPC policy): skip spec-string loading.
        self._policy: Any = policy
        self._load_error: str | None = None
        self._step_index = 0
        self.last_fallback = False
        self.last_error: str | None = None

    def _ensure_policy(self) -> Any:
        """延迟构造仓库策略；失败只记一次错误并静默回退。"""
        if self._policy is None and self._load_error is None:
            try:
                from envs.policy import build_policy

                self._policy = build_policy(self.spec, seed=self.seed, role=self.role)
                _warn(f"策略已加载：{self.spec!r} (seed={self.seed}, role={self.role})")
            except Exception as exc:  # noqa: BLE001
                self._load_error = f"{type(exc).__name__}: {exc}"
                _warn(f"加载策略 {self.spec!r} 失败，全程回退占位策略：{self._load_error}")
        return self._policy

    def decide(
        self, who: int, method: str, value: dict[str, Any], latest_state: Any = None
    ) -> dict[str, Any]:
        self.last_fallback = False
        self.last_error = None
        try:
            policy = self._ensure_policy()
            if policy is None:
                raise RuntimeError(self._load_error or "policy unavailable")
            if latest_state is None:
                raise RuntimeError("尚未收到任何 notification.state")
            resp = _repo_decide(policy, who, method, value, latest_state, self._step_index)
            self._step_index += 1
            return resp
        except Exception as exc:  # noqa: BLE001
            _warn(f"策略决策失败（{method}），回退占位策略：{type(exc).__name__}: {exc}")
            resp = self._fallback.decide(who, method, value, latest_state)
            # 占位策略会把 last_fallback 复位，所以必须在它之后再置位。
            self.last_fallback = True
            self.last_error = f"{type(exc).__name__}: {exc}"
            return resp


class LLMPolicy:
    """LLM 技能智能体（`skillloop.agent.SkillAgentPolicy`）的延迟构造包装。

    与 `RepoPolicy` 一样，重型依赖与模型客户端都在**首次决策**时才构造。策略本体
    由 `skillloop.run.build_policy` 构造（关卡 TaskSpec、RichStateRenderer、
    era/expert base、未过滤库、候选 cap 16、原顺序），网页桥只负责把 meta.stage
    与 runner 配置传进去。构造失败时回退到该牌组的 `resolve_base_policy`（era /
    expert；未知牌组才用 heuristic），而不是每步都掉到占位策略。

    `last_decision` 是这一步的审计信息（used_skills/system_hash/llm_calls）；
    `library_info` 是该座位实际加载的库描述，供 decisions.jsonl 记录。
    """

    def __init__(
        self,
        selection: str,
        deck: str | None,
        *,
        seed: int | None,
        role: str,
        fallback: Policy,
        spec: str | None = None,
        client_factory: Any = None,
        stage: str | None = None,
        session: str | None = None,
        eval_config: WebEvalConfig | None = None,
    ) -> None:
        self.selection = selection
        self.deck = deck
        self.spec = spec or (
            f"llm:{selection}#deck={deck}" if deck else f"llm:{selection}"
        )
        self.name = _llm_policy_name(selection)
        self.seed = 0 if seed is None else int(seed)
        self.role = role
        self.stage = stage
        self.session = session
        self.eval_config = eval_config or WebEvalConfig()
        self._fallback = fallback
        self._client_factory = client_factory
        self._policy: Any = None
        self._load_error: str | None = None
        self._scripted: RepoPolicy | None = None
        self._step_index = 0
        self.base_spec: str | None = None
        self.library_info = resolve_llm_library(selection)
        self.parity = False
        self.group: str | None = None
        self.library_root: str | None = None
        self.eval_deck: str | None = None
        self.loadout_path: str | None = None
        self.last_fallback = False
        self.last_error: str | None = None
        self.last_decision: dict[str, Any] | None = None

    # -- construction --------------------------------------------------------
    def _scripted_spec(self) -> str:
        """机械兜底 spec 的标签：era/expert 牌组用自家脚本，未知牌组用 heuristic。"""
        if not self.deck:
            return "heuristic"
        try:
            from skillloop.era import is_era

            if is_era(self.deck):
                return f"expert:{self.deck}"
            from envs.decks import expert_decks_by_name

            if self.deck in expert_decks_by_name():
                return f"expert:{self.deck}"
        except Exception as exc:  # noqa: BLE001
            _warn(f"无法解析牌组 {self.deck!r}，LLM 兜底改用 heuristic：{exc}")
        return "heuristic"

    def _ensure_policy(self) -> Any:
        if self._policy is None and self._load_error is None:
            try:
                from common.llm import build_llm_client

                client = (
                    self._client_factory()
                    if self._client_factory is not None
                    else build_llm_client()
                )
                if client is None:
                    raise RuntimeError("LLM 不可用（.env 未配置 key/base_url/model）")
                policy, info = build_web_llm_policy(
                    self.selection,
                    self.deck,
                    stage_slug=self.stage,
                    config=self.eval_config,
                    client=client,
                    seed=self.seed,
                )
                self._policy = policy
                self.base_spec = getattr(getattr(policy, "base", None), "name", None)
                self.parity = bool(info.get("parity"))
                self.group = info.get("group")
                self.library_root = info.get("library_root")
                self.eval_deck = info.get("deck")
                self.loadout_path = info.get("loadout")
                library_root = info.get("library_root")
                if library_root:
                    resolved = _abs_path(library_root)
                    if (resolved / "library.json").is_file():
                        self.library_info = _library_info(resolved, self.selection)
                _warn(
                    f"LLM 策略已加载：{self.name}（stage={info.get('stage')}, "
                    f"parity={self.parity}, group={self.group}, base={self.base_spec}, "
                    f"deck={self.eval_deck}, library={info.get('library_root')}）"
                )
            except Exception as exc:  # noqa: BLE001
                self._load_error = f"{type(exc).__name__}: {exc}"
                _warn(f"构造 LLM 策略 {self.spec!r} 失败，回退脚本策略：{self._load_error}")
        return self._policy

    def _scripted_policy(self) -> RepoPolicy:
        if self._scripted is None:
            base = None
            if self.deck:
                try:
                    from skillloop.era import resolve_base_policy

                    base = resolve_base_policy(self.deck, seed=0)
                except Exception as exc:  # noqa: BLE001
                    _warn(f"无法构造牌组 {self.deck!r} 的 era/expert 兜底：{exc}")
            if base is None:
                from envs.policy import heuristic_policy

                base = heuristic_policy()
            self._scripted = RepoPolicy(
                self._scripted_spec(),
                seed=self.seed,
                role=self.role,
                fallback=self._fallback,
                policy=base,
            )
        return self._scripted

    # -- decision ------------------------------------------------------------
    def decide(
        self, who: int, method: str, value: dict[str, Any], latest_state: Any = None
    ) -> dict[str, Any]:
        self.last_fallback = False
        self.last_error = None
        self.last_decision = None
        try:
            policy = self._ensure_policy()
            if policy is None:
                raise RuntimeError(self._load_error or "policy unavailable")
            if latest_state is None:
                raise RuntimeError("尚未收到任何 notification.state")
            from common.llm import set_trace_context

            set_trace_context(
                session=self.session,
                who=who,
                step=self._step_index,
                method=method,
                stage=self.stage,
            )
            before = len(getattr(policy, "decisions", []) or [])
            resp = _repo_decide(
                policy,
                who,
                method,
                value,
                latest_state,
                self._step_index,
            )
            self._step_index += 1
            decisions = getattr(policy, "decisions", []) or []
            if len(decisions) > before:
                decision = decisions[-1]
                self.last_decision = {
                    "used_skills": list(getattr(decision, "used", ()) or ()),
                    "system_hash": getattr(decision, "system_hash", "") or "",
                    "llm_calls": int(getattr(decision, "http_calls", 0) or 0),
                    "request": getattr(decision, "request", "") or method,
                    "n_candidates": getattr(decision, "n_candidates", None),
                    # 模型选中的**候选下标**（不是引擎 chosenActionIndex），可空。
                    "model_choice": getattr(decision, "choice", None),
                    # 本次模型调用的解析错误（empty_response / unparseable / ...）。
                    "error": getattr(decision, "error", None),
                    "llm_called": True,
                    "reasoning": getattr(decision, "reasoning", "") or "",
                    "finish_reason": getattr(decision, "finish_reason", None),
                }
            else:
                self.last_decision = {
                    "used_skills": [],
                    "system_hash": getattr(policy, "_system_hash", "") or "",
                    "llm_calls": 0,
                    "request": method,
                    "n_candidates": None,
                    "model_choice": None,
                    "error": None,
                    # 这一步由 expert/占位策略直接回答，没有调用模型。
                    "llm_called": False,
                    "reasoning": "",
                    "finish_reason": None,
                }
            return resp
        except Exception as exc:  # noqa: BLE001
            _warn(f"LLM 策略决策失败（{method}），回退脚本策略：{type(exc).__name__}: {exc}")
            resp = self._scripted_policy().decide(who, method, value, latest_state)
            self.last_fallback = True
            self.last_error = f"{type(exc).__name__}: {exc}"
            return resp


def _llm_policy_name(selection: str) -> str:
    info = resolve_llm_library(selection)
    if info is None or selection in ("none", "llm_empty"):
        return f"llm:{selection}"
    return f"llm:{selection}[{info['resolved']}]"


def _flatten_ts_oneofs(descriptor: Any, obj: Any) -> Any:
    """把 protobuf-ts 的 JSON 转成 Python ``json_format.ParseDict`` 期望的形式。

    浏览器（``@gi-tcg/core``，protobuf-ts）把 oneof 序列化成
    ``{"<oneofName>": {"$case": "<member>", "value": {...}}}``，而 Python 的
    ``json_format`` 要求直接设 oneof 成员字段（``{"<memberJsonName": {...}}``）。
    同一消息两边表示不同，直接 ParseDict 会报
    ``Message type "Action" has no field named "action"``。

    本函数按消息描述符递归摊平：遇到 ``{oneofName: {$case, value}}`` 就改写成
    ``{memberJsonName: value}``，其余字段原样递归。
    """
    if not isinstance(obj, dict):
        return obj

    fields_by_name = {f.name: f for f in descriptor.fields}
    fields_by_json = {f.json_name: f for f in descriptor.fields}
    oneofs = {o.name: o for o in descriptor.oneofs}

    out: dict[str, Any] = {}
    for key, val in obj.items():
        oneof = oneofs.get(key)
        if oneof is not None and isinstance(val, dict) and "$case" in val:
            member = val["$case"]
            field = next(
                (f for f in oneof.fields if f.name == member or f.json_name == member),
                None,
            )
            if field is not None:
                out[field.json_name] = _flatten_ts_oneofs(
                    field.message_type, val.get("value") or {}
                )
                continue
        field = fields_by_name.get(key) or fields_by_json.get(key)
        if field is not None and field.message_type is not None:
            if field.is_repeated:
                out[key] = (
                    [_flatten_ts_oneofs(field.message_type, v) for v in val]
                    if isinstance(val, list)
                    else val
                )
            else:
                out[key] = _flatten_ts_oneofs(field.message_type, val)
        else:
            out[key] = val
    return out


def _repo_decide(
    policy: Any,
    who: int,
    method: str,
    value: dict[str, Any],
    latest_state: Any,
    step_index: int,
) -> dict[str, Any]:
    """用仓库策略跑一次决策：request JSON -> proto -> 决策上下文 -> 响应 JSON。"""
    from google.protobuf import json_format

    from gitcg.proto import rpc_pb2

    from reps.action_adapter import build_decision_context

    field = _METHOD_TO_FIELD[method]
    # 浏览器 payload 是 protobuf-ts JSON（oneof 为 {$case, value} 嵌套），
    # 先摊平再 ParseDict；否则 Action 等带 oneof 的消息会解析失败。
    flat = _flatten_ts_oneofs(rpc_pb2.Request.DESCRIPTOR, {field: value or {}})
    request_msg = rpc_pb2.Request()
    json_format.ParseDict(flat, request_msg)
    request = getattr(request_msg, field)

    decision_type = _decision_type_map()[method]
    view = _state_snapshot(latest_state)
    built = build_decision_context(
        acting_player=int(who),
        request_type=decision_type,
        request=request,
        full_state=view,
        player_view=view,
        full_state_json=None,
        step_index=int(step_index),
    )
    code = int(policy.choose(built))
    payload = built.payload_by_low_level_code.get(code)
    if payload is None:
        # 策略选了非法/未映射动作码（含 -1）——交给上层回退。
        raise RuntimeError(f"策略返回非法动作码：{code}")
    return _response_from_payload(method, payload)


def _state_snapshot(latest_state: Any) -> Any:
    """把最近一次 notification.state（protobuf-JSON）解成掩码 StateSnapshot。

    硬规则 C10：桥只有掩码后的 `notification.state`，绝不构造全量/god state。
    """
    from google.protobuf import json_format

    from gitcg.proto import state_pb2
    from reps.snapshot import snapshot_notification

    if isinstance(latest_state, str):
        payload: Any = json.loads(latest_state)
    else:
        payload = latest_state
    state = state_pb2.State()
    json_format.ParseDict(payload, state)
    return snapshot_notification(_NotificationLike(state))


class _NotificationLike:
    """`snapshot_notification` 只需要 `.state`，用它包一层 proto State。"""

    __slots__ = ("state",)

    def __init__(self, state: Any) -> None:
        self.state = state


# 方法名 -> reps.schema.DecisionType（延迟导入：默认路径不 import reps）。
def _decision_type_map() -> dict[str, Any]:
    from reps.schema import DecisionType

    return {
        "rerollDice": DecisionType.REROLL_DICE,
        "switchHands": DecisionType.SWITCH_HANDS,
        "chooseActive": DecisionType.CHOOSE_ACTIVE,
        "action": DecisionType.ACTION,
        "selectCard": DecisionType.SELECT_CARD,
    }


def _response_from_payload(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """仓库 payload 是 snake_case，浏览器引擎要 camelCase——逐方法精确映射。"""
    if method == "action":
        return {
            "chosenActionIndex": int(payload.get("chosen_action_index", 0)),
            "usedDice": [int(d) for d in payload.get("used_dice", ())],
        }
    if method == "chooseActive":
        return {"activeCharacterId": int(payload["active_character_id"])}
    if method == "selectCard":
        return {"selectedDefinitionId": int(payload["selected_definition_id"])}
    if method == "rerollDice":
        return {"diceToReroll": [int(d) for d in payload.get("dice_to_reroll", ())]}
    if method == "switchHands":
        return {"removedHandIds": [int(d) for d in payload.get("removed_hand_ids", ())]}
    raise ValueError(f"unknown rpc method: {method!r}")


# --------------------------------------------------------------------------- #
# C4：逐决策日志落盘
# --------------------------------------------------------------------------- #
class DecisionLog:
    """每局一个目录的 JSONL 决策日志（`data/web/<UTC时间戳>_<session>/`）。

    目录内容：`session.json`（开局元数据）、`decisions.jsonl`（逐决策 + game_end）、
    `summary.json`（关账汇总）、`game_log.json`（浏览器上传的引擎日志，含人类决策）。

    写盘失败只写 stderr，绝不打断对局——日志是调试辅助，不是强依赖。
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self._dirs: dict[str, Path] = {}
        self._names: set[str] = set()
        self._lock = threading.Lock()

    # -- 目录 ------------------------------------------------------------- #
    def open(self, session: str, meta: dict[str, Any] | None = None) -> Path | None:
        """给会话分配目录并写 `session.json`；已存在则只合并记录，不重置。"""
        with self._lock:
            path = self._dirs.get(session)
            if path is None:
                path = self._allocate(session)
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    _warn(f"决策日志目录创建失败（{path}），本局不落盘：{exc}")
                    return None
                self._dirs[session] = path
                fresh = True
            else:
                fresh = False
        if fresh:
            payload = {"session": session, "opened_at": _now_iso()}
            if meta:
                payload.update(meta)
            self._write_json(path / "session.json", payload)
        return path

    def _allocate(self, session: str) -> Path:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        base = f"{stamp}_{_safe_session(session)}"
        name = base
        index = 1
        while name in self._names or (self.root / name).exists():
            name = f"{base}_{index}"
            index += 1
        self._names.add(name)
        return self.root / name

    def path(self, session: str) -> Path | None:
        with self._lock:
            return self._dirs.get(session)

    # -- 写入 ------------------------------------------------------------- #
    def append(self, session: str, record: dict[str, Any]) -> None:
        path = self.path(session)
        if path is None:
            return
        try:
            with (path / "decisions.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                fh.flush()
        except OSError as exc:
            _warn(f"决策日志写入失败（{session}）：{exc}")

    def write_json(self, session: str, name: str, payload: Any) -> None:
        path = self.path(session)
        if path is None:
            return
        self._write_json(path / name, payload)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            _warn(f"日志写入失败（{path}）：{exc}")


def _safe_session(session: str) -> str:
    """会话 id 变成目录名片段：只留安全字符并截断。"""
    out = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session)
    return (out or "session")[:24]


# --------------------------------------------------------------------------- #
# 只读调试：最近 N 条 LLM 决策（技能引用）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def _skill_names_by_library(path: str) -> dict[str, str]:
    """``skill_id -> skill_name`` for one library, cached across polls."""
    try:
        from skillloop.library import SkillLibrary

        library = SkillLibrary.load(_abs_path(path))
    except Exception as exc:  # noqa: BLE001
        _warn(f"读取技能库 {path!r} 失败（/ai-decisions 回退 id）：{type(exc).__name__}: {exc}")
        return {}
    return {
        skill_id: str(getattr(skill, "skill_name", "") or skill_id)
        for skill_id, skill in library.skills.items()
    }


def _used_skill_names(record: dict[str, Any]) -> list[str]:
    used = [str(item) for item in (record.get("used_skills") or ())]
    if not used:
        return []
    library = record.get("library")
    mapping: dict[str, str] = {}
    if isinstance(library, dict) and library.get("path"):
        mapping = _skill_names_by_library(str(library["path"]))
    return [mapping.get(skill_id, skill_id) for skill_id in used]


def _read_ai_decisions(
    directory: str | os.PathLike[str] | None, who: int, limit: int
) -> list[dict[str, Any]]:
    """Tail of ``decisions.jsonl`` filtered by seat (read-only, best effort)."""
    if not directory:
        return []
    path = Path(directory) / "decisions.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # 正在追加的半行：跳过，不 500
                if not isinstance(record, dict) or record.get("type") != "decision":
                    continue
                try:
                    if int(record.get("who")) != int(who):
                        continue
                except (TypeError, ValueError):
                    continue
                rows.append(
                    {
                        "step": record.get("step"),
                        "method": record.get("method"),
                        "policy": record.get("policy"),
                        "model_choice": record.get("model_choice"),
                        "n_candidates": record.get("n_candidates"),
                        "used_skills": [
                            str(item) for item in (record.get("used_skills") or ())
                        ],
                        "used_skill_names": _used_skill_names(record),
                        "library": record.get("library"),
                        "fallback": bool(record.get("fallback", False)),
                        "llm_called": record.get("llm_called"),
                        "llm_error": record.get("llm_error"),
                        "reasoning": record.get("reasoning") or "",
                        "finish_reason": record.get("finish_reason"),
                        "error": record.get("error"),
                        "latency_ms": record.get("latency_ms"),
                        "system_hash": record.get("system_hash") or "",
                        "request": record.get("request") or record.get("method"),
                    }
                )
    except OSError as exc:  # noqa: BLE001
        _warn(f"读取决策日志失败（{path}）：{exc}")
        return []
    return rows[-limit:]


# --------------------------------------------------------------------------- #
# 状态：按 (session, who) 隔离
# --------------------------------------------------------------------------- #
class Seat:
    """一个 (会话, 座位) 的决策上下文：掩码视图、通知序号、策略实例、计数。"""

    __slots__ = (
        "session",
        "who",
        "policy",
        "latest_state",
        "latest_mutation",
        "state_seq",
        "applied_seq",
        "decisions",
        "run",
    )

    def __init__(self, session: str, who: int, policy: Any) -> None:
        self.session = session
        self.who = who
        self.policy = policy
        self.latest_state: Any = None
        self.latest_mutation: Any = None
        self.state_seq = 0        # 已应用的 /notify 条数（本局）
        self.applied_seq = -1     # 浏览器侧最后一个已应用的 seq（-1 = 未带 seq）
        self.decisions = 0        # 本座位决策条数（决策日志的 step）
        self.run = 1              # 该 (会话, 座位) 上第几局（续局会 +1）
        # 过期通知/回退计数记在会话记录里（summary 与 /results 读它）。


class State:
    """桥的全局状态：会话表 + 座位表 + 日志。

    * `policy` 保留为 seat0/未分座位时的策略（兼容直接构造的旧用法）；
    * `policy_factory(session, who)` 才是生产路径：每个座位、每个会话各拿一个
      独立实例（独立 RNG 与 step 计数，T1）；
    * `log` 为 None 时不落盘（单测默认路径）。
    """

    def __init__(
        self,
        policy: Any,
        *,
        policy_factory: Any = None,
        log: DecisionLog | None = None,
        ai_deck: dict[str, Any] | None = None,
        seat_specs: dict[tuple[str, int], str] | None = None,
        base_spec: str | None = None,
        llm_client_factory: Any = None,
        library_choice: str | None = None,
        eval_config: WebEvalConfig | None = None,
        session_stages: dict[str, str] | None = None,
    ) -> None:
        self.policy = policy
        self.policy_factory = policy_factory
        self.log = log
        self.ai_deck = ai_deck
        # 每 (会话, 座位) 的策略 spec 覆盖：由 /choose-deck + /session 派生，
        # 只在 policy_factory 读它时生效（座位一旦建好就不再改）。
        self.seat_specs = seat_specs if seat_specs is not None else {}
        self.base_spec = base_spec
        # 严格对齐 skillloop.run 的 runner 配置；座位工厂与 /npc-decks 共用。
        self.eval_config = eval_config or WebEvalConfig()
        # /session 的 meta.stage 按会话记下来，供座位工厂构造 LLMPolicy 时读取。
        shared_stages = session_stages
        if shared_stages is None:
            shared_stages = getattr(policy_factory, "session_stages", None)
        self.session_stages = shared_stages if shared_stages is not None else {}
        # LLM 库选择可被 `POST /ai-library` 改（只影响后续新会话）。base spec
        # 自带的选择是初始值；测试可注入 client_factory 避免真实网络。
        parsed = parse_llm_spec(base_spec)
        self.library_choice = (
            library_choice if library_choice is not None
            else (parsed[0] if parsed is not None else None)
        )
        self.llm_client_factory = llm_client_factory
        # LLM 不可用的原因按会话记（/session 的 ai 字段与 /ai?session= 读它）。
        self._session_ai_errors: dict[str, str | None] = {}
        # C1 反选结果，按会话记（/session 校验 AI 座位牌组时优先于 --ai-deck）。
        self.picks: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.rpc_counts: dict[str, int] = {}
        self.notify_count = 0
        # 兼容字段：最近一次被应用的 /notify state（/state 不带参数时返回它）。
        self.latest_state: Any = None
        self.latest_mutation: Any = None
        self._seats: dict[tuple[str, int], Seat] = {}
        self._sessions: dict[str, dict[str, Any]] = {}

    # -- 座位 ------------------------------------------------------------- #
    def _policy_for(self, session: str, who: int) -> Any:
        if self.policy_factory is not None:
            return self.policy_factory(session, who)
        return self.policy  # 直接构造 State(policy) 的旧用法（单测）：座位共用实例

    def seat(self, session: str, who: int) -> Seat:
        key = (session, who)
        with self.lock:
            seat = self._seats.get(key)
            if seat is None:
                seat = Seat(session, who, self._policy_for(session, who))
                self._seats[key] = seat
            return seat

    def peek_seat(self, session: str, who: int) -> Seat | None:
        with self.lock:
            return self._seats.get((session, who))

    # -- 会话 ------------------------------------------------------------- #
    @staticmethod
    def _new_session_info(session: str) -> dict[str, Any]:
        return {
            "session": session,
            "opened_at": _now_iso(),
            "closed_at": None,
            "winner": None,
            "close_source": None,
            "rounds": None,
            "dir": None,
            "runs": 1,
            "aborted": False,
            "abort_reason": None,
            "deck_mismatch": False,
            "protocol_mismatch": False,
            "stage": None,
            "seat_specs": {},
            "notify_0": 0,
            "notify_1": 0,
            "decisions_0": 0,
            "decisions_1": 0,
            "fallbacks_0": 0,
            "fallbacks_1": 0,
            "stale_drops": 0,
            "game_log": False,
            "ai": None,
            "meta": {},
        }

    def open_session(
        self,
        session: str,
        meta: dict[str, Any] | None = None,
        *,
        restart: bool = False,
    ) -> dict[str, Any]:
        """开局：建会话记录、开日志目录；重复调用只合并元数据、不重置计数。

        `restart=True` 是「从此处继续」——同一个 session 上开新的一局（浏览器会
        新建 io，seq 从 1 重新数）。此时：

        * 把该会话每个座位的 `applied_seq` 顶到上一局的末尾之后
          （`_RESUME_SEQ_GAP`），并把它作为新局的 **seq 基线** 由响应 `seq_base`
          交给浏览器（浏览器从基线 +1 接着数）——否则新局通知会全被判成过期，
          决策吃的是被终止那一局的残局状态；
        * 清掉上一局的 `latest_state`（那多半是被终止那一刻的分支状态）；
        * `runs` 计数 +1 并落一条 `run_start` 记录，日志里能分辨两段。
        """
        with self.lock:
            info = self._sessions.get(session)
            if info is None:
                info = self._new_session_info(session)
                self._sessions[session] = info
            if meta:
                info["meta"].update(meta)
                stage = meta.get("stage")
                if isinstance(stage, str) and stage:
                    self.session_stages[session] = stage
                    info["stage"] = stage
            if restart:
                info["runs"] = int(info.get("runs", 1)) + 1
                for (sess, _who), seat in self._seats.items():
                    if sess != session:
                        continue
                    seat.run = info["runs"]
                    seat.applied_seq += _RESUME_SEQ_GAP
                    seat.latest_state = None
                    seat.latest_mutation = None
                    seat.state_seq = 0
            snapshot = json.loads(json.dumps(info))  # 锁内深拷贝，返回给外部随便改
            runs = snapshot["runs"]
        if restart and self.log is not None:
            self.log.append(
                session,
                {
                    "t": time.time(),
                    "iso": _now_iso(),
                    "type": "run_start",
                    "session": session,
                    "run": runs,
                },
            )
        if self.log is not None:
            path = self.log.open(session, {"meta": snapshot["meta"]})
            if path is not None:
                with self.lock:
                    self._sessions[session]["dir"] = str(path)
                snapshot["dir"] = str(path)
        return snapshot

    def seq_baseline(self, session: str) -> dict[str, int]:
        """续局的 seq 基线：每个座位「上一局最后一个已应用的 seq」。

        返回的座位从 `基线 + 1` 继续数。没有座位（全新会话）就不在里面，
        浏览器对缺失座位按 0 起算。
        """
        with self.lock:
            return {
                str(who): seat.applied_seq
                for (sess, who), seat in self._seats.items()
                if sess == session
            }

    def update_session_info(self, session: str, **fields: Any) -> dict[str, Any] | None:
        """把字段写进**真实**会话记录（不是 `open_session` 返回的深拷贝快照）。"""
        with self.lock:
            info = self._sessions.get(session)
            if info is None:
                return None
            info.update(fields)
            return json.loads(json.dumps(info))

    def session_info(self, session: str) -> dict[str, Any] | None:
        with self.lock:
            info = self._sessions.get(session)
            return json.loads(json.dumps(info)) if info is not None else None

    def has_session(self, session: str) -> bool:
        with self.lock:
            return session in self._sessions

    def close_session(
        self,
        session: str,
        winner: Any = None,
        source: str = "notify",
        rounds: Any = None,
        *,
        aborted: bool = False,
        abort_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """终局关账：写 game_end + summary.json；已关账则返回 None（幂等）。

        `aborted=True` 表示这局不是真的分出胜负（典型：AI 桥 IO 故障，引擎按
        `gotWinner(flip(who))` 置了 winner 并结束）。此时 winner 一律记 None、
        `close_source` 记 `io_error`、会话标 `aborted`。已经因为 `/notify` 里看到
        winner 而自动关过账的会话允许**改判**成故障归档（`amended` 记录一条），
        否则一次桥故障就会被当成一场真实胜负。
        """
        if aborted:
            winner = None
            source = "io_error"
        with self.lock:
            info = self._sessions.get(session)
            if info is None:
                # 没开过会话就直接收到终局（老客户端）：补一条极简记录。
                info = self._new_session_info(session)
                self._sessions[session] = info
            amended = False
            if info["closed_at"] is not None:
                if not aborted or info.get("aborted"):
                    return None
                # 已经（多半是 /notify 自动）关过账：把「胜负」改判成故障归档。
                amended = True
            info["closed_at"] = _now_iso()
            info["winner"] = winner
            info["close_source"] = source
            info["rounds"] = rounds
            if aborted:
                info["aborted"] = True
                info["abort_reason"] = abort_reason
            snapshot = json.loads(json.dumps(info))
        record = {
            "t": time.time(),
            "iso": _now_iso(),
            "type": "game_end",
            "session": session,
            "winner": winner,
            "source": source,
            "rounds": rounds,
            "aborted": bool(snapshot.get("aborted")),
            "abort_reason": snapshot.get("abort_reason"),
            "amended": amended,
            "decisions": [snapshot["decisions_0"], snapshot["decisions_1"]],
            "notify": [snapshot["notify_0"], snapshot["notify_1"]],
            "fallbacks": [snapshot["fallbacks_0"], snapshot["fallbacks_1"]],
            "stale_drops": snapshot["stale_drops"],
        }
        if self.log is not None:
            self.log.append(session, record)
            self.log.write_json(session, "summary.json", snapshot)
        return snapshot

    def sessions(self) -> list[dict[str, Any]]:
        with self.lock:
            return [json.loads(json.dumps(v)) for v in self._sessions.values()]

    def total_stale_drops(self) -> int:
        with self.lock:
            return sum(v["stale_drops"] for v in self._sessions.values())

    # -- C1 反选 / 按座位派生策略 ------------------------------------------ #
    def record_pick(self, session: str, pick: dict[str, Any]) -> None:
        with self.lock:
            self.picks[session] = dict(pick)

    def expected_deck(self, session: str) -> dict[str, Any] | None:
        """AI 座位「应该用哪套牌」：反选结果优先，其次 --ai-deck。"""
        with self.lock:
            pick = self.picks.get(session)
        if pick:
            return pick
        return self.ai_deck

    def assign_seat_specs(self, session: str, meta: dict[str, Any]) -> dict[int, str]:
        """开局时按各座位**实际**牌组派生策略 spec。

        * base 是 `expert:*`：牌组是池内 20 套之一 → 派它自己的脚本
          `expert:<slug>`；否则改派 deck-agnostic 的 `heuristic`（硬套别人的脚本
          每步都会找不到自家卡而回退）。
        * base 是 `llm:*`：**只有 `meta.ai_seat` 那一侧**用
          `llm:<library>#deck=<pool_slug或空>`，其它座位仍按实际牌组派
          `expert:<slug>` / `heuristic`（人类执恰好一方）。LLM 不可用时 AI 座位
          也退回脚本，并把原因记进会话（不让每步都走 fallback）。
        * 其它 base（`neural:`/`heuristic`/占位等用户显式选的）一律不动。

        座位已经建好（局中调用）就不改——策略实例是按 (会话, 座位) 一次性建的。
        """
        llm_spec = parse_llm_spec(self.base_spec)
        if llm_spec is None:
            return self._assign_scripted_specs(session, meta)
        return self._assign_llm_specs(session, meta, llm_spec[0])

    def _assign_scripted_specs(
        self, session: str, meta: dict[str, Any]
    ) -> dict[int, str]:
        if not self.base_spec or not self.base_spec.startswith("expert:"):
            return {}
        decks = meta.get("decks")
        if not isinstance(decks, dict):
            return {}
        from web.counterpick import pool_slug_for_deck

        assigned: dict[int, str] = {}
        for key, deck in decks.items():
            try:
                who = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(deck, dict) or self.peek_seat(session, who) is not None:
                continue
            slug = pool_slug_for_deck(deck)
            spec = f"expert:{slug}" if slug else _NON_POOL_DECK_SPEC
            if spec == self.base_spec:
                continue  # 与 base 相同，没必要覆盖
            with self.lock:
                self.seat_specs[(session, who)] = spec
            assigned[who] = spec
            if slug is None:
                _warn(
                    f"会话 {session} 座位 {who} 的牌组不在评测池 20 套里，"
                    f"已把策略从 {self.base_spec!r} 改派成 {_NON_POOL_DECK_SPEC!r}"
                    "（deck-specific 脚本只认自家牌组）。"
                )
        return assigned

    def _assign_llm_specs(
        self, session: str, meta: dict[str, Any], base_selection: str
    ) -> dict[int, str]:
        from web.counterpick import pool_slug_for_deck

        decks = meta.get("decks")
        if not isinstance(decks, dict):
            decks = {}
        ai_seat = _ai_seat_of(meta)
        available, error, _model = self.llm_probe()
        with self.lock:
            self._session_ai_errors[session] = None if available else error
        if not available:
            _warn(
                f"会话 {session} 的 LLM 不可用（{error}），"
                "AI 座位改用脚本策略（不会逐步回退占位）。"
            )
        selection = self.library_choice_value() or base_selection

        stage_slug = meta.get("stage") if isinstance(meta.get("stage"), str) else None
        stage_obj = _stage_or_none(stage_slug) if stage_slug else None
        self_play = bool(meta.get("ai_self_play"))

        targets: set[int] = set()
        for key in decks:
            try:
                targets.add(int(key))
            except (TypeError, ValueError):
                continue
        targets.add(ai_seat)

        assigned: dict[int, str] = {}
        for who in sorted(targets):
            if self.peek_seat(session, who) is not None:
                continue
            deck = decks.get(str(who)) or decks.get(who)
            slug = pool_slug_for_deck(deck) if isinstance(deck, dict) else None
            if who == ai_seat and available:
                spec = (
                    f"llm:{selection}#deck={slug}" if slug else f"llm:{selection}"
                )
            elif who != ai_seat and self_play:
                # 自对弈的 NPC 座位：真实 NpcPolicy（与 RunSpec.rotation 一致）；
                # 缺 stage 时退回 deck-agnostic 的 heuristic。
                spec = f"npc:{stage_obj.name}" if stage_obj is not None else _NON_POOL_DECK_SPEC
            else:
                spec = f"expert:{slug}" if slug else _NON_POOL_DECK_SPEC
            if spec == self.base_spec:
                continue  # 与 base 相同，没必要覆盖
            with self.lock:
                self.seat_specs[(session, who)] = spec
            assigned[who] = spec
            if slug is None and not (who == ai_seat and available) and not spec.startswith("npc:"):
                _warn(
                    f"会话 {session} 座位 {who} 的牌组不在评测池 20 套里，"
                    f"已把策略从 {self.base_spec!r} 改派成 {spec!r}"
                    "（deck-specific 脚本只认自家牌组）。"
                )
        return assigned

    # -- LLM 状态 / /ai ------------------------------------------------------ #
    def llm_probe(self) -> tuple[bool, str | None, str | None]:
        """探测 LLM 可用性：返回 `(available, error, model)`，不发任何网络请求。"""
        try:
            from common.llm import build_llm_client, load_llm_config
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}", None
        try:
            client = (
                self.llm_client_factory()
                if self.llm_client_factory is not None
                else build_llm_client()
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}", None
        model = getattr(getattr(client, "config", None), "model", None)
        if client is None:
            try:
                config = load_llm_config()
                model = config.model if config is not None else model
            except Exception:  # noqa: BLE001
                pass
            return False, "LLM 不可用（.env 未配置 key/base_url/model）", model
        return True, None, model if isinstance(model, str) else None

    def library_choice_value(self) -> str | None:
        with self.lock:
            return self.library_choice

    def set_library_choice(self, selection: str) -> None:
        with self.lock:
            self.library_choice = selection

    def current_selection(self) -> str | None:
        with self.lock:
            selection = self.library_choice
        if selection:
            return selection
        parsed = parse_llm_spec(self.base_spec)
        return parsed[0] if parsed is not None else None

    def stage_of(self, session: str | None) -> str | None:
        if not session:
            return None
        with self.lock:
            stage = self.session_stages.get(session)
            if stage:
                return stage
            info = self._sessions.get(session) or {}
        meta = info.get("meta") or {}
        value = meta.get("stage")
        return value if isinstance(value, str) and value else None

    def eval_target(self, stage_slug: str | None, *, selection: str | None = None) -> dict[str, Any]:
        return _eval_target(
            stage_slug,
            selection=selection if selection is not None else self.current_selection(),
            config=self.eval_config,
        )

    def reported_ai_deck(self, session: str | None) -> Any:
        if not session:
            return None
        info = self.session_info(session)
        if info is None:
            return None
        meta = info.get("meta") or {}
        decks = meta.get("decks")
        if not isinstance(decks, dict):
            return None
        seat = _ai_seat_of(meta)
        return decks.get(str(seat)) or decks.get(seat)

    def ai_info(self, session: str | None = None) -> dict[str, Any]:
        """序列化 AI 信息（`/ai`、`/session` 共用）。

        带 session 时报**该会话实际用到**的座位 spec（LLM 不可用时就是回落后的
        `expert:*`/`heuristic`）；不带 session 时报全局设置（当前库选择）。
        """
        info = self.session_info(session) if session is not None else None
        parsed = parse_llm_spec(self.base_spec)
        spec = self.base_spec
        session_error: str | None = None
        if parsed is not None:
            if info is not None:
                seat_specs = info.get("seat_specs") or {}
                spec = seat_specs.get(str(_ai_seat_of(info.get("meta") or {}))) or spec
                with self.lock:
                    session_error = self._session_ai_errors.get(session)
            else:
                selection = self.library_choice_value() or parsed[0]
                spec = f"llm:{selection}"
        return self._ai_payload(spec, session_error, session=session)

    def _ai_payload(
        self,
        spec: str | None,
        session_error: str | None = None,
        *,
        session: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_llm_spec(spec)
        if parsed is not None:
            mode = "llm"
        elif spec:
            mode = "scripted"
        else:
            mode = "placeholder"
        available, error, model = self.llm_probe()
        selection = parsed[0] if parsed is not None else None
        if selection is None:
            # LLM 不可用时会话 spec 已回落到脚本，但 /ai 仍应报告配置的库/group。
            base_parsed = parse_llm_spec(self.base_spec)
            selection = base_parsed[0] if base_parsed is not None else None
        library = resolve_llm_library(selection) if selection is not None else None
        if selection in _LOADOUT_GROUPS and self.eval_config.library_override:
            override = _abs_path(self.eval_config.library_override)
            if (override / "library.json").is_file():
                library = _library_info(override, selection)

        stage = self.stage_of(session)
        target = self.eval_target(stage, selection=selection)
        parity = bool(target.get("parity"))
        if parity and target.get("deck"):
            matched = _deck_matches(target["deck"], self.reported_ai_deck(session))
            if matched is not None:
                parity = matched

        group = _selection_group(selection) if selection is not None else None
        library_root: str | None = None
        if group is not None:
            try:
                library_root = _display_path(
                    resolve_library_root(selection, self.eval_config.library_override)
                )
            except Exception:  # noqa: BLE001
                library_root = None
        card_version = self.eval_config.card_version or target.get("card_version")
        return {
            "mode": mode,
            "spec": spec,
            "llm_available": available,
            "llm_error": session_error or error,
            "model": model,
            "library": library,
            "options": llm_library_options(),
            "protocol": _PROTOCOL_NAME,
            "stage": target.get("stage") or stage,
            "parity": parity,
            "group": group,
            "library_root": library_root,
            "library_resolved": library.get("resolved") if library else None,
            "card_version": card_version,
            "scratchpad": bool(self.eval_config.scratchpad),
            "reasoning": self.eval_config.reasoning,
            "rich_render": bool(self.eval_config.rich_render),
            "eval_deck": target.get("deck"),
            "loadout": target.get("loadout"),
        }


def _notify_rounds(state_json: Any) -> Any:
    """从掩码 state 里读回合数（终局关账时用）。"""
    if isinstance(state_json, dict):
        value = state_json.get("roundNumber")
        if isinstance(value, int):
            return value
    return None


def make_handler(state: State, quiet: bool, cors_origin: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "giTcgAiBridge/0.2"

        # ---- helpers ---------------------------------------------------
        def _path(self) -> str:
            """去掉 query string 的路径，用于精确路由匹配。"""
            return urlparse(self.path).path

        def _query(self) -> dict[str, list[str]]:
            return parse_qs(urlparse(self.path).query)

        def _cors(self) -> None:
            # 默认仅回显本机 Origin（localhost/127.0.0.1/::1）；--cors-origin 可显式覆盖。
            origin = self.headers.get("Origin")
            if cors_origin is not None:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Vary", "Origin")
            elif origin and _is_local_origin(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            # 让浏览器缓存预检结果（否则每个跨域请求都配一个 OPTIONS）。
            self.send_header("Access-Control-Max-Age", str(_CORS_MAX_AGE_SECONDS))

        def _json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if not quiet:
                super().log_message(fmt, *args)

        def _read_json(self) -> Any:
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        @staticmethod
        def _session_of(body: dict[str, Any]) -> str:
            session = body.get("session")
            return session if isinstance(session, str) and session else DEFAULT_SESSION

        def _dispatch_rpc(self, session: str, who: int, request: dict[str, Any]) -> dict[str, Any]:
            req = (request or {}).get("request") or {}
            method = req.get("$case")
            value = req.get("value") or {}
            if method not in _METHOD_TO_FIELD:
                raise ValueError(f"unknown rpc method: {method!r}")
            if not state.has_session(session):
                # 只发 rpc 不发 notify 的客户端（例如关掉 forwardNotifications）
                # 也要有会话目录，否则决策日志无处可写。
                state.open_session(session)
            seat = state.seat(session, who)
            with state.lock:
                state.rpc_counts[method] = state.rpc_counts.get(method, 0) + 1
                info = state._sessions.get(session)
                if info is not None:
                    info["decisions_%d" % who] += 1
            # 模型策略/占位策略共用 decide()；自身保证不抛异常。
            started = time.perf_counter()
            resp_value = seat.policy.decide(who, method, value, seat.latest_state)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            fallback = bool(getattr(seat.policy, "last_fallback", False))
            if fallback:
                with state.lock:
                    info = state._sessions.get(session)
                    if info is not None:
                        info["fallbacks_%d" % who] += 1
            with state.lock:
                step = seat.decisions
                seat.decisions += 1
                notify_seq = seat.applied_seq
                state_seq = seat.state_seq
                run = seat.run
            # LLM 策略另带审计字段（used_skills/system_hash/llm_calls/library）；
            # 其它策略没有 last_decision，字段不变，保持旧日志格式兼容。
            llm_extra: dict[str, Any] = {}
            decision_info = getattr(seat.policy, "last_decision", None)
            if isinstance(decision_info, dict):
                llm_extra["used_skills"] = list(decision_info.get("used_skills") or ())
                llm_extra["system_hash"] = decision_info.get("system_hash") or ""
                llm_extra["llm_calls"] = int(decision_info.get("llm_calls") or 0)
                llm_extra["request"] = decision_info.get("request") or method
                llm_extra["n_candidates"] = decision_info.get("n_candidates")
                llm_extra["model_choice"] = decision_info.get("model_choice")
                llm_extra["llm_called"] = bool(decision_info.get("llm_called"))
                llm_extra["llm_error"] = decision_info.get("error")
                llm_extra["reasoning"] = str(decision_info.get("reasoning") or "")
                llm_extra["finish_reason"] = decision_info.get("finish_reason")
                llm_extra["library"] = getattr(seat.policy, "library_info", None)
            self._log_decision(
                session=session,
                who=who,
                method=method,
                step=step,
                run=run,
                state_seq=state_seq,
                notify_seq=None if notify_seq < 0 else notify_seq,
                latency_ms=round(elapsed_ms, 3),
                policy=getattr(seat.policy, "name", "unknown"),
                fallback=fallback,
                error=getattr(seat.policy, "last_error", None),
                response=resp_value,
                **llm_extra,
            )
            return {"response": {"$case": method, "value": resp_value}}

        def _log_decision(self, **fields: Any) -> None:
            if state.log is None:
                return
            record: dict[str, Any] = {"t": time.time(), "iso": _now_iso(), "type": "decision"}
            record.update(fields)
            state.log.append(fields.get("session", DEFAULT_SESSION), record)

        def _open_session(self, body: dict[str, Any]) -> dict[str, Any]:
            session = self._session_of(body)
            meta = body.get("meta")
            if not isinstance(meta, dict):
                meta = {}
            # 续局（「从此处继续」）：同一个 session 开新的一局，seq 要从基线接着数。
            restart = bool(body.get("resume"))
            info = state.open_session(session, meta, restart=restart)
            stage = meta.get("stage") if isinstance(meta.get("stage"), str) else None
            mismatch = self._check_deck(session, meta)
            protocol_mismatch = self._check_protocol_deck(session, stage, meta)
            mismatch = mismatch or protocol_mismatch
            specs = state.assign_seat_specs(session, meta)
            seat_specs = {str(who): spec for who, spec in sorted(specs.items())}
            # 记进**真实**会话记录（不能只改 open_session 返回的快照，
            # 否则 summary.json / /results / session.json 里看不到）。
            stored = state.update_session_info(
                session,
                deck_mismatch=mismatch,
                protocol_mismatch=protocol_mismatch,
                stage=stage,
                seat_specs=seat_specs,
            )
            # 该会话**实际**使用的 AI 信息（LLM 不可用时这里会如实显示回落后的
            # 脚本 spec 与原因），复用 /ai 的序列化函数。
            ai_info = state.ai_info(session)
            stored = state.update_session_info(session, ai=ai_info) or stored
            info["deck_mismatch"] = mismatch
            info["protocol_mismatch"] = protocol_mismatch
            info["stage"] = stage
            info["seat_specs"] = seat_specs
            info["ai"] = ai_info
            info["runs"] = (stored or info).get("runs", 1)
            info["seq_base"] = state.seq_baseline(session)
            if state.log is not None and stored is not None:
                state.log.write_json(session, "session.json", stored)
            if specs:
                print(
                    f"[ai-bridge] 会话 {session} 按实际牌组派生座位策略："
                    + ", ".join(f"seat{who}={spec}" for who, spec in sorted(specs.items())),
                    file=sys.stderr,
                    flush=True,
                )
            if restart:
                print(
                    f"[ai-bridge] 会话 {session} 续局（run {info['runs']}）："
                    f"seq_base={info['seq_base']}，已清掉上一局掩码状态",
                    file=sys.stderr,
                    flush=True,
                )
            return info

        def _check_deck(self, session: str, meta: dict[str, Any]) -> bool:
            """T3+C1：浏览器上报的实际牌组 vs 期望牌组（反选结果优先，否则 --ai-deck）。"""
            with state.lock:
                has_pick = session in state.picks
            expected = state.expected_deck(session)
            if not expected:
                return False
            label = "反选结果" if has_pick else "--ai-deck"
            decks = meta.get("decks")
            if not isinstance(decks, dict):
                return False
            seat_no = int(meta.get("ai_seat", 0))
            actual = decks.get(str(seat_no)) or decks.get(seat_no)
            if isinstance(actual, str) or not isinstance(actual, dict):
                return False
            expected_chars = list(expected.get("characters") or [])
            expected_cards = sorted(int(c) for c in expected.get("cards") or [])
            actual_chars = [int(c) for c in actual.get("characters") or []]
            actual_cards = sorted(int(c) for c in actual.get("cards") or [])
            if actual_chars == expected_chars and actual_cards == expected_cards:
                return False
            declared = expected.get("slug") or "(未给 slug)"
            _warn(
                f"会话 {session} 的 AI 座位牌组与 {label} {declared!r} 不一致：实际 "
                f"characters={actual_chars} cards={len(actual_cards)}，期望 "
                f"characters={expected_chars} cards={len(expected_cards)}。"
                "脚本专家规则是按自家牌组写的，牌组不符会静默变蠢。"
            )
            return True

        def _check_protocol_deck(
            self, session: str, stage: str | None, meta: dict[str, Any]
        ) -> bool:
            """AI 座位上报的牌组是否与 ``/npc-decks.eval.deck`` 一致。

            这是严格对齐 ``skillloop.run`` 的额外校验：评价协议里玩家侧牌组是
            固定的（loadout > stage_decks），网页上报的 AI 牌组必须一致，否则
            本局只能算非评价协议。
            """
            if not stage:
                _warn(
                    f"会话 {session} 的 meta.stage 缺失：本局按非评价协议"
                    "（parity=false）运行；网页桥仍按 skillloop.run 构策略。"
                )
                return False
            target = state.eval_target(stage)
            deck = target.get("deck")
            if not deck:
                return False
            matched = _deck_matches(deck, state.reported_ai_deck(session))
            if matched is None or matched:
                return False
            _warn(
                f"会话 {session} 的 AI 座位牌组与评价协议关卡 {stage!r} 的 "
                f"eval.deck {deck!r} 不一致：本局按非评价协议（parity=false）运行。"
            )
            return True

        # ---- routes ----------------------------------------------------
        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.send_header("content-length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self._path()
            if path == "/health":
                stale = state.total_stale_drops()  # 自己拿锁，别嵌进下面的 with
                with state.lock:
                    self._json(
                        {
                            "ok": True,
                            "policy": getattr(state.policy, "name", "unknown"),
                            "rpc": dict(state.rpc_counts),
                            "notify": state.notify_count,
                            "sessions": len(state._sessions),
                            "stale_notify": stale,
                            "ai_deck": (state.ai_deck or {}).get("slug"),
                            "log_dir": str(state.log.root) if state.log else None,
                        }
                    )
            elif path == "/state":
                query = self._query()
                if "session" in query or "who" in query:
                    session = query.get("session", [DEFAULT_SESSION])[0]
                    who = int(query.get("who", ["0"])[0])
                    seat = state.peek_seat(session, who)
                    self._json(
                        {
                            "state": seat.latest_state if seat is not None else None,
                            "session": session,
                            "who": who,
                            "state_seq": seat.state_seq if seat is not None else 0,
                        }
                    )
                else:
                    with state.lock:
                        self._json({"state": state.latest_state})
            elif path == "/ai-deck":
                deck = state.ai_deck or {}
                self._json(
                    {
                        "slug": deck.get("slug"),
                        "characters": list(deck.get("characters") or []),
                        "cards": list(deck.get("cards") or []),
                    }
                )
            elif path == "/npc-decks":
                try:
                    from web.npc_assets import npc_custom_data
                    from web.npc_decks import npc_decks

                    payload = npc_decks()
                    # 每个 NPC 阵容带上评价协议（stage deck / card pool / rules），
                    # 前端用它把 AI 牌组自动回填成与 skillloop.run 一致的值。
                    payload["decks"] = [
                        {**deck, "eval": state.eval_target(deck.get("slug"))}
                        for deck in payload.get("decks", ())
                    ]
                    # C3：NPC 的名字/技能描述/骰子费用来自 .gts，镜像里没有——
                    # 前端用它构造 `new AssetsManager({customData})` 传给 createClient，
                    # 详情面板与棋盘才会显示（否则 getData 会拿到镜像的 404 JSON）。
                    payload["assets"] = npc_custom_data()
                    self._json(payload)
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, status=500)
            elif path == "/results":
                self._json(
                    {
                        "log_dir": str(state.log.root) if state.log else None,
                        "sessions": state.sessions(),
                    }
                )
            elif path == "/ai":
                session = self._query().get("session", [None])[0]
                self._json(state.ai_info(session))
            elif path == "/ai-decisions":
                query = self._query()
                session = query.get("session", [DEFAULT_SESSION])[0]
                try:
                    who = int(query.get("who", [""])[0])
                except (TypeError, ValueError):
                    self._json({"error": "who must be 0 or 1"}, status=400)
                    return
                if who not in (0, 1):
                    self._json({"error": "who must be 0 or 1"}, status=400)
                    return
                try:
                    limit = int(query.get("limit", ["20"])[0])
                except (TypeError, ValueError):
                    self._json({"error": "limit must be an integer in 1..100"}, status=400)
                    return
                if not 1 <= limit <= 100:
                    self._json({"error": "limit must be an integer in 1..100"}, status=400)
                    return
                info = state.session_info(session)
                directory = info.get("dir") if info else None
                self._json(
                    {
                        "session": session,
                        "who": who,
                        "decisions": _read_ai_decisions(directory, who, limit),
                    }
                )
            elif path == "/ai-prompt-log":
                query = self._query()
                session = query.get("session", [None])[0]
                who_raw = query.get("who", [None])[0]
                try:
                    who = int(who_raw) if who_raw not in (None, "") else None
                except (TypeError, ValueError):
                    self._json({"error": "who must be 0 or 1"}, status=400)
                    return
                fmt = (query.get("format", ["md"])[0] or "md").lower()
                trace_path = os.environ.get("GITCG_LLM_TRACE")
                if not trace_path:
                    self._json(
                        {"error": "GITCG_LLM_TRACE 未开启；重启时设置 LLM_TRACE=..."
                         "（serve_web_demo.sh 默认 data/web/llm_trace.jsonl）"},
                        status=404,
                    )
                    return
                from skillloop.prompt_log import filter_trace, load_trace, render_markdown

                rows = filter_trace(
                    load_trace(trace_path), session=session, who=who
                )
                if fmt == "jsonl":
                    body_text = "".join(
                        json.dumps(row, ensure_ascii=False) + "\n" for row in rows
                    )
                    content_type = "application/x-ndjson; charset=utf-8"
                    filename = f"prompt-{session or 'all'}.jsonl"
                else:
                    body_text = render_markdown(
                        rows,
                        title=f"Prompt log · session={session or 'all'}",
                        max_reasoning=6000,
                    )
                    content_type = "text/markdown; charset=utf-8"
                    filename = f"prompt-{session or 'all'}.md"
                body = body_text.encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.send_header(
                    "content-disposition", f'attachment; filename="{filename}"'
                )
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(
                    {
                        "service": "gi-tcg-ai-bridge",
                        "endpoints": [
                            "/rpc",
                            "/notify",
                            "/session",
                            "/choose-deck",
                            "/game-end",
                            "/game-log",
                            "/health",
                            "/state",
                            "/ai-deck",
                            "/ai",
                            "/ai-decisions",
                            "/ai-prompt-log",
                            "/ai-library",
                            "/npc-decks",
                            "/results",
                        ],
                    }
                )

        def do_POST(self) -> None:  # noqa: N802
            try:
                body = self._read_json()
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"bad json: {e}"}, status=400)
                return
            path = self._path()
            if path == "/rpc":
                try:
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                    who = int(body.get("who", 0))
                    out = self._dispatch_rpc(
                        self._session_of(body), who, body.get("request") or {}
                    )
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, status=500)
                    return
                self._json(out)
            elif path == "/notify":
                # 与 /rpc 一致：任何解析/形状错误都返回错误响应，不让连接静默断开。
                try:
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                    note = body.get("notification")
                    if note is not None and not isinstance(note, dict):
                        raise ValueError("notification must be a JSON object")
                    note = note or {}
                    out = self._apply_notification(
                        self._session_of(body),
                        int(body.get("who", 0)),
                        body.get("seq"),
                        note,
                    )
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, status=400)
                    return
                self._json(out)
            elif path == "/session":
                try:
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                    out = self._open_session(body)
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, status=400)
                    return
                self._json(out)
            elif path == "/choose-deck":
                # C1 反选：人的牌组 -> 应对牌组（D19：对位矩阵 + decklab 固定规则）。
                try:
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                    session = self._session_of(body)
                    from web.counterpick import counter_pick, deck_view_from_json

                    human = deck_view_from_json(body.get("deck"))
                    pick = counter_pick(human)
                    state.record_pick(session, pick.as_dict())
                    payload = pick.as_dict()
                    payload.update(
                        {
                            "session": session,
                            "base_policy": state.base_spec,
                            # 只有 deck-specific 的 expert:* / 按座位派生的 llm:*
                            # 会被重新分配；neural:/heuristic/占位保持不动（牌组照用）。
                            "policy_applied": bool(
                                state.base_spec
                                and state.base_spec.startswith(("expert:", "llm:"))
                            ),
                        }
                    )
                    print(
                        f"[ai-bridge] 会话 {session} 反选：{pick.slug} "
                        f"({pick.method}, proxy={pick.proxy})",
                        file=sys.stderr,
                        flush=True,
                    )
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, status=400)
                    return
                self._json(payload)
            elif path == "/ai-library":
                # 只改**后续新会话**的库选择；已建好的座位不受影响。
                try:
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                    selection = body.get("library")
                    if selection not in LLM_LIBRARY_CHOICES:
                        raise ValueError(
                            f"library must be one of {list(LLM_LIBRARY_CHOICES)}"
                        )
                    library = resolve_llm_library(selection)
                    if selection not in ("none",) and library is None:
                        raise ValueError(f"技能库 {selection!r} 未解析到目录")
                    state.set_library_choice(selection)
                    out = {
                        "ok": True,
                        "library": library,
                        "note": "下一局生效",
                    }
                    print(
                        f"[ai-bridge] LLM 技能库选择已更新：{selection} "
                        f"({library['resolved'] if library else None})，下一局生效",
                        file=sys.stderr,
                        flush=True,
                    )
                except ValueError as e:
                    self._json({"error": str(e)}, status=400)
                    return
                self._json(out)
            elif path == "/game-end":
                try:
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                    session = self._session_of(body)
                    state.open_session(session)  # 会话可能没开过
                    rounds = body.get("rounds")
                    # aborted：这局不是真的分出胜负（典型：AI 桥 IO 故障，
                    # 引擎按 gotWinner(flip(who)) 置了 winner）。
                    aborted = bool(body.get("aborted"))
                    reason = body.get("abort_reason")
                    info = state.close_session(
                        session,
                        winner=body.get("winner"),
                        source="io_error" if aborted else "game-end",
                        rounds=rounds if isinstance(rounds, int) else None,
                        aborted=aborted,
                        abort_reason=reason if isinstance(reason, str) else None,
                    )
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, status=400)
                    return
                self._json(
                    {
                        "ok": True,
                        "closed": info is not None,
                        "aborted": bool(info.get("aborted")) if info else aborted,
                        "session": session,
                    }
                )
            elif path == "/game-log":
                try:
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                    session = self._session_of(body)
                    info = state.open_session(session)
                    if state.log is not None:
                        state.log.write_json(session, "game_log.json", body.get("log"))
                    with state.lock:
                        if session in state._sessions:
                            state._sessions[session]["game_log"] = True
                    out = {"ok": True, "dir": info.get("dir")}
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, status=400)
                    return
                self._json(out)
            else:
                self._json({"error": "not found"}, status=404)

        # ---- 通知应用（T1 座位隔离 + T2 序号护栏） ------------------------
        def _apply_notification(
            self,
            session: str,
            who: int,
            seq: Any,
            note: dict[str, Any],
        ) -> dict[str, Any]:
            seat = state.seat(session, who)
            try:
                seq_value = int(seq) if seq is not None else None
            except (TypeError, ValueError):
                seq_value = None

            applied = True
            stale = False
            with state.lock:
                if seq_value is not None and seq_value <= seat.applied_seq:
                    # T2：过期通知（乱序/重发）——保留更新的状态，只记一笔。
                    stale = True
                    applied = False
                    info = state._sessions.get(session)
                    if info is not None:
                        info["stale_drops"] += 1
                else:
                    seat.latest_state = note.get("state")
                    seat.latest_mutation = note.get("mutation")
                    seat.state_seq += 1
                    if seq_value is not None:
                        seat.applied_seq = seq_value
                    state.notify_count += 1
                    state.latest_state = seat.latest_state
                    state.latest_mutation = seat.latest_mutation
                    info = state._sessions.get(session)
                    if info is not None:
                        info["notify_%d" % who] += 1

            if stale:
                return {"applied": False, "reason": "stale_seq", "seq": seq_value}

            # 终局检测：state.winner 被置位（或 phase 变成 gameEnd）就关账。
            if not state.has_session(session):
                state.open_session(session)
            winner = _winner_of(seat.latest_state)
            if winner is not None or _is_end_phase(seat.latest_state):
                state.close_session(
                    session,
                    winner=winner,
                    source="notify",
                    rounds=_notify_rounds(seat.latest_state),
                )
            return {"applied": True, "seq": seq_value, "state_seq": seat.state_seq}

    return Handler


def _winner_of(state_json: Any) -> Any:
    """掩码 state 里读 winner；没设置（未终局）返回 None。"""
    if not isinstance(state_json, dict):
        return None
    winner = state_json.get("winner")
    return winner if winner in (0, 1, "0", "1") else None


def _is_end_phase(state_json: Any) -> bool:
    if not isinstance(state_json, dict):
        return False
    return state_json.get("phase") in _END_PHASES


def eval_config_from_args(args: argparse.Namespace) -> WebEvalConfig:
    """Read the runner-alignment knobs from CLI args (tolerant of test namespaces)."""
    return WebEvalConfig(
        stage_decks_root=getattr(args, "stage_decks", None) or DEFAULT_STAGE_DECKS,
        loadout_root=getattr(args, "loadout_root", None),
        library_override=getattr(args, "library", None),
        card_version=getattr(args, "card_version", None),
        rotation=getattr(args, "rotation", None),
        stage_rules=bool(getattr(args, "stage_rules", True)),
        scratchpad=bool(getattr(args, "scratchpad", False)),
        reasoning=str(getattr(args, "reasoning", None) or "none"),
        rich_render=not bool(getattr(args, "compact_render", False)),
    )


def build_policy_from_args(
    args: argparse.Namespace,
    *,
    seat_specs: dict[tuple[str, int], str] | None = None,
    client_factory: Any = None,
    session_stages: dict[str, str] | None = None,
    eval_config: WebEvalConfig | None = None,
) -> tuple[Any, Any]:
    """按命令行参数组装策略：返回 `(seat0 策略, policy_factory)`。

    `policy_factory(session, who)` 每次返回**新实例**（T1：座位/会话隔离，
    独立 RNG 与 step 计数）。有 `--policy` 走模型桥，否则纯占位；
    `--policy llm:<library>` 走与 `skillloop.run.build_policy` 一致的 LLM 策略；
    `npc:<stage>` 是自对弈时非 AI 座位的真实 NPC 策略。

    `seat_specs` 是 C1 的按座位 spec 覆盖（`/choose-deck` + `/session` 派生，
    见 `State.assign_seat_specs`）；没有覆盖时用 `args.policy`。
    `session_stages` 让工厂读到 `/session` 的 meta.stage（LLM 策略必须知道关卡）。
    `client_factory` 是测试注入入口（默认 `common.llm.build_llm_client`）。
    """
    from common.seeding import derive_seed

    config = eval_config or eval_config_from_args(args)
    stages = session_stages if session_stages is not None else {}

    def factory(session: str, who: int) -> Any:
        role = _role_for(session, who)
        base_seed = None if args.seed is None else int(args.seed)
        seat_seed = None if base_seed is None else derive_seed(base_seed, role)
        placeholder = Policy(strategy=args.strategy, seed=seat_seed)
        spec = None if seat_specs is None else seat_specs.get((session, who))
        spec = spec or args.policy
        if not spec:
            return placeholder
        llm_spec = parse_llm_spec(spec)
        if llm_spec is not None:
            return LLMPolicy(
                llm_spec[0],
                llm_spec[1],
                seed=base_seed,
                role=role,
                fallback=placeholder,
                spec=spec,
                client_factory=client_factory,
                stage=stages.get(session),
                session=session,
                eval_config=config,
            )
        if spec.startswith("npc:"):
            stage_slug = spec.split(":", 1)[1]
            try:
                from npc.policy import npc_policy_for_stage

                stage_obj = _stage_or_none(stage_slug)
                if stage_obj is None:
                    raise KeyError(f"unknown NPC stage: {stage_slug!r}")
                policy = npc_policy_for_stage(stage_obj, rotation=config.rotation)
            except Exception as exc:  # noqa: BLE001
                _warn(f"构造 NPC 策略 {spec!r} 失败，改用 heuristic：{type(exc).__name__}: {exc}")
                return RepoPolicy(
                    spec=_NON_POOL_DECK_SPEC,
                    seed=base_seed,
                    role=role,
                    fallback=placeholder,
                )
            return RepoPolicy(
                spec=spec,
                seed=base_seed,
                role=role,
                fallback=placeholder,
                policy=policy,
            )
        return RepoPolicy(
            spec=spec,
            seed=base_seed,
            role=role,
            fallback=placeholder,
        )

    # Let ``State`` share the same stage map even when the caller did not pass one.
    factory.session_stages = stages
    return factory(DEFAULT_SESSION, 0), factory


def resolve_ai_deck(args: argparse.Namespace) -> dict[str, Any] | None:
    """T3：解析 AI 座位牌组（`--ai-deck`，或 `--policy expert:<slug>` 隐含）。"""
    slug = args.ai_deck
    policy_spec = args.policy or ""
    if not slug and policy_spec.startswith("expert:"):
        slug = policy_spec.split(":", 1)[1]
    llm_spec = parse_llm_spec(policy_spec)
    if not slug and llm_spec is not None and llm_spec[1]:
        slug = llm_spec[1]
    if not slug:
        return None
    if policy_spec.startswith("expert:") and policy_spec.split(":", 1)[1] != slug:
        _warn(
            f"--policy {policy_spec!r} 与 --ai-deck {slug!r} 不一致："
            "脚本专家规则按自家牌组写，建议两者取同一套。"
        )
    try:
        from envs.decks import deck_spec  # 延迟导入：默认零依赖路径不 import gitcg

        spec = deck_spec(slug)
    except Exception as exc:  # noqa: BLE001
        _warn(f"解析 AI 牌组 {slug!r} 失败，/ai-deck 将返回空：{type(exc).__name__}: {exc}")
        return None
    return {
        "slug": spec.name,
        "characters": [int(c) for c in spec.characters],
        "cards": [int(c) for c in spec.cards],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="GI-TCG browser<->Python AI bridge")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--strategy", choices=["greedy", "random"], default="random")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument(
        "--policy",
        default=None,
        help=(
            "策略 spec；留空则用 --strategy 占位策略。"
            " 脚本：expert:<slug> | heuristic | legal_random | random；"
            " LLM 技能智能体：llm:<library>，library ∈ "
            "none / llm_empty（data/skills/rules_v1，仅游戏规则，17 条 rule）"
            " | static（data/skills/static_v1，17 rule + 10 strategy）"
            " | dynamic（data/skills/dyn_v4，17 rule + 23 strategy）。"
            " loadout 只对 static/dynamic 生效。"
            " 例：--policy llm:dynamic。LLM 不可用时 AI 座位自动退回脚本策略；"
            " 需要仓库根 .env 里的 LLM key/base_url/model。"
        ),
    )
    ap.add_argument(
        "--ai-deck",
        default=None,
        help="AI 座位应使用的牌组 slug（T3 牌组对齐）；留空则从 --policy expert:<slug> 推断",
    )
    ap.add_argument(
        "--stage-decks",
        default=DEFAULT_STAGE_DECKS,
        help="每关固定牌组映射（默认 configs/stage_decks.json，与 skillloop.run 一致）",
    )
    ap.add_argument(
        "--loadout-root",
        default=None,
        help="冻结的技能 loadout 根目录（reports/loadouts；static/dynamic 生效）",
    )
    ap.add_argument(
        "--library",
        default=None,
        help="覆盖 static/dynamic 解析到的技能库目录（传给 skillloop.run.RunSpec）",
    )
    ap.add_argument(
        "--card-version",
        default=None,
        help="固定引擎卡池（如 v3.3.0）；默认用 eval.card_version/引擎默认",
    )
    ap.add_argument(
        "--rotation",
        default=None,
        help="自对弈 NPC 座位的 rotation（与 skillloop.run --rotation 一致）",
    )
    ap.add_argument(
        "--scratchpad",
        action="store_true",
        help="局内记录板（与 skillloop.run --scratchpad 一致），默认关；仅用于 A/B",
    )
    ap.add_argument(
        "--reasoning",
        default="none",
        help="AI 决策的 reasoning effort（none/low/medium/high；非 none 时 max_tokens=12288）",
    )
    ap.add_argument(
        "--compact-render",
        action="store_true",
        help="旧紧凑 prompt（仅复现历史）；默认使用完整 prompt",
    )
    ap.add_argument(
        "--no-stage-rules",
        dest="stage_rules",
        action="store_false",
        help="不在 /npc-decks.eval 里返回关卡特殊规则",
    )
    ap.add_argument(
        "--log-dir",
        default=DEFAULT_LOG_DIR,
        help=f"逐决策日志根目录（默认 {DEFAULT_LOG_DIR}，在 gitignore 内）",
    )
    ap.add_argument("--no-log", action="store_true", help="关闭逐决策日志落盘")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--cors-origin",
        default=None,
        help="显式设置 Access-Control-Allow-Origin（如 '*'）；默认仅放行本机 Origin",
    )
    args = ap.parse_args()

    seat_specs: dict[tuple[str, int], str] = {}
    session_stages: dict[str, str] = {}
    eval_config = eval_config_from_args(args)
    seat0_policy, factory = build_policy_from_args(
        args,
        seat_specs=seat_specs,
        session_stages=session_stages,
        eval_config=eval_config,
    )
    ai_deck = resolve_ai_deck(args)
    log = None if args.no_log else DecisionLog(args.log_dir)
    state = State(
        seat0_policy,
        policy_factory=factory,
        log=log,
        ai_deck=ai_deck,
        seat_specs=seat_specs,
        base_spec=args.policy,
        eval_config=eval_config,
        session_stages=session_stages,
    )
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(state, args.quiet, args.cors_origin)
    )
    print(
        f"[ai-bridge] listening on http://{args.host}:{args.port} "
        f"(policy={seat0_policy.name}, seed={args.seed}, "
        f"ai_deck={ai_deck['slug'] if ai_deck else None}, "
        f"log={'off' if log is None else log.root}, "
        f"cors={'local-only' if args.cors_origin is None else args.cors_origin})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
