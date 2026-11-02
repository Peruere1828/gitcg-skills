# t5b：effort=none 的五组 10-seed 结果（2026-11-01）

> **2026-11-02 更正**：`record_decisions=False` 的判定 bug 已修复；本文中所有「装备/事件目标恒为 false」的结论是**测量假象**，不是行为。受影响关卡的数值待重跑。
>
> 修复后同 seeds 免费重放：`scripted` 0.3812 → **0.4250**（68/160）、`contestant` 0.4062 → **0.4813**（77/160）；scripted +7 局、contestant +12 局翻正，0 局翻负。`timaeus` 装备已可达成（scripted 2/10、contestant 10/10），`uncletian` 事件已可达成（scripted 8/10、contestant 10/10）；`wagner` 仍 0/10（`superconduct_aggro` 无武器牌，牌组不可达）。本文 LLM 三组仍是旧判定，需重跑。

> **生成命令**：`bash data/run_t5b.sh`（2026-11-01 16:32:13 → 17:04:47 UTC，单次跑完，无 resume）。
> 本文数字全部取自该脚本产物：`data/logs_t5b.log`、`data/runs/t5b_*.jsonl{,.meta.json}`、
> `data/logs_t5b_{llm_empty,static,dynamic,extract,compare}.log`、`reports/compare/t5b_*.json`；
> 逐关 / 逐目标表格按 §7 的命令从 JSONL 复算，非手工转录。

## 1. 口径

| 项 | 值 |
|---|---|
| 关卡 | 16 关（`configs/npc/npc_stages_mond_liyue.json`，指纹 `da3ad153e409`） |
| 种子 | **0–9**（每组 16 关 × 10 种子 = 160 局；五组均 160/160 有效，0 truncated，0 error row） |
| 牌组 | `superconduct_aggro`（`skillloop.run` 默认值，脚本未显式传 `--deck`） |
| 三星阈值 | `top_k=5`（阈值统一 5，D7） |
| 引擎 / 卡池 | `configs/engine.lock` 指纹 `fa42fdfec70b`（gitcg 0.21.0，engine commit `6f01d5c1`）；`hp_table` 指纹 `0bbae5d8798a`（D4 血量补丁） |
| 模型 | `deepseek-v4.1-flash`，`temperature=0`；对局 `reasoning_effort=none` + `max_tokens=64`（D10）；提取 `effort=high` + `max_tokens=8192` |
| 并发 / 预算 | 24 workers / 组；每次调用上限 `GITCG_LLM_BUDGET_USD=1.50`（D11） |
| 组 | `scripted`、`contestant`（复用 t5 链，不调模型）、`llm_empty`、`static`（`data/skills/static_v1`，v1，27 技能）、`dynamic`（`data/skills/dyn_v1`，v2，42 技能） |
| 样本量 | 每组每关 n=10；门禁 160 对，0 对丢弃 |

`scripted` / `contestant` 是 t5 链的复用 run（`data/runs/t5_scripted.jsonl`、`t5_contestant.jsonl`）：
脚本 preflight 校验过 fingerprint（group / seeds 0–9 / top_k=5 / deck / `library_root=None`），
且两者不调用模型，`reasoning_effort` 与其结果无关，因此不复跑。

## 2. 先说硬限制：static→dynamic 有泄漏嫌疑

> **泄漏嫌疑（未解决，勿删）**：`dyn_v1` 的 15 条候选技能是从 `data/runs/t5b_static.jsonl` 提取的，
> 而这个 run 用的就是 seeds 0–9 —— 与 dynamic 组随后评测的 10 个种子完全相同。
> `scripts/extract_skills.py` 的设计口径是「在 run A 上提取 → 在未见过的种子上做门禁」，
> 本链路没有留出种子：提取时模型已经看到这 160 局里哪些关卡失败、失败在哪类目标上。
> 因此 static→dynamic 的 **+0.1250（CI [+0.0075, +0.2425]，accept=True）在 seeds 10–19 的
> 留出门禁复现之前一律标为 leakage-suspect**，不能作为「技能库提分」的证据。

