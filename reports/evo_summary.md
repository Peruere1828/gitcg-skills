# evo：三轮进化 + 留出种子转移测试（2026-11-02）

> **2026-11-02 更正**：`record_decisions=False` 的判定 bug 已修复；本文中所有「装备/事件目标恒为 false」的结论是**测量假象**，不是行为。受影响关卡的数值待重跑。
>
> 修复后同 seeds 免费重放：`scripted` 0.3812 → **0.4250**（68/160）、`contestant` 0.4062 → **0.4813**（77/160）。`timaeus` 装备 scripted 0/10 → 2/10、contestant 0/10 → 10/10；`uncletian` 事件 scripted 0/10 → 8/10、contestant 0/10 → 10/10；`wagner` 仍 0/10（牌组无武器）。本文所有 LLM 组 run 仍是旧判定，需重跑后重算。

> **生成命令**：`bash data/run_evo.sh`（2026-11-02 04:10:06 → 04:54:57 UTC，44m51s，compare failures=0，无 resume）。
> **数据来源**：`data/logs_evo.log`、`data/logs_evo_{r2,r3}_{extract,dynamic}.log`、
> `data/logs_evo_hold_{static,dynamic,llm_empty}.log`、`data/runs/evo_*.jsonl{,.meta.json}`、
> `reports/compare/evo_*.json`。原始逐局 JSONL 在 `data/runs/`（`.gitignore` 的 `data/*`，**不入库**）；
> 本文是聚合摘要，表中数字由 JSONL 复算（复核命令见 §7），不是从日志手抄。

## 1. 口径

| 项 | 值 |
|---|---|
| 关卡 | 16 关（`configs/npc/npc_stages_mond_liyue.json`，指纹 `da3ad153e409`） |
| 训练种子 | **0–9**（16 关 × 10 种子 = 160 局/run） |
| 转移种子 | **10–19**（留出；提取阶段从未使用这 10 个种子） |
| 样本完整性 | `run_evo.sh` 汇总的 7 个 run（5 个新 run + 2 个复用 r1 run）均 160/160 有效，0 truncated，0 error row；本文另引用 r1 的 `t5b_llm_empty`（160/160）作空库基线 |
| 牌组 | `superconduct_aggro` |
| 三星阈值 | `top_k=5`（阈值统一 5，D7） |
| 引擎 / 卡池 | `configs/engine.lock` 指纹 `fa42fdfec70b`（gitcg 0.21.0，engine commit `6f01d5c1`）；`hp_table` 指纹 `0bbae5d8798a`（D4 血量补丁） |
| 模型 | `deepseek-v4.1-flash`，`temperature=0`；对局 `reasoning_effort=none` + `max_tokens=64`（D10） |
| 提取 | `reasoning_effort=high` + `max_tokens=8192`，每轮一次 |
| 并发 | 训练 24 workers/组；转移 16 workers/组 × 3 组并行（48 进程） |
| 预算 | `GITCG_LLM_BUDGET_USD=1.50`/调用 × 7 次调用，名义上限 **$10.50**；`scripts.extract_skills` 不读该变量，两次提取的预算标记为「set-but-unenforced」 |

**判定口径**：本文汇总的 7 个 run（5 个新 run + 2 个复用 r1 run；另引用 `t5b_llm_empty` 作空库基线）都由修复前的 `skillloop.run` 产出（`record_decisions=False`），`timaeus`/`uncletian` 的装备/事件目标恒为 false；三星率与门禁需用修复后的 runner 重跑。

**链路**（每轮的提取来源与评测对局）：

| 轮 | 提取来源（seeds 0–9） | 产出库 | 训练评测 run |
|---|---|---|---|
| r1（t5b，历史） | `data/runs/t5b_static.jsonl` | `dyn_v1`（v2，42 技能） | `data/runs/t5b_dynamic.jsonl` |
| r2 | `data/runs/t5b_dynamic.jsonl` | `dyn_v2`（v3，42 技能） | `data/runs/evo_r2_dynamic.jsonl` |
| r3 | `data/runs/evo_r2_dynamic.jsonl` | `dyn_v3`（v4，44 技能） | `data/runs/evo_r3_dynamic.jsonl` |
| 转移 | 冻结 `dyn_v3`，不重新提取 | `static_v1` / `dyn_v3` / 空库 | `data/runs/evo_hold_{static,dynamic,llm_empty}.jsonl`（seeds 10–19） |

