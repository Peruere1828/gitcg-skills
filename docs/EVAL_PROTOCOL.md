# EVAL_PROTOCOL — PvE 技能实验的评测口径

> 权威：本文件。任务定义见 [`PLAN.md §1`](../PLAN.md)；当前数字见 [`STATUS.md`](./STATUS.md)。

## 1. 对象

| 符号 | 含义 |
|---|---|
| `S` | 16 个关卡（`configs/npc/npc_stages_*.json`） |
| `K` | 每组每关的种子数（筛选 24，确认 50） |
| `g` | 实验组：`scripted` / `llm_empty` / `static` / `dynamic` |
| `x` | 一局的三星（1/0）；`objectives` 记录逐目标达成 |

四组只允许技能库不同；库/loadout 的缺省与覆盖规则：

| 组 | 库 | 推荐 loadout（需显式 `--loadout`） | `--library` 覆盖 |
|---|---|---|---|
| `scripted` | 无（无 LLM） | 不适用 | 不适用 |
| `llm_empty` | `data/skills/rules_v1`（17 rule，仅游戏规则） | 不加载 | 忽略 |
| `static` | `data/skills/static_v1`（17 rule + 10 strategy） | `reports/loadouts/v1` | 允许 |
| `dynamic` | `data/skills/dyn_v4`（17 rule + 23 strategy） | `reports/loadouts/v4` | 允许 |

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

- 每局记录 `stage / seed / deck / group / library_root / library_version / model / prompt_hash`；
- run 元数据指纹里 `stages` 是关卡列表，关卡文件哈希在 `stages_file_sha1`；
  指纹使用**实际解析后的库根**（`llm_empty` 也会记录 `data/skills/rules_v1`）；
- 原始逐局 JSONL 落 `data/`（gitignore），聚合摘要落 `reports/`（入库）；
- LLM 温度 0；但本端点实测 `temperature=0` **不是字节级可复现**（只降低采样方差）。复现以 50 seeds 的统计而非单局复现为准。

## 6. 成本门禁（任何调用模型的 run）

上全量网格**之前**必须完成四步：

1. **小样本标定**：跑 1 关 × 1 种子（或 ≥20 次决策），测出 `completion_tokens/次`、`calls/局`、`$/局`；
2. **外推**：用已有 run 的逐局 JSONL 算出全量成本（无需联网/引擎）：
   `"$PY" -m scripts.estimate_cost --rows data/runs/llm_empty.jsonl --per-stage-seeds 50 --stages 16`
   （`scripts/run_all_groups.sh` 的四组输出为 `data/runs/<group>.jsonl`；也可用 `--games N`）；
   输出实测 `$/局`、`calls/局`、`completion tokens/次` 与 projected total；
3. **设上限**：写明本次预算上限并在运行环境设 `GITCG_LLM_BUDGET_USD`，由运行器在启动新局前检查累计 `cost_usd`（D11；普通 `skillloop.run` 与 `scripts/run_parked` 共用同一守卫）。
   触顶只停止启动新局，已在飞的局会跑完；因此超额最多是当时在飞局（含刚越过阈值的那一局）的成本，不随网格规模放大；
4. **写口径**：把 `completion_tokens/次`、`$/局`、预算上限三项写进该 run 的 meta 与报告。

> 没做标定的配置不得上全量。模型、`max_tokens`、`reasoning_effort`、并发任一变更都算**新配置**，需重新标定。

## 7. 可选：局内记录板（scratchpad）

`skillloop.run --scratchpad`（默认关）给 LLM 组加一块**每局独立**的工作记忆：模型可在
回复 JSON 里多写一个 `note`（1–3 句整体思路/当前轮计划/下一手意图），下一次决策把最新
`note` 原样放进 user message 前部。它不写技能库、不跨局，也不参与 `choice/used` 解析。

- **默认关**：prompt、system hash、`max_tokens=64` 与旧 run 完全一致，旧结果可直接比较。
- **A/B**：只能在**同 stages / 同 seeds** 下与关闭组配对；run 指纹已记录 `scratchpad`
  （`data/runs/<group>.jsonl.meta.json`），开启与关闭的 run 不能续写进同一个 `--out`。
- **成本**：每次 prompt 多一段记录板（≈ 记录板长度；上限 `note_max_chars=240`），
  且 `max_tokens` 从 64 提到 160。按 §6 重新标定 `completion_tokens/次`、`$/局` 后再上量。
- **缓存**：记录板说明追加在 stable system 末尾（可缓存）；记录板正文只在 user message
  （动态尾部），不会破坏 system prefix 的缓存键。

同属可选项、默认关闭、各自进指纹的还有三个实验开关（只在显式 A/B 时成组使用）：

| 开关 | 作用 |
|---|---|
| `--round-plan` | 记录板改为轮级计划（隐含 `--scratchpad`）：每轮第一次决策提示「目标/本轮路线/风险/留骰」 |
| `--rich-render` | 状态/候选渲染更丰富：元素附着名、手牌 `N/10`、对手结束状态、双方支援/召唤物/状态、角色装备、技能类型与反应提示、卡牌描述 |
| `--base-guard` | 只有模型引用了非 rule 技能时才采纳其选择；否则退回牌组 expert 的脚本动作 |
| `--base-guard-strict` | 配合 `--base-guard`，只认 `stage_specific` 或以 `_<stage>` 结尾的技能 id |
| `--override-kinds` | 只允许模型在这些高层动作类型上越权（如 `action_use_skill`），其余交还 expert |
| `--override-stages` | 只在这些关卡调用模型；其他关卡纯 expert，不产生 API 调用 |
| `--expert-hint` | 把 expert 的脚本建议写进 user message，作为模型的先验 |
| `--base-seed` | expert 的 tie-break seed（诊断用；比较时必须用同 seed 重跑 scripted） |
| `--reasoning` | 模型推理强度（默认 `none` 对局；`low`/`medium`/`high` 会把回复预算提到 8192 token） |
| `--stage-profiles` | 按关卡覆盖 era expert 规则参数（如 `configs/stage_profiles.json` 的 lan `setup_rounds=0`）；进指纹，treatment/control 可用同一画像比较 |

`--base-guard*` 只是**决策兜底**，不改变模型调用次数与 prompt；它用同一 LLM 选择做门控，
因此不会破坏同种子配对。爬坡结论见 `reports/dynamic_improvements_20261113.md`。