具体链路：`t5b_static.jsonl`（seeds 0–9）→ `scripts.extract_skills`（16 个关卡失败画像 → 15 条候选，
14 次调用，$0.0105）→ `data/skills/dyn_v1`（v2）→ `t5b_dynamic.jsonl`（**同样** seeds 0–9）。
同一批对局既参与了「学」也参与了「考」，所以 §4 里唯一通过的这一行只能读作
「统计口径通过」，不能读作「技能库被证明有效」。§8 给了留出种子的复现方案。

## 3. 结果（160 局/组）

| 组 | 三星率 | 三星局 | 胜率 | 调用/局 | prompt tokens/局 | 缓存命中 | 成本/局 | 该组总成本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| scripted | **0.3812** | 61/160 | 0.9437 | 0 | 0 | — | 0 | 0 |
| contestant（参照） | **0.4062** | 65/160 | 0.9500 | 0 | 0 | — | 0 | 0 |
| llm_empty | **0.3563** | 57/160 | 0.9625 | 27.3 | 13.7k | 0.0% | $0.0023 | $0.3674 |
| static（规则库 v1） | **0.3000** | 48/160 | 0.9437 | 25.0 | 39.1k | 70.1% | $0.0021 | $0.3402 |
| dynamic（演进库 v2） | **0.4250** | 68/160 | 0.9437 | 25.0 | 47.5k | 72.5% | $0.0024 | $0.3814 |

**旧判定数值**：`scripted` 0.3812 / `contestant` 0.4062 是 `record_decisions=False` 下的结果；修复后同 seeds 免费重放为 **0.4250**（68/160）与 **0.4813**（77/160）。三个 LLM 组尚未重跑。本节及 §4/§5 的跨组 Δ 需在重跑后重算。

- 三个 LLM 组合计 **$1.0890**；再做一次技能提取 **$0.0105**（14 次调用）→ 本轮全链 **$1.0995**。
- 成本与 t5（effort=low，$0.037–0.040/局）相比降了一个数量级，effort=none 生效。
- 开销从高到低：dynamic > llm_empty > static（dynamic 的 prompt 最长，47.5k tokens/局，缓存命中 72.5% 抵消了一部分）。
- `static` 的三星率（0.3000）**低于** `llm_empty`（0.3563）：规则库在阈值 5 下反而更差，与 `docs/STATUS.md` §3.3 的方向一致（那里 static vs llm_empty 是 −0.012，跨 0）。两行都是旧判定；重跑后复核。
- 胜率五组都在 0.94–0.96：三星期差主要来自目标项，不是输赢。

## 4. 门禁（按关配对 t，Δ = b − a）

口径：对每关算 Δ_s = rate_b(s) − rate_a(s)（同 seeds），16 关的 Δ 做单样本 t，
df=15、t=2.131；**accept ⇔ CI 下界 > 0**（`docs/EVAL_PROTOCOL.md` §4，`skillloop/gate.py`）。

| 比较 (a → b) | Δ | 95% CI | 对 | 门禁 |
|---|---:|---|---:|---|
| static → dynamic | **+0.1250** | [+0.0075, +0.2425] | 160 | **accept=True**（泄漏嫌疑，见 §2） |
| llm_empty → dynamic | +0.0687 | [−0.0507, +0.1882] | 160 | accept=False |
| scripted → dynamic | +0.0437 | [−0.1458, +0.2333] | 160 | accept=False |
| scripted → static | −0.0813 | [−0.2310, +0.0685] | 160 | accept=False |
| scripted → llm_empty | −0.0250 | [−0.1763, +0.1263] | 160 | accept=False |
| scripted → contestant | +0.0250 | [−0.0114, +0.0614] | 160 | accept=False |

读法：

- **只有 static→dynamic 过线**，而且过得很窄：下界 0.0075。单关 10 局里翻 1 局 = 该关 Δ 变 0.1
  = 16 关均值变 0.00625，所以这个「过线」只相当于全局 1 局左右的抖动。
  扣掉 §2 的泄漏嫌疑后，这一行不足以支撑「自进化有效」。
