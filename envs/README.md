# envs — 环境与工程基座（PLAN.md WS0 / M0）

> 状态见 [`docs/STATUS.md`](../docs/STATUS.md)；采集/评测流程见 [`docs/PLAYBOOK.md`](../docs/PLAYBOOK.md)。

gitcg (pybinding) 的 Gym 式封装、Player 子类（on_action/on_reroll_dice/on_choose_active/on_select_card/on_switch_hands/on_notify）、
多进程 rollout runner、episode 日志格式。观测只允许来自 notification.state（信息隐藏版，见 tests/ 的泄漏测试）。

## 历史实现（2026-10-07；当前接口以代码为准）

- `policy.py`：`Policy` 协议（`choose(built) -> action_code`）+ `ScriptedPolicy`（包装 `ExpertRuleAgent`）+
  `BaselinePolicy`（legal-random / heuristic）+ `RandomPolicy` / `CallablePolicy`；`expert_policy(slug)` 加载脚本对手。
- `decks.py`：20 套脚本卡组 profile → `DeckSpec` / `gitcg.Deck`。
- `match.py`：`PolicyPlayer`（把 5 类 RPC 请求接到 `Policy`，只用 `notification.state` 构造决策上下文，
  非法/异常动作回退到 `declare_end`）与 `run_match(...)`（同步跑完整局，返回 `MatchRecord`，可选逐决策日志）。
  牌堆在 Python 侧按种子预洗 + `NO_SHUFFLE=1` 保证同种子可复现（见 `NOTICE.md`）。
- 待办：episode JSONL 落盘（`eval/arena` 已落盘原始对局记录 `data/arena/*.jsonl`）、
  多进程 rollout（已实现：`rollout.py` + `rollout_worker.py`，JSON-lines 子进程协议）、
  吞吐基准（已实现：`benchmark.py`，见 `reports/LOCAL_BASELINE.md`）。
