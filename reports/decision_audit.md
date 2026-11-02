# Decision audit — what one model call actually decides

> **2026-11-02 已修复**：`record_decisions=False` 的判定 bug 已修复（`skillloop/run.py::play_one`
> 改为 `True`，回归测试 `tests/test_skill_run.py::test_play_one_records_decisions_for_card_objectives`）。
> 本文 §5/§6 记录的是 bug 本身；修复后牌组结构上限是 **15/16**（仅 `wagner`：牌组 0 武器），不再是 13/16。
> 旧 run 的数值仍需用修复后的 runner 重跑。

Scope: `skillloop/agent.py`, `skillloop/render.py`, `skillloop/library.py`, `envs/match.py`,
`reps/action_adapter.py`, and the installed `gitcg 0.21.0`. No model or network calls.

Measurement used the offline recorder in `scripts/audit_trajectory.py` (present in the
worktree; run to `/tmp` for this audit): 32 expert-vs-NPC games, **16 stages × seeds 1,2**,
deck `superconduct_aggro`, 1,098 player-0 engine requests. It wraps player 0 and calls
`select_candidates` exactly as `SkillAgentPolicy` does. The exact command was:

```bash
PYTHONPATH=. python scripts/audit_trajectory.py --stages all --seeds 1,2 --workers 1 \
  --out /tmp/opencode/trajectory_audit.md --json /tmp/opencode/trajectory_audit.jsonl
```

The expert trajectory is not an LLM trajectory; actual LLM runs in `data/runs/` have median
24 calls/game (see §5), close to the 28 model-eligible requests/game measured here.

## Executive summary

1. **One model call = one engine request = one atomic action** (or one mulligan / one
   active-character pick), not one round. A game has 34.3 requests on average (median 34),
   ~6.7/round; ~82% are model-eligible. The model gets no history, no previous choice, no
   plan.
2. **The dice reroll is never a model decision.** 0/166 rerolls reached the model; all were
   the scripted expert's. Even if added to `request_types`, the current dedupe would collapse
   every reroll mask to one "keep all" candidate.
3. **The 16-candidate cap is a hard, order-biased bottleneck.** 58.7% of action requests are
   truncated; `switch_active` is dropped from 70.6% of the options where it is legal and
   `declare_end` from 58.7%. The expert's own chosen action is outside the model's window in
   **27.0%** of action requests (203 of 296 expert switches).
4. **The skill library is a static per-game prior, not per-decision guidance.** Retrieval runs
   once, at the mulligan (round 0), and the same 5 strategy paragraphs + 17 rule paragraphs
   are shown at every subsequent decision. There is no per-decision retrieval, no "which
   skill applies here", and no plan step.
5. **The artifacts cannot support per-decision attribution — and 3/16 stages are mis-scored.**
   `record_decisions=False`, so no per-decision row exists. Worse, `play_equipment_card` and
   `play_event_cards` are judged from `DecisionRecord`s that are therefore always empty:
   `timaeus`, `wagner`, `uncletian` were structurally capped at 2 stars in every
   `skillloop.run` group before the fix. A controlled replay of `uncletian` seed 2 flips 2★→3★ when only
   `record_decisions` is turned on. **Fixed 2026-11-02**: the runner now records decisions; the
   remaining deck-based cap is 15/16, with only `wagner` (0 weapons) unable to pass.

---

## 1. What is one model decision?

The engine has **five** request types, expressed as a protobuf `oneof` on `Request`
(`gitcg/proto/rpc_pb2.pyi:79-91`; documented in `gitcg/player.py:44-49`). There is no
`RequestType` enum in `gitcg 0.21.0`; the repo mirrors the five in `DecisionType`
(`reps/schema.py:10-16`), with an extra, unused `TERMINAL` value. `PolicyPlayer` handles
exactly the five (`envs/match.py:384-422`).

Routing is a string membership test: `context.request_type.value in
AgentConfig.request_types` (`skillloop/agent.py:43,153-154`). The default is
`("action", "switch_hands", "choose_active")`. `run.py` constructs `AgentConfig` with only
`model`, `max_calls_per_game`, `top_k` (`skillloop/run.py:150`), so every normal
`skillloop.run` game uses that default.

