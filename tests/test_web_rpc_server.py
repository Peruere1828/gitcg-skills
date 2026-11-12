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
        if pick["method"] == "decklab-rule":
            # 没有 reports/arena 矩阵时的 decklab 兜底牌组没有配套脚本 → heuristic。
            assert pick["policy"] == "heuristic"
        else:
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
        # 官方卡（2301/2202，11/10）与 NPC 变体（9301/9202，13/12）是两张牌；
        # CustomData 现在如实包含 overlay 里的全部角色（含两张 NPC 变体）。
        custom_ids = {ch["id"] for ch in assets["characters"]}
        assert 9301 in custom_ids and 9202 in custom_ids
        assert 3302 in custom_ids
        # liusu 的阵容引用的是 NPC 变体 9301，而不是官方角色卡 2301。
        liusu = next(d for d in payload["decks"] if d["slug"] == "liusu")
        assert 9301 in liusu["characters"]
        assert 2301 not in liusu["characters"]
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


# --------------------------------------------------------------------------- #
# LLM 技能智能体：库解析 / 座位分配 / /ai / /ai-library / 延迟构造
# --------------------------------------------------------------------------- #
_POOL_DECK = "natlan_battleship"
_NPC_DECK = {"characters": [3302, 3302, 3101], "cards": [1, 2, 3]}


def test_parse_llm_spec():
    assert srv.parse_llm_spec("llm:dynamic") == ("dynamic", None)
    assert srv.parse_llm_spec("llm:static#deck=natlan_battleship") == (
        "static",
        "natlan_battleship",
    )
    assert srv.parse_llm_spec("llm:none") == ("none", None)
    assert srv.parse_llm_spec("expert:x") is None
    assert srv.parse_llm_spec(None) is None


def test_resolve_llm_library_choices():
    assert srv.resolve_llm_library("none") is None

    empty = srv.resolve_llm_library("llm_empty")
    assert empty["resolved"] == "llm_empty"
    assert empty["version"] == 0 and empty["skills"] == 0
    assert empty["kind"] == "llm_empty" and empty["path"] is None

    static = srv.resolve_llm_library("static")
    assert static["resolved"] == "static_v1"
    assert static["kind"] == "rules"
    assert static["version"] == 1 and static["skills"] == 27
    assert static["path"] == os.path.join("data", "skills", "static_v1")

    # dynamic 必须按 built_at 选，而不是目录名/version：dyn_v4 的 version 是 2，
    # 但它是 evolved 库里最新的一个。
    dynamic = srv.resolve_llm_library("dynamic")
    assert dynamic["resolved"] == "dyn_v4"
    assert dynamic["kind"] == "evolved"
    assert dynamic["version"] == 2 and dynamic["skills"] == 40
    assert dynamic["built_at"] == "2026-11-02T11:44:13Z"


def test_llm_library_options_always_list_four():
    options = srv.llm_library_options()
    assert [o["value"] for o in options] == ["none", "llm_empty", "static", "dynamic"]
    labels = {o["value"]: o["label"] for o in options}
    assert "static_v1" in labels["static"] and "27" in labels["static"]
    assert "dyn_v4" in labels["dynamic"] and "40" in labels["dynamic"]


def _llm_server(monkeypatch, *, available: bool, base: str = "llm:dynamic",
                choice: str | None = "dynamic") -> "_Server":
    monkeypatch.setattr(
        "common.llm.build_llm_client",
        lambda *a, **k: object() if available else None,
    )
    args = argparse.Namespace(policy=base, strategy="greedy", seed=1, ai_deck=None)
    seat_specs: dict[tuple[str, int], str] = {}
    seat0, factory = srv.build_policy_from_args(args, seat_specs=seat_specs)
    state = srv.State(
        seat0,
        policy_factory=factory,
        seat_specs=seat_specs,
        base_spec=base,
        library_choice=choice,
    )
    return _Server(state=state)


def _llm_session_body(session: str) -> dict:
    return {
        "session": session,
        "meta": {
            "ai_seat": 1,
            "decks": {
                "0": _NPC_DECK,
                "1": _deck_payload(_POOL_DECK),
            },
        },
    }


