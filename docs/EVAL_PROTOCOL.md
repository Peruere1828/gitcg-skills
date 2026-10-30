# EVAL_PROTOCOL — PvE 技能实验的评测口径

> 权威：本文件。任务定义见 [`PLAN.md §1`](../PLAN.md)；当前数字见 [`STATUS.md`](./STATUS.md)。

## 1. 对象

| 符号 | 含义 |
|---|---|
| `S` | 16 个关卡（`configs/npc/npc_stages_*.json`） |
| `K` | 每组每关的种子数（筛选 24，确认 50） |
| `g` | 实验组：`scripted` / `llm_empty` / `static` / `dynamic` |
| `x` | 一局的三星（1/0）；`objectives` 记录逐目标达成 |

一局 **有效** 的条件：`error == 0` 且 `truncated == 0`；无效局不计入分母，但必须报数量。

## 2. 主指标

1. **三星率**（全部目标达成）= 主指标；
2. **逐目标达成率**（按目标类型聚合）= 诊断（哪类目标在丢分）；
3. 次指标：平均回合数、每局 LLM 调用数、token 消耗。

> 不要跨关直接平均「星数」：各关目标数 1–3 不等，stars 不可比；用三星率或「达成目标数/目标总数」。

## 3. 统计

**主口径 = 按关配对**：

```
对每关 s：Δ_s = rate_g2(s) − rate_g1(s)     （同 seeds）
聚合：Δ̄ = mean_s(Δ_s)，SE = sd_s(Δ_s)/√16，CI = Δ̄ ± t(15)·SE
```

理由：16 关的三星率从 ~0.2 到 ~1.0，pooled 方差被关卡难度主导；配对后关卡难度抵消。
（上游项目的 pooled Wilson / cluster CI 见 `docs/PROVENANCE.md` 链接；本仓库不采用 pooled 作主判据。）

**功效（估算）**：每关 50 seeds 时，单关 Δ 的 SE ≈ 0.10；16 关聚合 SE ≈ 0.025 →
95% CI 可检出 **≥5pp** 的总体差异。24 seeds 只用于筛选（检出 ~8–10pp）。

## 4. 预注册规则

- 候选库版本 v_{n+1} 采纳条件：**留出/全关配对 Δ 的 CI 下界 > 0**；
- 若 CI 跨 0：不采纳，记入 `docs/failures/`；
- 每个实验组固定种子清单，**不允许事后换种子**；种子清单写进报告；
- 引擎/血量/卡池版本写进每个 run 的元数据（`engine.lock` + `hp_table.json`）。

## 5. 可复现

- 每局记录 `stage / seed / deck / group / library_version / model / prompt_hash`；
- 原始逐局 JSONL 落 `data/`（gitignore），聚合摘要落 `reports/`（入库）；
- LLM 温度 0；若供应商不保证确定性，则以 50 seeds 的统计而非单局复现为准。
