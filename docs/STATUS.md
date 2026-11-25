# STATUS — 当前状态（2026-11-24）

> 只写「现在有效」的事实；历史结果见 [`../reports/`](../reports/)，负结果见 [`failures/`](./failures/)。
> 任务与验收见 [`../PLAN.md`](../PLAN.md)，统计口径见 [`EVAL_PROTOCOL.md`](./EVAL_PROTOCOL.md)。

## 1. 仓库与环境

| 项 | 状态 |
|---|---|
| 仓库 | `~/projects/gitcg-skills`，从上游 `genshin-GITCG@98b4dfa` 快照（2026-10-28） |
| 环境层 | `envs/ reps/ npc/ coach/ agents/ eval/ common/ web/ configs/ engine-overlay/` 原样快照 |
| conda env | `gitcg-skill`（Python 3.12，`gitcg==0.21.0`，numpy，pytest；**无 torch**） |
| 算力 | 本机 6C/12T；实验限 6–8 worker，`OMP_NUM_THREADS=1` |
| LLM | OpenAI 兼容端点，模型 `deepseek-v4.1-flash`；`temperature=0`，对局 `reasoning_effort=none` |

## 2. 引擎与血量

- 引擎已重链并装入 `gitcg-skill`，overlay + NPC 变体生效，版本记入 `configs/engine.lock`。
- **官方角色卡** 2301/2202 跟随引擎内置卡池（`configs/engine.lock`：最新卡池，2301=11）。
- **NPC 变体** 9301/9202 承载 13/12 难度血量；血量规则「≤6→6，其余 +2」，
  审计快照 `configs/npc/hp_table.json`（19 类敌人 / 16 关）。
- 网页调试严格对齐 `skillloop.run`：关卡 TaskSpec、未过滤库、era/expert base、cap16、
  原顺序、`stage_decks`/loadout/`card_version`/`stage_rules`/NPC policy；人类执 NPC，
  `/ai-decisions` 监控 AI 引用。入口 `bash scripts/serve_web_demo.sh`。
- NPC 座位按游戏内规则置 `alwaysOmni`（`game.set_attr`，每轮初始 8 万能；NPC policy 不重掷，重掷策略不打破该规则）。

## 3. 评测口径

- **16 关**；每关牌组固定于 `configs/stage_decks.json`（era 牌组）。
- **按关配对**比较；建议 **50 seeds/关**（先 3–5 seeds 冒烟）。
- 主指标：**三星率**；次指标：逐目标达成、LLM calls/局、token/局。
- 只在同 `stage / deck / card_version / stage_rules / loadout` 下比较。
- 卡池 = 引擎内置（`configs/engine.lock`），不 pin 旧版本。启用关卡特殊规则 `--stage-rules`。
- 每局记录 `stage/seed/deck/group/library_version/model/prompt_version/system_hash`；
  原始 JSONL 落 `data/`，聚合摘要进 `reports/`。成本门禁见 `EVAL_PROTOCOL.md §6`。

## 4. 四组（新口径）

| 组 | 策略 | 技能库 | loadout | 说明 |
|---|---|---|---|---|
| `scripted` | 固定脚本，无 LLM | — | — | 免费基线（expert/era base） |
| `llm_empty` | LLM + **仅游戏规则** | 无（规则在 system） | — | 不加载技能库 |
| `static` | LLM + 规则 + 静态 skill | `data/skills/static_v1`（10 strategy，v3） | `reports/loadouts/v2`（可选） | 规则 + 静态经验 |
| `dynamic` | LLM + 规则 + 动态 skill | `data/skills/dyn_v4`（10 strategy + 28 stage_specific，v5） | `reports/loadouts/v4`（重建后重生成） | 规则 + 进化经验；旧 dyn_v1–v3/v5–v6 已删 |

规则（含 target/objective 判定文本）不在技能库里，唯一来源是 system 的 `GAME_RULES_V2`；
`static_v1` / `dyn_v4` 都不含 `rule` 层。

`contestant` 仅作参照，不在四组内。

`static` 的 loadout 只含 strategy 层（每关 1–3 条）：规划器不再产出 rule/`规则_目标_*` pin，
运行时即使遇到也会丢弃；`reports/loadouts/{v2,v4}` 里仍带旧 rule pin 的是**历史文件**（未重新生成）。
`static` 与 `llm_empty` 的差异只剩 `static_v1` 里的 10 条策略。

- loadout 不含 rule pin；逐局行 `injected_skills` 才是**实际注入**，`pinned_skills` 只是请求集。

### 4.1 当前基线（2026-11-25）

**当前 prompt（无 rule 层 + 对局历史），seeds 1–5**：

| 组 | 三星 | 三星率 | 成本 |
|---|---|---|---|
| `scripted` | 51/80 | 63.75% | $0 |
| `llm_empty`（无库） | 44/80 | 55.00% | $0.370 |
| `static`（每关 5 条策略） | 38/80 | 47.50% | $0.398 |

