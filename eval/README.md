# eval — 评测与基准（PLAN.md WS2，先于训练建成）

对手池注册表（20 套，D11；refs/Rebel_base_RL/.../gitcg_expert_system/deck_rules/）、arena 并行批量对局、
TrueSkill/Elo 阶梯（固定基线 + 历史 checkpoint 双向）、分卡组胜率矩阵、逐决策日志（供 coach/ 消费）。

## 已实现（2026-10-07）

- `opponents.py`：对手池注册表（20 套脚本专家 `expert:<slug>` + heuristic / legal_random 基线）。
- `stats.py`：Wilson 置信区间与 `estimate_rate`（小样本验收口径）。
- `arena.py`：`ArenaSpec`/`run_arena`（contestant × pool × paired seeds，可选 swap），输出
  每对局矩阵（含 CI）、contestant 总胜率、Elo；落盘 `reports/arena/*.json|csv`，原始记录 `data/arena/*.jsonl`。
  CLI：`python -m eval.arena --smoke --seeds 3 --workers 12`。
- `ladder.py`：基于对局记录的 Elo（固定基线 + 历史 checkpoint 的阶梯骨架）。

## 强度评测协议（2026-10-11）

- `scoreboard.py`：协议统计层（无引擎依赖，纯 stdlib）——逐对手 + 汇总胜率（Wilson 95% CI）、
  **对手聚类 CI**（对手为簇 + 聚类 bootstrap）、配对种子 A/B 差（`paired_difference` / 非劣性门禁）、
  固定锚点 **Bradley-Terry/Elo MLE**（`anchored_bt_rating`）、决策质量代理、最优反应探针（`best_response_probe`）、
  样本量规划（`sample_size_table` / `floor_sample_size`）。
- `scripts/eval_protocol.py`：跑分板 runner——任意 `build_policy` spec（`expert:`/`neural:`）对固定池、
  双侧同种子、逐局流式落盘 + 按 `task_index` 断点续跑 + spec 指纹护栏；聚合落 `reports/explore/`，含 `--baseline` 配对门禁与 `--sample-sizes`。
- 协议全文与验收映射：`docs/EVAL_PROTOCOL.md`；测试 `tests/test_eval_protocol.py`。
  命令：`python -m scripts.eval_protocol --sample-sizes`、`--smoke` 式小样本见文档 §9。
- 当前用 Bradley-Terry/Elo（`anchored_bt_rating`）；待办：历史 checkpoint 双向阶梯、逐决策日志接入 coach、曲线出图。
  另：搜索值 regret / teacher 一致率尚未接入评测流（见 `docs/EVAL_PROTOCOL.md` §4/§11）。
