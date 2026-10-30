# coach — 复盘/教练 Agent（PLAN.md WS4）

> 状态见 [`docs/STATUS.md`](../docs/STATUS.md)；本文件只描述接口与计划，不代表当前主线。

消费 eval/ 的逐决策日志：失误归因（搜索值/胜率掉点）、课程建议（自博弈配比）、BC 标签。输出结构化 JSON 直接进训练管线。

## 历史实现（2026-10-07；当前接口以代码为准）

- `replay.py`：`ReplayCoach.review(match_dict) -> dict`。输入是 arena 的逐局记录（含 `decisions`，每步有
  `player_view` 摘要）；输出 `{summary, mistakes[], deck_lessons[], rule_flags[]}`。
  - LLM 可选：无 client 时退化为确定性 `rule_based_flags`（检测 fallback / policy_error），pipeline 不阻塞。
  - LLM 用推理模型（`deepseek-flash`）+ 大 max_tokens 做深度复盘；prompt 只含公开信息。
- `review.py` CLI：`python -m coach.review --input data/arena/<run>.jsonl --limit 2`。

前提：arena 记录需带逐决策与视图（`ArenaSpec(record_decisions=True)` 已自动开 `record_views`）。

待办：课程建议 → 自博弈配比、BC 标签回灌训练、与 `train/` 搜索值联动做归因。
