# Star-gap analysis — where the three stars are actually lost

> **2026-11-02 更正**：`record_decisions=False` 的判定 bug 已修复；本文中所有「装备/事件目标恒为 false」的结论是**测量假象**，不是行为。受影响关卡的数值待重跑。
>
> **English:** the `record_decisions=False` judge bug is fixed; every "equipment/event is constant / failing in all groups" finding below is a **measurement artifact**, not behaviour. `wagner` remains impossible for this deck (0 weapons); `timaeus` and `uncletian` are reachable. All six 160-game runs below were scored with the broken judge, so their equipment/event rows and the headroom derived from them are pending a rerun.

Scope: six existing runs, **160 rows each** (16 stages × 10 seeds), deck `superconduct_aggro`,
engine lock `fa42fdfec70b`, hp table `0bbae5d8798a`. No engine, no LLM: this is a re-read of the
recorded `objectives` dicts, paired by `(stage, seed)`. Rows are `data/runs/*.jsonl`; the tables
below are reproducible with

```bash
PYTHONPATH=. python scripts/star_gap_analysis.py
```

Groups (library · seeds): `static_v1 · s0-9`, `dyn_v1 · s0-9`, `dyn_v3 · s0-9`,
`static_v1 · s10-19`, `dyn_v3 · s10-19`, `empty · s10-19`.

## 1. Headline

| group | 3-star | objective stars | win rate |
|---|---:|---:|---:|
| static_v1 · s0-9 | 0.3000 | 322/470 | 0.9437 |
| dyn_v1 · s0-9 | 0.4250 | 344/470 | 0.9437 |
| dyn_v3 · s0-9 | 0.4250 | 349/470 | 0.9500 |
| static_v1 · s10-19 | 0.3250 | 331/470 | 0.9750 |
| dyn_v3 · s10-19 | 0.3875 | 356/470 | 0.9812 |
| empty · s10-19 | 0.3688 | 346/470 | 0.9875 |

(470 = 160 × 3 objectives minus 10 `swan` rows that have only 2 objectives.)

Read the deltas against the right baseline: on seeds 10–19, `dyn_v3` is **+1.9pp vs the empty
library** (0.3875 vs 0.3688), and the +6.25pp static→dynamic figure is mostly recovering the
**−4.4pp deficit of `static_v1`** (0.3250 vs 0.3688). The skill library is close to neutral; it is
not adding 6pp of new capability. These are old-judge figures: `timaeus`/`uncletian` were scored as
constant failures, so the "neutral library" conclusion must be re-checked after the rerun.

## 2. Failures by objective — total (sole failure)

"Sole" = this is the only failed objective in that row. A type that is never sole can only matter
when something else is fixed first.

| objective | static s0-9 | dyn_v1 s0-9 | dyn_v3 s0-9 | static s10-19 | dyn_v3 s10-19 | empty s10-19 |
|---|---:|---:|---:|---:|---:|---:|
| `win` | 9 (0) | 9 (0) | 8 (0) | 4 (0) | 3 (0) | 2 (0) |
| `win_within_rounds` | 83 (51) | 66 (38) | 60 (36) | 82 (51) | 70 (54) | 77 (54) |
| `defeated_less_than` | 3 (0) | 2 (0) | 1 (0) | 6 (0) | 0 (0) | 2 (0) |
| `use_elemental_burst` | 2 (0) | 3 (0) | 4 (1) | 0 (0) | 0 (0) | 1 (0) |
| `trigger_elemental_reactions` | 21 (7) | 16 (8) | 18 (9) | 17 (7) | 11 (7) | 12 (0) |
| `play_equipment_card` † | 20 (17) | 20 (15) | 20 (13) | 20 (14) | 20 (13) | 20 (15) |
| `play_event_cards` † | 10 (5) | 10 (3) | 10 (9) | 10 (5) | 10 (8) | 10 (9) |

Two rows are *exactly constant* across all six 160-game groups: equipment 20 (12.5%) and events 10
(6.25%). **That constancy is the bug, not the behaviour.** All six runs were scored with
`record_decisions=False`, so `npc/objectives.py` never saw a card play and both objectives were false
by construction (see [`docs/failures/record-decisions-judge-blind.md`](../docs/failures/record-decisions-judge-blind.md)).
Corrected free replays (same seeds, no model) pass `timaeus` equipment in 2/10 scripted and 10/10
contestant games, and `uncletian` events in 8/10 and 10/10; `wagner` stays 0/10 because
`superconduct_aggro` has no weapon. Read the 20/20 and 10/10 rows above as *unobservable*, not as
failures. († marks the two unobservable rows in the tables below.) Wins are almost never the binding
constraint (2–9/160, never sole).

