"""C2 模型桥（web/ai_rpc_server.py）+ T1/T2/T3/C4 单测。

覆盖五块：
  * 占位策略默认路径（不依赖任何重型依赖）；
  * snake_case payload -> camelCase 响应映射、protobuf-JSON -> StateSnapshot 转换；
  * 模型桥（`--policy`）端到端决策：用一次短种子对局在运行时生成真实
    `notification.state` 与 `action` 请求（protobuf-JSON），再喂给
    `RepoPolicy.decide` / HTTP handler，验证响应形状与回退行为。
  * **T1/T2 隔离与护栏**：座位/会话各自一套掩码状态与策略实例；`/notify`
    的 `seq` 护栏丢弃过期通知（老客户端不带 seq 时行为不变）。
  * **T3/C4 调试闭环**：`/ai-deck` 与开局牌组校验、逐决策 JSONL 落盘、
    终局关账与 `/results` 汇总、日志失败不影响对局。

不依赖训练 checkpoint、不依赖浏览器；HTTP 用例绑 127.0.0.1 临时端口。
"""

from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from web import ai_rpc_server as srv

A, B = "superconduct_aggro", "natlan_battleship"

# 仓库根（本文件在 tests/ 的上一层），用于子进程直接运行 web/ai_rpc_server.py。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# 运行时确定性夹具：跑一小段真实对局，抓 notification / 五类请求的 proto-JSON
# --------------------------------------------------------------------------- #
class _CapturePolicyPlayer:
    """薄包装：正常运行 PolicyPlayer，同时抓首批 notification 与请求 JSON。"""

    def __new__(cls, *args, **kwargs):
        from envs.match import PolicyPlayer
        from google.protobuf import json_format

        class _Player(PolicyPlayer):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.requests: dict[str, dict[str, str]] = {}

            def _capture(self, method: str, request) -> None:
                if method in self.requests:
                    return
                self.requests[method] = {
                    "request_json": json_format.MessageToJson(request),
                    "notification_json": (
                        json_format.MessageToJson(self.notification)
                        if self.notification is not None
                        else ""
                    ),
                }

            def on_reroll_dice(self, request):
                self._capture("rerollDice", request)
                return super().on_reroll_dice(request)

            def on_switch_hands(self, request):
                self._capture("switchHands", request)
                return super().on_switch_hands(request)

            def on_choose_active(self, request):
                self._capture("chooseActive", request)
                return super().on_choose_active(request)

            def on_select_card(self, request):
                self._capture("selectCard", request)
                return super().on_select_card(request)

            def on_action(self, request):
                self._capture("action", request)
                return super().on_action(request)

        return _Player(*args, **kwargs)


@pytest.fixture(scope="module")
def captured() -> dict[str, dict[str, str]]:
    """确定性跑一段对局，返回 {method: {request_json, notification_json}}。"""
    from gitcg import Game, low_level

    from envs.decks import deck_spec
    from envs.match import PolicyPlayer, _build_create_param
    from envs.policy import legal_random_policy
    from reps.action_hierarchy import reset_default_hierarchical_action_codebook

    reset_default_hierarchical_action_codebook()
    game = Game(create_param=_build_create_param(deck_spec(A), deck_spec(B), seed=7))
    game.set_attr(low_level.ATTR_PLAYER_ALWAYS_OMNI_0, 1)
    game.set_attr(low_level.ATTR_PLAYER_ALWAYS_OMNI_1, 1)
    cap = _CapturePolicyPlayer(0, legal_random_policy(seed=1))
    game.set_player(0, cap)
    game.set_player(1, PolicyPlayer(1, legal_random_policy(seed=2)))
    game.start()
    steps = 0
    while game.is_running() and "action" not in cap.requests and steps < 600:
        game.step()
        steps += 1
    assert "action" in cap.requests, "未能在限定步数内抓到 action 请求"
    return cap.requests


def _payload(entry: dict[str, str]) -> dict:
    return json.loads(entry["request_json"])


def _state(entry: dict[str, str]) -> dict:
    return json.loads(entry["notification_json"])["state"]


# --------------------------------------------------------------------------- #
# 默认路径：占位策略
# --------------------------------------------------------------------------- #
def test_placeholder_name_and_action_pick():
    policy = srv.Policy(strategy="greedy", seed=1)
    assert policy.name == "placeholder:greedy"
    out = policy.decide(
        0,
        "action",
        {
            "action": [
                {"validity": 0, "action": {"$case": "declareEnd"}},
                {"validity": 0, "action": {"$case": "useSkill"}, "autoSelectedDice": [8]},
            ]
        },
    )
    # greedy 优先非 declareEnd，并使用引擎提示的 autoSelectedDice
    assert out == {"chosenActionIndex": 1, "usedDice": [8]}


def test_placeholder_other_methods():
    policy = srv.Policy(strategy="random", seed=1)
    assert policy.decide(0, "rerollDice", {}) == {"diceToReroll": []}
    assert policy.decide(0, "switchHands", {}) == {"removedHandIds": []}
    assert policy.decide(
        0, "chooseActive", {"candidateIds": [11, 22]}
    ) == {"activeCharacterId": 11}
    assert policy.decide(
        0, "selectCard", {"candidateDefinitionIds": [7, 9]}
    ) == {"selectedDefinitionId": 7}


# --------------------------------------------------------------------------- #
# snake -> camel 响应映射
# --------------------------------------------------------------------------- #
def test_response_mapping_is_exact():
    assert srv._response_from_payload(
        "rerollDice", {"dice_to_reroll": [1, 2]}
    ) == {"diceToReroll": [1, 2]}
    assert srv._response_from_payload(
        "switchHands", {"removed_hand_ids": [3]}
    ) == {"removedHandIds": [3]}
    assert srv._response_from_payload(
        "chooseActive", {"active_character_id": 5}
    ) == {"activeCharacterId": 5}
    assert srv._response_from_payload(
        "selectCard", {"selected_definition_id": 7}
    ) == {"selectedDefinitionId": 7}
    assert srv._response_from_payload(
        "action", {"chosen_action_index": 2, "used_dice": [8, 4]}
    ) == {"chosenActionIndex": 2, "usedDice": [8, 4]}


