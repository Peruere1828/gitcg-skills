# 局内记录板（scratchpad）pilot（2026-11-13）

## 设计

- `skillloop.run --scratchpad`（默认关）：模型可在 JSON 里多写一个 `note`
  （1–3 句本局路线/当前轮计划/下一手意图，≤240 字）；下一次决策把最新 note
  放回 user message 开头，供模型回看。
- 记录板**每局独立**（一个 `SkillAgentPolicy` 实例），不写入技能库、不跨局。
- 默认关闭时 prompt/system hash/`max_tokens=64` 与旧行为逐字节一致；开启时
  system 只追加一段稳定的记录板说明（可缓存），note 在 user 动态尾部，
  `max_tokens=160`。run fingerprint 记录 `scratchpad`，开/关不能混写同一输出。
- 决策 `Decision.note`/`scratchpad_chars` 可审计；web 桥/`run_parked` 也支持
  `--scratchpad`。

## 3-seed pilot（dyn_v4 + loadouts/v4，6 workers）

| 组 | 种子 | 局数 | 三星 | 三星率 | 成本 | calls/局 |
|---|---|---:|---:|---:|---:|---:|
| 关闭 | 1–3 | 48 | 26 | **54.17%** | $0.102 | 25.4 |
| 开启 | 1–3 | 48 | 25 | **52.08%** | $0.151 | 25.1 |

- 0 error / 0 truncated。
- 记录板没有提升（-2.1pp，3 seeds 内属噪声），成本约 **+48%**（completion
  `max_tokens` 64→160 与 note/说明的 prompt tokens）。
- 单局长度/决策数与关闭组接近，说明模型并非“多写导致调用更多”，主要是
  每次请求更贵。

## 解释与建议

- 当前 note 是**可选**的；模型可能不写、写得很短或不把旧 note 当约束，
  所以 3-seed 内看不到收益。记录板本身不改变技能库，也不改变候选动作。
- 若要继续，建议改成**结构化且强制**的局内计划：只在每轮开始写一次
  `目标/本轮路线/下一轮风险`（而不是每个动作），并要求下一次决策显式检查
  一致性；或单独做一次 round-level planner 调用（成本更高，但 plan 更稳）。
- 不建议默认开启；可保留为 A/B 开关，并在 web `/ai-decisions` 面板展示
  模型笔记（便于人类调试，即使不提升胜率）。
- 3 seeds 不足以判定；若要做正式 A/B，用同 seeds 的 10–50 seeds 配对，
  并按 `docs/EVAL_PROTOCOL.md` §6 重新做成本标定。
