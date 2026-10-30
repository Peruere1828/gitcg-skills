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
#   * `--policy SPEC`（C2 模型桥）：把五种请求都路由到仓库已训练策略
#       `envs.policy.build_policy(SPEC, seed=SEED, role=…)` +
#       `reps.action_adapter.build_decision_context`。SPEC 支持
#       `neural:<checkpoint.pt>[#t=<temp>]`、`heuristic`、`legal_random`、
#       `expert:<slug>`、`random`。reps/train/torch 等依赖只在请求模型策略时
#       才延迟导入，默认路径仍是零依赖。任何加载/转换/选择失败都回退占位策略
#       （回退会写进决策日志的 `fallback` 字段）。
#
# T3 牌组对齐：`--ai-deck <slug>`（或 `--policy expert:<slug>` 隐含）声明 AI 座位
# 应该用的牌组，`/ai-deck` 把它给前端回填；开局 `/session` 里浏览器上报实际牌组，
# 与声明不一致就 stderr 告警并记进会话（`deck_mismatch` + `seat_specs`，脚本专家
# 规则是按自家牌组写的，牌组不符会静默变蠢）。
#
# C1 反选（D19）：`POST /choose-deck` 拿人的牌组 → `web.counterpick` 用对位矩阵挑
# 一套应对牌组，结果记进会话；开局 `/session` 时若 base 策略是 deck-specific 的
# `expert:*`，就按**各座位实际牌组**改派 `expert:<slug>`（人的牌组若也是池内 20 套
# 之一，自对弈时同样派它自己的脚本）。`neural:` 等用户显式选的策略绝不被覆盖。
#
# C3 NPC 卡组（D18）：`GET /npc-decks` 给出可当玩家牌组用的 NPC 阵容
# （角色取自 `configs/npc/`，牌由 decklab 池 C 填充）。
#
# 跑：
#   conda run -n gitcg python web/ai_rpc_server.py --port 8770
#   conda run -n gitcg python web/ai_rpc_server.py --port 8770 \
#       --policy "neural:data/checkpoints/curve_dfbcsa_e15_d128_n48000.pt#t=0.2"
#   conda run -n gitcg python web/ai_rpc_server.py --port 8770 \
#       --policy expert:superconduct_aggro --ai-deck superconduct_aggro
# 前端（standalone）里勾选「AI 对手（HTTP 桥）」并填 http://127.0.0.1:8770 即可。

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
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
    ) -> None:
        self.spec = spec
        self.name = spec
        self.seed = 0 if seed is None else int(seed)
        self.role = role
        self._fallback = fallback
        self._policy: Any = None
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
    ) -> None:
        self.policy = policy
        self.policy_factory = policy_factory
        self.log = log
        self.ai_deck = ai_deck
        # 每 (会话, 座位) 的策略 spec 覆盖：由 /choose-deck + /session 派生，
        # 只在 policy_factory 读它时生效（座位一旦建好就不再改）。
        self.seat_specs = seat_specs if seat_specs is not None else {}
        self.base_spec = base_spec
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
            "seat_specs": {},
            "notify_0": 0,
            "notify_1": 0,
            "decisions_0": 0,
            "decisions_1": 0,
            "fallbacks_0": 0,
            "fallbacks_1": 0,
            "stale_drops": 0,
            "game_log": False,
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
        """开局时按各座位**实际**牌组派生策略 spec（**只在 base 是 `expert:*` 时改派**）。

        * 牌组是池内 20 套之一 → 派它自己的脚本 `expert:<slug>`（deck-specific 的
          规则只认自家牌组，牌组对齐到脚本才是对的）；
        * 牌组不在池内（NPC 阵容 / 自改牌组 / decklab 牌组）→ 改派 **deck-agnostic
          的 `heuristic`**：硬套 `expert:<别的牌组>` 会让规则每步找不到自家卡而回退，
          既慢又难看（决策日志里会刷一屏 fallback）；
        * base 不是 `expert:*`（`neural:`/`heuristic`/占位等用户显式选的）一律不动。
        座位已经建好（局中调用）就不改——策略实例是按 (会话, 座位) 一次性建的。
        """
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
            mismatch = self._check_deck(session, meta)
            specs = state.assign_seat_specs(session, meta)
            seat_specs = {str(who): spec for who, spec in sorted(specs.items())}
            # 记进**真实**会话记录（不能只改 open_session 返回的快照，
            # 否则 summary.json / /results / session.json 里看不到）。
            stored = state.update_session_info(
                session, deck_mismatch=mismatch, seat_specs=seat_specs
            )
            info["deck_mismatch"] = mismatch
            info["seat_specs"] = seat_specs
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
                            # 只有 deck-specific 的 expert:* 才会被按牌组改派；
                            # neural:/heuristic/占位保持不动（牌组照用）。
                            "policy_applied": bool(
                                state.base_spec
                                and state.base_spec.startswith("expert:")
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


def build_policy_from_args(
    args: argparse.Namespace,
    *,
    seat_specs: dict[tuple[str, int], str] | None = None,
) -> tuple[Any, Any]:
    """按命令行参数组装策略：返回 `(seat0 策略, policy_factory)`。

    `policy_factory(session, who)` 每次返回**新实例**（T1：座位/会话隔离，
    独立 RNG 与 step 计数）。有 `--policy` 走模型桥，否则纯占位。

    `seat_specs` 是 C1 的按座位 spec 覆盖（`/choose-deck` + `/session` 派生，
    见 `State.assign_seat_specs`）；没有覆盖时用 `args.policy`。
    """
    from common.seeding import derive_seed

    def factory(session: str, who: int) -> Any:
        role = _role_for(session, who)
        base_seed = None if args.seed is None else int(args.seed)
        seat_seed = None if base_seed is None else derive_seed(base_seed, role)
        placeholder = Policy(strategy=args.strategy, seed=seat_seed)
        spec = None if seat_specs is None else seat_specs.get((session, who))
        spec = spec or args.policy
        if not spec:
            return placeholder
        return RepoPolicy(
            spec=spec,
            seed=base_seed,
            role=role,
            fallback=placeholder,
        )

    return factory(DEFAULT_SESSION, 0), factory


def resolve_ai_deck(args: argparse.Namespace) -> dict[str, Any] | None:
    """T3：解析 AI 座位牌组（`--ai-deck`，或 `--policy expert:<slug>` 隐含）。"""
    slug = args.ai_deck
    policy_spec = args.policy or ""
    if not slug and policy_spec.startswith("expert:"):
        slug = policy_spec.split(":", 1)[1]
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
            "模型桥策略 spec（走 envs.policy.build_policy）；留空则用 --strategy 占位策略。"
            " 例：neural:<ckpt.pt>[#t=<temp>] | heuristic | legal_random | expert:<slug> | random"
        ),
    )
    ap.add_argument(
        "--ai-deck",
        default=None,
        help="AI 座位应使用的牌组 slug（T3 牌组对齐）；留空则从 --policy expert:<slug> 推断",
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
    seat0_policy, factory = build_policy_from_args(args, seat_specs=seat_specs)
    ai_deck = resolve_ai_deck(args)
    log = None if args.no_log else DecisionLog(args.log_dir)
    state = State(
        seat0_policy,
        policy_factory=factory,
        log=log,
        ai_deck=ai_deck,
        seat_specs=seat_specs,
        base_spec=args.policy,
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
