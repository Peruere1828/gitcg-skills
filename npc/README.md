# npc — NPC PvE 调试系统（PLAN.md §3 WS7 / §4.4，D17/D19）

确定性 NPC 假想敌池 + 关卡目标判定 + 独立门禁 **PvE-v1**（decklab 组牌 × 16 关 × 多种子，三星达成率 ≥ 60%）。
定位＝**调试/训练辅助**，不追求复刻游戏内体验、不改动 WS1/WS3/WS4 主线；强度口径同为**纯策略**（LLM 不参与）。
敌人/关卡数据的权威在 [`configs/npc/`](../configs/npc/README.md)（本包只读不写），机制差异与 schema 未覆盖项也在那里。

## 模块

| 文件 | 职责 |
|---|---|
| `spec.py` | `NpcStage`/`NpcEnemy`/`NpcSkill`/`NpcObjective` 的类型化校验视图；名称→引擎角色 id 映射；技能伤害表（skill id 约定 `character_id * 10 + k`，k 按 A/E/Q/被动的顺序）。加载 `configs/npc/*.json` |
| `policy.py` | `NpcPolicy`——确定性 greedy + rotation 敌人策略（`ROTATIONS` 命名为 `greedy`/`cycle_*`）；NPC 不打行动牌、不元素调谐，切人交给引擎被动；`npc_policy_for_stage` |
| `objectives.py` | 挑战目标判定 + 三星决策（全目标达成）；逐目标类型的证据来源见模块 docstring |
| `contestant.py` | 门禁参赛方 `ContestantPolicy`——确定性基策略（默认脚本专家）+ 目标注入（按 kind 打装备/事件牌、用爆发）；`build_contestant_profile` |
| `eval.py` | `NpcEvalSpec` / `run_npc_eval`——关卡 × 种子评测（胜率 + 三星率 + 逐目标达成率），流式落盘 + 指纹护栏 + 按 `task_index` 断点续跑 |
| `eval_worker.py` | 子进程 worker（JSON-lines，stdin/stdout），由 `envs.rollout` 形态并发拉起 |

## 入口与命令

```bash
# 冒烟评测（脚本专家 vs 全 16 关 × 3 种子）
python -m npc.eval --stages all --deck superconduct_aggro \
    --policy expert:superconduct_aggro --seeds 1,2,3 --workers 6 --tag npc_pve_smoke

# PvE-v1 门禁（decklab 池 C 组牌 × 16 关 × 多种子 + 组牌/打牌消融，可断点续跑）
python -m scripts.run_npc_gate --stages all --seed-count 24 --workers 8 --tag pve_v1_gate
```

逐局原始记录落 `data/npc/`（gitignore，+ `.meta.json` 指纹护栏），聚合摘要落 [`reports/npc/`](../reports/npc/README.md)（入库）。

## 硬约束

- 对局只经 `envs.match.run_match` + `Policy`（观测仅取 `notification.state`），信息泄漏门禁同样适用。
- **玩家恒定先手**（复刻时 NPC = player 1）。
- 确定性：`NpcPolicy` / `ContestantPolicy` 是合法动作集（+ 少量每局计数器）的纯函数，同种子逐字节可复现。
- 目标判定需要 `record_decisions=True, record_events=True, record_final_state=True`（`npc.eval` 已默认开）。
- 出牌策略由我们自己生成，**不**照抄原作逐回合序列。

## 现状

- 门禁已达标：`reports/npc/pve_v1_gate.json`（16 关 × 24 种子 = 384 局，三星率 0.747、wilson 下界 0.702、对手聚类下界 0.630，`passed: true`）。
- 已知上限：`win_within_rounds` 是主要失分项（打装备/事件牌牺牲节奏，"目标达成"与"速胜"存在张力）。
- 未在引擎内模拟的关卡特殊规则、来源待核清单、schema 未覆盖的**支援区/多轮挑战**：见 [`configs/npc/README.md`](../configs/npc/README.md)。

## 测试

`tests/test_npc_{spec,enemies,policy,objectives,contestant,eval,gate}.py`。

## 待办

- 关卡特殊规则引擎内模拟（`reports/npc/pve_v1_gate.json` 的 `open_issues`）与待核数据补齐。
- decklab 学习/Agent 式自动组牌落地后**重跑门禁**（当前用 `decklab/builder.py` 规则组牌近似 D19）。
