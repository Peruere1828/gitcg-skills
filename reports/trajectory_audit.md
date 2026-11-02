# Trajectory audit — scripted player, no LLM

Generated: `PYTHONPATH=. python scripts/audit_trajectory.py --stages all --seeds 1,2 --deck superconduct_aggro --workers 6 --out reports/trajectory_audit.md --leverage --leverage-stages chefmao,ellin,irontonguetian,liusu,patton,shitou,timmie,timaeus --leverage-seeds 1`

Grid: 32 games (16 stages × [1, 2] seeds), deck `superconduct_aggro`

> **2026-11-02 附注**：本文的逐局记录使用 `record_decisions=True`，因此不受 `skillloop.run` 判定 bug 影响（见 [`docs/failures/record-decisions-judge-blind.md`](../docs/failures/record-decisions-judge-blind.md)）；文中的星星/目标数已与修复后的免费重放一致。

## 1. Decisions per game and per round, by request type

| stage | seed | winner | rounds | stars | decisions | per round | action | choose_active | reroll_dice | select_card | switch_hands |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| blanche | 1 | 1 | 11 | 1/3 | 73 | 6.6 | 57 | 3 | 11 | 1 | 1 |
| blanche | 2 | 0 | 10 | 2/3 | 60 | 6.0 | 46 | 2 | 10 | 1 | 1 |
| buyun | 1 | 0 | 4 | 3/3 | 32 | 8.0 | 25 | 1 | 4 | 1 | 1 |
| buyun | 2 | 0 | 3 | 3/3 | 18 | 6.0 | 12 | 1 | 3 | 1 | 1 |
| chefmao | 1 | 0 | 6 | 2/3 | 41 | 6.8 | 32 | 1 | 6 | 1 | 1 |
| chefmao | 2 | 0 | 6 | 2/3 | 40 | 6.7 | 31 | 1 | 6 | 1 | 1 |
| ellin | 1 | 0 | 8 | 2/3 | 54 | 6.8 | 43 | 1 | 8 | 1 | 1 |
| ellin | 2 | 0 | 7 | 2/3 | 49 | 7.0 | 39 | 2 | 7 | 0 | 1 |
| irontonguetian | 1 | 0 | 6 | 2/3 | 40 | 6.7 | 31 | 1 | 6 | 1 | 1 |
| irontonguetian | 2 | 0 | 6 | 2/3 | 38 | 6.3 | 29 | 1 | 6 | 1 | 1 |
| jifang | 1 | 0 | 3 | 3/3 | 18 | 6.0 | 12 | 1 | 3 | 1 | 1 |
| jifang | 2 | 0 | 3 | 3/3 | 17 | 5.7 | 11 | 1 | 3 | 1 | 1 |
| lan | 1 | 0 | 5 | 3/3 | 36 | 7.2 | 28 | 1 | 5 | 1 | 1 |
| lan | 2 | 0 | 4 | 3/3 | 26 | 6.5 | 20 | 1 | 4 | 0 | 1 |
| liusu | 1 | 0 | 6 | 2/3 | 41 | 6.8 | 32 | 1 | 6 | 1 | 1 |
| liusu | 2 | 0 | 6 | 2/3 | 40 | 6.7 | 31 | 1 | 6 | 1 | 1 |
| marjorie | 1 | 0 | 5 | 3/3 | 32 | 6.4 | 24 | 1 | 5 | 1 | 1 |
| marjorie | 2 | 0 | 4 | 3/3 | 25 | 6.2 | 18 | 1 | 4 | 1 | 1 |
| patton | 1 | 0 | 5 | 3/3 | 36 | 7.2 | 28 | 1 | 5 | 1 | 1 |
| patton | 2 | 0 | 6 | 2/3 | 38 | 6.3 | 29 | 1 | 6 | 1 | 1 |
| shitou | 1 | 0 | 5 | 2/3 | 40 | 8.0 | 31 | 2 | 5 | 1 | 1 |
| shitou | 2 | 0 | 5 | 3/3 | 34 | 6.8 | 25 | 2 | 5 | 1 | 1 |
| swan | 1 | 0 | 5 | 2/3 | 35 | 7.0 | 26 | 2 | 5 | 1 | 1 |
| swan | 2 | 0 | 5 | 2/3 | 29 | 5.8 | 21 | 1 | 5 | 1 | 1 |
| timaeus | 1 | 0 | 6 | 1/3 | 38 | 6.3 | 29 | 1 | 6 | 1 | 1 |
| timaeus | 2 | 0 | 5 | 2/3 | 29 | 5.8 | 21 | 1 | 5 | 1 | 1 |
| timmie | 1 | 0 | 5 | 3/3 | 33 | 6.6 | 25 | 1 | 5 | 1 | 1 |
| timmie | 2 | 0 | 5 | 3/3 | 31 | 6.2 | 23 | 1 | 5 | 1 | 1 |
| uncletian | 1 | 0 | 3 | 2/3 | 22 | 7.3 | 16 | 1 | 3 | 1 | 1 |
| uncletian | 2 | 0 | 4 | 3/3 | 25 | 6.2 | 18 | 1 | 4 | 1 | 1 |
| wagner | 1 | 0 | 2 | 2/3 | 14 | 7.0 | 10 | 1 | 2 | 0 | 1 |
| wagner | 2 | 0 | 2 | 2/3 | 14 | 7.0 | 10 | 1 | 2 | 0 | 1 |

