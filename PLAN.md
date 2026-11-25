# PLAN — 任务设计、验收与排期

> 本文件是本课程项目的**唯一权威**：任务定义、实验分组、统计口径、里程碑。
> 当前状态见 [`docs/STATUS.md`](./docs/STATUS.md)；有效方法见 [`docs/PLAYBOOK.md`](./docs/PLAYBOOK.md)；
> 负结果见 [`docs/failures/`](./docs/failures/)；评测口径见 [`docs/EVAL_PROTOCOL.md`](./docs/EVAL_PROTOCOL.md)。

## 0. 决策记录（追加不改写）

| # | 日期 | 决策 | 理由 |
|---|---|---|---|
| D1 | 2026-10-24 | 主环境选 **PvE 关卡**（16 关 + 三星判定），不用 PvP 脚本池 | 三星是**绝对**判据（不需要论证对手强度），且是**分级**奖励（0–3 星），样本效率远高于胜负 |
| D2 | 2026-10-24 | 智能体 = **LLM 逐决策选动作**；不做决策编译/硬规则 | 牌局决策因素多，固定模板必然漏；改为「库级配对 A/B」来验证技能，而不是逐技能可执行化 |
| D3 | 2026-10-24 | 技能 = **自然语言 description + 元数据**（条件/证据/版本/状态） | 元数据只用于检索与「激活」统计，不参与执行 |
| D4 | 2026-10-24 | 难度：敌人血量 **≤6 → 6，其余 +2**，一次性重链引擎 | 胜率已饱和（99.7%），需要把难度推到「星级有区分度」的区间 |
| D5 | 2026-10-24 | 独立仓库 `gitcg-skills` + 独立 conda env `gitcg-skill`（**无 torch**） | 课程交付物独立；避免与上游强度主线的纯策略口径/引擎构建互相污染 |
| D6 | 2026-10-30 | 统计：**按关配对**为主口径，50 seeds/关 | 关卡间三星率 20.8%–100%，pooled 方差极大；配对可消掉关卡难度差异 |
| D7 | 2026-10-30 | `win_within_rounds` **统一保持 5**（不用 P70 标定值） | 标定显示脚本/参照策略的胜局回合 P70 普遍 >5（blanche 9、chefmao/ellin 7），但**这正说明 5 回合是一个「技能目标」而不是阈值伪影**；把它调松等于取消这项考试。记录标定结果备查，阈值改回 5 |
| D8 | 2026-10-30 | LLM 开启思考模式 `reasoning_effort=low`、`max_tokens=4096` | 用户要求不计 token 成本换质量；`low` 下单次约 8–14 s、约 450 completion tokens（含思考） |
| D9 | 2026-10-30 | 「静态 skill」= **游戏规则 + 手写的简单打牌策略**；「动态 skill」= 同一底座 + 可增删的策略 | 课程要求的分组定义；静态组可复现、不含学到的内容 |
| D10 | 2026-11-01 | 对局决策回退到 `reasoning_effort=none`（`max_tokens=64`）；思考只用于技能提取/反思（`effort=high`, `max_tokens=8192`） | **暂时回退**：off 时 15 completion tokens/次、$0.004/局；on(low) 时 1215 tokens/次、$0.040/局，成本 10× 而无质量证据。重新开启必须以成对 A/B 证明收益 |
| D11 | 2026-11-01 | 运行器提供 `GITCG_LLM_BUDGET_USD` 硬上限：滑动窗口维持 `workers` 局在飞，每完成一局即检查累计 `cost_usd`，触顶后不再启动新局；已在飞的局跑完、已完成的行照常落盘，有预算时 summary 记 `budget_usd` / `spent_usd`，触顶时加 `aborted: "budget"`。上限只对**本次调用**生效：resume / 重跑时 `spent` 从 0 重计，不跨调用累计。`scripts/estimate_cost.py --rows <run>.jsonl` 加 `--games N` 或 `--per-stage-seeds N --stages 16`，从实测逐局成本外推全量 | t5 烧到额度耗尽才停（当日 $7.93）；成本约束必须是运行时门禁而非文档约定 |
| D12 | 2026-11-12 | 引擎数据把「官方角色卡」与「NPC 变体」拆成两张牌：官方 2301/2202 保持版本卡面值（v3.3.0 下 2301=10；v7.1.0 下 11），新增 NPC 变体 9301/9202（13/12，独立技能/状态号段），`ENEMY_ENGINE_IDS` 指向变体 | 全局 `.gts` 血量补丁会污染玩家牌组里的角色卡；理论/实际都是两张牌，拆开后 PvE 难度与角色卡数据各归其位 |
| D13 | 2026-11-12 | 网页调试必须严格复现 `skillloop.run` 评测协议：关卡 TaskSpec、未过滤库、era/expert base、cap16、原顺序、stage_decks/loadout/card_version/特殊规则/NPC policy；移除 web-only 战术/过滤/重排/cap24；新增只读 `/ai-decisions` 监控面板 | 网页只是 human 扮 NPC 的调试接口，必须能忠实观察自进化经验；web-only 行为会让调试点与实际评测不一致 |
| D14 | 2026-11-24 | 决策/复盘 prompt 重构：规则改 wiki 原文并前置、关卡特殊规则与卡组信息进 system、需求/支付分离、候选保底（宣布结束 + 每张手牌）、回放改为公开状态差分；卡池改为引擎内置（不再 pin v3.3.0）；引擎 seed 固定 1..N（0 是 minstd 吸收态） | 旧 prompt 的费用/血量/候选截断会直接误导动作选择；资产 API 下线，pin 与唯一可用卡面文本错配 |
| D15 | 2026-11-24 | `static_v1` 升 v2：修正「装备前两轮才回本」的错误启发；重生成 `reports/loadouts/v2`；旧实验全部作废 | 错误启发直接进入 prompt；库版本必须与 loadout 匹配 |
| D16 | 2026-11-25 | PvE 路径（`skillloop.run` / `npc.eval` / `parked`）给 NPC 座位启用引擎 `alwaysOmni`：每轮初始 8 万能骰 | 游戏内 NPC 规则如此；此前 NPC 掷随机骰，所有历史对局与 prompt 样本失真 |
| D17 | 2026-11-25 | 以 seeds 1–5 三组建立新基线；**旧验收区间（纯脚本 30–45%）作废**，待按新基线重标定 | 现状实测 scripted 63.75%（旧区间来自随机 NPC 骰 + 旧 prompt）；基线见 `reports/baseline_20261125_pve_prompt_v1.md` |
| D18 | 2026-11-25 | 删除 skill 的 rule 层：库/代码/文档全部移除，规则只保留 system 内置文本；`llm_empty` 改为无库 | 规则与 wiki 文本重复、rule 条目从未进入 rich prompt，保留只会造成口径漂移（rule_equipment 与 GAME_RULES_V2 冲突） |

