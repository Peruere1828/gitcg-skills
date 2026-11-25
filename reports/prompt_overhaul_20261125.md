# Prompt 改造：删除 rule 层 + 加入对局历史（2026-11-25）

> 口径：16 关 × seeds 1–5 = 80 局/组，`temperature=0`，配对比较；PvE NPC `alwaysOmni`；
> 卡池 = 引擎内置。数据 `data/runs/v12_*`（未入库），对照组 `data/runs/v10_*`。
> 本文件同时记录验证证据：历史段只含 P0 可见信息、样本与真实调用逐字节一致。

## 1. 改了什么

### 1.1 删除 `rule` 层（库 + 代码 + 文档）

| 项 | 之前 | 之后 |
|---|---|---|
| `data/skills/rules_v1/` | 17 条 rule | **目录删除** |
| `static_v1` | 17 rule + 10 strategy（v2） | 10 strategy（**v3**） |
| `dyn_v4` | 17 rule + 28 ss + 10 strategy（v4） | 10 strategy + 28 ss（**v5**） |
| `schema.LAYERS` | `rule/strategy/stage_specific` | `strategy/stage_specific` |
| `rules_text()` / `rules_note` | 存在于 library/agent/render/parked/extract | 全部删除 |
| `llm_empty` | 加载 `rules_v1` | **无技能库**（规则只在 system） |

理由：rich prompt 从未注入 rule 条目（`system_hash` 12/12 验证），规则唯一来源是
`prompt_v2.GAME_RULES_V2`；而 rule 文本已经和 wiki 口径冲突（`rule_equipment` 的「1 张特技」）。
旧 JSON 里残留的 rule 条目在 `SkillLibrary.load` 时丢弃并打一行 stderr。

### 1.2 决策 prompt 加入「对局历史」

- `envs/match.py`：`PolicyPlayer._build` 把「上次决策以来的公开事件」投影进
  `DecisionContext.metadata["recent_events"]`；对方 `card_move` 只保留方向/张数，
  不传 `definition_id`。
- `skillloop/history.py`（新）：事件行 + 公开字段差分，最多保留 12 行，插在
  「局面」与「合法动作」之间；自己的动作按标签记账，同名事件匹配后丢弃（一次动作只说一次）。
- 示例（真实调用，`static/blanche/seed 1`）：

```
对局历史（仅你方可见信息，按时间顺序，最后一条最新）：
R0 我方 · 起手换牌：全部保留
R0 我方 · 选择出战角色：愚人众·火之债务处理人
R0 我方 · 使用 愚人众·火之债务处理人·潜行大师
R0 对方 · 使用 火斧丘丘暴徒·流焰磨刃（×2）
R1 我方·刻晴 HP 10→7
R1 我方 · 抽到「元素共鸣：热诚之火」「白垩之术」
```

### 1.3 顺带修复

- loadout 规划器不再产出 rule pin；逐局记录 `injected_skills`（实际注入）与 `pinned_skills`（请求）。
- 回放：换牌显示弃置/获得卡名、重掷 `[旧]→[新]`、所有进手牌的牌都有带名抽行、
  机械行标真实归属（同步 runner 的换牌/选人是 LLM）。
- run meta：`prompt_version/code_hash` 进 fingerprint；`code_sha/code_dirty/system_hashes` 进 meta。

## 2. 验证

| 项 | 方法 | 结果 |
|---|---|---|
| 样本真实性 | `reports/prompts/decision_prompt.md` 23 个 system + 23 个 user 块 vs `trace_decision_v13.jsonl` | **48/48 逐字节一致** |
| 历史注入位置 | 22 个含历史块的 user：`对方已宣布结束` < 标题 < `合法动作：` | **22/22 PASS**，首决策无历史 |
| 历史泄漏 | 6 局（chefmao/1、blanche/2、wagner/1、timmie/1、ellin/2、irontonguetian/1）拿上帝视角逐条比对 | 380 张对手手牌 id/名字、对手骰面、牌堆未来牌 **0 命中**；白盒 spy 证明名字解析器未收到对手独有 id |
| 数值正确性 | 42 条链式断言（HP/能量/附着起止值与面板一致、骰子行=面板骰面） | **42/42 PASS** |
| 可读性 | 483 行历史里 `#id` 回退 | 1 处（`#31023`），已修：三个丘丘射手被动别名冲突，改精确 id 优先，现为 `冰箭丘丘人·躲起来` |
| 重复 | 真实样本里的「标签+事件」双行 | 修后复查 1 处，经回放反证是角色被动（`选择出战角色：X` + `X·潜行大师`），非重复 |
| 测试 | `pytest -m "not slow"` | **656 passed, 8 skipped** |