库的构成：`static_v1` v1 = 17 `rule` + 10 `strategy`（27）；`dyn_v1` v2 = 17 + 25（42）；
`dyn_v2` v3 = 17 + 25（42）；`dyn_v3` v4 = 17 + 27（44）。轮 2 的 16 个关卡画像产出 **13 条候选**
（13 次调用，$0.0093），按同名覆盖，库规模仍是 42；轮 3 产出 **14 条候选**（12 次调用，$0.0088），
其中 12 条同名覆盖、2 条新增（`1_marjorie`、`3_irontonguetian`），库规模到 44。

## 2. 训练集结果（seeds 0–9；4 个库 + 空库参照）

| 组 | 库（版本 / 规模） | 三星率 | 三星局 | 胜率 | $/局 | 该组总成本 |
|---|---|---:|---:|---:|---:|---:|
| static | `static_v1`（v1 / 27） | **0.3000** | 48/160 | 0.9437 | $0.002126 | $0.340219 |
| llm_empty（参照） | 空库 | **0.3563** | 57/160 | 0.9625 | $0.002296 | $0.367380 |
| dyn_v1（r1） | `dyn_v1`（v2 / 42） | **0.4250** | 68/160 | 0.9437 | $0.002384 | $0.381416 |
| dyn_v2（r2） | `dyn_v2`（v3 / 42） | **0.4437** | 71/160 | 0.9688 | $0.002101 | $0.336084 |
| dyn_v3（r3） | `dyn_v3`（v4 / 44） | **0.4250** | 68/160 | 0.9500 | $0.002202 | $0.352245 |

> `static` / `llm_empty` / `dyn_v1` 三行复用 r1 的 `data/runs/t5b_{static,llm_empty,dynamic}.jsonl`
> （同 seeds 0–9、同 top_k / deck / engine lock；static 与 dynamic 经 `run_evo.sh` 的 preflight
> 校验 fingerprint，llm_empty 的 `.meta.json` 另行核对）；`dyn_v2` / `dyn_v3` 是本次新跑。

表中三星率都是旧判定结果；修复后免费基线已变化（0.4250/0.4813），库间比较需重跑后再读。

## 3. 门禁（六对，按关配对 t；Δ = b − a）

口径同 `docs/EVAL_PROTOCOL.md` §4：每关先算 `Δ_s = rate_b(s) − rate_a(s)`（同 seeds），
16 关的 Δ 做单样本 t（df=15，t=2.131）；**accept ⇔ 95% CI 下界 > 0**。

| 比较（a → b） | 种子 | Δ | 95% CI | 对 | 门禁 |
|---|---|---:|---|---:|---|
| dyn_v1 → dyn_v2 | 0–9 | +0.0188 | [−0.0403, +0.0778] | 160 | accept=False |
| dyn_v2 → dyn_v3 | 0–9 | −0.0188 | [−0.0809, +0.0434] | 160 | accept=False |
| static_v1 → dyn_v3 | 0–9 | **+0.1250** | **[+0.0176, +0.2324]** | 160 | **accept=True** |
| static_v1 → dyn_v3 | 10–19 | +0.0625 | [−0.0220, +0.1470] | 160 | accept=False |
| llm_empty → dyn_v3 | 10–19 | +0.0188 | [−0.0750, +0.1125] | 160 | accept=False |
| llm_empty → static_v1 | 10–19 | −0.0437 | [−0.1307, +0.0432] | 160 | accept=False |

> accept 规则只看 CI 下界是否 > 0，不看 pooled 差；`reports/compare/evo_*.json` 里
> `n_pairs=160`、`dropped_pairs=0`。训练三行只有 static→dyn_v3 过线；转移三行全部 accept=False。
> 表中所有 Δ 都基于旧判定 run；`timaeus`/`uncletian` 的装备/事件判定修正后需重算。

## 4. 转移结果（seeds 10–19，冻结 `dyn_v3`）

| 组 | 库 | 三星率 | 三星局 | 胜率 | $/局 | 该组总成本 | Δ vs static（pooled） |
|---|---|---:|---:|---:|---:|---:|---:|
| static | `static_v1`（v1 / 27） | **0.3250** | 52/160 | 0.9750 | $0.002039 | $0.326223 | — |
| llm_empty | 空库 | **0.3688** | 59/160 | 0.9875 | $0.002251 | $0.360095 | +0.0438 |
| dynamic | `dyn_v3`（v4 / 44） | **0.3875** | 62/160 | 0.9812 | $0.002141 | $0.342606 | +0.0625 |

