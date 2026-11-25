# 004 — 手写策略注入越多，三星率越低

- status: open（2026-11-25 实测；机制未定，需逐条消融）
- 证据：`data/runs/v10_static_s1_5.jsonl`（旧 loadout，实际注入 1–3 条策略）vs
  `data/runs/v12_static_s1_5.jsonl`（loadout 修好后每关注入 5 条），同 stage/seed 配对，
  16 关 × seeds 1–5 = 80 局，0 错误。
- 同批对照：`v12_llm_empty`（无库）相对 `v10_llm_empty` 净 −1（p=1.00），说明 prompt 结构、
  历史段与 rule 层删除没有拖累决策；差异集中在策略注入。

## 现象

| 组 | 策略注入 | 三星 | 三星率 |
|---|---|---|---|
| `v10 static` | 每关 1–3 条（rule pin 占掉了名额） | 47/80 | 58.75% |
| `v12 static` | 每关 5 条 | 38/80 | 47.50% |
| `v12 llm_empty` | 0 条 | 44/80 | 55.00% |

配对 `v12 vs v10`：仅前者胜 3、仅后者胜 12，净 −9，McNemar p=0.035。
掉的关：`lan 4→2`、`uncletian 4→2`、`timmie 2→0`、`wagner 4→3`、`marjorie 2→1`。

## 为什么可疑

- 5 条策略里包含 `strat_dice_economy`（「用最少骰子完成本轮目标，剩骰优先调和/保留」）和
  `strat_end_round`（「没收益就结束回合」）。这两条在 PvE 里可能鼓励**过早收手**：
  三星目标常要求「N 回合内胜利」，结束回合会把先手让给 NPC。
- `strat_reaction`/`strat_focus_fire` 与关卡目标（如 `win_within_rounds` + `defeated_less_than`）
  经常冲突：集火残血会放弃对高威胁角色的控制。
- 旧 loadout 只留 1–3 条，恰好是规划器认为最相关的；补到 5 条后，冲突策略同时出现，
  模型需要在互相矛盾的指令里自己仲裁。

## 影响面

- 所有 `static`/`dynamic` 组的结果：此前「static 略高于 llm_empty」的结论不成立
  （v12 里 static 比 llm_empty 低 7.5pp，p=0.146）。
- dynamic 两轮（`reports/dynamic_rounds_20261125.md`）同样观察到「加技能无收益」，
  与本条方向一致。

## 下一步

1. 逐条消融：固定 0 条为基线，每关逐条开关策略（5 条 → 单条），同 seeds 配对；
   只保留 `CI_low > 0` 的策略。
2. 若消融后仍无正收益，把 `static` 组定义为「策略数可调」而不是固定 5 条，
   并在论文/报告里如实报告「手写策略在此环境中无正向证据」。
3. 不要再用「补满 loadout 名额」作为改进；名额本身不是目标。