## 1. 任务定义

```
task = (stage, deck, seed)
自然语言: "在【{关卡名}】使用【{牌组}】三星通关：{目标1}、{目标2}、{目标3}。"
成功判据: stars == 3  且  error == 0  且  truncated == 0
```

- 16 关，敌人由确定性 `NpcPolicy`（greedy + rotation）驱动，玩家恒为先手；
- 失败判据：未三星（含未通关、目标未达成、超回合）；
- 评测牌组由 `configs/stage_decks.json` / loadout 固定；卡池 = 引擎内置（`configs/engine.lock`），
  时代牌组只描述牌组来源，不再 pin 引擎版本。`configs/pools/` 的旧池仍用于历史实验/对照。

## 2. 难度标定（D4）

血量补丁（`engine-overlay/` + `configs/npc/`，脚本 `scripts/patch_npc_hp.py`）：

| 原 HP | 新 HP |
|---|---|
| 3–4 | 6 |
| 8 / 9 / 10 | 10 / 11 / 12 |

标定流程：
1. 重链引擎 → 装进 `gitcg-skill` env；
2. 用**已知手写目标策略**（`npc/contestant.py`）跑 24 seeds/关，得到回合数分布；
3. 每关 `win_within_rounds` 取该分布的 **P70**（难关可放宽到 P80）；
4. **阈值保持 5（D7）**，不做放宽；标定结果只作为「难度画像」存档
   （`reports/calibration/thresholds.json`）：脚本/参照策略的胜局回合 P70 为 6–9，
   说明 5 回合本身就是一个需要**主动加速**的技能目标。
5. 验收区间：纯脚本组 **30–45%**、参照组 **35–50%**，且**未饱和**（留 ≥50pp 空间）。
   > 2026-11-25：该区间作废（D17）。新基线下 scripted 63.75%，需按新骰/新 prompt 重标定。

