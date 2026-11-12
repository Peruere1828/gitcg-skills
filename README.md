# gitcg-skills — 卡牌对局中的 LLM 智能体 Skill 自进化

> 课程项目：《专创融合课程 · 面向大模型智能体的 Skill 自进化》
> 原始要求见 [`docs/课程要求.md`](./docs/课程要求.md)。

本项目让一个 **LLM 智能体**在《七圣召唤》PvE 关卡里「打牌」，并把对局经验沉淀成**可检索、可更新、
可淘汰的技能（Skill）库**，在不更新模型参数的前提下越打越好。

- **环境**：本项目自带 PvE 关卡（16 关，蒙德/璃月 NPC，三星判定），引擎为开源的
  `gitcg 0.21.0`（见 [`docs/PROVENANCE.md`](./docs/PROVENANCE.md)）。
- **智能体**：LLM 直接读「状态摘要 + 合法动作候选 + 检索到的技能」选一个动作；无训练、无 torch。
- **核心**：Skill 的 **产生 → 存储 → 检索 → 注入 → 用结果更新/淘汰** 闭环。
- **验证**：按关配对的同种子对照 + 预注册门控（不是「LLM 觉得这条经验好」）。

## 1. 项目要回答的问题

| 课程要求 | 本项目的回答方式 |
|---|---|
| 任务执行 | 每局是一个自然语言任务（关卡 + 三星目标 + 种子），LLM 逐决策选动作 |
| 经验记录 | 逐决策 JSONL（状态摘要、候选、所选动作、检索到的技能、结果、三星明细） |
| Skill 提取 | 从失败/成功局里抽取候选技能（LLM 写 description，规则填 metadata） |
| Skill 检索 | 条件过滤（关卡/目标类型/敌人/回合）+ BM25，取 top-k 注入上下文 |
| Skill 更新 | **库级配对 A/B** 通过才入库；滑窗为负则退役；冲突技能二选一 |
| 创新点 | ① 以「同种子配对反事实」做入库门控 ② 技能分层（规则层/策略层/关卡特化层）③ 激活日志驱动的技能裁撤 |

## 2. 实验分组

| 组 | 技能库 | 说明 |
|---|---|---|
| ① 脚本 `scripted` | 无 | 手写规则/启发式（对照组，无 LLM） |
| ②′ `llm_empty` | `data/skills/rules_v1`（17 条 rule） | 同一条 LLM 智能体 + **仅游戏规则**；不含任何 strategy/stage_specific，不用 loadout |
| ② 静态 `static` | `data/skills/static_v1`（17 rule + 10 strategy） | 游戏规则 + 手写静态策略；可用 `reports/loadouts/v1` |
| ③ 动态 `dynamic` | `data/skills/dyn_v4`（17 rule + 23 strategy） | 从 ② 起步，自主扩展/合并/淘汰；可用 `reports/loadouts/v4` |

指标：**三星率**（主）、逐目标达成率、每局 LLM 调用数/token；按关配对比较，50 seeds/关。

## 3. 快速开始

```bash
conda activate gitcg-skill          # 独立环境，无 torch
cd ~/projects/gitcg-skills

# 跑一关的冒烟
python -m npc.eval --stages timmie --deck superconduct_aggro \
    --policy expert:superconduct_aggro --seeds 1,2 --workers 2 --tag smoke

# 四组实验（按实验协议；库/关卡牌组/loadout 按 run 配置）
python -m skillloop.run --group scripted --stages all --seeds 1 --workers 4
python -m skillloop.run --group dynamic --library data/skills/dyn_v4 \
    --stage-decks configs/stage_decks.json --loadout reports/loadouts/v4 \
    --card-version v3.3.0 --stages all --seeds 1 --workers 4

# 网页调试：人类扮 NPC、AI 跑实际评测协议 + 决策/技能监控
bash scripts/serve_web_demo.sh   # http://<本机IP>:7001/
```

## 4. 目录

```
gitcg-skills/
├── envs/ reps/ npc/ coach/ agents/ eval/ common/ web/   # 从上游快照的环境层（见 PROVENANCE）
├── skillloop/                 # ★ 本项目新增：技能库/检索/提取/门控/LLM 智能体
├── scripts/run_npc_gate.py    # PvE 门禁
├── configs/npc/ configs/pools/# 关卡、敌人、卡池
├── engine-overlay/            # 引擎 overlay（NPC 敌人卡 + 血量补丁）
├── docs/                      # 状态/评测协议/方法/失败/来源
└── reports/                   # 聚合结果（原始轨迹在 data/，不入库）
```

## 5. 许可与来源

- 本仓库代码：AGPL-3.0。
- 环境层快照自 `genshin-GITCG`（commit 见 `docs/PROVENANCE.md`），后者移植自
  `github.com/piovium/genius-invokation`（引擎）与 SoG / Rebel_base_RL（表示层）。
- 上游改动与我们的创新点逐条记在 `docs/PROVENANCE.md` 与代码内 `INNOVATION:` 注释。