> `Δ vs static（pooled）` 是表中三星率之差；门禁表用按关配对的 Δ。`llm_empty` 行因舍入为
> 0.0437（pooled 0.0438），其余一致。
> 表中三星率是旧判定；`timaeus`/`uncletian` 的 0 待重跑，`wagner` 的 0 是牌组结构（无武器）。

### 4.1 逐关三星率（每关 n=10）

| 关卡 | static | llm_empty | dyn_v3 | Δ(dyn−static) | Δ(dyn−llm_empty) |
|---|---:|---:|---:|---:|---:|
| blanche | 0.0 | 0.0 | 0.0 | 0 | 0 |
| buyun | 0.3 | 0.6 | 0.3 | 0 | −0.3 |
| chefmao | 0.3 | 0.3 | 0.2 | −0.1 | −0.1 |
| ellin | 0.3 | 0.2 | **0.5** | **+0.2** | **+0.3** |
| irontonguetian | 0.5 | 0.3 | 0.4 | −0.1 | +0.1 |
| jifang | 1.0 | 1.0 | 1.0 | 0 | 0 |
| lan | 0.4 | 0.3 | 0.4 | 0 | +0.1 |
| liusu | 0.2 | 0.5 | 0.2 | 0 | −0.3 |
| marjorie | 0.8 | 0.7 | 0.7 | −0.1 | 0 |
| patton | 0.1 | 0.5 | **0.5** | **+0.4** | 0 |
| shitou | 0.0 | 0.1 | **0.2** | **+0.2** | +0.1 |
| swan | 0.9 | 1.0 | **1.0** | **+0.1** | 0 |
| timaeus | 0.0 | 0.0 | 0.0 | 0 | 0 |
| timmie | 0.4 | 0.4 | **0.8** | **+0.4** | **+0.4** |
| uncletian | 0.0 | 0.0 | 0.0 | 0 | 0 |
| wagner | 0.0 | 0.0 | 0.0 | 0 | 0 |
| **总体** | **0.3250** | **0.3688** | **0.3875** | **+0.0625** | **+0.0188** |

- 对 static：**5 关涨、3 关跌、8 关平**。涨的是 `timmie` +0.4、`patton` +0.4、`ellin` +0.2、
  `shitou` +0.2、`swan` +0.1；跌的是 `chefmao` −0.1、`irontonguetian` −0.1、`marjorie` −0.1。
- 对 llm_empty：`timmie` +0.4、`ellin` +0.3 是主要正项，`buyun` −0.3、`liusu` −0.3 抵消掉大半，
  总体只剩 +0.0188。
- 旧判定的三个转移组里，`blanche` / `timaeus` / `uncletian` / `wagner` 都是 0，但原因不同：
  `timaeus` / `uncletian` 的 0 是旧判定（`record_decisions=False`）造成的**测量假象**，修复后同
  seeds 免费重放已能达成（`timaeus` 装备 scripted 2/10、contestant 10/10；`uncletian` 事件
  scripted 8/10、contestant 10/10）；`wagner` 仍是 0，因为 `superconduct_aggro` 没有武器牌，
  装备目标由牌组结构封死。因此不能说「从 r1 起就有对应技能，但行为没有变化」——LLM 组是否遵循
  技能，需用修复后的 runner 重跑后才能判断。`blanche` 是另一类问题（回合/阈值），与装备/事件判定无关。
- 单关 n=10，±0.1 就是 1 局；逐关数字只能当画像，不能当逐关结论。

## 5. 三条结论

### 5.1 训练集唯一过线的 +0.1250，没有在留出种子上复现

同一对库（`static_v1` → `dyn_v3`）：训练 seeds 0–9 是 **+0.1250**（CI [+0.0176, +0.2324]，
accept=True），留出 seeds 10–19 只有 **+0.0625**（CI [−0.0220, +0.1470]，accept=False）。
留出增益正好是训练增益的一半。

这可能是对 seeds 0–9 的过拟合，也可能只是 10 seeds/关的功效不足；现有数据分不清。方向仍为正，
逐关也仍是 `timmie` / `patton` / `ellin` / `shitou` 这些带额外目标项的关卡在涨，画像与技能库的写法
一致，但按 `EVAL_PROTOCOL` §4 这还不能算「技能库提分」的证据。