| Engine request (`DecisionType`) | Routed to model? | Actual decider (32-game probe) | Mean /game | /round | Candidate set |
|---|---:|---|---:|---:|---|
| `action` (`ACTION`) | yes | LLM 833/833 | 26.0 (median 25.5) | 5.0 median | one of 5 action kinds + one payment; legal = deduped p50 19; 16 shown |
| `switch_hands` (`SWITCH_HANDS`) | yes | LLM 32/32 | 1.0 | once, round 0 | 32 subsets → 6 deduped |
| `choose_active` (`CHOOSE_ACTIVE`) | yes | LLM 38/39; 1 forced expert | 1.2 | 0.2 | 2–3 characters |
| `reroll_dice` (`REROLL_DICE`) | **no** | expert 166/166 | 5.2 | exactly 1.0 | 96–144 subsets → 1 deduped |
| `select_card` (`SELECT_CARD`) | **no** | expert 28/28 | 0.9 | 0.2 | 2 definitions |
| `terminal` | — | not an engine request | — | — | — |

Totals: **34.3 requests/game**, median 6.7/round, range 5.7–8.0. 903/1,098 (**82.2%**) are
model-eligible (routed and ≥2 candidates). The model never sees 195/1,098 (**17.8%**): 166
`reroll_dice` + 28 `select_card` + **1** forced `choose_active`. Only that 1 is the
"candidate set has <2" rule; the other 194 are excluded by `request_types`.

The `<2` rule itself is `select_candidates` returning `[]` when there are ≤1 raw legal
actions (`skillloop/render.py:233-234`), followed by `if len(candidates) < 2: return
base_code` (`skillloop/agent.py:158-159`). On this deck action/switch/select were never
forced; only one `choose_active` was (1/39). `max_calls_per_game=80` is never binding —
the maximum in `data/runs/` is 54 calls, and the probe implies ~28.

### What the model actually sees per call

System message, built once per game (`skillloop/render.py:198-207`):

```
你是一个《七圣召唤》对局助手 ...                     # role, 73 chars
<all active rule skills, one line each>               # 17 lines, 1,238 chars
关卡【<stage>】用【<deck>】出战。三星目标：<objectives>。  # task
可参考的技能（来自历史经验，优先级低于关卡目标）：
- [skill_id] name: description                         # 5 retrieved skills, 1,061 chars
只输出 JSON：{"choice": <编号>, "used": [<技能 id，最多 2 个>]}。  # 74 chars
```

User message, rebuilt every call (`skillloop/render.py:128-135,209-215`):

```
第{round}轮 {phase} 行动方={我方|对方}
我方：{角色} {hp}/{max_hp}[ 能量{e}/{max}][ {aura}][★出战][✖]，...
我方骰子：{元素符号}（{n}个）
我方手牌：{卡牌名}，...
{opponent}：{角色}；手牌{n}张，骰子{n}个
我方已宣布结束={bool}

合法动作：
[0] {动作类型}：{subject}{ → 目标}{ 花费骰子}
[1] ...
```

What is **not** in the prompt: the previous action or the model's previous choice; any plan;
the fact that the candidate list was truncated; `is_fast` (it is collected in
`reps/action_adapter.py:487` but never rendered); card/skill effect text (only names,
targets, cost); the candidate count beyond the numbered list. The model is stateless across
calls: `client.chat([system, user], ...)` (`skillloop/agent.py:164-171`), with
`reasoning_effort="none"`, `max_tokens=64` (`skillloop/agent.py:39-40`), and an output
contract of only `{"choice": i, "used": [...]}`.

Measured prompt sizes: `dyn_v3` system 2,538 chars (1,238 rules + 1,061 skills), `static_v1`
1,922 chars, `llm_empty` 211 chars. In `data/runs`, median prompt tokens/call are ~1,965
(dynamic), ~1,560 (static), ~504 (empty); provider cache hit is 76.4% / 71.5% / 0%.

---

## 2. Dice decisions

**No: `reroll_dice` is never sent to the model.**

- `AgentConfig.request_types` omits it (`skillloop/agent.py:43`).
- `SkillAgentPolicy.choose` returns `base_code` for any request not in that tuple
  (`skillloop/agent.py:152-154`). `base` is the deck's scripted expert.
- `run.py` never overrides `request_types` (`skillloop/run.py:150`).
- The expert dispatches rerolls to `_choose_reroll` (`agents/scripted/agent.py:86-87`); for
  this deck it scores subsets by kept electro/cryo/omni dice and rerolls off-element dice
  (`agents/scripted/deck_rules/superconduct_aggro.py:38-89`).
- Measured: 166/166 rerolls in 32 games were expert-only, exactly one per roll phase
  (median 5/game, matching the median 5 rounds).

