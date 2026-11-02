# 001 — 对局内思考模式：成本 10× 且无质量证据

- status: negative
- 日期: 2026-10-31（t5 中止）/ 2026-11-01（D10 回退）
- 证据: `data/runs/t5_static.jsonl`（82/160 行）、`data/runs/t5_llm_empty.jsonl`（90/160 行）；
  对照 `data/runs/smoke2_static.jsonl`、`data/runs/smoke2_llm_empty.jsonl`（各 160 行）；可引用基线 commit `f79868f`；
  聚合口径见 `docs/STATUS.md §3.4`
- 口径: 16 关 × 10 seeds × 2 组（t5 未跑完）；`superconduct_aggro`；`deepseek-v4.1-flash`；2026-10-30/31

## 现象

同一套 prompt / 牌组，只把对局思考从 `none` 改成 `low`（`max_tokens` 48→4096）：

| 配置 | completion tokens/次 | $/局 | 相对成本 |
|---|---:|---:|---:|
| `effort=none`（smoke2） | 15 | 0.0038–0.0042 | 1× |
| `effort=low`（t5） | 1167–1215 | 0.0374–0.0400 | **约 10×** |

t5 在 2026-10-31 因额度耗尽中止：`static` 只完成 82/160 行、`llm_empty` 90/160 行。
当日日志总花费 **$7.93**，其中 t5 占 **$6.64**。

## 根因

- D8 为质量把 `max_tokens` 从 48 提到 4096、开 `reasoning_effort=low`；该端点把 reasoning tokens 计入 **completion/output** 计费。
- 每局调用数几乎不变（25 左右），成本完全由单次输出 token 放大：15 → 1215，约 80×/次，摊到每局约 10×。
- 并发 48 局（两个组各 24 worker）把烧钱速率放大到约 **$7/h**，预算在跑完一组之前就耗尽。

## 为什么没早发现

- STATUS 里的 ~$0.004/局 是**开思考之前**（`effort=none`）测的；改配置后没有重新标定单价。
- 没有 token / 成本标定门禁：小样本 → 外推 → 设预算这一步缺失，直接上了 160 局 × 多组。
- 逐局 `cost_usd` 有记录，但只有跑完才汇总；运行中没有硬上限，额度耗尽才被动停止。

## 处置

- **D10**：对局回退 `reasoning_effort=none` + `max_tokens=64`；思考只用于技能提取/反思（`effort=high`, 8192）。重新开启必须以成对 A/B 证明质量收益。
- **D11**：运行器加 `GITCG_LLM_BUDGET_USD` 硬上限（契约，代码并行落地），触顶后不再启动新局，summary 记 `aborted: budget`。
- **EVAL_PROTOCOL §6**：任何调用模型的 run 先做 ≥20 次决策标定，再用 `scripts/estimate_cost.py` 外推、写预算、写口径。
- t5 的部分结果**作废**，不进入任何表格或结论；最后可引用仍是 `f79868f` 的 640 局（`effort=none`）。
  （2026-11-02 注：这 640 局同样由 `record_decisions=False` 的 runner 产出，`timaeus`/`uncletian`
  判定失真；见 [`002`](./record-decisions-judge-blind.md)。）

## 教训

- 一次配置改动的成本影响，必须先测 **20 次调用**再上全量。
- 「不计成本换质量」也要有上限和证据：成本 10× 必须换来可检出的质量差，否则回退。
- 并发是成本放大器：预算按「局」算，烧钱按「小时」发生。

## 重开条件

在对局内重新开启思考，必须同时满足：同种子成对 A/B（50 seeds/关）质量 Δ 的 **CI 下界 > 0**；
按 §6 完成 token 标定、成本外推并设置 `GITCG_LLM_BUDGET_USD`。否则保持 `effort=none`。