def test_unknown_method_rejected():
    with pytest.raises(ValueError):
        srv._response_from_payload("nope", {})


# --------------------------------------------------------------------------- #
# protobuf-JSON -> StateSnapshot（只用掩码后的 notification.state）
# --------------------------------------------------------------------------- #
def test_state_snapshot_from_browser_json(captured):
    view = srv._state_snapshot(_state(captured["action"]))
    assert len(view.players) == 2
    assert isinstance(view.round_number, int)
    # snapshot_state 走的是 gitcg 绑定的 store-JSON，不能吃浏览器 proto-JSON：
    # 这里固化“必须走 ParseDict -> snapshot_notification”这一事实。
    from reps.snapshot import snapshot_state

    with pytest.raises(Exception):
        snapshot_state(None, json.dumps(_state(captured["action"])))


# --------------------------------------------------------------------------- #
# 模型桥端到端
# --------------------------------------------------------------------------- #
def test_repo_decide_heuristic_action(captured):
    entry = captured["action"]
    action_count = len(_payload(entry)["action"])
    policy = srv.RepoPolicy(
        "heuristic", seed=3, role="web", fallback=srv.Policy("random", seed=1)
    )
    out = policy.decide(0, "action", _payload(entry), _state(entry))
    assert set(out) == {"chosenActionIndex", "usedDice"}
    assert 0 <= out["chosenActionIndex"] < action_count
    assert all(isinstance(d, int) for d in out["usedDice"])


@pytest.mark.parametrize("spec", ["heuristic", "legal_random", "random"])
def test_build_policy_specs_route(captured, spec):
    entry = captured["action"]
    policy = srv.RepoPolicy(
        spec, seed=5, role="web", fallback=srv.Policy("random", seed=1)
    )
    out = policy.decide(0, "action", _payload(entry), _state(entry))
    assert "chosenActionIndex" in out


def test_repo_decide_all_captured_methods(captured):
    """对局中真实出现的非 action 请求也要能过模型桥。"""
    policy = srv.RepoPolicy(
        "legal_random", seed=9, role="web", fallback=srv.Policy("greedy", seed=1)
    )
    checked = 0
    for method in ("rerollDice", "switchHands", "chooseActive", "selectCard"):
        entry = captured.get(method)
        if entry is None:
            continue
        out = policy.decide(0, method, _payload(entry), _state(entry))
        assert isinstance(out, dict) and out, f"{method} 未返回响应"
        checked += 1
    assert checked >= 1, "对局中应至少出现一个非 action 的请求"


def test_repo_decide_accepts_protobuf_ts_nested_oneof(captured, capsys):
    """浏览器（protobuf-ts）把 oneof 序列化成 ``{oneofName: {$case, value}}``，
    与 Python ``json_format`` 的扁平表示不同——直接 ParseDict 会报
    ``Message type "Action" has no field named "action"``。桥必须能吃下这种形式。

    手搓一个 protobuf-ts 风格的 ActionRequest（``Action.action`` 为嵌套 oneof）。
    用 capsys 断言没有走回退（否则单动作时占位策略也会返回 index 0，无法区分）。
    """
    entry = captured["action"]
    ts_request = {
        "action": [
            {
                "action": {"$case": "declareEnd"},
                "validity": 0,
                "isFast": True,
                "requiredCost": [],
                "autoSelectedDice": [],
                "preview": [],
            }
        ]
    }
    policy = srv.RepoPolicy(
        "legal_random", seed=1, role="web", fallback=srv.Policy("random", seed=1)
    )
    out = policy.decide(0, "action", ts_request, _state(entry))
    assert set(out) == {"chosenActionIndex", "usedDice"}
    assert out["chosenActionIndex"] == 0
    assert "回退" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 回退：加载失败 / choose 抛错 / 非法动作码 / 无通知
# --------------------------------------------------------------------------- #
def test_repo_policy_load_failure_falls_back(captured):
    entry = captured["action"]
    policy = srv.RepoPolicy(
        "definitely-not-a-spec", seed=1, role="web", fallback=srv.Policy("random", seed=1)
    )
    out = policy.decide(0, "action", _payload(entry), _state(entry))
    assert "chosenActionIndex" in out  # 未崩溃，回退占位策略


def test_repo_policy_choose_raises_falls_back(captured):
    entry = captured["action"]

    class _Boom:
        def choose(self, built):
            raise RuntimeError("boom")

    policy = srv.RepoPolicy(
        "heuristic", seed=1, role="web", fallback=srv.Policy("random", seed=1)
    )
    policy._policy = _Boom()  # 绕过加载，注入会抛错的策略
    out = policy.decide(0, "action", _payload(entry), _state(entry))
    assert "chosenActionIndex" in out


def test_repo_policy_illegal_code_falls_back(captured):
    entry = captured["action"]

    class _Illegal:
        def choose(self, built):
            return -1

    policy = srv.RepoPolicy(
        "heuristic", seed=1, role="web", fallback=srv.Policy("random", seed=1)
    )
    policy._policy = _Illegal()
    out = policy.decide(0, "action", _payload(entry), _state(entry))
    assert "chosenActionIndex" in out


def test_repo_policy_without_notification_falls_back():
    policy = srv.RepoPolicy(
        "heuristic", seed=1, role="web", fallback=srv.Policy("greedy", seed=1)
    )
    out = policy.decide(0, "action", {"action": [{"validity": 0}]}, None)
    assert "chosenActionIndex" in out


# --------------------------------------------------------------------------- #
# HTTP 路由：/health（含 policy）/ /notify / /state / /rpc 及回退
# --------------------------------------------------------------------------- #
class _Server:
    def __init__(self, policy=None, *, state=None) -> None:
        from http.server import ThreadingHTTPServer

        self.state = state if state is not None else srv.State(policy)
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), srv.make_handler(self.state, quiet=True)
        )
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, body: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"content-type": "application/json"} if payload else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            return resp.status, data
        finally:
            conn.close()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def http_server(captured):
    server = _Server(
        srv.RepoPolicy(
            "heuristic", seed=2, role="web", fallback=srv.Policy("random", seed=1)
        )
    )
    try:
        yield server
    finally:
        server.close()


