# 来源与我们的改动（课程要求：必须注明）

> 课程要求：「如参考开源项目，在报告中注明项目来源、写清项目网址、写清创新点，并在代码中注明每一个创新点。」

## 1. 上游链路

| 层 | 项目 | 版本/commit | 许可 |
|---|---|---|---|
| 引擎 | [piovium/genius-invokation](https://github.com/piovium/genius-invokation) | `6f01d5c1c1cee1791d6b9b3bfff5312208ce0a21`（PyPI `gitcg==0.21.0`） | AGPL-3.0 |
| 本仓库环境层快照 | [Peruere1828/genshin-GITCG](https://github.com/Peruere1828/genshin-GITCG) | `98b4dfa`（2026-10-28） | AGPL-3.0 |
| 表示层蓝本 | Rebel_base_RL（SoG world model） | `b211b031cbfaa1117a60290b32c102fa166a9517` | AGPL-3.0 |
| 脚本专家蓝本 | `gitcg_expert_system`（同上游仓） | 随快照 | AGPL-3.0 |

## 2. 我们从上游「原样拿来」的部分（未改动）

- `envs/`（对局、策略接口、rollout 子进程协议）、`reps/`（观测编码、动作抽象、公共态）、
  `coach/`（复盘）、`agents/scripted/`（20 套脚本牌组规则）、`eval/stats.py`（Wilson/簇区间）、
  `common/`（LLM 客户端、路径、种子）、`web/`（浏览器调试桥）、`npc/`（PvE 关卡/目标/三星判定）、
  `configs/npc/`、`configs/pools/`、`engine-overlay/`（NPC 敌人 `.gts` 与构建脚本）。
- 这些目录的来源与改动逐条见上游 `NOTICE.md`；本仓库保留该文件。

## 3. 上游基础上我们做的改动

| 文件 | 改动 | 目的 |
|---|---|---|
| `envs/policy.py` | 删除 `neural:<ckpt>` 分支，改为显式报错 | 本仓库**不依赖 torch**（课程项目不训练模型） |
| `engine-overlay/overlay/packages/core/src/**` | PvE 冒险挑战关卡规则：`EntityState.stageRule/removable`；`Game.start()` 经正常支援牌打出路径放置初始支援（计入 4 张上限）；`initial_energy`；行动阶段开始的 `element_application` 合成技能；不可移除支援的替换/弃置保护（`state.ts`、`game.ts`、`mutator.ts`、`utils.ts`、`runtime/skill_context.ts`、`base/mutation.ts`、新增 `stage_rule_effects.ts`） | 让关卡特殊规则成为可序列化、可复现的真实引擎状态，而不是 Python 侧近似；无规则属性时行为不变 |
| `engine-overlay/overlay/packages/cbinding/js/{constant.ts,main.ts}` | 新增字符串 CreateParam 属性 `GITCG_ATTR_CREATEPARAM_STAGE_RULES = 2002`（JSON: `player`/`supports`/`effects`） | 把 `configs/npc/special_rules.json` 的规则从 Python 传入引擎；`gitcg.h` 由 `prepare.ts` 自动重生成，无 C++ 改动 |
| `engine-overlay/patches/stage-rules.patch` | 上述 core/cbinding 改动的完整 diff（与 `overlay/` 内容一致） | 引擎重克隆后可直接 `git apply` 复现；自动构建走 `overlay/` |
| `engine-overlay/**/characters/*.gts` | 敌人 `health` 按 D4 规则上调（≤6→6，其余 +2）。官方可玩角色 2301/2202 保持原版 11/10；PvE 血量 13/12 移到独立 NPC 变体 `*_npc.gts`（9301/9202，独立技能/状态号段） | 难度标定，让三星率有区分度，同时不污染角色卡数据 |
| `configs/npc/*.json` | `hp_card` / `lineup[].hp` 同步；`swan` 补目标；`shitou` 修 `win_within_rounds(44)`；`hp_table` 指向 NPC 变体文件 | 数据一致性 + 任务可判定 |
| 删除 | `train/`、`kaggle/`、`scripts/` 训练相关脚本、搜索/分叉模块 | 交付物只保留课程项目需要的部分 |
| 删除 | 26 个上游遗留测试模块 + `tests/test_belief_root.py` 中 7 个 fork-search 用例 | 这些测试只覆盖已删除的 `train/`、`envs/fork_*`、`envs/cfr_search`、`scripts/*`；留着会让 `pytest -m "not slow"` 收集失败 |
| `envs/match.py`、`npc/stage_rules.py`、`npc/eval.py` | 新增 `CreateParam` 字符串属性 `ATTR_CREATEPARAM_STAGE_RULES=2002`，按 `configs/npc/special_rules.json` 注入关卡初始支援 / 开局效果 | 让冒险挑战特殊规则真正进入引擎，而不只停留在配置/文档 |
| `common/llm.py` | 可选 `GITCG_LLM_TRACE`：每次调用追加 JSONL（messages/content/reasoning_content/finish_reason/usage）；实例暴露 `last_reasoning` / `last_finish_reason` | 调试 prompt 与思考输出；不参与决策，密钥不落盘 |
| `web/ai_rpc_server.py`、`engine-overlay/.../AiDecisionPanel.tsx` | 决策行增加 `llm_called`/`llm_error`/`reasoning`/`finish_reason` | 区分 expert 步、解析失败、max_tokens 截断，并展示思考摘录 |

## 4. 本项目的创新点（代码中标注 `INNOVATION:`）

1. **库级配对反事实门控**：技能能否入库由「同种子、同关卡、开/关技能库」的配对三星率决定，
   而不是 LLM 自评（`skillloop/gate.py`）。
2. **分层技能库**：规则层 / 策略层 / 关卡特化层，检索时按条件优先匹配特化层（`skillloop/library.py`）。
3. **激活日志驱动的裁撤**：每条技能记录被检索/被引用的决策数，长期零激活或消融为负则退役
   （`skillloop/library.py`、`skillloop/extract.py`）。
4. **难度标定任务集**：按目标类型与回合分布标定三星阈值，使任务处于「可达成但有区分度」区间
   （`scripts/patch_npc_hp.py`、`PLAN.md §2`）。
5. **LLM 智能体的紧凑渲染**：状态摘要 + 候选 ID + top-k 技能，稳定前缀以命中 prompt 缓存
   （`skillloop/render.py`）。