The model can only react **after** the dice are fixed: the first user message of the round
shows `我方骰子：...` (post-reroll), and then the model chooses among pre-enumerated spend
payments. It can also choose `元素调和` (tune a card into a die), but it cannot choose which
dice to keep at the start of the round. That planning lever is 100% the scripted policy's.

Even if `reroll_dice` were added to `request_types`, the current candidate builder would make
it a fake choice: `_reroll_specs` enumerates every multiset subset
(`reps/action_adapter.py:378-397`), but `select_candidates`' key omits `reroll_dice_mask` and
every reroll spec has `subject_definition_id=0`, no targets, and no `used_dice`
(`skillloop/render.py:238`). All 96–144 masks collapse to **one** candidate — the first
subset, which is the empty set, i.e. "reroll none". `len(candidates) < 2` would then send it
back to the expert (`skillloop/agent.py:158-159`). Measured: 166/166 reroll requests produced
exactly 1 rendered candidate.

---

## 3. Candidate granularity

### What one candidate is

`LowLevelActionSpec` (`reps/schema.py:137-155`) is one engine action plus a concrete
resolution: `kind`, `subject_definition_id`, `target_slots`, `used_dice`,
`choose_active_slot`, `select_card_definition_id`, `switch_hand_slot_mask`,
`reroll_dice_mask`, etc.

- For **`action`**, one candidate = one `request.action[i]` (one skill / one card / one
  switch / one tuning / end) **plus one enumerated dice payment**. The enumeration is
  `for used_dice in payments` (`reps/action_adapter.py:447-466`); the candidate's payload is
  `{"chosen_action_index": action_index, "used_dice": [...]}` (`:490-493`). **It cannot chain
  several actions.** "Tune two cards then use a skill" is three separate `action` requests
  and three separate model calls. A card that is a fast action still gets its own request;
  the model is not told it is fast.
- For **`switch_hands`**, one candidate is a whole subset of cards to replace
  (`reps/action_adapter.py:400-432`).
- For **`reroll_dice`**, one candidate is a whole subset of dice to reroll
  (`reps/action_adapter.py:378-397`).

So the unit is atomic for `action` and set-valued for the two "choose a subset" requests.
There is no candidate that means "play this card and then switch" or "tune these dice and
then use this skill".

### What the dedupe key collapses

Key: `(kind, subject_definition_id, target_slots, used_dice)` (`skillloop/render.py:238`).

| Request | Legal → deduped | What happens |
|---|---:|---|
| `action` | 19 → 19 (p50) | **Nothing collapses on this deck.** `used_dice` is part of the key, so different dice-value payments of the same skill/card remain separate candidates. Identical `(kind, subject, targets, used_dice)` duplicates collapse. |
| `switch_hands` | 32 → 6 | The key uses the **first removed card** as subject and omits `switch_hand_slot_mask`; all supersets whose first removed card is the same collapse. Only "keep all" + "remove exactly one copy of each distinct card" survive. |
| `reroll_dice` | 96–144 → 1 | Subject is 0 and the mask is omitted; every subset collapses to the first one, the empty set ("reroll none"). |
| `choose_active` | 3 → 3 | Subject is the character definition id; distinct characters survive. |
| `select_card` | 2 → 2 | Subject is the card definition id; distinct cards survive. |

A natural but wrong reading is that many dice-subset variants of the same **skill** become
one candidate. For `action` they do **not** collapse: they stay separate and are exactly what
floods the window. The collapse-to-one happens for **reroll**, and partial collapse happens
for **switch_hands**. `action` is already deduped upstream with a fuller key that includes
the masks (`reps/action_adapter.py:60-79`), so `select_candidates` is a no-op for `action` here.

### Measured candidate counts (32 games, 833 action requests)

| Request | n | legal p50/p90/max | deduped p50/p90/max | shown p50/p90/max | cap hit | expert choice outside shown |
|---|---:|---|---|---|---:|---:|
| `action` | 833 | 19 / 30 / 36 | 19 / 30 / 36 | 16 / 16 / 16 | **489 (58.7%)** | **225 (27.0%)** |
| `switch_hands` | 32 | 32 / 32 / 32 | 6 / 6 / 6 | 6 / 6 / 6 | 0 | 0 |
| `choose_active` | 39 | 3 / 3 / 3 | 3 / 3 / 3 | 3 / 3 / 3 | 0 | 1 |
| `reroll_dice` | 166 | 96 / 144 / 144 | 1 / 1 / 1 | 1 / 1 / 1 | 0 | 166 |
| `select_card` | 28 | 2 / 2 / 2 | 2 / 2 / 2 | 2 / 2 / 2 | 0 | 0 |