- dynamic 对 `llm_empty`（+0.0687）和 `scripted`（+0.0437）都是跨 0 的正向差异：方向对，功效不够。
  每关 10 个种子太薄，这正是需要 50 seeds/关的原因（`EVAL_PROTOCOL.md` §3）。
- `scripted→static` 为负且跨 0：在阈值 5 下**规则库没有帮助**，与 `docs/STATUS.md` §3.3 的 −0.012 同向。

## 5. 逐关三星率（每组每关 n=10）

| 关卡 | scripted | contestant | llm_empty | static | dynamic | Δ(static→dynamic) |
|---|---:|---:|---:|---:|---:|---:|
| blanche | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| buyun | 0.8 | 0.8 | 0.5 | 0.4 | 0.4 | 0 |
| chefmao | 0.1 | 0.1 | 0.0 | 0.2 | **0.7** | **+0.5** |
| ellin | 0.2 | 0.2 | 0.4 | 0.2 | **0.8** | **+0.6** |
| irontonguetian | 0.0 | 0.2 | 0.4 | 0.1 | 0.5 | +0.4 |
| jifang | 1.0 | 1.0 | 0.9 | 0.9 | 1.0 | +0.1 |
| lan | 0.7 | 0.7 | 0.4 | 0.4 | 0.4 | 0 |
| liusu | 0.0 | 0.2 | 0.6 | 0.1 | 0.4 | +0.3 |
| marjorie | 0.4 | 0.4 | 0.5 | 0.7 | 0.5 | **−0.2** |
| patton | 0.9 | 0.9 | 0.3 | 0.0 | 0.2 | +0.2 |
| shitou | 0.3 | 0.3 | 0.1 | 0.0 | 0.1 | +0.1 |
| swan | 0.9 | 0.9 | 1.0 | 1.0 | 0.9 | **−0.1** |
| timaeus | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| timmie | 0.8 | 0.8 | 0.6 | 0.8 | 0.9 | +0.1 |
| uncletian | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| wagner | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| **总体** | **0.3812** | **0.4062** | **0.3563** | **0.3000** | **0.4250** | **+0.1250** |

**旧判定总体**：`scripted` 0.3812、`contestant` 0.4062；修复后同 seeds 为 **0.4250 / 0.4813**。表中 LLM 三列仍待重跑。

- 最大涨幅：`ellin` +0.6（0.2→0.8）、`chefmao` +0.5（0.2→0.7）、`irontonguetian` +0.4（0.1→0.5）、`liusu` +0.3（0.1→0.4）。
- 只有两关下降：`marjorie` −0.2（0.7→0.5）、`swan` −0.1（1.0→0.9）。
- **四关在五组里全是 0**（旧判定）：`blanche`、`timaeus`、`uncletian`、`wagner`。修复后 `timaeus` / `uncletian` 不再恒零：免费基线 scripted 三星为 `timaeus` 1/10、`uncletian` 6/10（contestant 4/10、8/10）；`blanche` 与 `wagner` 仍为 0（`wagner` 无武器牌，牌组不可达）。LLM 三组待重跑。
- 单个 Δ 只是 10 局伯努利样本：±0.1 就是 1 局，所以上表的逐关数字只能当画像，不能当逐关结论。

## 6. 演进库 `dyn_v1` 与诊断

**库描述**（`data/skills/dyn_v1/library.json`）：

