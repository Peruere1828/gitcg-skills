# STATUS — 当前状态（2026-11-13）

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
- **官方角色卡** 2301/2202 保持版本卡面值（`v3.3.0` 下 2301=10；`v7.1.0` 下 11）。
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
- 固定卡池 `--card-version v3.3.0`，启用关卡特殊规则 `--stage-rules`。
- 每局记录 `stage/seed/deck/group/library_version/model/prompt_hash`；
  原始 JSONL 落 `data/`，聚合摘要进 `reports/`。成本门禁见 `EVAL_PROTOCOL.md §6`。

## 4. 四组（新口径）

| 组 | 策略 | 技能库 | loadout | 说明 |
|---|---|---|---|---|
| `scripted` | 固定脚本，无 LLM | — | — | 免费基线（expert/era base） |
| `llm_empty` | LLM + **仅游戏规则** | `data/skills/rules_v1`（17 rule） | — | 不加载 strategy |
| `static` | LLM + 规则 + 静态 skill | `data/skills/static_v1`（17 rule + 10 strategy） | `reports/loadouts/v1`（可选） | 规则 + 静态经验 |
| `dynamic` | LLM + 规则 + 动态 skill | `data/skills/dyn_v4`（17 rule + 23 strategy） | `reports/loadouts/v4`（可选） | 规则 + 进化经验 |

`contestant` 仅作参照，不在四组内。

## 5. 运行命令（当前有效）

```sh
REPO=~/projects/gitcg-skills
export PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
export OMP_NUM_THREADS=1
cd "$REPO"
COMMON="--stages all --seeds 50 --workers 6 --stage-decks configs/stage_decks.json --card-version v3.3.0 --stage-rules"

# scripted（免费）
"$PY" -m skillloop.run --group scripted $COMMON --out data/runs/scripted.jsonl

# llm_empty（仅规则；需预算上限）
GITCG_LLM_BUDGET_USD=... "$PY" -m skillloop.run --group llm_empty $COMMON \
  --out data/runs/llm_empty.jsonl

# static
GITCG_LLM_BUDGET_USD=... "$PY" -m skillloop.run --group static \
  --library data/skills/static_v1 --loadout reports/loadouts/v1 \
  $COMMON --out data/runs/static.jsonl

# dynamic
GITCG_LLM_BUDGET_USD=... "$PY" -m skillloop.run --group dynamic \
  --library data/skills/dyn_v4 --loadout reports/loadouts/v4 \
  $COMMON --out data/runs/dynamic.jsonl
```

- `--seeds 50` 即 seeds 0–49。冒烟把 `--seeds` 换成 `3` 或 `3,4,5`。
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

## 6. 当前结果（v3 回跑，2026-11-12）

口径：16 关 × seeds **1–5**（80 局/组），`stage_decks` + `card_version=v3.3.0`
+ `--stage-rules`；旧 v2/t5 结果全部作废，不与本表混用。

| 组 | 局数 | 三星 | 三星率 | 成本 | 备注 |
|---|---:|---:|---:|---:|---|
| `scripted` | 80 | 57 | **71.25%** | $0 | 固定脚本 |
| `llm_empty` | 80 | 42 | **52.50%** | $0.147 | 仅 17 条 rule |
| `static` | 80 | 46 | **57.50%** | $0.153 | static_v1 + loadouts/v1 |
| `dynamic` | 80 | 41 | **51.25%** | $0.173 | dyn_v4 + loadouts/v4（round1） |

- 0 error / 0 truncated（四组各 80 valid）。
- LLM 组 4 workers；平均 ~80 s/局、~26 calls/局；无 429/回退。
- 配对 vs `scripted`（同 stage/seed，n=80 对；`scripts.compare_groups`）：
  - `llm_empty` Δ = **−0.1875**，CI [−0.3987, +0.0237] → 跨 0，不采纳；
  - `static` Δ = **−0.1375**，CI [−0.2763, +0.0013] → 跨 0，不采纳。
- 5 seeds 的 CI 很宽，**不作显著性/胜率结论**；`dynamic` 未跑，四组完整对比待补。
- 原始行：`data/runs/v3_{scripted,llm_empty,static}.jsonl`；比较：`reports/compare/v3_scripted_vs_*.json`。

### dynamic 迭代（2026-11-12，未达标）

- dyn_v4 → dyn_v5 → dyn_v6，3 轮/12 seeds（6 并发，预算 <$0.5）：
  round1 41/80=51.25%、round2 31/64=48.44%、round3 20/48=41.67%；
  累计 **92/192=47.92%**，scripted 同 12 seeds **124/192=64.58%**。
- 剩余 3 seeds（48 局）即使全 3★，dynamic 上限 140/240=58.33% < scripted
  1–15 的 159/240=66.25%，目标已数学上不可达；停止第 4 轮。
- 复盘 prompt 已增强（牌组/特殊规则/现有技能、stage_specific 绑定 stages、
  loadout 只列适用技能），但第 2/3 轮反而下降；瓶颈在 LLM 逐动作决策，不在技能文本。
