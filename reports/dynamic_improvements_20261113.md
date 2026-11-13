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

## 5. 第二轮：把越权收紧到 expert 附近的四种尝试（2026-11-13 下午）

前述 `rich_guard` 未过门后，继续在 `dyn_v4` 上尝试了四种“少越权/有依据才越权”的方案。
口径相同（16 关 × seeds 1–5，6 workers，`--stage-rules`，`v3.3.0`）。

| 方案 | 做法 | 三星 | 对照（同配对 scripted） | 成本 |
|---|---|---:|---:|---:|
| `strict` 全关 | 仅当引用 `_<stage>` 技能时越权 | 48/80 = 60.00% | 57/80 = 71.25% | $0.1932 |
| `skillonly`（6 个亏损关） | 仅在技能选择上越权，打牌/切人交 expert | 18/30 = 60.00% | 27/30 = 90.00% | $0.0618 |
| `expert-hint`（同 6 关） | prompt 里给出 expert 建议 | 15/30 = 50.00% | 27/30 = 90.00% | $0.0898 |
| `noncombat`（5 个弱关） | 只让模型决定留牌/重掷/选人 | 7/25 = 28.00% | 10/25 = 40.00% | $0.0850 |

- `strict` 是四种里最好的，但仍比 scripted 低 9 个百分点；逐关看，收益只有
  buyun +1、timaeus +1，亏损为 iron −4、marjorie −2、timmie −2、liusu −1、
  shitou −1、uncletian −1，其余关与 scripted 完全相同。
- `skillonly` 把 iron 从 0/5 修回 4/5，但 shitou 从 4/5 掉到 0/5，净负；
  `expert-hint` 让 shitou 掉到 1/5；`noncombat` 在 blanche/timaeus/buyun 全部 0 增益。
- 结论：模型在任何一个决策子空间上都没有稳定优于 expert 的信号。

### expert 规则旋钮网格（免费，无 API）

为了确认上限是否来自 expert 本身，用 `era_policy(rule_overrides=...)` 扫了
`setup_rounds ∈ {0,1,2}` × `switch_hp_threshold ∈ {4,6}`（seeds 1–5，16 关，无 LLM）：

| 配置 | 三星 |
|---|---:|
| `setup_rounds=2, switch_hp_threshold=4`（默认） | **57/80** |
| `1,4` | 57/80 |
| `0,4` | 55/80 |
| `0,6` | 49/80 |
| `2,6` | 46/80 |

默认配置就是最优；`--base-seed 1` 重跑 scripted 与 seed 0 **逐局完全一致**（0 flips）。
因此 expert 规则/平局种子没有可利用的余量，dynamic 无法靠“换一个更强的脚本底座”超过
scripted。

## 6. 总预算与结论

- 第二轮新增花费 $0.4298；加上第一轮 $0.9932，本轮全部实验 **≈$1.42**，低于 $3。
- 所有尝试（记录板、轮级计划、丰富渲染、三种兜底、动作类型闸门、关卡白名单、
  expert 提示、非战斗越权、expert 旋钮网格）都**没有**让 dynamic 在配对口径上超过
  scripted。最好的纯 LLM 方案是 `rich_guard` 合并 82/144 = 56.94%（scripted
  95/144 = 65.97%）；最好的“少越权”方案是 `strict` 60.00% 对 71.25%。
- 结论：在当前模型（`deepseek-v4.1-flash`，`reasoning_effort=none`）、`dyn_v4`
  库和逐动作决策架构下，**dynamic 无法超过 scripted**。下一步若要继续，应改变架构
  而不是继续调 prompt：例如逐轮回放/搜索、让 LLM 写可执行规则再由 expert 执行、
  或换用更强模型并开启推理；这些都会改变评测口径，需重跑基线。

## 7. 复现

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
