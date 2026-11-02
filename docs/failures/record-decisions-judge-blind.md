# 002 — 判定看不到出牌：3 关在每组里恒为 0

- status: invalid（测量口径失效；2026-11-02 已修复，受影响 run 待重跑）
- 日期: 2026-11-02（发现、修复并补回归测试）
- 证据: `reports/decision_audit.md` §5（controlled replay：`uncletian` seed 2 只改 `record_decisions`
  就从 2★ 变 3★）；`reports/t5b_summary.md` §6（装备 20/20、事件 10/10 恒失败）；
  `reports/star_gap_analysis.md` §2/§5（六组里两行完全恒定）；修复后的免费重放产物
  `/tmp/opencode/free_scripted_on.jsonl`、`/tmp/opencode/free_contestant_on.jsonl`（工作区外临时产物；
  复跑命令见下）
- 口径: 16 关 × seeds 0–9 = 160 局/组；`superconduct_aggro`；`scripted`（expert）与 `contestant`；
  engine lock `fa42fdfec70b` / hp_table `0bbae5d8798a`；2026-11-02

## 现象

- `timaeus`（装备）、`wagner`（装备）、`uncletian`（事件牌）在 `skillloop.run` 的**每个组**里都是 0 星；
  `play_equipment_card` 恒为 **20/20 失败**、`play_event_cards` 恒为 **10/10 失败**，跨 static / dynamic /
  empty 完全不变（`reports/t5b_summary.md` §6、`reports/star_gap_analysis.md` §2）。
- 同一批种子上，固定脚本 expert 明明会打出对应卡牌：controlled replay 只把 `record_decisions` 从
  `False` 改成 `True`，`uncletian` seed 2 就从 2★ 变 3★（`reports/decision_audit.md` §5）。
- 修复后同 seeds 免费重放：
  `scripted` 0.3812 → **0.4250**（61→68/160，7 局翻正、0 局翻负），
  `contestant` 0.4062 → **0.4813**（65→77/160，12 局翻正、0 局翻负）。
  `timaeus` 装备：scripted 0/10 → 2/10 达成、contestant 0/10 → 10/10；
  `uncletian` 事件：scripted 0/10 → 8/10、contestant 0/10 → 10/10。
  `wagner` 仍 0/10：牌组没有武器牌，见「影响面」。

| 组 | 旧判定 3★ | 修复后 3★ | 翻转 | `timaeus` 装备 | `uncletian` 事件 |
|---|---:|---:|---:|---:|---:|
| scripted | 0.3812（61/160） | **0.4250**（68/160） | +7 / −0 | 0/10 → 2/10 | 0/10 → 8/10 |
| contestant | 0.4062（65/160） | **0.4813**（77/160） | +12 / −0 | 0/10 → 10/10 | 0/10 → 10/10 |

## 根因

- `npc/objectives.py` 的 `play_equipment_card` / `play_event_cards` 从 `MatchRecord.decisions` 里数
  `DecisionRecord(chosen_kind="action_play_card", card_kind=...)`；`_count_decisions` 对空列表求和恒为 0。
- `skillloop/run.py::play_one` 调 `run_match(..., record_decisions=False, ...)`，所以 `MatchRecord.decisions`
  永远是空列表；两个目标因此**永远为 false**，无论模型/脚本实际打了什么牌。
- 这是**判定输入没有被采集**，不是策略、技能库或检索的问题。`npc/objectives.py` 的模块 docstring 已经写明
  「完整判定需要 `record_decisions=True, record_events=True, record_final_state=True`」，但 runner 只开了后两个。

## 为什么没早发现

- 行级聚合看起来完全合理：三星率 0.30–0.48、胜率 0.94–0.99、逐关表有涨有跌，没有异常值或报错。
- 「3 个关卡一直 0」很容易被解释成「LLM 不会主动打装备/事件牌」，与「模型不擅长具体动作」的先验一致；
  `reports/t5b_summary.md` 当时就把它写成了「知道但没照做」。
- 没有正例断言：测试只覆盖了「没打牌 → 判 false」的负例，没有「打了牌 → 必须判 true」的正例，更没有一条
  正例穿过 `skillloop.run::play_one` 的真实 runner 路径。