def test_http_health_reports_policy(http_server):
    status, data = http_server.request("GET", "/health")
    assert status == 200
    assert data["ok"] is True
    assert data["policy"] == "heuristic"
    assert "rpc" in data and "notify" in data


def test_cors_preflight_caches_with_max_age(http_server):
    """预检响应要带 Access-Control-Max-Age，浏览器才会缓存 OPTIONS 结果，
    否则每个跨域请求都先配一次预检（/notify 一局几千条 → 请求数翻倍）。"""
    conn = http.client.HTTPConnection("127.0.0.1", http_server.port, timeout=10)
    try:
        conn.request("OPTIONS", "/notify", headers={"Origin": "http://172.26.108.168:7001"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 204
        assert resp.getheader("Access-Control-Max-Age") == str(srv._CORS_MAX_AGE_SECONDS)
        assert resp.getheader("Access-Control-Allow-Methods")
        assert resp.getheader("Access-Control-Allow-Headers")
    finally:
        conn.close()


def test_http_notify_then_state_and_rpc(http_server, captured):
    entry = captured["action"]
    notification = json.loads(entry["notification_json"])

    status, _ = http_server.request(
        "POST", "/notify", {"who": 0, "notification": notification}
    )
    assert status == 200

    status, state_resp = http_server.request("GET", "/state")
    assert status == 200 and state_resp["state"] == notification["state"]

    body = {
        "who": 0,
        "request": {"request": {"$case": "action", "value": _payload(entry)}},
    }
    status, resp = http_server.request("POST", "/rpc", body)
    assert status == 200
    assert resp["response"]["$case"] == "action"
    value = resp["response"]["value"]
    assert set(value) == {"chosenActionIndex", "usedDice"}
    assert 0 <= value["chosenActionIndex"] < len(_payload(entry)["action"])


def test_http_rpc_without_notification_falls_back():
    server = _Server(
        srv.RepoPolicy(
            "heuristic", seed=2, role="web", fallback=srv.Policy("greedy", seed=1)
        )
    )
    try:
        body = {"who": 0, "request": {"request": {"$case": "rerollDice", "value": {}}}}
        status, resp = server.request("POST", "/rpc", body)
        assert status == 200
        assert resp == {"response": {"$case": "rerollDice", "value": {"diceToReroll": []}}}
    finally:
        server.close()


def test_http_rpc_unknown_method_errors(captured):
    server = _Server(srv.Policy(strategy="random", seed=1))
    try:
        body = {"who": 0, "request": {"request": {"$case": "bogus", "value": {}}}}
        status, resp = server.request("POST", "/rpc", body)
        assert status == 500
        assert "error" in resp
    finally:
        server.close()


def test_direct_script_run_loads_model_policy(captured, tmp_path):
    """直接 `python web/ai_rpc_server.py` 运行时仓库根要能被加入 sys.path。

    回归 C2 的隐蔽 bug：直接运行脚本时 sys.path[0] 是 web/，`envs` 导入失败，
    模型策略全程静默回退占位（AI 变随机）。本用例以子进程方式直接运行脚本，
    喂入真实 notification + action 请求，确认模型策略真的被加载且未回退。
    """
    # 找空闲端口
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    err_log = tmp_path / "stderr.log"
    out_log = tmp_path / "stdout.log"
    with out_log.open("w") as out, err_log.open("w") as err:
        proc = subprocess.Popen(
            [
                sys.executable,
                os.path.join("web", "ai_rpc_server.py"),
                "--port",
                str(port),
                "--policy",
                "heuristic",
                # 桥的默认日志目录是**仓库内**的 `data/web/`（绝对路径，与 cwd 无关），
                # 不指走的话每跑一次本用例都会在仓库里新建一个日志目录。
                "--log-dir",
                str(tmp_path / "web-log"),
            ],
            cwd=REPO_ROOT,
            stdout=out,
            stderr=err,
        )
        try:
            # 等端口可连
            deadline = time.time() + 20
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.1)
            assert proc.poll() is None, "桥进程启动即退出"

            entry = captured["action"]
            notification = json.loads(entry["notification_json"])
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request(
                    "POST",
                    "/notify",
                    body=json.dumps({"who": 0, "notification": notification}),
                    headers={"content-type": "application/json"},
                )
                assert conn.getresponse().status == 200
                conn.request(
                    "POST",
                    "/rpc",
                    body=json.dumps(
                        {
                            "who": 0,
                            "request": {
                                "request": {
                                    "$case": "action",
                                    "value": json.loads(entry["request_json"]),
                                }
                            },
                        }
                    ),
                    headers={"content-type": "application/json"},
                )
                resp = conn.getresponse()
                body = json.loads(resp.read().decode())
                assert resp.status == 200
                assert set(body["response"]["value"]) == {"chosenActionIndex", "usedDice"}
            finally:
                conn.close()
            time.sleep(0.5)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    err_text = err_log.read_text()
    assert "策略已加载" in err_text
    assert "No module named 'envs'" not in err_text
    assert "回退" not in err_text


# --------------------------------------------------------------------------- #
# T1：按 (session, who) 隔离 —— 自对弈/多开标签页时状态与策略互不覆盖
# --------------------------------------------------------------------------- #
class _RecordingPolicy:
    """记录每次 decide 收到的 (who, latest_state)，用于断言座位视图隔离。"""

    name = "recording"
    last_fallback = False
    last_error = None

    def __init__(self) -> None:
        self.calls: list[tuple[int, dict | None]] = []

    def decide(self, who, method, value, latest_state=None):
        self.calls.append((who, latest_state))
        return {"chosenActionIndex": 0, "usedDice": []}


def _states_pair(captured) -> tuple[dict, dict]:
    """两条可区分的掩码 state（引擎按座位掩码，这里只改 roundNumber 以便区分）。"""
    base = _state(captured["action"])
    other = copy.deepcopy(base)
    other["roundNumber"] = int(base.get("roundNumber", 0)) + 7
    return base, other


def test_seat_state_is_isolated(captured):
    """两个座位的 /notify 各存各的掩码视图：seat1 上报后 seat0 决策仍看自己的。"""
    state_a, state_b = _states_pair(captured)
    server = _Server(_RecordingPolicy())
    try:
        assert server.request(
            "POST", "/notify",
            {"session": "g", "who": 0, "seq": 1, "notification": {"state": state_a}},
        )[0] == 200
        assert server.request(
            "POST", "/notify",
            {"session": "g", "who": 1, "seq": 1, "notification": {"state": state_b}},
        )[0] == 200

        _, out0 = server.request("GET", "/state?session=g&who=0")
        _, out1 = server.request("GET", "/state?session=g&who=1")
        assert out0["state"] == state_a
        assert out1["state"] == state_b

        for who, expected in ((0, state_a), (1, state_b)):
            status, resp = server.request(
                "POST", "/rpc",
                {"session": "g", "who": who,
                 "request": {"request": {"$case": "rerollDice", "value": {}}}},
            )
            assert status == 200 and "response" in resp
            seen_who, seen_state = server.state.policy.calls[-1]
            assert seen_who == who and seen_state == expected
    finally:
        server.close()


def test_session_state_is_isolated(captured):
    """两个会话（两个标签页/两局）各自一套座位状态，互不覆盖。"""
    state_a, state_b = _states_pair(captured)
    server = _Server(_RecordingPolicy())
    try:
        server.request("POST", "/notify",
                       {"session": "gA", "who": 0, "seq": 1, "notification": {"state": state_a}})
        server.request("POST", "/notify",
                       {"session": "gB", "who": 0, "seq": 1, "notification": {"state": state_b}})
        _, a = server.request("GET", "/state?session=gA&who=0")
        _, b = server.request("GET", "/state?session=gB&who=0")
        assert a["state"] == state_a and b["state"] == state_b
        _, health = server.request("GET", "/health")
        assert health["sessions"] == 2
    finally:
        server.close()


def test_build_policy_from_args_gives_per_seat_instances():
    """生产路径：每个 (会话, 座位) 一个独立策略实例，role 带座位与会话。"""
    args = argparse.Namespace(policy="heuristic", strategy="random", seed=2, ai_deck=None)
    seat0, factory = srv.build_policy_from_args(args)
    assert isinstance(seat0, srv.RepoPolicy)
    assert seat0.role == "web0"
    seat1 = factory("g1", 1)
    other_session = factory("g1", 0)
    assert seat1 is not seat0
    assert other_session is not seat0
    # role 带座位号；非默认会话再叠一个会话后缀（同一会话才可复现）
    assert seat1.role == "web1@g1"
    assert other_session.role == "web0@g1"
    assert factory(srv.DEFAULT_SESSION, 1).role == "web1"


def test_placeholder_seat_seeds_differ():
    """占位策略两座位 seed 派生不同，自对弈时不会走出同一条随机流。"""
    args = argparse.Namespace(policy=None, strategy="random", seed=7, ai_deck=None)
    _, factory = srv.build_policy_from_args(args)
    seat0 = factory("g", 0)
    seat1 = factory("g", 1)
    assert seat0.rng.random() != seat1.rng.random()


# --------------------------------------------------------------------------- #
# T2：/notify 序号护栏 —— 过期通知不覆盖新状态
# --------------------------------------------------------------------------- #
def test_notify_stale_seq_is_dropped(captured):
    state_a, state_b = _states_pair(captured)
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        _, out = server.request(
            "POST", "/notify",
            {"session": "g", "who": 0, "seq": 5, "notification": {"state": state_a}},
        )
        assert out["applied"] is True
        status, out = server.request(
            "POST", "/notify",
            {"session": "g", "who": 0, "seq": 2, "notification": {"state": state_b}},
        )
        assert status == 200
        assert out == {"applied": False, "reason": "stale_seq", "seq": 2}

        _, st = server.request("GET", "/state?session=g&who=0")
        assert st["state"] == state_a, "过期通知把新状态覆盖了"
        _, health = server.request("GET", "/health")
        assert health["stale_notify"] == 1

        # seq 是**座位内**单调：另一座位从 1 开始照样生效。
        _, out = server.request(
            "POST", "/notify",
            {"session": "g", "who": 1, "seq": 1, "notification": {"state": state_b}},
        )
        assert out["applied"] is True
        _, st1 = server.request("GET", "/state?session=g&who=1")
        assert st1["state"] == state_b
    finally:
        server.close()


def test_notify_without_seq_still_applies(captured):
    """老客户端（不带 seq）不受护栏影响，行为与 C2 一致。"""
    state_a, _ = _states_pair(captured)
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        _, out = server.request("POST", "/notify",
                                {"who": 0, "notification": {"state": state_a}})
        assert out["applied"] is True and out["seq"] is None
        _, st = server.request("GET", "/state")
        assert st["state"] == state_a
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# T3：AI 牌组对齐 —— /ai-deck + 开局牌组校验
# --------------------------------------------------------------------------- #
def test_resolve_ai_deck_and_endpoint():
    deck = srv.resolve_ai_deck(
        argparse.Namespace(ai_deck="superconduct_aggro", policy=None)
    )
    assert deck["slug"] == "superconduct_aggro"
    assert len(deck["characters"]) == 3 and len(deck["cards"]) == 30

    server = _Server(state=srv.State(srv.Policy(strategy="greedy", seed=1), ai_deck=deck))
    try:
        status, out = server.request("GET", "/ai-deck")
        assert status == 200
        assert out["slug"] == "superconduct_aggro"
        assert out["characters"] == deck["characters"]
        assert out["cards"] == deck["cards"]
    finally:
        server.close()


def test_ai_deck_endpoint_without_deck():
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        status, out = server.request("GET", "/ai-deck")
        assert status == 200 and out["slug"] is None and out["cards"] == []
    finally:
        server.close()


def test_resolve_ai_deck_from_expert_policy(capsys):
    """不给 --ai-deck 时从 --policy expert:<slug> 推断；两者不一致要告警。"""
    deck = srv.resolve_ai_deck(
        argparse.Namespace(ai_deck=None, policy="expert:natlan_battleship")
    )
    assert deck["slug"] == "natlan_battleship"
    assert "不一致" not in capsys.readouterr().err

    srv.resolve_ai_deck(
        argparse.Namespace(ai_deck="superconduct_aggro", policy="expert:natlan_battleship")
    )
    assert "不一致" in capsys.readouterr().err


def test_session_reports_deck_mismatch(capsys):
    expected = srv.resolve_ai_deck(
        argparse.Namespace(ai_deck="superconduct_aggro", policy=None)
    )
    actual = srv.resolve_ai_deck(
        argparse.Namespace(ai_deck="natlan_battleship", policy=None)
    )
    server = _Server(
        state=srv.State(srv.Policy(strategy="greedy", seed=1), ai_deck=expected)
    )
    capsys.readouterr()  # 清掉 resolve 阶段的输出
    try:
        status, out = server.request(
            "POST", "/session",
            {"session": "g",
             "meta": {"ai_seat": 0,
                      "decks": {"0": {"characters": actual["characters"],
                                      "cards": actual["cards"]}}}},
        )
        assert status == 200 and out["deck_mismatch"] is True
        # 不只是 HTTP 响应体：要真的记进会话（/results、summary.json 才查得到）
        assert server.state.session_info("g")["deck_mismatch"] is True
        assert "不一致" in capsys.readouterr().err

        status, out = server.request(
            "POST", "/session",
            {"session": "g2",
             "meta": {"ai_seat": 0,
                      "decks": {"0": {"characters": expected["characters"],
                                      "cards": expected["cards"]}}}},
        )
        assert status == 200 and out["deck_mismatch"] is False
        assert server.state.session_info("g2")["deck_mismatch"] is False
        assert "不一致" not in capsys.readouterr().err
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# C4：逐决策日志落盘 + 战绩汇总
# --------------------------------------------------------------------------- #
def test_decision_log_lifecycle(captured, tmp_path):
    log = srv.DecisionLog(tmp_path)
    server = _Server(state=srv.State(srv.Policy(strategy="greedy", seed=1), log=log))
    entry = captured["action"]
    notification = json.loads(entry["notification_json"])
    try:
        status, out = server.request(
            "POST", "/session",
            {"session": "g1", "meta": {"version": "v0.21.0", "ai_seat": 0}},
        )
        assert status == 200
        session_dir = Path(out["dir"])
        assert (session_dir / "session.json").exists()

        server.request("POST", "/notify",
                       {"session": "g1", "who": 0, "seq": 1, "notification": notification})
        status, resp = server.request(
            "POST", "/rpc",
            {"session": "g1", "who": 0,
             "request": {"request": {"$case": "action", "value": _payload(entry)}}},
        )
        assert status == 200 and "response" in resp

        lines = [
            json.loads(line)
            for line in (session_dir / "decisions.jsonl").read_text().splitlines()
        ]
        decision = lines[0]
        assert decision["type"] == "decision"
        assert decision["session"] == "g1" and decision["who"] == 0
        assert decision["method"] == "action" and decision["step"] == 0
        assert decision["state_seq"] == 1 and decision["notify_seq"] == 1
        assert decision["fallback"] is False
        assert decision["policy"] == "placeholder:greedy"
        assert decision["latency_ms"] >= 0
        assert "chosenActionIndex" in decision["response"]

        status, out = server.request("POST", "/game-end", {"session": "g1", "winner": 1})
        assert status == 200 and out["closed"] is True
        summary = json.loads((session_dir / "summary.json").read_text())
        assert summary["winner"] == 1
        assert summary["decisions_0"] == 1

        end_line = json.loads(
            (session_dir / "decisions.jsonl").read_text().splitlines()[-1]
        )
        assert end_line["type"] == "game_end" and end_line["winner"] == 1
        assert end_line["decisions"] == [1, 0]

        _, results = server.request("GET", "/results")
        assert results["log_dir"] == str(tmp_path)
        assert results["sessions"][0]["session"] == "g1"
        assert results["sessions"][0]["winner"] == 1

        # 幂等：重复关账不覆盖首局结果
        _, out = server.request("POST", "/game-end", {"session": "g1", "winner": 0})
        assert out["closed"] is False
        summary = json.loads((session_dir / "summary.json").read_text())
        assert summary["winner"] == 1
    finally:
        server.close()


def test_decision_log_records_fallback(captured, tmp_path):
    """模型桥回退占位时，日志里要能看到 fallback/error（调试闭环的关键字段）。"""
    policy = srv.RepoPolicy(
        "definitely-not-a-spec", seed=1, role="web",
        fallback=srv.Policy(strategy="random", seed=1),
    )
    server = _Server(state=srv.State(policy, log=srv.DecisionLog(tmp_path)))
    entry = captured["action"]
    try:
        server.request("POST", "/session", {"session": "gf", "meta": {}})
        server.request("POST", "/notify",
                       {"session": "gf", "who": 0, "seq": 1,
                        "notification": json.loads(entry["notification_json"])})
        status, resp = server.request(
            "POST", "/rpc",
            {"session": "gf", "who": 0,
             "request": {"request": {"$case": "action", "value": _payload(entry)}}},
        )
        assert status == 200 and "response" in resp
        session_dir = Path(server.state.session_info("gf")["dir"])
        decision = json.loads(
            (session_dir / "decisions.jsonl").read_text().splitlines()[0]
        )
        assert decision["fallback"] is True
        assert decision["error"]
        _, results = server.request("GET", "/results")
        assert results["sessions"][0]["fallbacks_0"] == 1
    finally:
        server.close()


def test_notify_winner_closes_session(captured, tmp_path):
    """浏览器没发 /game-end 时（老前端/标签页崩了），靠 state.winner 关账兜底。"""
    server = _Server(
        state=srv.State(srv.Policy(strategy="greedy", seed=1), log=srv.DecisionLog(tmp_path))
    )
    ended = dict(_state(captured["action"]))
    ended["winner"] = 1
    ended["phase"] = "PHASE_TYPE_GAME_END"
    try:
        _, out = server.request("POST", "/notify",
                                {"session": "ge", "who": 0, "seq": 1,
                                 "notification": {"state": ended}})
        assert out["applied"] is True
        _, results = server.request("GET", "/results")
        info = results["sessions"][0]
        assert info["winner"] == 1
        assert info["closed_at"] and info["close_source"] == "notify"
        assert info["rounds"] is not None
        summary = json.loads((Path(info["dir"]) / "summary.json").read_text())
        assert summary["winner"] == 1
    finally:
        server.close()


def test_game_log_upload(tmp_path):
    """浏览器上传引擎日志（含人类决策）→ 与桥侧决策日志同目录归档。"""
    server = _Server(
        state=srv.State(srv.Policy(strategy="greedy", seed=1), log=srv.DecisionLog(tmp_path))
    )
    try:
        status, out = server.request(
            "POST", "/game-log",
            {"session": "gl", "log": {"gv": "v0.21.0", "entries": [1, 2, 3]}},
        )
        assert status == 200 and out["dir"]
        payload = json.loads((Path(out["dir"]) / "game_log.json").read_text())
        assert payload == {"gv": "v0.21.0", "entries": [1, 2, 3]}
        _, results = server.request("GET", "/results")
        assert results["sessions"][0]["game_log"] is True
    finally:
        server.close()


def test_log_failure_does_not_break_rpc(captured, tmp_path, capsys):
    """日志目录建不起来（根路径是个文件）→ 告警但对局照跑，handler 不 500。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    server = _Server(
        state=srv.State(srv.Policy(strategy="greedy", seed=1), log=srv.DecisionLog(blocker))
    )
    entry = captured["action"]
    capsys.readouterr()
    try:
        status, out = server.request("POST", "/session", {"session": "gx", "meta": {}})
        assert status == 200 and out["dir"] is None
        status, resp = server.request(
            "POST", "/rpc",
            {"session": "gx", "who": 0,
             "request": {"request": {"$case": "action", "value": _payload(entry)}}},
        )
        assert status == 200 and "response" in resp
        assert "决策日志目录创建失败" in capsys.readouterr().err
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# C1：/choose-deck 反选 + 按座位派生策略（只在 base 是 expert:* 时改派）
# --------------------------------------------------------------------------- #
def _deck_payload(name: str) -> dict:
    from envs.decks import deck_spec

    spec = deck_spec(name)
    return {"characters": list(spec.characters), "cards": list(spec.cards)}


def test_choose_deck_returns_counter_pick():
    state = srv.State(srv.Policy(strategy="greedy", seed=1), base_spec="expert:x")
    server = _Server(state=state)
    try:
        status, pick = server.request(
            "POST", "/choose-deck",
            {"session": "g1", "deck": _deck_payload("natlan_battleship")},
        )
        assert status == 200
        assert pick["session"] == "g1"
        assert pick["method"] in {"matrix-proxy", "matrix-strength", "decklab-rule"}
        assert pick["policy"] == f"expert:{pick['slug']}"
        assert pick["policy_applied"] is True  # base 是 expert:* → 会按牌组改派
        assert pick["reason"]
        # 反选结果记进会话，供 /session 校验（优先于 --ai-deck）
        assert state.picks["g1"]["slug"] == pick["slug"]
    finally:
        server.close()


def test_choose_deck_rejects_bad_body():
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        status, out = server.request("POST", "/choose-deck", {"session": "g"})
        assert status == 400 and "error" in out
        status, out = server.request(
            "POST", "/choose-deck", {"session": "g", "deck": {"characters": []}}
        )
        assert status == 400 and "error" in out
    finally:
        server.close()


def test_choose_deck_reports_policy_not_applied_for_neural():
    """neural:* 是用户显式选的模型 → 只换牌组、不换策略，要如实告知。"""
    state = srv.State(
        srv.Policy(strategy="greedy", seed=1), base_spec="neural:ckpt.pt"
    )
    server = _Server(state=state)
    try:
        _, pick = server.request(
            "POST", "/choose-deck",
            {"session": "g2", "deck": _deck_payload("superconduct_aggro")},
        )
        assert pick["policy_applied"] is False
        assert pick["base_policy"] == "neural:ckpt.pt"
    finally:
        server.close()


def test_session_assigns_per_seat_expert_specs(capsys):
    """开局时按各座位**实际**牌组派生策略：池内 → 自己的脚本；非池内 → heuristic。"""
    args = argparse.Namespace(
        policy="expert:superconduct_aggro", strategy="random", seed=1, ai_deck=None
    )
    seat_specs: dict[tuple[str, int], str] = {}
    seat0, factory = srv.build_policy_from_args(args, seat_specs=seat_specs)
    state = srv.State(
        seat0,
        policy_factory=factory,
        seat_specs=seat_specs,
        base_spec="expert:superconduct_aggro",
    )
    server = _Server(state=state)
    capsys.readouterr()
    try:
        body = {
            "session": "gs",
            "meta": {
                "ai_seat": 1,
                "decks": {
                    # seat0 是池内另一套 → 改派成它自己的脚本
                    "0": _deck_payload("natlan_battleship"),
                    # seat1 是 NPC/未知牌组 → 改派 deck-agnostic 的 heuristic
                    "1": {"characters": [3302, 3302, 3101], "cards": [1, 2, 3]},
                },
            },
        }
        status, out = server.request("POST", "/session", body)
        assert status == 200
        assert out["seat_specs"] == {
            "0": "expert:natlan_battleship",
            "1": "heuristic",
        }
        assert state.seat_specs[("gs", 0)] == "expert:natlan_battleship"
        assert state.seat_specs[("gs", 1)] == "heuristic"
        # 派生结果也要记进会话记录（summary.json / /results 查得到）
        assert state.session_info("gs")["seat_specs"] == out["seat_specs"]
        # 非池内牌组要告警（否则用户不知道为什么换了策略）
        err = capsys.readouterr().err
        assert "不在评测池" in err and "heuristic" in err
        # 座位策略实例按改派后的 spec 建（懒加载，只看 spec 字段）
        assert state.seat("gs", 0).policy.spec == "expert:natlan_battleship"
        assert state.seat("gs", 1).policy.spec == "heuristic"
        # 改派只在座位还没建起来时生效
        assert state.assign_seat_specs("gs", body["meta"]) == {}
    finally:
        server.close()


def test_session_does_not_touch_non_expert_policy():
    """`neural:*` 等非 deck-specific 策略绝不能被按牌组改派。"""
    args = argparse.Namespace(policy="neural:ckpt.pt", strategy="random", seed=1, ai_deck=None)
    seat_specs: dict[tuple[str, int], str] = {}
    seat0, factory = srv.build_policy_from_args(args, seat_specs=seat_specs)
    state = srv.State(
        seat0, policy_factory=factory, seat_specs=seat_specs, base_spec="neural:ckpt.pt"
    )
    server = _Server(state=state)
    try:
        _, out = server.request(
            "POST", "/session",
            {"session": "gn", "meta": {"ai_seat": 0, "decks": {"0": _deck_payload("natlan_battleship")}}},
        )
        assert out["seat_specs"] == {}
        assert seat_specs == {}
    finally:
        server.close()


def test_session_deck_check_prefers_counter_pick_over_ai_deck(capsys):
    """反选之后，校验对象变成反选结果（否则会拿 --ai-deck 误报不一致）。"""
    declared = _deck_payload("superconduct_aggro")   # --ai-deck 声明的
    state = srv.State(
        srv.Policy(strategy="greedy", seed=1), ai_deck=declared, base_spec=None
    )
    server = _Server(state=state)
    capsys.readouterr()
    try:
        # 反选出另一套
        _, pick = server.request(
            "POST", "/choose-deck",
            {"session": "gp", "deck": _deck_payload("natlan_battleship")},
        )
        pick_deck = {"characters": pick["characters"], "cards": pick["cards"]}

        # 1) 用反选结果开局 → 不算不一致、不告警
        _, out = server.request(
            "POST", "/session",
            {"session": "gp", "meta": {"ai_seat": 0, "decks": {"0": pick_deck}}},
        )
        assert out["deck_mismatch"] is False
        assert "不一致" not in capsys.readouterr().err

        # 2) 没反选过的会话用同一套 → 与 --ai-deck 声明不符 → 告警（label=--ai-deck）
        _, out = server.request(
            "POST", "/session",
            {"session": "gq", "meta": {"ai_seat": 0, "decks": {"0": pick_deck}}},
        )
        assert out["deck_mismatch"] is True
        err = capsys.readouterr().err
        assert "不一致" in err and "--ai-deck" in err

        # 3) 反选过、但实际开局用的牌组被改了 → 告警（label=反选结果）
        server.request(
            "POST", "/choose-deck",
            {"session": "gr", "deck": _deck_payload("natlan_battleship")},
        )
        capsys.readouterr()
        _, out = server.request(
            "POST", "/session",
            {"session": "gr", "meta": {"ai_seat": 0, "decks": {"0": declared}}},
        )
        assert out["deck_mismatch"] is True
        err = capsys.readouterr().err
        assert "不一致" in err and "反选结果" in err
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# C3：/npc-decks（人数不固定，16 关全给）
# --------------------------------------------------------------------------- #
def test_npc_decks_endpoint():
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        status, payload = server.request("GET", "/npc-decks")
        assert status == 200
        assert len(payload["decks"]) == 16
        assert set(payload["semantics"]) >= {"lineup_size", "support", "waves"}
        sizes = [d["total"] for d in payload["decks"]]
        assert min(sizes) >= 1 and max(sizes) >= 4, "不能因为人数不为 3 就丢关卡"
        # NPC 方不该有手牌：牌组是空牌堆
        assert all(d["cards"] == [] for d in payload["decks"])
        # NPC 的名字/技能描述/骰子费用要一并给出（前端据此构造 AssetsManager）
        assets = payload["assets"]
        assert len(assets["characters"]) >= 17
        assert all(ch["name"] and ch["skills"] for ch in assets["characters"])
        # 返回的是牌组对象（NPC 角色编不出分享码，前端直传）
        timmie = next(d for d in payload["decks"] if d["slug"] == "timmie")
        assert timmie["n_on_field"] == 2 and timmie["n_support"] == 0
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# T2 续局：「从此处继续」= 同一个 session 上开新的一局，seq 从桥给的基线接着数
# （否则新局通知会被全判过期，决策吃的是被终止那一局的残局状态）
# --------------------------------------------------------------------------- #
def test_session_resume_gives_seq_baseline(captured):
    state_a, state_b = _states_pair(captured)
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        server.request("POST", "/session", {"session": "gr", "meta": {}})
        server.request(
            "POST", "/notify",
            {"session": "gr", "who": 0, "seq": 5, "notification": {"state": state_a}},
        )
        server.request(
            "POST", "/notify",
            {"session": "gr", "who": 1, "seq": 3, "notification": {"state": state_a}},
        )

        _, out = server.request(
            "POST", "/session", {"session": "gr", "meta": {}, "resume": True}
        )
        assert out["runs"] == 2
        base = out["seq_base"]
        assert int(base["0"]) > 5 and int(base["1"]) > 3, "基线要落在上一局序号之后"

        # 上一局在途的通知（seq 还在旧序号空间）→ 仍旧判过期，不污染新局状态
        _, stale = server.request(
            "POST", "/notify",
            {"session": "gr", "who": 0, "seq": 5, "notification": {"state": state_b}},
        )
        assert stale["applied"] is False

        # 新局从基线 + 1 接着数 → 生效
        _, ok = server.request(
            "POST", "/notify",
            {"session": "gr", "who": 0, "seq": int(base["0"]) + 1,
             "notification": {"state": state_b}},
        )
        assert ok["applied"] is True
        _, st = server.request("GET", "/state?session=gr&who=0")
        assert st["state"] == state_b
    finally:
        server.close()


def test_session_resume_clears_previous_state(captured):
    """续局要丢掉上一局留下的掩码状态：否则续局最初的决策读的是旧局状态。"""
    state_a, _ = _states_pair(captured)
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        server.request(
            "POST", "/notify",
            {"session": "gc", "who": 0, "seq": 1, "notification": {"state": state_a}},
        )
        server.state.close_session("gc", winner=0, source="notify", rounds=3)
        server.request("POST", "/session", {"session": "gc", "meta": {}, "resume": True})
        _, st = server.request("GET", "/state?session=gc&who=0")
        assert st["state"] is None, "续局后不该还能读到上一局的状态"
        assert st["state_seq"] == 0
    finally:
        server.close()


def test_session_without_resume_is_idempotent(captured):
    """普通的重复 /session（重渲染/重连）不重置计数、不动 seq 基线。"""
    state_a, _ = _states_pair(captured)
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        server.request("POST", "/session", {"session": "gi", "meta": {}})
        server.request(
            "POST", "/notify",
            {"session": "gi", "who": 0, "seq": 4, "notification": {"state": state_a}},
        )
        _, out = server.request("POST", "/session", {"session": "gi", "meta": {}})
        assert out["runs"] == 1
        assert int(out["seq_base"]["0"]) == 4
        # 基线没跳，seq 4 依旧算过期（护栏行为不变）
        _, stale = server.request(
            "POST", "/notify",
            {"session": "gi", "who": 0, "seq": 4, "notification": {"state": state_a}},
        )
        assert stale["applied"] is False
    finally:
        server.close()


def test_decision_log_records_run_after_resume(captured, tmp_path):
    """续局后的决策行要带 run，state_seq/notify_seq 才是可解释的（按局分段）。"""
    state_a, _ = _states_pair(captured)
    log = srv.DecisionLog(tmp_path)
    server = _Server(state=srv.State(srv.Policy(strategy="greedy", seed=1), log=log))
    try:
        server.request("POST", "/session", {"session": "gn", "meta": {}})
        _, out = server.request(
            "POST", "/notify",
            {"session": "gn", "who": 0, "seq": 1, "notification": {"state": state_a}},
        )
        assert out["applied"] is True
        server.request("POST", "/session", {"session": "gn", "meta": {}, "resume": True})
        base = server.state.seq_baseline("gn")
        server.request("POST", "/notify",
                       {"session": "gn", "who": 0, "seq": int(base["0"]) + 1,
                        "notification": {"state": state_a}})
        server.request(
            "POST", "/rpc",
            {"session": "gn", "who": 0,
             "request": {"request": {"$case": "rerollDice", "value": {}}}},
        )
        session_dir = Path(server.state.session_info("gn")["dir"])
        lines = [json.loads(x) for x in (session_dir / "decisions.jsonl").read_text().splitlines()]
        kinds = [line["type"] for line in lines]
        assert "run_start" in kinds, "续局要落一条 run_start"
        decision = [line for line in lines if line["type"] == "decision"][-1]
        assert decision["run"] == 2
        assert decision["state_seq"] == 1
        assert decision["notify_seq"] == int(base["0"]) + 1
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# C4 故障归档：AI 桥 IO 出错被引擎判负的局不能算真实胜负
# --------------------------------------------------------------------------- #
def test_game_end_aborted_marks_io_error(captured, tmp_path):
    state_a, _ = _states_pair(captured)
    state_end = copy.deepcopy(state_a)
    state_end["winner"] = 1  # 引擎按 gotWinner(flip(who)) 置的「胜负」
    log = srv.DecisionLog(tmp_path)
    server = _Server(state=srv.State(srv.Policy(strategy="greedy", seed=1), log=log))
    try:
        # /notify 里看到 winner → 桥先自动关账（source=notify，winner=1）
        server.request(
            "POST", "/notify",
            {"session": "ga", "who": 0, "seq": 1, "notification": {"state": state_end}},
        )
        info = server.state.session_info("ga")
        assert info["closed_at"] is not None and info["winner"] == 1

        # 浏览器随后上报「这是桥故障」→ 改判成故障归档
        _, out = server.request(
            "POST", "/game-end",
            {"session": "ga", "winner": 1, "rounds": 2,
             "aborted": True, "abort_reason": "io_error"},
        )
        assert out["closed"] is True and out["aborted"] is True

        info = server.state.session_info("ga")
        assert info["aborted"] is True
        assert info["winner"] is None, "故障局不该留下胜负"
        assert info["close_source"] == "io_error"
        records = [
            json.loads(line)
            for line in (Path(info["dir"]) / "decisions.jsonl").read_text().splitlines()
        ]
        end = [r for r in records if r["type"] == "game_end"][-1]
        assert end["aborted"] is True and end["amended"] is True
        assert end["winner"] is None
        summary = json.loads((Path(info["dir"]) / "summary.json").read_text())
        assert summary["aborted"] is True and summary["winner"] is None
    finally:
        server.close()


def test_game_end_normal_still_records_winner():
    """正常终局不受影响（winner 照记，aborted=False）。"""
    server = _Server(srv.Policy(strategy="greedy", seed=1))
    try:
        _, out = server.request(
            "POST", "/game-end", {"session": "gn2", "winner": 0, "rounds": 5}
        )
        assert out["closed"] is True and out["aborted"] is False
        info = server.state.session_info("gn2")
        assert info["winner"] == 0 and info["aborted"] is False
        assert info["close_source"] == "game-end"
    finally:
        server.close()


def test_session_check_fields_land_in_files(tmp_path):
    """deck_mismatch/seat_specs 要落进 session.json 与 summary.json，不只是响应体。"""
    expected = srv.resolve_ai_deck(
        argparse.Namespace(ai_deck="superconduct_aggro", policy=None)
    )
    actual = srv.resolve_ai_deck(
        argparse.Namespace(ai_deck="natlan_battleship", policy=None)
    )
    log = srv.DecisionLog(tmp_path)
    server = _Server(
        state=srv.State(srv.Policy(strategy="greedy", seed=1), log=log, ai_deck=expected)
    )
    try:
        server.request(
            "POST", "/session",
            {"session": "gf",
             "meta": {"ai_seat": 0,
                      "decks": {"0": {"characters": actual["characters"],
                                      "cards": actual["cards"]}}}},
        )
        session_dir = Path(server.state.session_info("gf")["dir"])
        session_json = json.loads((session_dir / "session.json").read_text())
        assert session_json["deck_mismatch"] is True
        assert "seat_specs" in session_json

        server.request("POST", "/game-end", {"session": "gf", "winner": 0, "rounds": 3})
        summary = json.loads((session_dir / "summary.json").read_text())
        assert summary["deck_mismatch"] is True
        assert "seat_specs" in summary
    finally:
        server.close()