def test_llm_seat_assignment_only_ai_seat(monkeypatch, capsys):
    server = _llm_server(monkeypatch, available=True)
    try:
        status, out = server.request("POST", "/session", _llm_session_body("gllm"))
        assert status == 200
        assert out["seat_specs"] == {
            "0": "heuristic",  # NPC 阵容 → deck-agnostic 脚本
            "1": f"llm:dynamic#deck={_POOL_DECK}",  # 只有 ai_seat 用 LLM
        }
        assert server.state.seat_specs[("gllm", 1)] == (
            f"llm:dynamic#deck={_POOL_DECK}"
        )
        # /session 的 ai 字段复用 /ai 序列化
        assert out["ai"]["mode"] == "llm"
        assert out["ai"]["llm_available"] is True
        assert out["ai"]["library"]["resolved"] == "dyn_v4"
        seat1 = server.state.seat("gllm", 1)
        assert seat1.policy.spec == f"llm:dynamic#deck={_POOL_DECK}"
        assert isinstance(seat1.policy, srv.LLMPolicy)
    finally:
        server.close()


def test_llm_unavailable_falls_back_at_seat_assignment(monkeypatch, capsys):
    server = _llm_server(monkeypatch, available=False)
    capsys.readouterr()
    try:
        status, out = server.request("POST", "/session", _llm_session_body("gnollm"))
        assert status == 200
        assert out["seat_specs"] == {
            "0": "heuristic",
            "1": f"expert:{_POOL_DECK}",  # 池内牌组 → 自家脚本，而不是 llm:*
        }
        assert out["ai"]["mode"] == "scripted"
        assert out["ai"]["llm_available"] is False
        assert out["ai"]["llm_error"]
        err = capsys.readouterr().err
        assert "LLM 不可用" in err
        # 座位建好就是脚本策略，不会每步走 LLMPolicy 的 fallback
        assert isinstance(server.state.seat("gnollm", 1).policy, srv.RepoPolicy)
    finally:
        server.close()


def test_ai_endpoint_reports_llm_and_options(monkeypatch):
    server = _llm_server(monkeypatch, available=True)
    try:
        status, out = server.request("GET", "/ai")
        assert status == 200
        assert out["mode"] == "llm"
        assert out["spec"] == "llm:dynamic"
        assert out["llm_available"] is True
        assert out["llm_error"] is None
        assert out["library"]["resolved"] == "dyn_v4"
        assert [o["value"] for o in out["options"]] == [
            "none", "llm_empty", "static", "dynamic"
        ]
    finally:
        server.close()


def test_ai_library_updates_only_future_sessions(monkeypatch):
    server = _llm_server(monkeypatch, available=True)
    try:
        _, first = server.request(
            "POST", "/session",
            {"session": "g_before", "meta": {"ai_seat": 0,
                                             "decks": {"0": _deck_payload(_POOL_DECK)}}},
        )
        assert first["seat_specs"] == {
            "0": f"llm:dynamic#deck={_POOL_DECK}"
        }
        built = server.state.seat_specs[("g_before", 0)]

        status, out = server.request("POST", "/ai-library", {"library": "static"})
        assert status == 200
        assert out["ok"] is True
        assert out["note"] == "下一局生效"
        assert out["library"]["resolved"] == "static_v1"

        # 已建好的座位 spec 不变
        assert server.state.seat_specs[("g_before", 0)] == built
        assert server.state.seat("g_before", 0).policy.spec == built

        # /ai 全局设置立刻反映新选择
        _, ai = server.request("GET", "/ai")
        assert ai["library"]["resolved"] == "static_v1"

        # 后续新会话使用新库
        _, second = server.request(
            "POST", "/session",
            {"session": "g_after", "meta": {"ai_seat": 0,
                                            "decks": {"0": _deck_payload(_POOL_DECK)}}},
        )
        assert second["seat_specs"] == {
            "0": f"llm:static#deck={_POOL_DECK}"
        }
        _, session_ai = server.request("GET", "/ai?session=g_after")
        assert session_ai["library"]["resolved"] == "static_v1"

        status, bad = server.request("POST", "/ai-library", {"library": "bogus"})
        assert status == 400 and "error" in bad
    finally:
        server.close()


