# dynamic 未能超过 scripted（2026-11-12 迭代）

## 结论

在 6 并发、预算 $2、最多 4 轮/15 seed 的约束下，**dynamic 三星率没有超过 scripted**，
且在第 3 轮后已**数学上不可达**：dynamic 前 12 个 seed 为 92/192（47.92%），scripted
同 12 个 seed 为 124/192（64.58%）；即使剩余 3 个 seed（48 局）全部 3★，dynamic 上限
140/240=58.33%，仍低于 scripted 1–15 的 159/240=66.25%。因此停止第 4 轮，未继续烧预算。

## 口径

- 6 workers；`stage_decks` + `card_version=v3.3.0` + `--stage-rules`；16 关。
- scripted：固定 era/expert 脚本（无 LLM）。
- dynamic：LLM + dyn 库 + per-stage loadout。
- 成本：round1 $0.173、extract v5 $0.019、round2 $0.138、extract v6 $0.013、
  round3 $0.101；loadout 规划未单独计（< $0.05）；合计 < $0.5（预算 $2）。
- 6 并发无 429/error；三组动态 run 均 0 error / 0 truncated。

## 迭代记录

| 轮 | 库/loadout | seeds | 三星 | 率 | 成本 | 备注 |
|---|---|---|---:|---:|---:|---|
| 1 | dyn_v4 + v4 | 1–5 | 41/80 | 51.25% | $0.173 | 基线 dynamic |
| 2 | dyn_v5 + v5 | 6–9 | 31/64 | 48.44% | $0.138 | 复盘 prompt 增强 + 14 条候选 |
| 3 | dyn_v6 + v6 | 10–12 | 20/48 | 41.67% | $0.101 | 合并前 9 seed 再提取 +6 条 |
| 累计 | — | 1–12 | **92/192** | **47.92%** | <$0.5 | scripted 同 12 seed 124/192=64.58% |

scripted 基线：seeds 1–5 57/80=71.25%；6–9 38/64=59.38%；10–12 29/48=60.42%；
1–12 124/192=64.58%；1–15 159/240=66.25%。

## 复盘 prompt 改动（已实现）

- 给复盘专家加入：固定牌组/角色/卡牌事实、关卡特殊规则、当前库中相关/通用技能
  （可批判/替换），要求每关最多 2 条、「触发条件 → 具体动作/优先级」、禁止空泛。
- 提取候选的 `stage_specific` 自动绑定 `conditions.stages`；loadout 只列出当前关适用
  技能，并拒绝越界 id；修复了 dyn_v5 首次 loadout 把 liusu/shitou 的 stage-specific
  技能选进 chefmao 的 bug。
- 这些改动让候选更具体（例如 chefmao 的卯师傅/行动阶段附火、timaeus 的蒂玛乌斯
  首轮免费圣遗物），但第 2/3 轮 dynamic 仍下降，说明**瓶颈不在技能文本**。

## 解释与下一步建议

- dynamic 的 LLM 逐动作决策弱于 scripted expert 的固定规则；新增的 stage-specific
  技能反而可能让模型过度遵循局部指令，牺牲当轮节奏。
- 继续在同一“LLM 直选动作”架构里调复盘 prompt/技能，边际收益为负；不应继续消耗预算。
- 若要继续冲 scripted，建议改**共享评测协议**而不是 web-only：
  1. 让 scripted expert 先提议，LLM 只在技能明确触发时重排/覆盖（LLM rerank/assist）；
  2. 或在共享 in-game prompt 中加入候选效果/反应/牌组匹配（所有 LLM 组同步，需重跑基线）；
  3. 用门控在**同种子配对**上验证新库，而不是把失败局直接作为 active 技能。
- 本结论只针对当前协议/模型/预算；不建议把 dyn_v5/v6 当作“有效技能库”写入主结果。

## 复现

- 行数据：`data/runs/v4_dynamic_r1.jsonl`、`v5_dynamic_r2.jsonl`、`v6_dynamic_r3.jsonl`
  （gitignored）。
- 库/loadout：`data/skills/dyn_v5`、`dyn_v6`；`reports/loadouts/v5/v4`、`v6/v5`。
- scripted：`data/runs/v3_scripted_1_15.jsonl`（gitignored）。