> 2026-11-12（D12）：血量只作用于 **NPC 变体**（9301/9202）；官方角色卡
> 2301/2202 使用引擎内置卡池的卡面值。上表的「原 HP → 新 HP」读作
> 「NPC 敌人原 HP → NPC 变体 HP」。

## 3. 技能（Skill）

```json
{
  "skill_id": "speed_stage_equip_cap",
  "skill_name": "速通关卡：装备最多 1 张",
  "description": "当关卡目标包含「N 回合内胜利」时，第二张装备牌的节奏损失大于收益……",
  "conditions": {"objective_types": ["win_within_rounds"], "round_max": 5, "enemy_tags": ["monster"]},
  "layer": "stage_specific",
  "evidence": {"library_version": 4, "games": 320, "delta_three_star": 0.06, "ci_low": 0.01},
  "version": 2, "status": "active", "source_games": ["timmie:1001", "wagner:1007"]
}
```

**分层**：`strategy`（跨关策略）→ `stage_specific`（关卡特化）；游戏规则不在库里，只在 system 的 `GAME_RULES_V2`。

## 4. 自进化机制

| 机制 | 做法 | 回答课程里的哪个问题 |
|---|---|---|
| **库级配对门控** | 候选技能成一个库版本 v_{n+1}；与 v_n 在**同一批 seeds** 上配对比较；三星率 CI 下界 > 0 才采纳 | 「什么经验该成为 Skill」 |
| **批量更新** | 每 20 局（可调）触发一次提取+门控，而不是每局 | 「Skill 什么时候该更新」 |
| **分层组织** | 策略层按条件检索；特化层优先级最高（规则不在库内，始终在 system） | 「多个 Skill 如何组织」 |
| **激活日志 + 消融** | 每条技能记录被检索/被引用的决策数；长期零激活或消融为负 → 退役 | 「错误 Skill 如何淘汰」 |
| **冲突处理** | 条件重叠且方向相反的技能强制 A/B，输者 `degraded` | 课程示例「冲突处理」 |

## 5. 实验

四组的**唯一变量是技能库**；LLM 组使用同一智能体、同一提示词骨架、同一候选顺序。

| # | 组名 | 技能库 | 内容 |
|---|---|---|---|
| ① | `scripted` | 无 | `expert:<deck>`（无目标注入，无 LLM）；参照天花板：`npc/contestant.py`（历史 74.7%） |
| ②′ | `llm_empty` | 无（规则在 system `GAME_RULES_V2`） | LLM + **仅游戏规则**；不加载技能库，不用 loadout |
| ② | `static` | `data/skills/static_v1`（10 strategy，v3） | LLM + 游戏规则 + 手写静态策略；可用 `reports/loadouts/v2` |
| ③ | `dynamic` | `data/skills/dyn_v4`（10 strategy + 28 stage_specific，v5，随轮次演进） | LLM + 游戏规则 + 动态策略；可用 `reports/loadouts/v4`，自演化 |

> `llm_empty` 不是「无提示词」：规则始终由 system 的 `GAME_RULES_V2` 注入（规则不在技能库里），
> 用于隔离「LLM + 规则」与「LLM + 规则 + 策略」的差别。`--library` 只能覆盖 static/dynamic；
> `--loadout` 只对 static/dynamic 生效。

**因子实验（≥3 个）**：更新门控（每局/每 20 局/配对门控）、检索 top-k（1/3/5/8）、
技能表示（自然语言 / 结构化 JSON / 两者）、泛化（留出 6 关）。

**预注册**：留出关卡上的三星率按关配对 Δ，`CI_low > 0` 才算有效；零结果照实写进 `docs/failures/`。

## 6. 里程碑

| 阶段 | 交付 | 验收 |
|---|---|---|
| M1 环境与标定 | 新仓库 + 独立 env + 血量重链 + 阈值重标 | 手写策略 60–75%，脚本组 15–35% |
| M2 静态组 | 静态策略库 + 检索 + LLM 智能体 + ② 组跑通 | 24 seeds/关筛选表 |
| M3 动态组 | 提取/门控/退役闭环 | ③ vs ② 按关配对 |
| M4 因子与泛化 | 4 个因子 + 留出关卡 | 曲线/表格 + 原因分析 |
| M5 报告与演示 | 综述 + 技术创新报告 + 演示 | 课堂检查 |