def test_llm_policy_constructs_skill_agent_with_injected_client(captured):
    from common.llm import MockLLMClient
    from skillloop.agent import SkillAgentPolicy

    entry = captured["action"]
    mock = MockLLMClient(['{"choice": 0, "used": []}'])
    policy = srv.LLMPolicy(
        "dynamic",
        _POOL_DECK,
        seed=1,
        role="web0",
        fallback=srv.Policy(strategy="random", seed=1),
        spec=f"llm:dynamic#deck={_POOL_DECK}",
        client_factory=lambda: mock,
    )
    out = policy.decide(0, "action", _payload(entry), _state(entry))
    assert set(out) == {"chosenActionIndex", "usedDice"}
    assert 0 <= out["chosenActionIndex"] < len(_payload(entry)["action"])
    assert isinstance(policy._policy, SkillAgentPolicy)
    assert policy._policy.library is not None
    assert policy.name == "llm:dynamic[dyn_v4]"
    assert policy.base_spec == f"expert:{_POOL_DECK}"
    assert policy.library_info["resolved"] == "dyn_v4"
    assert mock.prompts, "应真的走了一次 LLM 客户端（injected fake）"
    assert policy.last_decision["llm_calls"] == 1
    assert policy.last_decision["system_hash"]


def test_llm_decision_log_has_audit_fields(captured, tmp_path, monkeypatch):
    from common.llm import MockLLMClient

    entry = captured["action"]
    policy = srv.LLMPolicy(
        "dynamic",
        _POOL_DECK,
        seed=1,
        role="web0",
        fallback=srv.Policy(strategy="random", seed=1),
        client_factory=lambda: MockLLMClient(['{"choice": 0, "used": []}']),
    )
    server = _Server(
        state=srv.State(policy, log=srv.DecisionLog(tmp_path))
    )
    try:
        server.request("POST", "/session", {"session": "glog", "meta": {}})
        server.request(
            "POST", "/notify",
            {"session": "glog", "who": 0, "seq": 1,
             "notification": json.loads(entry["notification_json"])},
        )
        status, resp = server.request(
            "POST", "/rpc",
            {"session": "glog", "who": 0,
             "request": {"request": {"$case": "action", "value": _payload(entry)}}},
        )
        assert status == 200 and "response" in resp
        session_dir = Path(server.state.session_info("glog")["dir"])
        decision = json.loads(
            (session_dir / "decisions.jsonl").read_text().splitlines()[0]
        )
        assert "used_skills" in decision
        assert decision["system_hash"]
        assert decision["llm_calls"] == 1
        assert decision["library"]["resolved"] == "dyn_v4"
        # P4：可审计字段（模型请求/候选数/模型选中的候选下标）。
        assert decision["request"] == "action"
        assert decision["n_candidates"] >= 2
        assert decision["model_choice"] == 0
        assert policy.last_decision["request"] == "action"
        assert policy.last_decision["n_candidates"] == decision["n_candidates"]
        assert policy.last_decision["model_choice"] == 0
        # 旧字段保持不变
        assert decision["type"] == "decision"
        assert decision["fallback"] is False
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# 网页专用渲染 / 战术 / 技能库过滤
# --------------------------------------------------------------------------- #
def test_web_state_renderer_skill_candidate_has_type_and_description():
    from types import SimpleNamespace

    from reps.schema import DecisionType, LowLevelActionSpec, OptionKind

    try:
        from agents.scripted.assets import load_assets

        assets = load_assets()
        assets.skill(23012)  # 伺机而动：造成1点火元素伤害
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"assets 缓存不可用：{exc}")

    renderer = srv.WebStateRenderer(assets)
    context = SimpleNamespace(player_view=None, acting_player=0)
    use_skill = LowLevelActionSpec(
        action_code=1,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_USE_SKILL,
        label="",
        subject_definition_id=23012,
        used_dice=(3, 3, 3),
    )
    line = renderer.describe_candidate(context, use_skill, 3)
    assert "元素战技" in line
    assert "使用技能" not in line  # 类型标签替代了泛化标签
    assert "造成1点火元素伤害" in line
    assert "将附着火" in line  # 无状态时至少给出附着提示
    assert line.startswith("[3] 元素战技：")

    switch = LowLevelActionSpec(
        action_code=2,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_SWITCH_ACTIVE,
        label="",
        subject_definition_id=23012,
    )
    switch_line = renderer.describe_candidate(context, switch, 0)
    assert "切换出战角色" in switch_line
    assert "伤害" not in switch_line  # 不编造伤害