Action deduped-count distribution (buckets, n=833):

| deduped candidates | 3–5 | 6–10 | 11–15 | 16–20 | 21–25 | 26–30 | 31–36 |
|---|---:|---:|---:|---:|---:|---:|---:|
| requests | 155 | 67 | 113 | 137 | 168 | 130 | 63 |

The distribution is bimodal: 155 requests have 3–5 options (early round / empty hand), while
361 have 21–36 (mid-round, before cards and dice are spent). The cap binds almost entirely in
the second mode.

The cap is applied **after** dedupe, in engine order, and stops at 16
(`skillloop/render.py:230-244`). In the observed order, skills and cards come first, then
tuning; `switch_active` and `declare_end` are the tail (`declare_end` was last in 449/449
probed requests). When the cap binds, the model never sees that tail. Option-instance drop
rates across the 833 action requests:

| kind | legal instances | dropped by cap | drop rate |
|---|---:|---:|---:|
| `action_use_skill` | 459 | 0 | 0.0% |
| `action_play_card` | 833 | 361 | 43.3% |
| `action_elemental_tuning` | 495 | 369 | 74.5% |
| `action_switch_active` | 677 | 478 | **70.6%** |
| `action_declare_end` | 833 | 489 | 58.7% |

The expert chose `switch_active` 296 times, but only 93 of those choices (31.4%) were inside
the model's rendered window; 203 were invisible. It chose `declare_end` 137 times, and 131
(95.6%) were visible — because end is usually chosen late in the round when the menu has
already shrunk, while switch is chosen mid-round when the menu is wide. This is a structural
bias, not a random truncation.

For example, in a 19-legal request the model saw use_skill 2, play_card 9, tuning 5, and
**neither** of the 2 switches nor the end-turn option. This is the single clearest reason
the model cannot imitate the expert's own play.

---

## 4. Skill guidance path

### The pipeline, exactly

1. `skillloop.run.play_one` loads the library once per game (`skillloop/run.py:138-142`) and
   passes it to `SkillAgentPolicy`.
2. On the **first routed decision**, `_system_message` calls
   `library.retrieve(self.task.describe(), self._context_dict(context), k=top_k)`
   (`skillloop/agent.py:125-131`).
3. Query: `TaskSpec.describe()` = `关卡【<display>】用【<deck>】出战。三星目标：<objective texts>。`
   (`skillloop/render.py:58-60`). Context: `{"stage", "objective_types", "round"}`
   (`skillloop/agent.py:140-147`). No board state, no hand, no dice.
4. `retrieve` filters by `SkillConditions` (`stages`, `objective_types`, `enemies`,
   `round_min/max`) and by layer (`("strategy", "stage_specific")`), then ranks by BM25 over
   `skill_name + description` plus a layer bonus (`skillloop/library.py:179-208`). Rules are
   excluded from top-k.
5. `rules_text()` separately returns **all** active `layer="rule"` skills, sorted, with no
   condition filtering (`skillloop/library.py:137-140`). Both blocks go into the system
   message (`skillloop/render.py:198-207`).
6. The system message is cached in `self._system`; there is no invalidation path
   (`skillloop/agent.py:120,127-138`). Every later decision reuses it byte-for-byte.
7. The model returns `{"choice": i, "used": [skill ids]}`. `used` is filtered to the
   retrieved ids (`skillloop/agent.py:180-181`) and stored on `Decision`; it is **not** used
   to validate, re-rank, or condition the next call.

### Cadence and content

- **One retrieval per game.** In normal runs the first routed request is `switch_hands`
  (`request_types` includes it) and it happens at **round 0** — measured 32/32 games, step 1.
  The retrieval context therefore has `round=0`.
- **Same 5 skills at every decision.** The system message is frozen; the retrieved set
  cannot change mid-game. It changes only across games (different stage/objectives) or
  library versions.
- **5 is only the strategy depth.** `top_k=5` (`AgentConfig`, `skillloop/agent.py:45`;
  `RunSpec`/CLI default 5, `skillloop/run.py:57,377`). The model also gets **17 rule
  paragraphs**. For `dyn_v3` that is 27 strategy candidates competing for 5 slots plus 17
  always-on rules; for `static_v1`, 10 strategy candidates + 17 rules.
- **The evolved library is not actually layered.** `dyn_v3` has 44 skills: 17 `rule` + 27
  `strategy`, `stage_specific: 0` (`SkillLibrary.by_layer()`). The intended 1.5 layer bonus
  for `stage_specific` never applies. All 27 non-rule skills compete on BM25 + the same 0.8
  strategy bonus.
