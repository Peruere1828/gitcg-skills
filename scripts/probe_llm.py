#!/usr/bin/env python3
"""Live probe for the LLM endpoint (no secrets printed, no engine needed).

Checks the things our experiments depend on:

1. the endpoint answers at all (auth headers, session header, user agent);
2. `{"choice": n}` JSON mode works with a tiny `max_tokens`;
3. a repeated prefix actually hits the prompt cache (`cached_tokens`);
4. the reported cost/usage fields are what we use for the budget table.

Usage::

    python scripts/probe_llm.py
    python scripts/probe_llm.py --repeats 3 --model deepseek-v4.1-flash
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from common.llm import build_llm_client, load_llm_config, redact

SYSTEM = (
    "你是《七圣召唤》的对局助手。只输出 JSON：{\"choice\": <动作编号>}。"
    "关卡【提米】三星目标：赢得胜利；5回合内胜利；角色被击倒次数小于2。"
)
USER = (
    "第1轮 行动阶段 行动方=我方\n"
    "我方：刻晴 10/10、雷电将军 10/10★出战、甘雨 12/12\n"
    "我方骰子：万万万雷雷雷冰冰（8个）\n"
    "合法动作：\n[0] 使用技能：源流 花费雷冰冰\n[1] 使用技能：神变·恶曜开眼 花费雷雷雷\n"
    "[2] 打出卡牌：华饰之兜 → 我方·雷电将军 花费冰\n[3] 宣布结束回合\n"
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)

    config = load_llm_config()
    if config is None:
        print("[probe] no LLM config (check .env: base_url / model / key)")
        return 1
    print(f"[probe] base_url={config.base_url}")
    print(f"[probe] model={config.model} session={config.session_id} "
          f"reasoning={config.reasoning_effort} timeout={config.timeout}")
    print(f"[probe] key: present={bool(config.api_key)} len={len(config.api_key)}")

    client = build_llm_client()
    if client is None:
        print("[probe] build_llm_client() returned None")
        return 1

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER},
    ]
    for index in range(args.repeats):
        started = time.perf_counter()
        raw = client.chat(messages, max_tokens=48, temperature=0.0,
                          response_format_json=True, model=args.model)
        elapsed = time.perf_counter() - started
        text = redact(raw or "")
        print(f"[probe] call {index + 1}: {elapsed:.2f}s raw={text[:80]!r}")

    print(f"[probe] usage={json.dumps(client.usage.as_dict(), ensure_ascii=False)}")
    cached = client.usage.cached_tokens
    prompt = client.usage.prompt_tokens
    if prompt:
        print(f"[probe] cache hit rate = {cached / prompt:.1%} "
              f"({cached}/{prompt} tokens)")
    print(f"[probe] cost = {client.usage.cost_usd:.6f} USD for {client.usage.calls} calls")
    return 0 if client.usage.calls == args.repeats else 1


if __name__ == "__main__":
    sys.exit(main())