def _web_state_view(
    *,
    opp_aura: int = 0,
    hand_count: int = 0,
    me_declared_end: bool = False,
    opp_declared_end: bool = False,
):
    """Minimal masked-state shape for ``describe_state`` / reaction tests."""
    from types import SimpleNamespace

    def character(definition_id: int, *, aura: int = 0, active: bool = False):
        return SimpleNamespace(
            definition_id=definition_id,
            health=10,
            max_health=10,
            energy=0,
            max_energy=3,
            aura=aura,
            is_active=active,
            defeated=False,
        )

    me = SimpleNamespace(
        characters=[character(1403, aura=3, active=True)],
        dice=[3, 3, 8],
        hand_cards=[SimpleNamespace(definition_id=1) for _ in range(hand_count)],
        declared_end=me_declared_end,
    )
    opp = SimpleNamespace(
        characters=[character(3302, aura=opp_aura, active=True)],
        hand_cards=[],
        dice=[],
        declared_end=opp_declared_end,
    )
    return SimpleNamespace(
        round_number=2,
        phase="action",
        current_turn=0,
        players=[me, opp],
    )


def test_web_state_renderer_maps_aura_ids_to_elements():
    from types import SimpleNamespace

    try:
        from agents.scripted.assets import load_assets

        assets = load_assets()
        assets.character(1403)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"assets 缓存不可用：{exc}")

    renderer = srv.WebStateRenderer(assets)
    text = renderer.describe_state(
        SimpleNamespace(player_view=_web_state_view(opp_aura=3), acting_player=0)
    )
    assert "附着:火" in text
    assert " 3" not in text  # 不再出现裸 aura 整数
    text0 = renderer.describe_state(
        SimpleNamespace(player_view=_web_state_view(opp_aura=0), acting_player=0)
    )
    assert "无附着" in text0


def test_web_state_renderer_previews_reactions():
    from types import SimpleNamespace

    from reps.schema import DecisionType, LowLevelActionSpec, OptionKind

    try:
        from agents.scripted.assets import load_assets

        assets = load_assets()
        assets.skill(23012)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"assets 缓存不可用：{exc}")

    renderer = srv.WebStateRenderer(assets)
    skill = LowLevelActionSpec(
        action_code=1,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_USE_SKILL,
        label="",
        subject_definition_id=23012,
        used_dice=(3, 3, 3),
    )
    # 对方出战带冰附着（aura=1），火技能 → 融化。
    melting = renderer.describe_candidate(
        SimpleNamespace(player_view=_web_state_view(opp_aura=1), acting_player=0),
        skill,
        0,
    )
    assert "将对附着冰的出战角色触发融化" in melting
    # 无附着 → 只提示附着。
    attaching = renderer.describe_candidate(
        SimpleNamespace(player_view=_web_state_view(opp_aura=0), acting_player=0),
        skill,
        0,
    )
    assert "将附着火" in attaching
    assert "触发" not in attaching.split("（")[-1]


def test_filter_library_for_web_drops_stage_specific_skills():
    from skillloop.library import SkillLibrary

    root = Path(srv._REPO_ROOT) / "data" / "skills"
    dynamic = SkillLibrary.load(root / "dyn_v4")
    filtered = srv.filter_library_for_web(dynamic)
    ids = set(filtered.skills)

    for dropped in (
        "skill_timmie",
        "tempo_budget_for_round_cap_shitou",
        "frontload_reaction_into_kill_line_shitou",
        "strat_mulligan",
        "规则_目标_胜利",
        "规则_目标_回合上限",
    ):
        assert dropped in dynamic.skills
        assert dropped not in ids
    for kept in (
        "rule_reaction",
        "rule_switch",
        "strat_reaction",
        "strat_switch_cost",
    ):
        assert kept in ids
    assert 0 < len(filtered) < len(dynamic)
    assert filtered.version == dynamic.version
    assert filtered.meta == dynamic.meta

    # static_v1 也含 strat_mulligan 与 规则_目标_*，同样会被剔除。
    static = SkillLibrary.load(root / "static_v1")
    static_filtered = srv.filter_library_for_web(static)
    assert 0 < len(static_filtered) < len(static)
    assert "strat_mulligan" not in static_filtered.skills
    assert not any(sid.startswith("规则_目标_") for sid in static_filtered.skills)

    assert srv.filter_library_for_web(None) is None
    empty = SkillLibrary.from_skills([], version=0)
    assert srv.filter_library_for_web(empty) is empty


