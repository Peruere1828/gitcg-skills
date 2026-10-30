# decklab — 卡组构筑 Agent（PLAN.md WS4）

固定模板内调 5–15 张 flex 位：换卡敏感性分析、对评测池全部假想敌（当前 20 套，D11）对位胜率模拟。结论固化为新 deck-list 进评测池。

## 已实现（2026-10-07）

- `sensitivity.py`：固定 30 张模板，做「移 1 张 flex 卡 + 加 1 张候选卡」的换卡评测。
  - `mutate_deck`、`candidate_cards`（来自其它脚本卡组的候选池）。
  - `evaluate_swaps`：每张换卡在配对种子上 vs 固定对手池跑小样本，输出对 baseline 的胜率差 + Wilson CI。
  - 由于改牌后无对应脚本专家，己方用固定通用策略（`heuristic`），对手仍用脚本专家。
  - 通过 `MatchTask.deck0_inline` 把改后牌组传给 worker 进程（无需注册）。
- `corpus.py`：加载公开卡组语料（`data/deck_corpus/decks.json`，或提交的紧凑副本
  `reports/explore/deck_corpus.json`），产出 `DeckSpec`。采集/验证见 `scripts/explore/fetch_deck_corpus.py`，
  摘要见 `reports/explore/deck_corpus.md`。无网络。
  ```python
  from decklab.corpus import corpus_deck_specs
  specs = corpus_deck_specs()  # name = corpus slug
  ```
- CLI：`python -m decklab.sensitivity --deck superconduct_aggro --seeds 5 --workers 12`。
- `builder.py`（2026-10-12，PvE-v1 门禁 D19）：**确定性规则组牌**——给定池清单
  （`configs/pools/*.json`）与 3 名角色，按「固定 meta 核心 + 人工填充序」产出合法 30 张牌组
  （每卡 ≤2 张、全部来自池内）。`build_pool_c_superconduct()` 是 PvE-v1 门禁的 contestant 牌组。
  这不是学习/Agent 式自动组牌（见下「待办」），门禁报告已记录该偏差。

待办：LLM 提议换卡（结构化候选）、更强己方策略（训练后再做换卡敏感性）、结论固化进评测池；
学习/Agent 式自动组牌（D19 的目标形态，当前用 `builder.py` 规则组牌近似）。