## 3. Headroom: 3-star rate if one objective were granted for free

Difference from the actual rate; this is the upper bound on what fixing that objective alone can buy.

| group | actual | win | wwr | deaths<2 | burst | reactions | equipment † | events † |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| static s0-9 | 0.3000 | +0.000 | **+0.319** | +0.000 | +0.000 | +0.044 | +0.106 | +0.031 |
| dyn_v1 s0-9 | 0.4250 | +0.000 | **+0.237** | +0.000 | +0.000 | +0.050 | +0.094 | +0.019 |
| dyn_v3 s0-9 | 0.4250 | +0.000 | **+0.225** | +0.000 | +0.006 | +0.056 | +0.081 | +0.056 |
| static s10-19 | 0.3250 | +0.000 | **+0.319** | +0.000 | +0.000 | +0.044 | +0.087 | +0.031 |
| dyn_v3 s10-19 | 0.3875 | +0.000 | **+0.337** | +0.000 | +0.000 | +0.044 | +0.081 | +0.050 |
| empty s10-19 | 0.3688 | +0.000 | **+0.338** | +0.000 | +0.000 | +0.000 | +0.094 | +0.056 |

`win` is worth exactly 0.000 in every group: every lost game also fails another objective, so winning
more can never convert a star here.

† **The equipment and events columns are invalid as headroom.** They were computed from the
constant-false rows above: the table grants a star for an objective the judge could not observe. The
structural upper bound is 10/160 = +0.0625 for equipment (`timaeus` only; `wagner` is 0 because the
deck has no weapon) and 10/160 = +0.0625 for events (`uncletian` only). The old +0.081…+0.106
(equipment) overstate what a policy can win; the old +0.019…+0.056 (events) are invalid too (they were
measured on unobservable failures), even though they happen to sit below the structural cap. The `wwr`
column and the `win` / `deaths<2` zeros are unaffected. Corrected LLM-group columns require a rerun.

## 4. `win_within_rounds`: how far past 5 the failures are

Threshold is **5 rounds** on all 16 stages (verified in `npc.spec`). Counts are failing games.

| group | wwr fails | 6 | 7 | 8 | 9 | 10+ | ≤6 share | won but slow |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| static s0-9 | 83 | 41 | 19 | 9 | 7 | 6 | 49% | 74 |
| dyn_v1 s0-9 | 66 | 32 | 19 | 4 | 1 | 9 | 48% | 57 |
| dyn_v3 s0-9 | 60 | 28 | 17 | 5 | 2 | 7 | 47% | 52 |
| static s10-19 | 82 | 42 | 23 | 6 | 6 | 5 | 51% | 78 |
| dyn_v3 s10-19 | 70 | 46 | 10 | 6 | 4 | 4 | 66% | 67 |
| empty s10-19 | 77 | 51 | 18 | 4 | 2 | 2 | 66% | 75 |

About half of the failures are at **exactly 6 rounds**, i.e. one round short; ~80% are within two
rounds. Games that are 10+ rounds over are a minority (2–9 per group) and concentrated in `blanche`.

A "perfect one-round speedup" applied only to games whose sole failure is wwr at round 6 would be
worth **+18.1pp** on static s10-19 (29 games) and **+23.7pp** on dyn_v3 s10-19 (38 games). The library
actually captured +6.25pp under the old judge, so the headroom is real but far from saturated.

## 5. `defeated_less_than`, equipment, events — distance to passing

- **`defeated_less_than` (k=2)**: 3/2/1/6/0/2 failures, **never sole**. In all six groups every failure
  is a *won* game in which ≥2 of our characters died; the holdout failures are at rounds 7,7,9,7,7,8,
  so they are also wwr failures. Fixing the clock would fix them; fixing them alone buys 0.000.