**Total decisions/game**: median 34, range 14–73. **Per round**: median 6.7, range 5.7–8.0.

Global mix: `action` 833, `reroll_dice` 166, `choose_active` 39, `switch_hands` 32, `select_card` 28.

Median decisions per round by type (over games):

| request_type | median per game | median per round |
|---|---:|---:|
| `action` | 25.5 | 5.0 |
| `choose_active` | 1.0 | 0.2 |
| `reroll_dice` | 5.0 | 1.0 |
| `select_card` | 1.0 | 0.2 |
| `switch_hands` | 1.0 | 0.2 |

## 2. Candidate structure: legal list vs what the agent can pick

`n_legal` = engine list; `n_dedup` = `select_candidates` without the cap; `n_rendered` = what the LLM prompt would contain (16 cap, forced skipped).

| request_type | n | n_legal p50 / p90 / max | n_dedup p50 / p90 / max | n_rendered p50 / p90 / max | forced (n_legal<=1) | expert choice outside rendered |
|---|---:|---|---|---|---:|---:|
| `action` | 833 | 19 / 30 / 36 | 19 / 30 / 36 | 16 / 16 / 16 | 0 (0%) | 225 (27%) |
| `choose_active` | 39 | 3 / 3 / 3 | 3 / 3 / 3 | 3 / 3 / 3 | 1 (3%) | 1 (3%) |
| `reroll_dice` | 166 | 96 / 144 / 144 | 1 / 1 / 1 | 1 / 1 / 1 | 0 (0%) | 166 (100%) |
| `select_card` | 28 | 2 / 2 / 2 | 2 / 2 / 2 | 2 / 2 / 2 | 0 (0%) | 0 (0%) |
| `switch_hands` | 32 | 32 / 32 / 32 | 6 / 6 / 6 | 6 / 6 / 6 | 0 (0%) | 0 (0%) |

**Action requests**: forced (≤1 legal) 0/833 (0.0%); single deduped candidate 0 (0.0%); truncated by the 16 cap 489 (58.7%); expert chose outside the rendered set 225 (27.0%).

Dedupe/`kind` width of action requests:

| metric | p50 | p90 | max |
|---|---:|---:|---:|
| n_legal | 19 | 30 | 36 |
| n_dedup | 19 | 30 | 36 |
| n_subjects | 15 | 23 | 25 |
| n_kinds | 4 | 5 | 5 |

`n_subjects` = distinct (action type, subject card/skill) pairs, i.e. how many *different things* are on the table; `n_kinds` = distinct action types.

Action kinds present in the legal list but cut by the 16 cap: action_play_card=1613, action_elemental_tuning=1136, action_switch_active=882, action_declare_end=489 (option-instances across the grid).

## 3. Dice rerolls: who decides them

`reroll_dice` requests: 166 over 32 games (median 5/game, range 2–11). Legal masks per request: 96=66, 144=35, 128=25, 72=16, 108=12, 60=5, 36=3, 80=3, 48=1. That is one request per roll phase, not one per die.

Rendered candidates per reroll request: [(1, 166)] — the dedupe key in `select_candidates` is `(kind, subject_definition_id, target_slots, used_dice)` and does not include `reroll_dice_mask`, so all masks collapse into one. Distinct masks actually chosen per game: median 4, range 2–5.

**But the LLM never sees them**: `AgentConfig.request_types` is `("action", "switch_hands", "choose_active")`, so `reroll_dice` and `select_card` always go to the scripted expert. Dice rerolling is outside the skill layer by construction, not by accident.

## 4. One full round, request by request

Stage `ellin` seed 2 round 4 (game ended in round 7, winner 0). This is the densest round in
the grid (11 requests; median round = 6.7 requests), shown in full:

| step | round | request | n_legal | n_dedup | n_rendered | n_kinds | chosen kind |
|---:|---:|---|---:|---:|---:|---:|---|
| 24 | 4 | `reroll_dice` | 72 | 1 | 1 | 1 | `reroll_dice` |
| 25 | 4 | `action` | 22 | 22 | 16 | 5 | `action_use_skill` |
| 26 | 4 | `choose_active` | 2 | 2 | 2 | 1 | `choose_active` |
| 27 | 4 | `action` | 18 | 18 | 16 | 4 | `action_switch_active` |
| 28 | 4 | `action` | 20 | 20 | 16 | 5 | `action_switch_active` |
| 29 | 4 | `action` | 18 | 18 | 16 | 4 | `action_switch_active` |
| 30 | 4 | `action` | 11 | 11 | 11 | 3 | `action_switch_active` |
| 31 | 4 | `action` | 16 | 16 | 16 | 4 | `action_switch_active` |
| 32 | 4 | `action` | 4 | 4 | 4 | 2 | `action_play_card` |
| 33 | 4 | `action` | 8 | 8 | 8 | 3 | `action_switch_active` |
| 34 | 4 | `action` | 3 | 3 | 3 | 2 | `action_declare_end` |