| 项 | 值 |
|---|---|
| `library_version` | 2（`kind=evolved`，`built_at=2026-11-01T16:53:22Z`） |
| 规模 | 42 条 = 17 `rule` + 25 `strategy` |
| 父库 | `data/skills/static_v1`（v1，27 条 = 17 `rule` + 10 `strategy`） |
| 学习来源 | `source_rows=data/runs/t5b_static.jsonl`，`n_candidates=15` |
| 本次新增 | 15 条，全部 `layer=strategy`、`status=active`，按关卡命名：`skill_lan`、`skill_liusu`、`skill_blanche`、`skill_buyun`、`skill_chefmao`、`1_chefmao`、`skill_ellin`、`skill_irontonguetian`、`skill_marjorie`、`skill_patton`、`skill_shitou`、`skill_timaeus`、`skill_timmie`、`skill_uncletian`、`skill_wagner` |
| 覆盖 | 14 个关卡（`chefmao` 两条）；`swan` / `jifang` 无候选（static 下最差目标失败率 0.0 / 0.1，低于默认 `--min-failure-rate 0.15`） |
| 提取成本 | 16 个关卡画像 → 15 条候选，14 次调用，**$0.0105**（`data/logs_t5b_extract.log`） |

**逐目标失败率**（分母 = 含该目标的局；只有 LLM 三组可算）：

| 目标 | llm_empty | static | dynamic |
|---|---:|---:|---:|
| `win` | 6/160 | 9/160 | 9/160 |
| `win_within_rounds` | 75/160 (0.47) | 83/160 (0.52) | **66/160 (0.41)** |
| `trigger_elemental_reactions` | 13/40 | 21/40 | 16/40 |
| `use_elemental_burst` | 3/30 | 2/30 | 3/30 |
| `play_equipment_card` † | 20/20（旧判定） | 20/20（旧判定） | 20/20（旧判定） |
| `play_event_cards` † | 10/10（旧判定） | 10/10（旧判定） | 10/10（旧判定） |
| `defeated_less_than` | 0/50 | 3/50 | 2/50 |

† 旧判定（`record_decisions=False`）看不到出牌，这两行是恒定的假象，不是行为。修复后免费重放：`timaeus` 装备 scripted 2/10、contestant 10/10 达成；`uncletian` 事件 scripted 8/10、contestant 10/10；`wagner` 仍 0/10（牌组无武器）。LLM 三组必须重跑后重算。

- 提升几乎全部来自 `win_within_rounds`（失败 83→66）：这正是 15 条候选里绝大多数在讲的事
  （「回合预算」「目标动作前置」）。
- `play_equipment_card`（`timaeus` + `wagner`，20 局）与 `play_event_cards`（`uncletian`，10 局）
  在**旧判定的三组里都是 100% 失败**，但这是**测量假象**：旧 runner 用 `record_decisions=False`，目标判定
  看不到任何出牌。修复后免费重放已经能达成这些目标（`timaeus` 装备目标 scripted 2/10、
  contestant 10/10；`uncletian` 事件目标 scripted 8/10、contestant 10/10）。因此不能再说
  `dyn_v1`「写了技能但行为没变」，也不能据此推断「知道但没照做」；LLM 组是否遵循技能，
  必须用修复后的 runner 重跑再判断。
  `wagner` 是唯一仍然不可达的：`superconduct_aggro` 有 0 张武器牌，该装备目标由牌组结构封死，
  与策略/判定无关。`blanche` 则是另一种情况：爆发目标 0/30 失败（三组各 10 局），但
  `win_within_rounds` 10/10 失败，实际回合 6–11，而阈值是 5。
- 这条画像对下一轮比 +0.125 这个总数更有信息量，但装备/事件部分作废后，剩下的主要是回合预算画像，
  而且它也只是观察，不是因果结论。

## 7. 复现 / 复核命令

```bash
export PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
cd ~/projects/gitcg-skills

bash data/run_t5b.sh          # 本次结果就是这个命令的产物（可用同命令 resume）
```

脚本内部实际执行的四个调用（`data/run_t5b.sh` 第 235–282 行；`--seeds 10` 表示 seeds 0–9，
`--deck` 未传，默认 `superconduct_aggro`）：