## 3. 本轮结果（seeds 1–5，n=80/组，0 错误）

| 组 | 三星 | 三星率 | 平均星 | 成本 |
|---|---|---|---|---|
| `v12 llm_empty`（无库 + 历史） | 44/80 | 55.00% | 2.188 | $0.370 |
| `v12 static`（10 策略按关取 5 + 历史） | 38/80 | 47.50% | 2.025 | $0.398 |
| `v10 llm_empty`（旧：rules_v1 + 旧 prompt） | 45/80 | 56.25% | 2.275 | $0.390 |
| `v10 static`（旧：loadout 实际只注入 1–3 条） | 47/80 | 58.75% | 2.288 | $0.306 |
| `v10 scripted`（免费基线） | 51/80 | 63.75% | 2.263 | $0 |

配对（同 stage/seed）：

| 对比 | 仅前者 | 仅后者 | 净差 | McNemar p |
|---|---|---|---|---|
| `v12 llm_empty` vs `v10 llm_empty` | 7 | 8 | −1 | 1.00 |
| `v12 static` vs `v10 static` | 3 | 12 | **−9** | **0.035** |
| `v12 static` vs `v12 llm_empty` | 3 | 9 | −6 | 0.146 |

逐关（v10e / v12e / v10s / v12s）：

```
blanche         2  1  1  1      buyun      1  1  0  0
chefmao         1  0  0  0      ellin      5  4  5  5
irontonguetian  2  3  3  2      jifang     5  5  5  5
lan             3  4  4  2      liusu      3  5  3  4
marjorie        3  2  2  1      patton     5  5  5  5
shitou          0  0  1  1      swan       5  5  5  5
timaeus         1  2  3  2      timmie     1  0  2  0
uncletian       4  4  4  2      wagner     4  3  4  3
```

## 4. 解读

1. **`llm_empty` 基本不变**（−1 局，p=1.00）：删 rule 层、加历史、改 prompt 结构，
   对「只有规则」的智能体没有可测影响。历史段因此没有拖累决策质量。
2. **`static` 掉了 9 个百分点，名义显著（p=0.035）**。这是本轮唯一变动的 treatment：
   loadout 修好后，10 条策略里每关直接注入 5 条（旧版因为 rule pin 占位，实际只有 1–3 条）。
   掉的关集中在 `lan 4→2`、`uncletian 4→2`、`timmie 2→0`、`wagner 4→3`、`marjorie 2→1`。
3. 与 dynamic 两轮的结论一致：**手写策略注入越多，表现越差**。这轮把「无效名额」
   补成「有效策略」后，负效应第一次变得可测。下一步应先做策略级消融（每关逐条开关），
   而不是继续加技能。
4. 成本与调用数没有明显变化（static 1542 calls vs 旧 1636；$0.398 vs $0.306，
   差在 prompt 历史段与 5 条策略的 token）。

## 5. 复现

```sh
REPO=~/projects/gitcg-skills; PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
cd "$REPO"; export OMP_NUM_THREADS=1
COMMON="--stages all --seeds 1,2,3,4,5 --workers 3 --stage-rules --stage-decks configs/stage_decks.json"
PYTHON="$PY" GITCG_LLM_BUDGET_USD=1.2 "$PY" -m skillloop.run --group llm_empty $COMMON --trace \
  --out data/runs/v12_llm_empty_s1_5.jsonl
GITCG_LLM_BUDGET_USD=1.2 "$PY" -m skillloop.run --group static $COMMON --trace \
  --library data/skills/static_v1 --loadout reports/loadouts/v2 \
  --out data/runs/v12_static_s1_5.jsonl
```

## 6. 局限

- `static` 的下降是**整包**（5 条策略 + 历史 + rule 删除）里的净效果；由于 `llm_empty`
  在同批改动下不变，可以把下降归因到策略注入，但要严格拆分仍需逐条消融。
- 历史窗口只有最近 12 条，长局早期的关键失误会滚出窗口。
- 驻留 runner（`parked`）与网页调试路径没有接历史段；历史在两者中退化为 diff 兜底或缺失。
- 对手同型单位（两只火斧丘丘暴徒）在历史里文本相同、无法区分是哪一只。