Round picks: action_switch_active=6, action_declare_end=1, action_play_card=1, action_use_skill=1, choose_active=1, reroll_dice=1.

## 5. Sanity check: what the same scripted player scores

| stage | 3-star | mean rounds | objective failures |
|---|---:|---:|---|
| blanche | 0/2 | 10.5 | win_within_rounds=2, win=1 |
| buyun | 2/2 | 3.5 | — |
| chefmao | 0/2 | 6.0 | win_within_rounds=2 |
| ellin | 0/2 | 7.5 | win_within_rounds=2 |
| irontonguetian | 0/2 | 6.0 | win_within_rounds=2 |
| jifang | 2/2 | 3.0 | — |
| lan | 2/2 | 4.5 | — |
| liusu | 0/2 | 6.0 | win_within_rounds=2 |
| marjorie | 2/2 | 4.5 | — |
| patton | 1/2 | 5.5 | win_within_rounds=1 |
| shitou | 1/2 | 5.0 | trigger_elemental_reactions=1 |
| swan | 2/2 | 5.0 | — |
| timaeus | 0/2 | 5.5 | play_equipment_card=2, win_within_rounds=1 |
| timmie | 2/2 | 5.0 | — |
| uncletian | 1/2 | 3.5 | play_event_cards=1 |
| wagner | 0/2 | 2.0 | play_equipment_card=2 |

## 6. Counterfactual leverage: what one different visible action buys

Replayed 8 game(s), forcing one alternative at every `action` request (the first rendered candidate with a different type or subject than the expert's choice). Determinism check: 8/8 replays reproduced the original game byte-for-byte (same trace, rounds, winner). 251 single-decision deviations measured.

| slice | n | changes round count | changes winner | mean Δrounds | median |Δrounds| |
|---|---:|---:|---:|---:|---:|
| all deviations | 251 | 64 (25%) | 1 (0%) | +0.07 | 0.0 |
| same action type, different target/subject | 83 | 27 (33%) | 1 (1%) | +0.01 | 0.0 |
| different action type | 168 | 37 (22%) | 0 (0%) | +0.10 | 0.0 |

Δrounds distribution: 0=187, 1=37, -1=15, -2=6, 2=5, -3=1.

Per stage (deviations that change the round count): `chefmao` 4/32, `ellin` 13/43, `irontonguetian` 8/31, `liusu` 4/32, `patton` 5/28, `shitou` 21/31, `timaeus` 9/29, `timmie` 0/25.

## 7. Reading the hypothesis

*Decisions per game*: median 34 requests, of which median 26 are `action` (5.0 per round). There is no round-level object in the loop: one skill, one card, one switch or one end-declaration per request.

*Few real alternatives?* Contradicted at the raw level: 0/833 action requests are forced (≤1 legal), the median action request has 19 legal codes and 4 distinct action types. But the rendered window is capped at 16 and ordered by the engine: 59% of action requests are truncated, `declare_end` is dropped entirely in 59% of them, and the expert's own choice is outside the rendered window in 27% of action requests. The agent therefore has many *options* but not the expert's option set.

*Dice*: one `reroll_dice` request per roll phase (median 5/game), 36–144 legal masks (median 96), collapsed to 1 rendered candidate by `select_candidates`, and — decisively — never sent to the LLM at all (`AgentConfig.request_types` excludes `reroll_dice`). The library cannot touch the dice; the expert does.

*Can one micro-decision change the round?* Yes, in 64/251 (25%) single-decision replays — but the effect is asymmetric: 42 deviations made the game **slower** and only 22 made it faster (mean Δ +0.07 rounds). The winner changed in 1/251 replays. So the chain is sensitive on the clock metric, but local perturbation of the expert has no positive expected value: the scripted policy is at a local optimum for speed, and the skill library's effect is a diffusion around the threshold, not a shift.

**Verdict.** The hypothesis is right about the *shape* and wrong about the *mechanism*. The agent does decide at every micro-step, and the dice decision is entirely outside the library. But action decisions are not forced: they have 15–30 legal options and 4–5 action types. What actually blocks the library is (a) a 16-item window that hides `declare_end` most of the time and hides the expert's own choice 27% of the time, (b) a renderer that labels every card play as `打出卡牌：<name>` with no artifact/weapon/event kind, so a card-type skill can only be obeyed if the model recognises the card by name — it did not, in 160 games, and (c) a 5-round threshold sitting on the mode of the round distribution, where coordinated changes move games both ways and cancel out. The first two explain the frozen objectives; the third explains why the wwr gain halves out of sample.

