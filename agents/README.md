# agents — 对局 Agent（PLAN.md WS4 / M5）

> 状态见 [`docs/STATUS.md`](../docs/STATUS.md)；有效方法见 [`docs/PLAYBOOK.md`](../docs/PLAYBOOK.md)。

ResolvingAgent（纯策略模式，G1 验收口径）、llm_assist（LLM 辅助模式：候选校验/重排/兜底，延迟预算内，干预全量记录）、
脚本对手适配层。客户端强制降级链：本地 LLM → API → 纯策略。

## 历史实现（2026-10-07；当前接口以代码为准）

- `scripted/`：从 `refs/Rebel_base_RL/.../gitcg_expert_system` 逐模块移植的脚本对手
  （`ExpertRuleAgent` + `deck_rules/` 20 套 + `registry`/`profiles`/`codec`/`assets`/`models`），AGPL-3.0，见 `NOTICE.md`。
  - `load_deck_profiles()` / `load_deck_profiles_by_slug()` 解码 share code → `DeckProfile`；
    资产（卡名/标签/shareId 映射）首次从公开 assets API 拉取并缓存到 `data/assets/`（可设
    `GITCG_ASSETS_CACHE` / `GITCG_ASSETS_OFFLINE=1`）。
  - 经 `envs.policy.expert_policy(slug)` 接入对局。
- `llm_assist.py`：局内 LLM 辅助模式（`LLMAssistPolicy`）——基础策略提议 → LLM 在白名单候选里
  重排/校验，`AssistConfig(budget_per_game, max_candidates, min_legal_options, model, ...)`；
  只把 `player_view`（公开信息+己方私有）渲染进 prompt（对手手牌只给**数量**）；每次干预记录
  `Intervention`（base/llm/final code、reason、latency、error）。降级链：无 client → 纯策略。
  经 `scripts/run_llm_agent.py` 与纯策略小样本对比（分开报分，G1 只用纯策略）。

待办：`resolving_agent`（纯策略搜索，依赖 `train/`）、LLM 干预数据蒸馏回训练。