`v12` 相对旧 prompt（`v10`）：`llm_empty` −1 局（p=1.00，历史段没拖累）；
`static` **−9 局（p=0.035）**——loadout 修好后每关从 1–3 条策略变成 5 条，
手写策略的负作用第一次可测。见
[`failures/strategy-pins-hurt.md`](./failures/strategy-pins-hurt.md)。

**旧 prompt（v10，`rules_v1`、无历史）seeds 1–5**，仅作对照：

| 组 | 三星 | 三星率 | 成本 |
|---|---|---|---|
| `llm_empty` | 45/80 | 56.25% | $0.389 |
| `static` | 47/80 | 58.75% | $0.306 |

**评测块 seeds 6–10**（动态组的样本外评测；静态组基线也在这个块上）：

| 组 | 三星 | 三星率 | 成本 |
|---|---|---|---|
| `scripted` | 41/80 | 51.25% | $0 |
| `llm_empty` | 36/80 | 45.00% | $0.303 |
| `static` | 41/80 | 51.25% | $0.294 |
| `dynamic` r1（v3） | 39/80 | 48.75% | $0.297 |
| `dynamic` r2（v4） | 39/80 | 48.75% | $0.300 |

两块难度差异很大（scripted 63.75% → 51.25%），**跨块不可比，只做组内配对**。
动态两轮都没有可测收益：r2 vs static 净 −2（p=0.81）、r2 vs r1 净 0（p=1.00）；
r1 在训练块 +4 局、评测块 −2 局（样本内乐观）。
完整口径、逐关表、验证证据（样本逐字节、泄漏审计）与成本见
[`../reports/prompt_overhaul_20261125.md`](../reports/prompt_overhaul_20261125.md)；
动态两轮见 [`../reports/dynamic_rounds_20261125.md`](../reports/dynamic_rounds_20261125.md)。
原始数据 `data/runs/v10_*` / `v11_*` / `v12_*`（未入库，勿删）。
**早于本基线的一切胜率/门控结论作废**，包括 NPC 随机掷骰时期的全部 replay 与报告。

## 5. 运行命令（当前有效）

```sh
REPO=~/projects/gitcg-skills
export PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
export OMP_NUM_THREADS=1
cd "$REPO"
COMMON="--stages all --seeds 50 --workers 6 --stage-decks configs/stage_decks.json --stage-rules"

# scripted（免费）
"$PY" -m skillloop.run --group scripted $COMMON --out data/runs/scripted.jsonl

# llm_empty（无技能库；规则在 system；需预算上限）
GITCG_LLM_BUDGET_USD=... "$PY" -m skillloop.run --group llm_empty $COMMON \
  --out data/runs/llm_empty.jsonl

# static
GITCG_LLM_BUDGET_USD=... "$PY" -m skillloop.run --group static \
  --library data/skills/static_v1 --loadout reports/loadouts/v2 \
  $COMMON --out data/runs/static.jsonl

# dynamic：round 0 从 static_v1 v2 起（dyn_v4），每轮抽取后再评测
#   --library data/skills/dyn_v4 --loadout reports/loadouts/v4
```

- `--seeds 50` 即 seeds 1–50（无逗号时计数从 1 起）。**seed 0 已禁用**：引擎随机数是
  minstd LCG，0 是吸收态，首轮骰子全为冰（`DICE_TYPE_CRYO`）；逗号列表含 0 会直接报错。
  冒烟把 `--seeds` 换成 `3` 或 `3,4,5`。
- 并发：LLM 组 `--workers` 就是同时在飞的 API 请求数。本轮回跑用 4（稳定、无 429）；
  12 线程机器上建议默认 **6**（约 20% 提速），出现 429/超时再降到 4。scripted 可用 6–8。
- `GITCG_LLM_BUDGET_USD` 是**硬上限**（D11）：触顶只停止启动新局，已在飞的局跑完。
  模型与预算写进 run meta；成本先按 `EVAL_PROTOCOL.md §6` 标定再上全量。
- **规则的唯一来源是 system 里的 `prompt_v2.GAME_RULES_V2`**：技能库已无 `rule` 层
  （旧规则库目录已整个删除，见 D18），rich prompt 也不注入任何规则技能。
  `llm_empty` 是**无技能库**组，与 `static` 的规则段完全相同，差异只在 10 条策略技能。
- **旧 run 的 fingerprint 不兼容**：旧 meta 的 `stages` 字段是文件 hash；新代码
  `stages` 是关卡列表、文件 hash 改名为 `stages_file_sha1`。新实验请用新的
  `--out`，不要续跑旧输出。
- 冒烟要核查：无 `no_llm_config` / `unknown card` 错误；prompt 内库内容与
  loadout 版本匹配；`llm_empty` 不加载技能库（库内容为空）；逐局行
  `injected_skills` 与请求的 loadout pin 一致（rule pin 已被丢弃）。

## 6. Prompt（2026-11-24 重构）