- `record_decisions=False` 被当作省内存/省日志的默认值；它同时是判定器的必需输入，这个耦合没有任何测试保护。

## 影响面

- **自 runner 写出以来，所有用 `skillloop.run` / `play_one` 产出的 run**：t5 基线（`t5_scripted` / `t5_contestant`）、t5b 五组、
  evo 三轮 + 转移、以及用 `skillloop.run` 生成的 `calib_contestant.jsonl`（阈值标定）。这些 run 在
  `timaeus` / `uncletian` 上的三星率与 `play_*` 失败率系统性偏低；所有跨组 Δ/门禁若包含这两关，都需重算。
- **`wagner` 的 0 不是这个 bug 造成的**：`superconduct_aggro` 是 30 张 / 21 唯一，**0 张武器牌**，而
  `wagner` 要求打出武器。修复后牌组结构上限是 15/16（只有 `wagner` 不可达）。
- `skillloop/parked.py` **不受影响**：它不走 `run_match(record_decisions=...)`，而是自己用
  `PolicyPlayer(recorder=task.decisions)` 采集，再经 `_MiniRecord` 交给 `evaluate_stage`。
- `npc.eval` 一直显式 `record_decisions=True`，不受影响；`scripts/audit_trajectory.py` 的评分路径也用 `True`；
  `scripts/probe_render.py` 的 `False` 只用于渲染探针，不参与评分。

## 处置

- **修复**：`skillloop/run.py::play_one` 的 `record_decisions` 改为 `True`（一行）。
- **回归测试**：`tests/test_skill_run.py::test_play_one_records_decisions_for_card_objectives` 用
  `timaeus` seed 7 断言 `row["objectives"]["play_equipment_card"] is True`。它测的是「目标可观测」，
  不是「策略好」。
- **测试清理**：`tests/test_parked.py` 不再在 spy 里手动覆盖 `record_decisions=True`，因为 `play_one`
  现在自己就会记录。
- **报告更正**：`reports/t5b_summary.md`、`reports/star_gap_analysis.md`、`reports/evo_summary.md`、
  `docs/STATUS.md` 已加 2026-11-02 更正横幅；`reports/decision_audit.md` 记录 bug 与修复；
  `reports/trajectory_audit.md` 加了一行「不受影响」的附注。
- **重跑**：所有受影响 run 的三星率与门禁必须用修复后的 runner 重跑后才可引用。
  免费基线可这样复跑（不调模型）：

  ```bash
  PYTHONPATH=. python -m skillloop.run --group scripted --stages all --seeds 10 --top-k 5 \
    --workers 6 --out data/runs/judgefix_scripted.jsonl
  PYTHONPATH=. python -m skillloop.run --group contestant --stages all --seeds 10 --top-k 5 \
    --workers 6 --out data/runs/judgefix_contestant.jsonl
  ```

## 教训

- **每个被判定的目标都需要一个正例测试**，而不只是负例：不只验证「没做时判 false」，还要验证「做了时
  必须判 true」，并且让这条正例经过真实的 runner/评分路径（`play_one` → `run_match` → `evaluate_stage`），
  不能只测 `evaluate_objective`。
- **恒定不变的聚合列是危险信号**：如果一个目标在所有组、所有种子上完全一样，先怀疑测量，再解释行为。
- **判定器的输入开关应由判定需求声明**，而不是由 runner 默认关掉；`npc/objectives.py` 的 docstring 已经
  列出三个 flag，runner 却没有遵守。
- 一个「可达成但 100% 失败」的目标应该触发不变量/告警，而不是被写进分析当结论。

## 重开条件

- 用修复后的 `skillloop.run` 重跑 t5b / evo 全链（同 seeds、同库、同 engine lock），并重算门禁与逐关表。
- 在 `timaeus` / `uncletian` 上至少有一个组出现非零的 `play_equipment_card` / `play_event_cards`，
  证明修复后的判定在真实 runner 路径上能产出 true。
- 若重跑后这两个目标仍恒为 false，才按「策略 / 检索 / 指令遵循」问题重新立项，并附逐决策证据。