更硬的读法：dynamic 相对 **空库** 的配对 Δ 只有 +0.0188（CI [−0.0750, +0.1125]），所以相对
static 的 +0.0625 主要是把 static 的负作用抹平，而不是证明演进库超过了空库。机制上，dynamic 的
`win_within_rounds` 失败是 **70/160**，低于 static 的 82/160 和空库的 77/160；这与技能库主要在
写「回合预算」一致，但仍不足以把总体差异推过门禁。以上都是旧判定口径；修复后「正好一半」这个比例需重跑再确认。

### 5.2 多轮进化不单调：dyn_v2 见顶，dyn_v3 在同一批种子上回落

`dyn_v2` 在训练集到 **0.4437**（相对 dyn_v1 +0.0188，n.s.）；`dyn_v3` 在**同一批 seeds 0–9** 上
回落到 **0.4250**（相对 dyn_v2 −0.0188，n.s.）。轮 2 的 13 条候选按同名覆盖，库仍是 42 条；
轮 3 的 14 条里 12 条覆盖、2 条新增，库到 44 条。

`top_k=5` 下，轮 3 的 160 局只引用了 44 条中的 16 条：`1_chefmao` 被引用 128 次（占 640 次引用的
20%），`skill_lan` / `skill_wagner` 一次未被引用；轮 2 时 `skill_lan` 还被引用 80 次、`skill_wagner`
10 次。**这是「检索被挤占」的迹象，不是因果证明**——技能描述本身也改了，单看聚合引用数分不清是
「新技能挤掉旧技能」还是「描述变了导致检索偏好变了」。但至少可以说：每轮无条件接受提取结果，
在这条链上没有带来单调收益。

### 5.3 「手写静态库净负」在两个种子集上复现

| 种子集 | static | llm_empty | 差（static − empty，pooled） |
|---|---:|---:|---:|
| 训练 0–9 | 0.3000 | 0.3563 | **−0.0563** |
| 留出 10–19 | 0.3250 | 0.3688 | **−0.0438** |

方向在两个种子集上一致，差距都不小。补充复算（不是 `run_evo.sh` 预注册的六行门禁）：
训练 static→llm_empty 为 **+0.0562**，CI [−0.0466, +0.1591]；留出 llm_empty→static 为
**−0.0437**，CI [−0.1307, +0.0432]——两段都没过门禁。

所以这是目前最稳的**方向性**发现，不是显著结论；它足够支持一个工程决定：**后续提分应以
`llm_empty` 为基线，不要拿 static 当基线**。static 的负作用会把「相对 static 的提升」放大成
假象——本次训练集 +0.1250 的过线就有这个成分。本节的 static/empty 对比同样来自旧判定 run；
`timaeus`/`uncletian` 修正后可能改变数值，重跑后复核。

## 6. 成本

| 项 | 金额 | 说明 |
|---|---:|---|
| `evo_r2_dynamic.jsonl` | $0.336084 | 160 局，$0.002101/局 |
| `evo_r3_dynamic.jsonl` | $0.352245 | 160 局，$0.002202/局 |
| `evo_hold_static.jsonl` | $0.326223 | 160 局，$0.002039/局 |
| `evo_hold_dynamic.jsonl` | $0.342606 | 160 局，$0.002141/局 |
| `evo_hold_llm_empty.jsonl` | $0.360095 | 160 局，$0.002251/局 |
| **五个 run 小计** | **$1.717253** | 800 局 |
| r2 提取（13 次调用） | $0.0093 | `data/logs_evo_r2_extract.log` |
| r3 提取（12 次调用） | $0.0088 | `data/logs_evo_r3_extract.log` |
| **本轮总计** | **$1.735353 ≈ $1.7354** | 五个新 run + 两次提取 |

提取成本在日志里只有 4 位小数，因此总计末位有约 ±$0.0001 的舍入误差。**不含 r1（t5b）的成本**；
若把 r1 的 $1.0995 也算上，三轮全链约 **$2.8349**。

## 7. 复现 / 复核

完整生成命令：

```bash
export PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
cd ~/projects/gitcg-skills

bash data/run_evo.sh          # 本次结果就是这个命令的产物；可用同命令 resume
```

脚本末尾的六对比较（纯离线，不重跑对局）：