- **`play_equipment_card` (1 play)**: the old runs report 20 failures in every group = `wagner` 10 +
  `timaeus` 10, but that is the blind judge, not "0 of 1 plays". With decisions recorded, the same
  free replay passes the objective on `timaeus` in 2/10 scripted games (4 plays) and 10/10 contestant
  games; `wagner` remains 0/10 because the deck has no weapon. At most 10/160 games (all `timaeus`)
  can be rescued, i.e. +6.25pp — not the +8.1…+10.6pp in §3. The 13–17 "sole failures" counts are
  likewise an artifact; "second-largest block of dead stars" is withdrawn.
- **`play_event_cards` (2 plays)**: the 10 failures in every group are all `uncletian`, again constant
  by construction. With decisions recorded, the expert passes 8/10 (scripted) and 10/10 (contestant);
  the old 3–9 sole-failure counts and the "0–1 of 2 plays" distance are not behavioural. Structural
  headroom is 10/160 = +6.25pp.
- **`use_elemental_burst`**: 0–4 failures, essentially only on `irontonguetian`/`liusu`; never a
  standalone blocker except once. Not a lever.

The old JSONL rows store only the boolean per objective, not the judge's `detail` field, so the exact
distance (`defeated=`, `played=`) is not recoverable from these six files. The corrected distances
above come from free replays with `record_decisions=True`, not from these rows; the LLM-group
distances are still unknown until the rerun.

Deck check (why equipment is so hard): `superconduct_aggro` is 30 cards / 21 unique —
**17 events, 7 supports, 6 artifacts, 0 weapons**. `wagner` requires a *weapon* card, so that stage
**cannot be three-starred by this deck at all** (10/160 = 6.25pp permanently unavailable).
`timaeus` requires an *artifact*: the deck has 6, and an expert replay had an artifact legal in
154 request-instances over 10 seeds, but played one in only 2 of 10 games (4 plays, fixed judge).

For comparison, expert replays (not the recorded LLM rows) reach `uncletian`'s 2-event objective in
8/10 seeds and three-star it in 6/10; they three-star `timaeus` in 1/10 (scripted) and 4/10
(contestant). The LLM groups show **0/160** on both under the old judge, but that number says nothing
about behaviour because the judge never saw a card play. Whether `dyn_v3`'s cited skills change
equipment/event play is **unverified** and needs the rerun; what is known is that the stage-specific
skill is in the retrieved/cited set in 10/10 games on those stages.

## 6. Per-stage held-out gap (static_v1 → dyn_v3, seeds 10–19)

| stage | static 3★ | dyn 3★ | Δ | failure deltas | flips +/− |
|---|---:|---:|---:|---|---|
| blanche | 0/10 | 0/10 | 0 | — | +0/−0 |
| buyun | 3/10 | 3/10 | 0 | — | +2/−2 |
| chefmao | 3/10 | 2/10 | −1 | wwr 7→8, deaths<2 2→0 | +0/−1 |
| ellin | 3/10 | 5/10 | **+2** | wwr 7→5, deaths<2 1→0 | +3/−1 |
| irontonguetian | 5/10 | 4/10 | −1 | wwr 5→6 | +1/−2 |
| jifang | 10/10 | 10/10 | 0 | — | +0/−0 |
| lan | 4/10 | 4/10 | 0 | — | +1/−1 |
| liusu | 2/10 | 2/10 | 0 | — | +1/−1 |
| marjorie | 8/10 | 7/10 | −1 | wwr 2→3 | +2/−3 |
| patton | 1/10 | 5/10 | **+4** | wwr 5→2, reactions 7→3 | +5/−1 |
| shitou | 0/10 | 2/10 | **+2** | wwr 7→4, reactions 10→8 | +2/−0 |
| swan | 9/10 | 10/10 | +1 | win 1→0, wwr 1→0 | +1/−0 |
| timaeus | 0/10 | 0/10 | 0 | wwr 6→7 | +0/−0 |
| timmie | 4/10 | 8/10 | **+4** | wwr 6→2, deaths<2 3→0 | +5/−1 |
| uncletian | 0/10 | 0/10 | 0 | wwr 5→2 | +0/−0 |
| wagner | 0/10 | 0/10 | 0 | — | +0/−0 |

In these old-judge rows every stage delta is a **wwr** delta. Where a second objective also moves
(`ellin`, `timmie`, `patton`, `shitou`) it is `deaths<2` or `reactions` and co-occurs with the wwr
change. No stage's delta comes from `win`. The `timaeus` and `uncletian` zeros are not evidence that
the library failed — the judge could not see their card objectives; `wagner`'s zero is structural.
Corrected equipment/event deltas require the rerun.