- **Skills are not stage-gated.** The evolved skills' conditions use objective types and
  `round_max`, not `stages`: e.g. `skill_buyun` is `{"objective_types": [...], "round_max":
  5}`. So every stage with matching objectives is in the pool; only text similarity
  distinguishes them.
- **The stage's own skill is often not retrieved.** With `dyn_v3` and `round=0`, the skill
  authored for the stage appears in the top-5 for **7/16** stages (`dyn_v1`: 10/16;
  `dyn_v2`: 9/16). Examples: `wagner` gets `1_chefmao, skill_marjorie, skill_blanche,
  skill_timaeus, skill_buyun` and misses `skill_wagner`; `lan` misses `skill_lan`; `timmie`
  and `buyun` do get their own. This is not automatically wrong (the advice is largely
  objective-generic), but it shows retrieval is text-driven, not stage-gated.
- **Stale-by-construction advice.** Because retrieval happens at round 0, a skill with
  `round_max=2` (e.g. `skill_timaeus`) stays in the prompt through round 8. A skill with
  `round_min>=1` would never be retrieved at all. The `enemies` context is never supplied
  (`_context_dict` omits it), so enemy-conditioned skills can never match; current libraries
  do not use that field.
- **Rules are not filtered by objective.** `rules_text()` includes all 17 rule skills, so a
  `timmie` prompt contains rules for event cards, reactions, bursts, and equipment even
  though none of those are its objectives.
- **Citations are decorative.** The output contract asks for up to 2 skill ids. They are
  filtered to retrieved ids and stored, but nothing checks that the cited skill supports the
  chosen action, and the next prompt does not include them. `UsageLog` (the claimed
  activation accounting, `skillloop/library.py:248-273`) is never called by the agent; only
  tests use it. The per-game row stores the union `cited_skills`, not per-decision citations.

### Direct answer to "how does the library actually guide each decision?"

**It does not guide each decision individually.** It supplies a single, fixed system-prompt
prior for the whole game: 17 generic rule paragraphs + 5 BM25-selected strategy paragraphs.
Every decision then gets that same text plus the current board and ≤16 candidates. There is
no per-decision retrieval, no "which skill applies to this candidate", no plan step, and no
verification. The only per-decision skill signal is the model's own post-hoc `used` citation.
In the user's phrasing: the model is handed 5 strategy paragraphs (plus 17 rule paragraphs)
and the board; it is not handed a decision-specific skill.

---

## 5. Telemetry in the LLM runs

### What is stored

`skillloop.run.play_one` called `run_match(..., record_decisions=False, record_events=True,
record_final_state=True)` for every run described here (`skillloop/run.py:184-186`; now `True`
after the 2026-11-02 fix). The per-game row has exactly:

```
task_index, stage, seed, deck, group, model, library_version, winner, rounds,
three_star, stars, n_objectives, objectives, error, truncated, seconds,
llm_calls, llm_errors, llm_seconds, cited_skills,
prompt_tokens, completion_tokens, cached_tokens, cost_usd
```

(`skillloop/run.py:192-217`). `llm_calls` is `len(decisions)`, `llm_errors` is a count,
`llm_seconds` is a sum of latencies, `cited_skills` is the union of all `used` ids
(`skillloop/run.py:209-212`). `record_events=True` and `record_final_state=True` are used
only by `evaluate_stage`; neither events nor final state are written to the row.

The `Decision` dataclass computes `step, request, n_candidates, choice, used, error,
latency, system_hash, http_calls` (`skillloop/agent.py:49-71`), but `Decision.as_dict()` is
never called; `on_decision` is `None`; the list is only aggregated. In those runs,
`record_decisions=False` also meant no `PolicyPlayer.DecisionRecord` was produced (`chosen_action_code`,
`legal_action_codes`, `subject_definition_id`, `card_kind`, ...; `envs/match.py:477-504`).

For scale: the 160-game `evo_hold_*` runs have median 24 LLM calls/game (dynamic and
static) and 26 (empty), max 54. That is close to the 28 model-eligible requests/game in the
offline probe, so the probe's request mix is representative even though its trajectory is
the expert's.

