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

## 3. 基线（10 seeds × 16 关 = 160 局）

| 组 | 策略 | 三星率 | 胜率 | 备注 |
|---|---|---|---|---|
| 脚本 | `expert:superconduct_aggro` | **0.4125** | ~96% | 无目标注入；新血量下 |
| 参照 | `contestant` | 运行中 | — | 上游旧血量 74.7% |
| LLM 空库 | `deepseek-v4.1-flash` | 待跑 | — | 端点已接通（见 §5） |

脚本组逐关（新血量）：`jifang`/`swan` 1.00，`patton`/`lan` 0.90，`timmie`/`buyun` 0.80，
`shitou` 0.50，`marjorie` 0.40，`ellin` 0.20，`chefmao` 0.10，
`wagner`/`timaeus`/`liusu`/`irontonguetian`/`uncletian`/`blanche` **0.00**。
→ 血量补丁只压三星、几乎不影响胜负（胜率仍 ~96%），符合 D4 的预期。

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

## 3.1 阈值标定（24 seeds × 16 关 = 384 局，手写策略）

按「胜局回合 P70」重标 `win_within_rounds`（明细 `reports/calibration/thresholds.json`）：

| 关卡 | 旧 | 新 | 关卡 | 旧 | 新 |
|---|---|---|---|---|---|
| blanche | 5 | **9** | liusu | 5 | 6 |
| chefmao | 5 | 7 | shitou | **44** | 6 |
| ellin | 5 | 7 | timaeus | 5 | 6 |
| irontonguetian | 5 | 6 | wagner | 5 | **2** |
| jifang | 5 | 3 | marjorie | 5 | 6 |
| lan | 6 | 5 | **swan** | （无） | **+5** |

标定后手写策略总三星率 0.487（标定前 0.4375）。
仍为 0% 的 3 关（`wagner`/`timaeus`/`uncletian`）失分点不是回合数，而是
`play_equipment_card` / `play_event_cards` —— 这正是技能库要补的**策略**缺口（不是阈值问题）。

## 3.2 标定后 10-seed 复测（新任务集）

| 组 | 三星率 | 说明 |
|---|---|---|
| scripted | **0.5625** | 纯脚本（无目标意识） |
| contestant | **0.6250** | 手写目标注入；配对 Δ=+0.062，CI [−0.020,+0.145] |

**剩余失分画像（scripted，10 局/关）**：`play_equipment_card` 10/10 失败（timaeus、wagner）、
`play_event_cards` 10/10（uncletian）、`use_elemental_burst` 3–6/10（blanche、irontonguetian、liusu）、
`trigger_elemental_reactions` 5/10（shitou），其余主要是 `win_within_rounds`（约 30%）。
→ 任务集现在**未饱和**（0.56），且失分集中在可被"指令遵循"解决的具体行为上。

## 4. LLM 接入（2026-10-30 实测）

| 项 | 值 |
|---|---|
| 端点 | `https://opencode.ai/inference/go/openai/v1`（OpenAI 兼容） |
| 模型 | `deepseek-v4.1-flash`（1M 上下文；本仓库实测 2.6–4.9 s/次） |
| 认证 | `Authorization: Bearer $SKKEY` + **`x-opencode-session`（必填）** + 自定义 `User-Agent` |
| 关闭思考 | `reasoning_effort="none"`（否则小 `max_tokens` 返回空串） |
| 缓存 | 短 prompt（~170 tokens）实测 **0%** 命中；长前缀（>4k）才命中 |
| 成本 | 3 次真实调用 = **$0.00009**；按 800 局/轮估算 <$5 |
| 并发 | 128 并发实测全绿；256 起出现 429（有 `Retry-After`） |

`common/llm.py` 已改为：读 `SKKEY`、发 session/UA 头、空内容判失败、429 按 `Retry-After` 重试、
usage 记录 `cached_tokens` 与 `cost_usd`。探针：`python -m scripts.probe_llm`。

## 5. 已知风险

1. **任务集小**：16 关，留出泛化只有 6 关；靠「50 seeds/关 + 按关配对」提升功效。
2. **与上游共享机器**：本机 6C/12T，实验限 6–8 worker，`OMP_NUM_THREADS=1`。
3. **llm 成本口径**：缓存命中价差 50×，prompt 必须「稳定前缀 + 动态尾」。
4. **关卡数据**：`swan` 只有 `win` 一个目标（三星恒真）、`shitou` 的 `win_within_rounds(44)` 疑似错误，待修。