def test_llm_policy_prompt_has_web_tactics_and_filtered_library(captured):
    from common.llm import MockLLMClient

    entry = captured["action"]

    def make_policy(selection: str, deck: str | None) -> srv.LLMPolicy:
        return srv.LLMPolicy(
            selection,
            deck,
            seed=1,
            role="web0",
            fallback=srv.Policy(strategy="random", seed=1),
            client_factory=lambda: MockLLMClient(['{"choice": 0, "used": []}']),
        )

    policy = make_policy("dynamic", _POOL_DECK)
    policy.decide(0, "action", _payload(entry), _state(entry))
    agent = policy._policy
    assert agent.config.candidate_cap == 24
    assert agent.library is not None
    assert all(
        skill.layer == "rule" or skill.skill_id.startswith("strat_")
        for skill in agent.library
    )
    assert "strat_mulligan" not in agent.library.skills
    assert not any(sid.startswith("规则_目标_") for sid in agent.library.skills)
    system = agent._system["content"]
    assert "网页对局的助手" in system
    assert "普通攻击只造成物理伤害" in system
    assert "rule_reaction" in system or "strat_reaction" in system
    # 网页版不再用 PvE 关卡/三星目标框架。
    assert "三星" not in system
    assert "单人 PvE" not in system
    # 关卡特化 / mulligan 技能已过滤，不得出现在 stable prefix 里。
    assert "五回合窗口" not in system
    assert "tempo_budget_for_round_cap" not in system
    assert "frontload_reaction_into_kill_line" not in system
    assert "strat_mulligan" not in system

    # none 库也必须带网页战术（rules_note 不能为空）。
    none_policy = make_policy("none", None)
    none_policy.decide(0, "action", _payload(entry), _state(entry))
    none_system = none_policy._policy._system["content"]
    assert "普通攻击只造成物理伤害" in none_system
    assert none_policy._policy.library is None


def test_ai_endpoint_reports_applied_and_filtered_counts(monkeypatch):
    server = _llm_server(monkeypatch, available=True)
    try:
        status, out = server.request("GET", "/ai")
        assert status == 200
        library = out["library"]
        assert library["resolved"] == "dyn_v4"
        assert library["skills"] == 40
        assert library["filtered_out"] > 0
        assert library["applied_skills"] == library["skills"] - library["filtered_out"]

        # static_v1 的 strat_mulligan 与 规则_目标_* 同样会被剔除。
        extra = srv.resolve_llm_library("static")
        assert extra["skills"] == 27
        assert 0 < extra["applied_skills"] < extra["skills"]
        assert extra["applied_skills"] == extra["skills"] - extra["filtered_out"]
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# 网页候选排序 / 手牌提示 / 战术增补
# --------------------------------------------------------------------------- #
def _action_spec(kind, *, subject: int = 0, code: int = 0):
    from reps.schema import DecisionType, LowLevelActionSpec

    return LowLevelActionSpec(
        action_code=code,
        request_type=DecisionType.ACTION,
        kind=kind,
        label="",
        subject_definition_id=subject,
    )


def test_web_priority_key_tiers_and_stability():
    from types import SimpleNamespace

    from reps.schema import OptionKind

    try:
        from agents.scripted.assets import load_assets

        load_assets().skill(23012)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"assets 缓存不可用：{exc}")

    def key(view, spec):
        return srv.web_priority_key(
            SimpleNamespace(player_view=view, acting_player=0), spec
        )

    icy = _web_state_view(opp_aura=1)
    clear = _web_state_view(opp_aura=0)
    reaction = _action_spec(OptionKind.ACTION_USE_SKILL, subject=23012)  # 火 E vs 冰
    burst = _action_spec(OptionKind.ACTION_USE_SKILL, subject=23013)
    elemental = _action_spec(OptionKind.ACTION_USE_SKILL, subject=23012)
    card = _action_spec(OptionKind.ACTION_PLAY_CARD, subject=100)
    normal = _action_spec(OptionKind.ACTION_USE_SKILL, subject=23011)
    switch = _action_spec(OptionKind.ACTION_SWITCH_ACTIVE, subject=1403)
    tuning = _action_spec(OptionKind.ACTION_ELEMENTAL_TUNING)
    end = _action_spec(OptionKind.ACTION_DECLARE_END)

    assert key(icy, reaction) == 0
    assert key(clear, burst) == 1
    assert key(clear, elemental) == 2
    assert key(clear, card) == 3
    assert key(clear, normal) == 4
    assert key(clear, switch) == 5
    assert key(clear, tuning) == 5
    assert key(clear, end) == 6

    # 同优先级保持原顺序（sorted 稳定）。
    card_b = _action_spec(OptionKind.ACTION_PLAY_CARD, subject=101)
    specs = [card, card_b, end, normal]
    order = sorted(range(len(specs)), key=lambda i: key(clear, specs[i]))
    assert [specs[i].subject_definition_id for i in order] == [100, 101, 23011, 0]
    assert key(clear, card) == key(clear, card_b)


