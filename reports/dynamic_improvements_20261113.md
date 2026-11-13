# Dynamic 三星率提升实验（2026-11-13）

## 1. 问题与口径

在冻结的 `dyn_v4` 技能库与共享智能体上，尝试在不消耗复盘/迭代预算的前提下把
`dynamic` 的三星率推向 `scripted`。

- 16 关 × seeds **1–5**（80 局/变体），另用 seeds **6–9**（64 局/变体）做验证。
- `configs/stage_decks.json` + `reports/loadouts/v4` + `card_version=v3.3.0`
  + `--stage-rules`，6 workers，`temperature=0`，模型 `deepseek-v4.1-flash`。
- 主指标 `three_star`；配对口径 `scripts/compare_groups.py`（同 stage/seed）。
- 所有新开关默认关，且写入 run fingerprint，不与旧 run 混跑。

## 2. 本轮新增的三个杠杆（默认关）

| 开关 | 做法 | 是否改 prompt |
|---|---|---|
| `--rich-render` | 状态/候选更丰富：元素附着名、手牌 `N/10`、对手结束、技能类型/描述/反应提示、卡牌描述 | 是 |
| `--base-guard` | 模型选择仅在引用非 rule 技能时生效，否则退回牌组 expert 的脚本动作 | 否（只改最终动作） |
| `--round-plan` | 记录板改为轮级计划（隐含 `--scratchpad`），每轮首决策提示目标/路线/风险/留骰 | 是 |

三个开关分别进指纹；本轮把 `rich-render + base-guard` 作为一个组合臂（下称
`rich_guard`）测试。`round-plan` 与 `base-guard-strict` 单独试跑后因中途结果明显
更差而停止。

## 3. 结果

| 变体 | seeds | n | 三星 | 三星率 | 成本 |
|---|---|---:|---:|---:|---:|
| `dynamic` 基线（dyn_v4） | 1–5 | 80 | 41 | 51.25% | $0.1727 |
| `rich_guard` | 1–5 | 80 | 51 | 63.75% | $0.1982 |
| `dynamic` 基线（dyn_v4） | 6–9 | 64 | 34 | 53.12% | $0.1234 |
| `rich_guard` | 6–9 | 64 | 31 | 48.44% | $0.1647 |
| `scripted` 参照 | 1–5 | 80 | 57 | 71.25% | $0 |
| `scripted` 参照 | 6–9 | 64 | 38 | 59.38% | $0 |

- seeds 1–5 配对门控（`rich_guard` − 基线）：Δ = **+0.125**，
  95% CI [−0.044, +0.294]，**未过门**（CI 跨 0）。
- seeds 1–5 的增益集中在基线弱关：timaeus 0→4、uncletian 1→4、buyun 0→2、
  lan 1→3；同时 liusu 4→2、marjorie 3→2、swan 5→4、blanche 1→0。
- seeds 6–9 验证配对门控（`rich_guard` − 基线）：Δ = **−0.047**，
  95% CI [−0.178, +0.084]，**未过门**（方向甚至为负）。
- seeds 6–9 的 `rich_guard` 31/64 = 48.44%，低于基线 34/64 = 53.12%，也低于
  `scripted` 同期 38/64 = 59.38%。
- 全 1–9 seeds 配对门控：Δ = **+0.049**，95% CI [−0.059, +0.157]，**未过门**。
  合并三星率：基线 75/144 = 52.08%，`rich_guard` **82/144 = 56.94%**，
  `scripted` **95/144 = 65.97%**。

## 4. 结论

- seeds 1–5 的 +12.5pp 未通过配对门控；seeds 6–9 方向为负，合并后 CI 仍跨 0。
  `rich_guard` 不能作为 `dynamic` 的默认配置，也仍未追上 `scripted`。
- 两个杠杆在本轮是**混在一起**测的：行数据只保留每局 `cited_skills`，没有逐决策的
  `guarded` 计数，因此无法把增益归因到 `rich-render` 还是 `base-guard`。若继续，
  应先记录逐决策 `guarded/final_code`，再单开关 A/B。
- 负结果（不继续投入）：
  - `--scratchpad` 3 seeds pilot：关 26/48 = 54.17%，开 25/48 = 52.08%，
    成本 +48%（`reports/scratchpad_pilot_20261113.md`）。
  - `rich + round-plan` 跑到 42/80 局时 20/42 = 47.62%，停止。
  - `rich + base-guard-strict` 跑到 19/80 局时 7/19 = 36.84%，停止。
- 预算：本轮全部实验合计约 **$0.99**（基线 s1–5 $0.1727、`rich_guard` s1–5
  $0.1982、`rich_guard` s6–9 $0.1647、基线 s6–9 $0.1234、round-plan
  $0.1219、strict $0.0609、scratchpad pilot $0.1514），低于 $2 上限。

## 5. 复现

```sh
PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
COMMON="--stages all --workers 6 --stage-decks configs/stage_decks.json \
  --loadout reports/loadouts/v4 --card-version v3.3.0 --stage-rules --trace \
  --library data/skills/dyn_v4 --group dynamic"

GITCG_LLM_BUDGET_USD=... $PY -m skillloop.run $COMMON --seeds 6,7,8,9 \
  --out data/runs/v4_dynamic_base_s6_9.jsonl
GITCG_LLM_BUDGET_USD=... $PY -m skillloop.run $COMMON --seeds 6,7,8,9 \
  --rich-render --base-guard \
  --out data/runs/v4_dynamic_rich_guard_s6_9.jsonl
```

原始逐局行在 `data/runs/`（gitignore），配对比较可用：

```sh
PYTHONPATH=. $PY scripts/compare_groups.py \
  --a data/runs/v4_dynamic_r1.jsonl \
  --b data/runs/v4_dynamic_rich_guard_s1_5.jsonl \
  --label-a dyn_base --label-b rich_guard
```
