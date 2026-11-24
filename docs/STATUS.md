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

## 3. 评测口径

- **16 关**；每关牌组固定于 `configs/stage_decks.json`（era 牌组）。
- **按关配对**比较；建议 **50 seeds/关**（先 3–5 seeds 冒烟）。
- 主指标：**三星率**；次指标：逐目标达成、LLM calls/局、token/局。
- 只在同 `stage / deck / card_version / stage_rules / loadout` 下比较。
- 卡池 = 引擎内置（`configs/engine.lock`），不 pin 旧版本。启用关卡特殊规则 `--stage-rules`。
- 每局记录 `stage/seed/deck/group/library_version/model/prompt_hash`；
  原始 JSONL 落 `data/`，聚合摘要进 `reports/`。成本门禁见 `EVAL_PROTOCOL.md §6`。

## 4. 四组（新口径）

| 组 | 策略 | 技能库 | loadout | 说明 |
|---|---|---|---|---|
| `scripted` | 固定脚本，无 LLM | — | — | 免费基线（expert/era base） |
| `llm_empty` | LLM + **仅游戏规则** | `data/skills/rules_v1`（17 rule） | — | 不加载 strategy |
| `static` | LLM + 规则 + 静态 skill | `data/skills/static_v1`（17 rule + 10 strategy，v2） | `reports/loadouts/v2`（可选） | 规则 + 静态经验 |
| `dynamic` | LLM + 规则 + 动态 skill | 重建中（`dyn_v4–v6` 作废） | — | 规则 + 进化经验；重建前不评测 |

`contestant` 仅作参照，不在四组内。

## 5. 运行命令（当前有效）

```sh
REPO=~/projects/gitcg-skills
export PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
export OMP_NUM_THREADS=1
cd "$REPO"
COMMON="--stages all --seeds 50 --workers 6 --stage-decks configs/stage_decks.json --stage-rules"

# scripted（免费）
"$PY" -m skillloop.run --group scripted $COMMON --out data/runs/scripted.jsonl

# llm_empty（仅规则；需预算上限）
GITCG_LLM_BUDGET_USD=... "$PY" -m skillloop.run --group llm_empty $COMMON \
  --out data/runs/llm_empty.jsonl

# static
GITCG_LLM_BUDGET_USD=... "$PY" -m skillloop.run --group static \
  --library data/skills/static_v1 --loadout reports/loadouts/v2 \
  $COMMON --out data/runs/static.jsonl

# dynamic：旧库 dyn_v4–v6 已作废。重建后按 static 的方式加载
#   --library data/skills/dyn_vX --loadout reports/loadouts/vX
```

- `--seeds 50` 即 seeds 1–50（无逗号时计数从 1 起）。**seed 0 已禁用**：引擎随机数是
  minstd LCG，0 是吸收态，首轮骰子全为冰（`DICE_TYPE_CRYO`）；逗号列表含 0 会直接报错。
  冒烟把 `--seeds` 换成 `3` 或 `3,4,5`。
- 并发：LLM 组 `--workers` 就是同时在飞的 API 请求数。本轮回跑用 4（稳定、无 429）；
  12 线程机器上建议默认 **6**（约 20% 提速），出现 429/超时再降到 4。scripted 可用 6–8。
- `GITCG_LLM_BUDGET_USD` 是**硬上限**（D11）：触顶只停止启动新局，已在飞的局跑完。
  模型与预算写进 run meta；成本先按 `EVAL_PROTOCOL.md §6` 标定再上全量。
- **`llm_empty` 新口径会注入 17 条规则**，prompt/成本高于旧「空库」；上全量前先
  3–5 seeds 标定成本。
- **旧 run 的 fingerprint 不兼容**：旧 meta 的 `stages` 字段是文件 hash；新代码
  `stages` 是关卡列表、文件 hash 改名为 `stages_file_sha1`。新实验请用新的
  `--out`，不要续跑旧输出。