## 7. The gap is two opposing flows at the 5/6 boundary

Paired flips (same stage+seed, static → dynamic):

- **train s0-9**: +29 / −9 (net +20). Games won by the library finish at rounds
  `{5:2, 6:20, 7:3, 8:2, 9:2}`; games it loses now finish at `{6:4, 7:4, 8:1}`.
- **holdout s10-19**: +23 / −13 (net +10). Wins finish at `{4:1, 5:1, 6:14, 7:4, 8:2, 9:1}`;
  losses now finish at `{5:1, 6:11, 7:1}`.

The full round transition matrix on the holdout is a diffusion around 5/6, not a shift:

| transition | count | | transition | count |
|---|---:|---|---|---:|
| 6 → 5 | 14 | | 5 → 6 | 13 |
| 7 → 5 | 6 | | 6 → 7 | 2 |
| 7 → 6 (still fails) | 11 | | 5 → 7 | 1 |
| 5 → 4 | 8 | | 6 → 8 | 3 |
| 6 → 4 | 3 | | 6 → 9 | 1 |
| 6 → 3 | 2 | | 6 → 10 | 1 |

So the library does buy rounds (7→5, 6→3, 14→8) and it also gives them back (5→6, 6→10). The
headline +6.25pp is the residual of two large flows, and the train/holdout difference (+20 → +10
net) is the loss side doubling (9 → 13), not the gain side collapsing (29 → 23).

## 8. Five findings

1. **Only `win_within_rounds` has enough headroom to move the headline.** Granting it for free is
   worth +22.5 to +33.8pp in every group. The equipment figure (+8.1 to +10.6pp) is withdrawn: it
   came from constant-false rows, and the structural cap is +6.25pp (`timaeus` only; `wagner` is
   deck-impossible). Events are likewise capped at +6.25pp (`uncletian` only); the old +1.9…+5.6pp
   figures are invalid, not a measured headroom. `win` is worth exactly 0.000pp because no game is
   lost *only* on the win objective.
2. **Half of all wwr failures are one round short (round 6), not four.** 41/83 (static s0-9),
   42/82 (static s10-19), 46/70 (dyn_v3 s10-19). The threshold sits on the densest part of the round
   distribution, so the same change flips games both ways.
3. **The library's effect is a symmetric boundary flow, and that is why the gain halves out of
   sample.** Train: +29/−9 (net +20). Holdout: +23/−13 (net +10). Holdout round transitions are
   14 × (6→5) against 13 × (5→6), plus 11 × (7→6) that improved but stayed one round short. The
   upside flow is stable; the downside flow is what grew. These are old-judge figures; the +6.25pp
   may move after the equipment/event correction.
4. **20/160 games (12.5%) were frozen at zero stars by the judge, not by play.** `timaeus` (artifact)
   and `uncletian` (2 events) showed 10/10 failures in every group because the judge never saw a card
   play; corrected free replays three-star them in 1/10 and 6/10 (scripted), 4/10 and 8/10
   (contestant). Of the original 40 frozen games, the remaining 20 split into `wagner` (10,
   structurally impossible: it needs a weapon and the deck has none) and `blanche` (10, a separate
   real gap — it finishes well above the 5-round cap, 6–14 rounds observed). The "remaining 120 games, +8.3pp" figure is
   stale: it excluded 40 frozen games, 20 of which are no longer frozen. Recompute after the rerun.
5. **Wins and deaths are already saturated or collateral.** Win rate 0.94–0.99, lost games 2–9/160,
   never sole. `defeated_less_than` is never sole and every one of its failures is a won game that is
   also too slow. So the agent cannot "play better" its way to a star: it must either shorten the
   game by a round on stages where everything else already passes, or actually play the card type the
   stage asks for. The library does the first only at the margin. Whether it does the second is
   **unverified**: the old runs could not observe card plays at all, so the free baseline shows the
   objective is reachable, not that the library helps. Measured against the empty library on the same
   holdout seeds, `dyn_v3` is only **+1.9pp** (0.3875 vs 0.3688) — an old-judge figure, to be
   recomputed after the rerun.
