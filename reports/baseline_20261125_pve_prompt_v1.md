# 基线：修正后的 PvE 规则 + 重构 prompt（seeds 1–5）

> **2026-11-25 superseded**：此后 prompt 增加了「对局历史」段，`rule` 层已从库中删除，
> `static` 的 loadout 重规划为每关 5 条 strategy（旧版只有 1–3 条生效）。
> 本文数字属于旧 prompt / 旧库口径，只能作历史记录；新结果见 `data/runs/v12_*`。

> 生成日期 2026-11-25。**这是当前唯一有效的性能基线。** 之前所有结果（NPC 随机掷骰、
> 旧 prompt、seed 0、pin 旧卡池）一律作废，不作为对照。
> 数据：`data/runs/v10_{scripted,llm_empty,static}_s1_5.jsonl`（逐局 JSONL，未入库）。
> 复现命令见 `docs/STATUS.md §5`；口径见 `docs/EVAL_PROTOCOL.md`。

## 1. 口径

| 项 | 值 |
|---|---|
| 日期 | 2026-11-25（UTC 04:13–06:29） |
| 关卡 | 16 关（`configs/stage_decks.json`，era 牌组） |
| seeds | 1–5（seed 0 禁用：minstd LCG 吸收态，首轮全冰骰） |
| 对局数 | 80（16 关 × 5 seeds），三组同 stage/seed 配对 |
| 卡池 | 引擎内置（`card_version=null`，不 pin） |
| 关卡特殊规则 | `--stage-rules` 开启 |
| NPC | PvE 座位 `alwaysOmni`：每轮初始 8 万能骰，NPC 不重掷（`5d9e445`） |
| prompt | 2026-11-24 重构版（wiki 规则 + 引擎 `preview` 结算 + 候选上限 16/起手 32） |
| 样本 sha256 前缀 | `decision_prompt.md bf657d3fff2bce4f` / `reflection_prompt.md 56837ea2802af02b` |
| 模型 | `deepseek-v4.1-flash`，`temperature=0`，`reasoning_effort=none` |
| 并发 | `scripted` 6 workers；`llm_empty` 6；`static` 4（用户要求降并发重跑） |

`static` 的 loadout pin 在 `de0bf9d` 起只保留 strategy 层（每关 1–3 条），
rule/目标类条目被过滤——**`static` 与 `llm_empty` 的差异只剩这 10 条手写策略**，
两者 system prompt 的规则部分完全相同。

## 2. 结果

| 组 | 三星 | 三星率 | 平均星 | LLM calls | 成本 |
|---|---|---|---|---|---|
| `scripted`（固定脚本，免费基线） | 51/80 | **63.75%** | 2.263 | 0 | $0 |
| `llm_empty`（LLM + 仅规则） | 45/80 | **56.25%** | 2.275 | 1,630 | $0.389 |
| `static`（LLM + 规则 + 10 条策略） | 47/80 | **58.75%** | 2.288 | 1,636 | $0.306 |

错误/截断：三组均 0。单局成本：`llm_empty` $0.00487、`static` $0.00382。

### 配对比较（同 stage/seed，n=80）

| 对比 | 仅 A 三星 | 仅 B 三星 | 净差 | McNemar 精确 p |
|---|---|---|---|---|
| `static` vs `llm_empty` | 8 | 6 | +2（+2.5pp） | 0.79 |
| `static` vs `scripted` | 12 | 16 | −4（−5.0pp） | 0.57 |
| `llm_empty` vs `scripted` | 12 | 18 | −6（−7.5pp） | 0.36 |

三组差异均不显著。n=80 时 McNemar 约需净差 ±15 才能接近 p<0.05。

## 3. 逐关三星数（scr / empty / static，各关 n=5）

| 关卡 | scripted | llm_empty | static |
|---|---|---|---|
| blanche | 0 | 2 | 1 |
| buyun | 0 | 1 | 0 |
| chefmao | 4 | 1 | 0 |
| ellin | 2 | 5 | 5 |
| irontonguetian | 5 | 2 | 3 |
| jifang | 5 | 5 | 5 |
| lan | 5 | 3 | 4 |
| liusu | 5 | 3 | 3 |
| marjorie | 1 | 3 | 2 |
| patton | 4 | 5 | 5 |
| shitou | 2 | 0 | 1 |
| swan | 5 | 5 | 5 |
| timaeus | 0 | 1 | 3 |
| timmie | 4 | 1 | 2 |
| uncletian | 5 | 4 | 4 |
| wagner | 4 | 4 | 4 |

## 4. 读法

- **策略技能暂无收益证据**：`static` 相对 `llm_empty` 净 +2 局，落在噪声内。
  10 条手写策略里没有一条能单独解释差异；要下结论需 50 seeds（见 §5 成本）。
- **两组 LLM 都略低于脚本**，但同样不显著。逐关看方向相反：LLM 在
  `blanche/timaeus/ellin/marjorie` 更好，在 `chefmao/irontonguetian/lan/liusu/timmie` 更差——
  说明差距主要来自特定关卡的决策模式，而不是整体水平。
- 平均星数是 2.26–2.29，三组几乎相同：三星率差异集中在「卡在 2★」的那批局。

## 5. 上 50 seeds 的成本

按本批单局成本线性外推（800 局/组）：

| 组 | 预计调用 | 预计成本 | 4 workers 预计耗时 |
|---|---|---|---|
| `llm_empty` | ~16,300 | **$3.9** | ~5 h |
| `static` | ~16,400 | **$3.1** | ~5 h |

`scripted` 免费，约 5 分钟。若只关心 `static` vs `llm_empty`，两组共约 **$7 / 10 h**。

## 6. 局限

- 5 seeds 只能筛掉大偏差；配对差异需 50 seeds 才有统计意义。
- `static` 用 4 workers、其余 6；并发不影响 `temperature=0` 的单局结果，但严格同口径可重跑。
- 未做 `reasoning=low` 对照（旧结论作废，须重测）。
- 未测 `dynamic`：`dyn_v4–v6` 含已否定的旧策略文本，重建前不得评测。