**Consequently, after the fact we cannot reconstruct:** which request type each call
answered; how many candidates it saw; which candidate/action the model chose; whether the
model disagreed with the expert's `base_code`; per-decision skill citations; per-decision
latency; the system hash; whether the cap truncated the list; which decision or round
produced which star/objective; or even the actual model name (`model` is `null` in every row
and meta because `RunSpec.model` is not set; the real name comes from
`GITCG_LLM_MODEL`/`DEEPSEEK_MODEL` in `common/llm.py:113`). `cited_skills` is the only
per-decision-derived field, and it is a per-game union.

### Worse: `record_decisions=False` corrupts the star metric on 3 stages (fixed 2026-11-02)

The objective judge uses `DecisionRecord`s for `play_equipment_card` and
`play_event_cards` (`npc/objectives.py:22-25,176-205`). `_count_decisions` sums over
`record.decisions` (`npc/objectives.py:93-94`). With `record_decisions=False`, that list is
empty, so those two objectives are **always false**, regardless of what the agent played.

Controlled replay (same seed, same scripted expert, only `record_decisions` differs):

| stage | seed | OFF | ON |
|---|---:|---|---|
| `uncletian` | 2 | 2★ (`play_event_cards=False`) | **3★** (`play_event_cards=True`, 2 events) |
| `wagner` | 1,2 | 2★ | 2★ (no equipment played in these seeds) |
| `timaeus` | 1,2 | 1–2★ | 1–2★ (no equipment played in these seeds) |

The flip proves the flag changes the score. The other rows do not prove the agent failed;
they only prove the judge could not see. Since `timaeus`, `wagner` (equipment) and
`uncletian` (events) are the only stages with these objectives, **before the fix every
`skillloop.run` group was structurally capped at 13/16 three-star** (max 0.8125). **After the
fix the flag no longer caps the metric; the deck-based cap is 15/16**, with only `wagner`
unable to pass (`superconduct_aggro` has 0 weapons). The 0/20 equipment / 0/10 event numbers
in `reports/t5b_summary.md:128-134` are not behavioral evidence. The scripted and contestant
baselines used the same flag (`data/runs/t5_scripted.jsonl`, `t5_contestant.jsonl`), so the
comparison is internally consistent but the absolute rates are understated; under the old flag
those three stages could not respond to a skill-library change.

---

## 6. Implications / what is measurable next

1. **Fix the star metric before interpreting any skill gain.** **Done 2026-11-02**: `play_one`
   now passes `record_decisions=True`, with a positive-case regression test. The deck-based cap
   is 15/16 (`wagner` only). The old runs' equipment/event analysis remains invalid; re-run and
   re-score before comparing libraries.
2. **Fix the candidate window before adding more skills.** The current 16-slot prefix is
   order-biased: skills 0% dropped, cards 43%, tuning 75%, switch 71%, end 59%. Cheap fixes
   to A/B offline: put `declare_end` and `switch_active` first or reserve per-kind slots;
   collapse payment variants of the same subject; raise/remove the cap; sort by strategic
   value rather than engine order. The offline metric `expert_choice_outside_rendered`
   (27.0% now) gives a model-free target.
3. **Add round-level planning, or at least history.** One call per round (reroll + a short
   plan) with an executor would reduce ~26 action calls/game to ~5–6 and let the model
   reason about sequencing; alternatively include the previous action, remaining dice, and
   `is_fast` in the user message. This is the direct test of the user's hypothesis. Measure
   calls/game, candidate cap hits, and paired-seed stars.
4. **Decide the dice lever explicitly.** Either keep reroll expert-only and stop claiming
   the model learns dice planning, or delegate it with a compact mask representation and a
   dedupe key that includes `reroll_dice_mask`. Today even a wired reroll would show the
   model a single "keep all" option.
5. **Make retrieval actually per-decision or stop calling it that.** Add `stages` to evolved
   skill conditions, re-retrieve per round (or per decision) with board context, filter
   rules by objective, and measure self-skill hit (7/16 now) / citation usefulness. The
   current fixed 5+17 block is a static prior; its effect is measurable only as a
   between-library A/B, not as per-decision guidance.
6. **Persist per-decision rows.** At minimum: request type, `n_candidates`, chosen action
   code, expert `base_code`, `used`, `system_hash`, latency, and model name. That makes
   model-vs-expert disagreement, cap-hit rate, and per-decision skill citation auditable.

**Not measured here:** the actual LLM choices and disagreement rate (no per-decision rows);
the star impact of widening/reordering the candidate set (needs model runs); how many of the
three mis-scored stages would pass with correct recording (needs a re-run with recording on;
the controlled replay shows at least one flip); and whether the model reads or follows the
cited skills (only the per-game union is logged).