- 冒烟要核查：无 `no_llm_config` / `unknown card` 错误；prompt 内库内容与
  loadout 版本匹配；`llm_empty` 只含 17 条 rule、无 strategy。

## 6. Prompt（2026-11-24 重构）

- 决策 system 顺序：角色 → 游戏规则（wiki 原文 34 节，无「特技」）→ 关卡特殊规则 → 当前对局卡组信息（我方角色/技能、对方角色/技能、我方牌组、名词解释）→ 获胜目标与判定 → 候选「预计」优先级说明 → 学习技能 → 输出契约。
- 决策 user：进度（第 R 轮；三星轮数上限；对局上限 15 轮；未完成目标）→ 局面（双方角色/能量/附着/装备/状态、骰子、手牌、支援/召唤物、牌堆张数）→ 合法动作（需求 / 支付 / 目标 / 反应预览 / 引擎结算「预计」）。
- 候选上限 16（起手换牌 32，保证 5 张手牌的全部组合可选），保底「宣布结束」与每张手牌至少一个动作；起手换牌按 mask 保留组合（不再折叠成单张）。
- 复盘 system：教练角色 + 同一份游戏规则 + 关卡特殊规则 + 卡组信息 + 目标判定 + 输出要求；user：统计（不含关卡目标）+ 失败回放（对应失败计数最大的 objective）+ 三星回放 + 相关技能。
- 回放：骰子元素、`自动支付`、`→ 此后场上` 公开状态差分、`最终`、截断标记、脚本行标注（重掷/换牌/选人）。
- 两份样本是**真实调用原文**（`GITCG_LLM_TRACE` 的 `messages` 逐字节落盘）：
  [`../reports/prompts/decision_prompt.md`](../reports/prompts/decision_prompt.md)、
  [`../reports/prompts/reflection_prompt.md`](../reports/prompts/reflection_prompt.md)。
  生成器 `scripts/prompt_samples.py`；禁止手写或二次渲染。
- 抽取输出预算 `max_tokens=24576`；`finish_reason=length` 时同 messages 翻倍重试一次（上限 49152），仍截断则告警跳过。

## 7. 已知限制

- 对方骰子：`skillloop.run` 已用 `full_state_for_agents=True` 从非掩码全量状态读取并显示真实骰子（`骰子N个：<符号串>`）；web 调试端手工构造 `DecisionContext`（`full_state=view`），看不到对方骰子，只显示「引擎视图未提供」。对方手牌两边都只给数量。
- 名词解释对同一名称最多列 2 个**不同**描述（同文去重），每条尽量标来源 `（来自「X」）`——来源是资产库里 description 提到该名词的卡牌/技能/被动，找不到则标「（另一版本）」；资产库没有「状态↔来源」的真实关联，标签是启发式。
- `重击`/`下落攻击` 按 wiki 保留，但当前牌组不使用。
- `dyn_v4–v6` 为旧结果产物，含已否定的 `strat_equipment_timing` 旧文本；重建前不得用于评测。

## 8. 下一步

1. seeds 1–5 冒烟：`scripted` / `llm_empty` / `static`（`static_v1` v2 + `loadouts/v2`），先标定成本。
2. `reasoning=none` vs `low` 对照（旧结论作废，必须重测）。
3. 冒烟通过后按 50 seeds/关、6 workers 跑配对；门控按 `EVAL_PROTOCOL.md §4`。
4. dynamic 线从 `static_v1` v2 重建后再进 gate。
5. 任何 prompt 改动：重采两份样本，并记录 system sha / prompt hash。

## 9. 历史

- 2026-11-13 及以前的胜率与门控结论**全部作废**（卡池 pin 与资产文本错配、seed 0 退化、prompt 含错误费用/血量与候选截断）。
- 留档见 [`../reports/`](../reports/) 与 [`failures/`](./failures/)。
