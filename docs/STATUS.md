# STATUS — 当前状态（2026-10-30）

> 只写「现在有效」的事实；历史进 `reports/`，负结果进 `docs/failures/`。
> 任务与验收见 [`../PLAN.md`](../PLAN.md)，统计口径见 [`EVAL_PROTOCOL.md`](./EVAL_PROTOCOL.md)。

## 1. 仓库与环境

| 项 | 状态 |
|---|---|
| 仓库 | `~/projects/gitcg-skills`，从上游 `genshin-GITCG@98b4dfa` 快照（2026-10-28） |
| 环境层 | `envs/ reps/ npc/ coach/ agents/ eval/ common/ web/ configs/ engine-overlay/` 原样快照 |
| 已删除 | `train/`、`kaggle/`、`scripts/` 训练脚本、`envs/cfr_*`、`envs/fork_*`、`agents/neural.py` |
| conda env | `gitcg-skill`（Python 3.12，`gitcg==0.21.0`，numpy，pytest；**无 torch**） |
| 引擎 | 上游 overlay 版 `libgitcg.so`（快照时的构建）；血量补丁后**待重链** |

## 2. 难度标定（D4）

- 规则：敌人血量 **≤6 → 6，其余 +2**；审计快照 `configs/npc/hp_table.json`（19 类敌人 / 16 关）。
- 已改：17 个 overlay `.gts`；2 个上游敌人（`fatui_pyro_agent` 11→13、`mirror_maiden` 10→12）
  已复制为 overlay 覆盖文件。
- 发现并修正：`fatuipyroagent` 的 `hp_card` 原记 9，引擎实际 11（配置与引擎不一致）。
- 待办：增量重链 `libgitcg.so` → 装进 `gitcg-skill` → 用 `contestant` 跑 24 seeds → 重标
  `win_within_rounds`（P70）→ 记录新基线。

## 3. 基线（待填）

| 组 | 策略 | 三星率 | 备注 |
|---|---|---|---|
| 脚本 | `expert:<deck>` | TBD | 无目标注入 |
| 参照 | `contestant`（上游手写目标策略） | 上游旧血量 74.7% | 新血量下待测 |
| LLM 空库 | TBD | TBD | 等 DeepSeek 接入 |

## 4. 已完成 / 进行中

已完成：
- 仓库快照 + 裁剪 + 文档骨架 + 课程要求入库（首个 commit）。
- `gitcg-skill` env（gitcg 0.21.0 / numpy / pytest，无 torch）。
- 血量补丁（19 敌人 / 16 关）+ 审计 `configs/npc/hp_table.json`。
- `skillloop/` 六个模块：`schema`（技能表示）、`library`（分层检索/激活/版本快照）、
  `render`（稳定前缀 + 编号候选，真实对局实测 system 211 字符 / user 506 字符）、
  `agent`（LLM 直选 + 回退 + 逐决策日志）、`gate`（按关配对 t 检验）、`extract`（失败画像 → 候选技能）。
- 34 个 engine-free 单测全绿；`scripts/probe_render.py` 可在真实对局上检查 prompt。

进行中：
- 引擎增量重链（V8 全量首编，`build/engine-build.log`，约 583/1232）。
- DeepSeek/OpenCode Console 接入探针（子任务），产出 base_url / model / 计费字段。

## 5. 已知风险

1. **任务集小**：16 关，留出泛化只有 6 关；靠「50 seeds/关 + 按关配对」提升功效。
2. **与上游共享机器**：本机 6C/12T，实验限 6–8 worker，`OMP_NUM_THREADS=1`。
3. **llm 成本口径**：缓存命中价差 50×，prompt 必须「稳定前缀 + 动态尾」。
4. **关卡数据**：`swan` 只有 `win` 一个目标（三星恒真）、`shitou` 的 `win_within_rounds(44)` 疑似错误，待修。