- 决策 system 顺序：角色 → 游戏规则（`GAME_RULES_V2`，wiki 原文 34 节，无「特技」）→ 关卡特殊规则 → 当前对局卡组信息（我方角色/技能、对方角色/技能、我方牌组、名词解释）→ 获胜目标与判定 → 候选「预计」优先级说明 → 学习技能 → 输出契约。规则只来自 system 内置文本，技能库里没有 rule 条目。
- 决策 user：进度（第 R 轮；三星轮数上限；对局上限 15 轮；未完成目标）→ 局面（双方角色/能量/附着/装备/状态、骰子、手牌、支援/召唤物、牌堆张数）→ **对局历史（仅 P0 可见信息）** → 合法动作（需求 / 支付 / 目标 / 反应预览 / 引擎结算「预计」）。对局历史由 `skillloop.history.MatchHistory` 生成：上一次决策以来的公开事件（双方技能/反应、我方抽到的牌名、对方抽牌只给张数）＋公开局面差（双方角色 HP/能量/附着/装备/状态/召唤/支援、我方手牌新增与骰子；对方手牌只比张数），最多保留最近 12 行；对方手牌内容与骰面永不进入该段（引擎掩码 id 也不出现）。首次决策没有历史段；脚本回退的决策不写「我方」行。
- 候选上限 16（起手换牌 32，保证 5 张手牌的全部组合可选），保底「宣布结束」与每张手牌至少一个动作；起手换牌按 mask 保留组合（不再折叠成单张）。
- 复盘 system：教练角色 + 同一份游戏规则 + 关卡特殊规则 + 卡组信息 + 目标判定 + 输出要求；user：统计（不含关卡目标）+ 失败回放（对应失败计数最大的 objective）+ 三星回放 + 相关技能。
- 回放：骰子元素、每轮双方掷骰、跨轮抽牌内容；换牌显示「弃置「X」→ 获得「Y」」并带真实归属标注（`（LLM 决策）`/`（脚本策略）`；同步 runner 的换牌/选人是 LLM、重掷是脚本，驻留 runner 三者都是脚本）；重掷显示 `[旧池] → [新池]`；所有进手牌的牌都有带名字的抽牌行（NPC 只给张数）；开局起手也打印；`自动支付`、`→ 此后场上` 公开状态差分、`最终`、截断标记；动作的手牌弃置/获得与角色附属来自所选动作的引擎 `preview`（例如 `手牌：弃置「雷楔」；附属「雷元素附魔」`），不靠手牌数量反推。
- 两份样本是**真实调用原文**（`GITCG_LLM_TRACE` 的 `messages` 逐字节落盘）：
  [`../reports/prompts/decision_prompt.md`](../reports/prompts/decision_prompt.md)、
  [`../reports/prompts/reflection_prompt.md`](../reports/prompts/reflection_prompt.md)。
  生成器 `scripts/prompt_samples.py`；禁止手写或二次渲染。
- 抽取输出预算 `max_tokens=32768`；`finish_reason=length` 时同 messages 翻倍重试一次（上限 65536），仍截断则告警跳过。

## 7. 已知限制

- 对方骰子：`skillloop.run` 已用 `full_state_for_agents=True` 从非掩码全量状态读取并显示真实骰子（`骰子N个：<符号串>`）；web 调试端手工构造 `DecisionContext`（`full_state=view`），看不到对方骰子，只显示「引擎视图未提供」。对方手牌两边都只给数量。
- 名词解释对同一名称最多列 2 个**不同**描述（同文去重），每条尽量标来源 `（来自「X」）`——来源是资产库里 description 提到该名词的卡牌/技能/被动，找不到则标「（另一版本）」；资产库没有「状态↔来源」的真实关联，标签是启发式。
- `重击`/`下落攻击` 按 wiki 保留，但当前牌组不使用。
- `dyn_v4` 已按新口径重建（v5：10 strategy + 28 stage_specific）；更早的动态库产物含已否定的 `strat_equipment_timing` 旧文本，不得用于评测。

## 8. 下一步

1. ~~seeds 1–5 冒烟~~ / ~~评测块 6–10 三组基线~~ / ~~dynamic 两轮~~ 已完成，见 §4.1。
2. dynamic 逐技能门禁：整库接受时 18 条改写会互相抵消，下一步对候选做
   `skillloop.gate.paired_delta`，只保留 CI_low>0 的改动；同轮同一 `replaces`
   冲突要拒绝后者。
3. `reasoning=none` vs `low` 对照（旧结论作废，必须重测）。
4. 按 50 seeds/关、4–6 workers 跑配对；门控按 `EVAL_PROTOCOL.md §4`。
5. 任何 prompt 改动：重采两份样本（`reports/prompts/`），并记录 `prompt_version`。

## 9. 历史

- 2026-11-25：建立新基线（seeds 1–5 三组 + seeds 6–10 四组 + dynamic 两轮，≈ $2.3）。此前所有胜率与门控结论**全部作废**：
  NPC 座位未按游戏规则置 `alwaysOmni`（掷随机骰）、prompt 含错误费用/血量与候选截断、
  卡池 pin 与资产文本错配、seed 0 退化。
- 2026-11-13 及以前的胜率与门控结论**全部作废**。
- 留档见 [`../reports/`](../reports/) 与 [`failures/`](./failures/)。
