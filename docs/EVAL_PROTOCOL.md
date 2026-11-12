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
3. 次指标：每局 LLM 调用数、token 消耗（回合数见 §2.1，不是单独的「次指标」）。

> 不要跨关直接平均「星数」：各关目标数 1–3 不等，stars 不可比；用三星率或「达成目标数/目标总数」。

## 2.1 二维主指标：三星率 + 达成三星的回合数

基准已饱和（16 关中 11 关是 5/5 三星），单看三星率无法区分强弱，因此主指标固定为**两维**，必须同时报告：

| 指标 | 定义 | 口径 |
|---|---|---|
| 三星率 | 三星局 / 有效局 | 每关 + pooled（全关合并） |
| 达成三星的回合数 | **仅在三星局内**的 `rounds` 均值 | 每关 + pooled；括号内报 3★ 局数 |

规则：

1. **回合数只在 3★ 子集内可比**。未达三星的局没有「达成三星所需回合数」；把快速失败局混进来会奖赏「更快输掉」。因此条件均值是主口径。
2. 同时报无条件回合均值（全部有效局）仅作上下文；两者不可混读。
3. **高三星率但 3★ 局更慢的 run 不自动更好**：两维必须并列报告，不做未经声明的标量化。若两维冲突，按预注册的任务目标取舍（例如先保三星率，再在同三星率下比回合数），并在报告中写明。
4. 成对检验（`scripts/compare_groups.py`）输出两个门禁：
   - `gate`：三星率，`Δ = b − a`，`CI_low > 0` 才采纳；
   - `rounds_gate`：仅取**两侧都三星**的 `(stage, seed)` 对，`Δ = rounds_b − rounds_a`（**负 = b 更快**），`CI_high < 0` 才采纳；不足 2 关或 5 对时标 `insufficient`，不产出 CI。
5. 种子配对（`(stage, seed)`）是前提；条件子集的样本量小于全量，报告必须写出 `n_pairs` / `n_dropped`。

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
- LLM 温度 0；但本端点实测 `temperature=0` **不是字节级可复现**（只降低采样方差）。复现以 50 seeds 的统计而非单局复现为准。

## 6. 成本门禁（任何调用模型的 run）

上全量网格**之前**必须完成四步：

1. **小样本标定**：跑 1 关 × 1 种子（或 ≥20 次决策），测出 `completion_tokens/次`、`calls/局`、`$/局`；
2. **外推**：用已有 run 的逐局 JSONL 算出全量成本（无需联网/引擎）：
   `"$PY" -m scripts.estimate_cost --rows data/runs/smoke2_static.jsonl --per-stage-seeds 50 --stages 16`
   （或 `--games N`）；输出实测 `$/局`、`calls/局`、`completion tokens/次` 与 projected total；
3. **设上限**：写明本次预算上限并在运行环境设 `GITCG_LLM_BUDGET_USD`，由运行器在启动新局前检查累计 `cost_usd`（D11；普通 `skillloop.run` 与 `scripts/run_parked` 共用同一守卫）。
   触顶只停止启动新局，已在飞的局会跑完；因此超额最多是当时在飞局（含刚越过阈值的那一局）的成本，不随网格规模放大；
4. **写口径**：把 `completion_tokens/次`、`$/局`、预算上限三项写进该 run 的 meta 与报告。

> 没做标定的配置不得上全量。模型、`max_tokens`、`reasoning_effort`、并发任一变更都算**新配置**，需重新标定。