- 负结果与复现：[`failures/dynamic-vs-scripted-20261112.md`](./failures/dynamic-vs-scripted-20261112.md)。

### 共享智能体开关（2026-11-13，未采纳）

- 新增实验开关：`--rich-render`（丰富状态/候选渲染，后已升为默认）、`--base-guard`
  （无 strategy 引用时退回 expert 动作）、`--round-plan`（轮级记录板）、
  `--base-guard-strict`；均写入 fingerprint，详见 `EVAL_PROTOCOL.md §7`。
- `rich-render + base-guard`：seeds1–5 **51/80 = 63.75%**（基线 41/80），
  seeds6–9 **31/64 = 48.44%**（基线 34/64），合并 **82/144 = 56.94%**
  （基线 75/144 = 52.08%）；配对门控合并 Δ = +0.049，CI [−0.059, +0.157]，
  **未过门**。`scripted` 同 9 seeds 95/144 = 65.97%。
- `round-plan` 与 `base-guard-strict` 中途分别 20/42、7/19 三星，已停止。
- 进一步尝试（均 16 关 × seeds1–5）：`base-guard-strict` 全关 **48/80 = 60.00%**
  （scripted 57/80）；skills-only 越权 18/30、expert-hint 15/30、非战斗越权 7/25，
  对照 scripted 分别为 27/30、27/30、10/25。expert 规则旋钮网格（免费）默认配置
  即最优，`--base-seed 1` 与 seed 0 逐局一致。
- **唯一数值超过 scripted 的配置不含 LLM 调用**：`configs/stage_profiles.json`
  给 lan 设 `setup_rounds=0`（1–5: 5/5、6–9: 3/4、10–15: 6/6，合计 13/15；
  默认 10/15）。dynamic 组 `--stage-profiles --max-calls 0` 全 15 seeds
  **162/240 = 67.50%** vs scripted **159/240 = 66.25%**（+3 局，全部来自 lan，
  McNemar 不显著）。这是 expert 画像，不是 LLM 逐动作策略。
- 结论：LLM dynamic 逐动作策略**无法超过 scripted**；全部实验约 $1.42。
  报告 [`../reports/dynamic_improvements_20261113.md`](../reports/dynamic_improvements_20261113.md)。

### Prompt 重写（2026-11-13 晚）

- 旧紧凑 prompt 信息不足（无元素/武器、支援/召唤物/状态/装备，附着只有数字，
  卡牌只有名字）。新 prompt 已设为**默认协议**：system 含 3.3 规则/元素反应效果表、
  双方角色全部技能、整套牌资料；user 局面给元素/武器/附着/区域/装备/手牌费用与效果。
- effort=none、16 关 × seeds1–5：**45/80 = 56.25%**（同配对 scripted 57/80）；
  比旧紧凑 dynamic 41/80 有提升，但 iron/marjorie/shitou 仍低于 expert。
- effort=low 单局约 $0.045、10–20 分钟；分两批共 39 局、覆盖 10 关：
  **32/39 = 82.05%**，同配对 scripted **24/39 = 61.54%**（+8 局；配对 Δ=+0.173，
  CI [−0.007,+0.353]，贴近显著）。增益在 blanche 0→2、buyun 1→4、chefmao 2→4、
  iron 4→5、marjorie 2→3；ellin 4→3。未测 6 关若与 scripted 持平，总投影
  low 55/69≈79.7% vs scripted 47/69≈68.1%。
- 结论：**完整 prompt 只有配合 low 思考才显著生效**；none 仍低于 scripted。
  预算 none $0.263 + low $1.624 = **$1.887**（上限 $2）。
- 报告：[`../reports/prompt_rewrite_20261113.md`](../reports/prompt_rewrite_20261113.md)；
  艾琳起手 prompt：[`../reports/prompts/ellin_mulligan_20261113.md`](../reports/prompts/ellin_mulligan_20261113.md)。

## 7. 下一步

1. dynamic 迭代停留在 3 轮/12 seeds（目标未达）；不要继续消耗预算刷同一架构。
2. 逐动作越权已被证明是负收益：rich/base-guard、strict、skills-only、expert-hint、
   非战斗越权均低于同配对 scripted；expert 规则旋钮也无余量。不要再在同一架构上
   调 prompt。若要继续冲 scripted，需换架构（回放/搜索、LLM 写可执行规则、或更强
   模型 + 推理），并重跑全部基线。
3. 5 seeds 结果只作冒烟；全量按 50 seeds/关、6 workers 跑，预算按
   `EVAL_PROTOCOL.md §6` 分组设置。
4. 按关配对门控（`EVAL_PROTOCOL.md §4`）：看 Δ 与 CI，跨 0 不采纳。
5. 摘要进 `reports/`（带 stage/seeds/n/group/library_version/model/date），
   失败进 `docs/failures/`。

## 8. 历史

- 历史结果：[`../reports/`](../reports/)（`t5b_summary.md`、`evo_summary.md`、
  `compare/`、`calibration/`、`deck_matrix.md`、`era_decks.md` 等）。
- 负结果：[`failures/`](./failures/)。
- 旧数字仅作留档，STATUS 不再列出。