def test_web_llm_reorder_keeps_payload_by_code():
    from reps.action_adapter import BuiltDecisionContext
    from reps.schema import (
        DecisionContext,
        DecisionType,
        OptionKind,
    )

    end = _action_spec(OptionKind.ACTION_DECLARE_END, code=7)
    card = _action_spec(OptionKind.ACTION_PLAY_CARD, subject=100, code=8)
    switch = _action_spec(OptionKind.ACTION_SWITCH_ACTIVE, subject=1403, code=9)
    payloads = {
        7: {"chosen_action_index": 5, "used_dice": []},
        8: {"chosen_action_index": 2, "used_dice": [8]},
        9: {"chosen_action_index": 4, "used_dice": [8]},
    }
    context = DecisionContext(
        acting_player=0,
        request_type=DecisionType.ACTION,
        step_index=0,
        full_state=None,  # type: ignore[arg-type]
        legal_low_level_codes=(7, 8, 9),
        legal_low_level_specs=(end, card, switch),
        legal_low_level_mask=(True, True, True),
        player_view=None,
    )
    built = BuiltDecisionContext(
        context=context,
        payload_by_low_level_code=payloads,
        label_by_low_level_code={},
        low_to_high_code={},
    )
    reordered = srv._reorder_for_web_llm(built)
    assert reordered.context.legal_low_level_codes == (8, 9, 7)
    assert [spec.kind for spec in reordered.context.legal_low_level_specs] == [
        OptionKind.ACTION_PLAY_CARD,
        OptionKind.ACTION_SWITCH_ACTIVE,
        OptionKind.ACTION_DECLARE_END,
    ]
    assert reordered.context.legal_low_level_mask == (True, True, True)
    # payload 表按原引擎 code 映射，未随排序改动。
    assert reordered.payload_by_low_level_code == payloads
    for code in reordered.context.legal_low_level_codes:
        assert code in reordered.payload_by_low_level_code
    assert reordered.payload_by_low_level_code[7]["chosen_action_index"] == 5


def test_web_state_and_user_message_hand_hints():
    from types import SimpleNamespace

    from reps.schema import OptionKind

    try:
        from agents.scripted.assets import load_assets

        assets = load_assets()
        assets.character(1403)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"assets 缓存不可用：{exc}")

    renderer = srv.WebStateRenderer(assets)
    view = _web_state_view(hand_count=8, opp_declared_end=True)
    context = SimpleNamespace(player_view=view, acting_player=0)
    state_text = renderer.describe_state(context)
    assert "（8/10）" in state_text
    assert "对方已宣布结束=True" in state_text

    end = _action_spec(OptionKind.ACTION_DECLARE_END, code=3)
    user = renderer.user_message(context, [(3, end)], progress="PLACEHOLDER")
    content = user["content"]
    assert "手牌 8/10" in content
    assert "手牌接近上限" in content
    assert "未完成目标" not in content
    assert "PLACEHOLDER" not in content  # PvE progress 不再显示

    # 已宣布结束时不再提示继续行动。
    ended_view = _web_state_view(hand_count=9, me_declared_end=True)
    ended = renderer.user_message(
        SimpleNamespace(player_view=ended_view, acting_player=0),
        [(3, end)],
    )
    assert "手牌接近上限" not in ended["content"]


def test_web_tactics_cover_card_and_position_bias():
    from web.llm_render import WEB_TACTICS

    for phrase in (
        "每轮先检查手牌",
        "手牌接近 10 先打掉会溢出的牌",
        "元素调和凑同色",
        "普通攻击只是默认动作",
        "普通攻击常排在候选 0",
        "结束回合是最后手段",
        "天赋/装备牌注意出战角色/武器匹配",
    ):
        assert phrase in WEB_TACTICS