```bash
"$PY" -m scripts.compare_groups --a data/runs/t5b_dynamic.jsonl --label-a dynamic_r1 \
  --b data/runs/evo_r2_dynamic.jsonl --label-b dynamic_r2 \
  --out reports/compare/evo_train_r1_vs_r2.json
"$PY" -m scripts.compare_groups --a data/runs/evo_r2_dynamic.jsonl --label-a dynamic_r2 \
  --b data/runs/evo_r3_dynamic.jsonl --label-b dynamic_r3 \
  --out reports/compare/evo_train_r2_vs_r3.json
"$PY" -m scripts.compare_groups --a data/runs/t5b_static.jsonl --label-a static_r1 \
  --b data/runs/evo_r3_dynamic.jsonl --label-b dynamic_r3 \
  --out reports/compare/evo_train_static_vs_r3.json
"$PY" -m scripts.compare_groups --a data/runs/evo_hold_static.jsonl --label-a static \
  --b data/runs/evo_hold_dynamic.jsonl --label-b dynamic \
  --out reports/compare/evo_hold_static_vs_dynamic.json
"$PY" -m scripts.compare_groups --a data/runs/evo_hold_llm_empty.jsonl --label-a llm_empty \
  --b data/runs/evo_hold_dynamic.jsonl --label-b dynamic \
  --out reports/compare/evo_hold_llm_empty_vs_dynamic.json
"$PY" -m scripts.compare_groups --a data/runs/evo_hold_llm_empty.jsonl --label-a llm_empty \
  --b data/runs/evo_hold_static.jsonl --label-b static \
  --out reports/compare/evo_hold_llm_empty_vs_static.json
```

复算逐关表（读 JSONL，不重跑对局）：

```bash
"$PY" - <<'PY'
import json
from pathlib import Path

GROUPS = [
    ("static", "data/runs/evo_hold_static.jsonl"),
    ("llm_empty", "data/runs/evo_hold_llm_empty.jsonl"),
    ("dynamic", "data/runs/evo_hold_dynamic.jsonl"),
]
for name, path in GROUPS:
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    rows = [r for r in rows if not r.get("error") and not r.get("truncated")]
    per = {}
    for r in rows:
        per.setdefault(r["stage"], []).append(bool(r["three_star"]))
    print(name, round(sum(bool(r["three_star"]) for r in rows) / len(rows), 4),
          {s: round(sum(v) / len(v), 2) for s, v in sorted(per.items())})
PY
```

## 8. 未验证 / 下一步

1. **功效不足**：每关 10 个种子，转移门禁的 CI 宽度约 0.17；要检验 +0.0625 是否真实为正，
   需要更多留出种子（按 `EVAL_PROTOCOL` §3 的 50 seeds/关口径），而不是再加一轮提取。
2. **按轮门禁，而不是每轮都接受提取**：r2→r3 在训练集上是 −0.0188；如果只保留过线的库版本，
   `dyn_v3` 根本不会成为候选。下一步应把「提取 → 门禁 → 晋级」拆开，未过线的库只进档案。
3. **检索挤占只是观测**：`cited_skills` 是聚合字段，不能还原每次决策的候选列表。
   `record_decisions=True` 已在 2026-11-02 的判定修复中打开；重跑后可用逐决策记录复核候选/激活，
   但这仍不是本轮数据能回答的。
4. **四个全零关**（`blanche` / `timaeus` / `uncletian` / `wagner`）在旧判定下跨组不变：其中 `timaeus` /
   `uncletian` 的 0 是旧判定造成的测量假象，不是行为；`wagner` 仍不可达（牌组无武器），
   `blanche` 的 `win_within_rounds` 10/10 失败是真实的回合问题。原来说的「知道但没照做」不能由
   本轮数据支持；要用修复后的 runner 重跑三组 LLM 再判断。
5. 两次提取的 `GITCG_LLM_BUDGET_USD` 不生效（脚本注释已标明）；本轮的提取花费很小（$0.0181），
   但预算口径不能按「7 × $1.50 硬上限」理解。
6. 本次新生成的 `data/skills/dyn_v2`、`data/skills/dyn_v3` 和 `reports/compare/evo_*.json` 目前是
   工作区新文件；`data/skills/**` 在 `.gitignore` 白名单内，需随本报告一起入库。原始 JSONL
   仍在 `data/runs/`，按约定不入库。