```bash
# 1) llm_empty 与 static 并行
GITCG_LLM_BUDGET_USD=1.50 "$PY" -m skillloop.run --group llm_empty \
  --stages all --seeds 10 --top-k 5 --workers 24 \
  --out data/runs/t5b_llm_empty.jsonl

GITCG_LLM_BUDGET_USD=1.50 "$PY" -m skillloop.run --group static \
  --stages all --seeds 10 --top-k 5 --workers 24 \
  --library data/skills/static_v1 --out data/runs/t5b_static.jsonl

# 2) 一次性技能提取（反思 effort=high）
GITCG_LLM_BUDGET_USD=1.50 "$PY" -m scripts.extract_skills \
  --rows data/runs/t5b_static.jsonl --base data/skills/static_v1 \
  --out data/skills/dyn_v1 --effort high --max-tokens 8192

# 3) dynamic（用刚提取的库）
GITCG_LLM_BUDGET_USD=1.50 "$PY" -m skillloop.run --group dynamic \
  --stages all --seeds 10 --top-k 5 --workers 24 \
  --library data/skills/dyn_v1 --out data/runs/t5b_dynamic.jsonl
```

复核门禁（纯离线，不开引擎、不联网）：

```bash
"$PY" -m scripts.compare_groups --a data/runs/t5b_static.jsonl --label-a static \
  --b data/runs/t5b_dynamic.jsonl --label-b dynamic \
  --out reports/compare/t5b_static_vs_dynamic.json
# 另外五对：scripted→contestant / scripted→llm_empty / scripted→static /
#           scripted→dynamic / llm_empty→dynamic，参数同上，产物见 reports/compare/t5b_*.json
```

复算 §5 的逐关表（读 JSONL，不重跑对局）：

```bash
"$PY" - <<'PY'
import json, collections
G = [("scripted","data/runs/t5_scripted.jsonl"), ("contestant","data/runs/t5_contestant.jsonl"),
     ("llm_empty","data/runs/t5b_llm_empty.jsonl"), ("static","data/runs/t5b_static.jsonl"),
     ("dynamic","data/runs/t5b_dynamic.jsonl")]
for name, path in G:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    rows = [r for r in rows if not r.get("error") and not r.get("truncated")]
    per = collections.defaultdict(list)
    for r in rows:
        per[r["stage"]].append(bool(r["three_star"]))
    print(name, round(sum(map(sum, per.values())) / len(rows), 4),
          {s: round(sum(v) / len(v), 2) for s, v in sorted(per.items())})
PY
```

## 8. 未验证 / 下一步

1. **留出种子复现（最高优先级）**：用**冻结的** `dyn_v1`（不要重新提取），在 seeds 10–19 上重跑
   `static` 与 `dynamic`，再跑一次 static→dynamic 门禁。写法：`--seeds 10,11,12,13,14,15,16,17,18,19`
   （`skillloop.run` 的 `--seeds` 只接「个数」或逗号列表，**不接受区间**）。
   只有那里的 CI 下界 > 0，本文 §4 的 +0.125 才能从 leakage-suspect 转正；否则应进 `docs/failures/`。
   预计成本：两组 × 160 局 × 约 $0.0022 ≈ **$0.7**（按本次 $/局 外推）。
2. 提取的 $0.0105 只出现在 `data/logs_t5b_extract.log`，没有写进任何 run 的 `.meta.json`；
   按 run 汇总成本时别漏掉它。
3. 本次 run 的 `record_decisions=False`（**这就是 2026-11-02 修复的判定 bug**），逐决策的 `prompt_hash`
   没有落盘（只有 `cited_skills` 聚合），且装备/事件目标恒为 false。修复后 `play_one` 已记录 decisions；
   本文数据仍来自旧 runner，需重跑才能得到逐决策复核与正确的装备/事件数值。
4. 每关 10 个种子：单关 Δ 的噪声约 ±0.2（1–2 局），任何逐关结论都要 50 seeds/关再确认。
5. 四关全 0 的旧结论已部分作废：`timaeus` / `uncletian` 的 0 来自判定 bug，修复后免费基线已能拿星；
   `wagner` 的装备目标因牌组无武器而不可达，`blanche` 仍需单独查回合阈值/打法。
   下一步不是继续加技能，而是用修复后的 runner 重跑三组 LLM，确认装备/事件目标是否真的被遵循。
   `wagner` 的 2 回合上限仍被列为可能标定过紧（`docs/STATUS.md` §5.4）。
