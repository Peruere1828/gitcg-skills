# 结果清单与去留（2026-11-25 盘点）

> 生成于 2026-11-25。回答一个问题：2026-11-25 基线之前的结果，哪些能删。
> 判定口径见 `docs/STATUS.md §4.1` / `PLAN.md` D16–D17；基线数值见
> [`baseline_20261125_pve_prompt_v1.md`](./baseline_20261125_pve_prompt_v1.md)。

## 0. 两个硬事实

1. **`data/runs/` 不入库**（`.gitignore: data/*`，`git ls-files data/runs` = 0），删了不可恢复；
   共 61 个 `.jsonl`、9.5 MB、累计花费约 **$17.0**。
2. **run meta 里没有 `prompt_hash`**，也没有 `code_sha`。`fingerprint` 只覆盖引擎锁、血量表、
   stage_decks/loadout hash——**`v3`–`v9` 与 `v10` 的 `engine_lock` 完全相同**，
   单看指纹无法区分 `5d9e445`（NPC 万能骰）之前/之后。历史 run 的「是否旧 prompt」
   只能靠 mtime + `skill_loadout_hash` + git 历史判定。

`skill_loadout_hash` 命中率是唯一可靠的结构化判据：以 `v10_static` 的 16 关哈希为基准，
**只有 `v10_static_s1_5` 80/80 命中**（当前 `static_v1` v2 + `loadouts/v2`）；
`v2/v3/v4/v5/v6/v7/v8/v9` 的 static/dynamic run 全部 0/N。

## 1. 必须保留

| 项 | 原因 |
|---|---|
| `data/runs/v10_{scripted,llm_empty,static}_s1_5.jsonl` + `.meta.json` | 当前唯一有效基线；不入库，删了永久丢失 |
| `reports/prompts/`（恰好 2 个文件） | `tests/test_prompt_samples.py` 断言精确集合，多/少一个都 FAIL |
| `reports/loadouts/v2/` | 当前 static 基线；`run_all_groups.sh`、`plan_loadout.py` 默认值 |
| `reports/loadouts/v4/` | `tests/test_web_rpc_server.py` 硬编码该路径 |
| `reports/baseline_20261125_pve_prompt_v1.md` | `docs/STATUS.md §4.1` 链接 |
| `reports/dynamic_improvements_20261113.md` | `configs/stage_profiles.json` 的 lan 依据；数值已作废但设计说明被引用 |
| `reports/{decision_audit,t5b_summary,star_gap_analysis,evo_summary,trajectory_audit}.md` | 被 `docs/failures/*` 逐条引用 |
| `reports/calibration/thresholds.json` | `PLAN.md` D7 的标定存档 |
| `reports/{deck_matrix,era_decks,npc_special_rules,engine_stage_rules_plan}.md` | 分别是 `configs/stage_decks.json`、era 牌组、`special_rules.json`、overlay 的 provenance |
| `data/runs/{t5b_static,t5b_dynamic,evo_r3_dynamic,evo_hold_*,v4_dynamic_r1,v5_dynamic_r2,v6_dynamic_r3,v3_scripted_1_15}` | `scripts/star_gap_analysis.py` 硬编码、`docs/failures/dynamic-vs-scripted-20261112.md` 引用 |
| `data/runs/era_matrix.jsonl` | `scripts/probe_stall.py --replay-all` 依赖 |

## 2. 口径已废（保留，仅作历史证据）

`data/runs/` 下其余 50 个 run 全部属于此类：seed 0 时代（`smoke*`、`t5*`、`calib_*`）、
pin `card_version=v3.3.0`（`v2`/`v3`/`v4`）、旧 prompt（`v7`/`v8`/`v9`）、
已废技能库（`dyn_v1`–`v6`）。它们是 14 份 report/failure 的数字来源，**删了会让文档悬空**。

已删除（仅 2 份，3 行残片，无任何引用）：

```
data/runs/v7_llm_empty_full_none_s1_5.jsonl(.meta)   # 中止残片
data/runs/v7_static_full_none_s1_5.jsonl(.meta)      # 中止残片
```

## 3. 报告层处理

| 文件 | 处理 |
|---|---|
| `reports/prompt_rewrite_20261113.md` | 加「数值作废」横幅，保留设计演进 |
| `reports/prompt_audit_20261114.md` | 加横幅：15 条规则修复仍有效，数值作废 |
| `reports/compare/`、`reports/loadouts/v1|v5|v6`、`reports/*_20261112.md`、`era_decks_pre_rules.md`、`stall_analysis.md` | 未动。无测试依赖，但与 failures 的引用链绑定；删了只省几百 KB，收益不抵悬空风险 |
| `reports/arena/` | 不存在。`web/counterpick.py` 读不到返回 `None`，对应测试 skip |

## 4. 结论

**不建议批量删除。** 除已删的 2 份残片外，每个旧 run 要么被脚本/文档引用，要么是
`sunk cost` 已经付过的唯一证据；而 `data/runs` 不入库，删除不可逆。需要「一眼看出哪些作废」
时用本文档，不要靠删文件。

要避免下次再需要这个盘点，应在 run meta 里补两件事（尚未实现）：

- `code_sha`（`git rev-parse HEAD`）——现有 `engine_lock` 不覆盖 `envs/`、`skillloop/`；
- `prompt_version` + 首个决策的 `system_hash`——`Decision.system_hash` 目前已计算但未落盘。
