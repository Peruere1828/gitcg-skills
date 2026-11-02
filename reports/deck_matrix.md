# Deck strength matrix and fixed per-stage deck choices

Scripted expert vs NPC sweep used to pick **one fixed deck per stage** for every experiment group. No LLM/network calls; fully reproducible.

- Generated: 2026-11-02 07:32
- Screen: 16 stages x 20 decks x 5 seeds = 1600 games (seeds 0, 1, 2, 3, 4); 1600 valid screen rows (errors/truncated: 0)
- Shortlist: 3 decks/stage x 10 seeds = 480 shortlist cell-games (240 new games after the screen; total rows 1840)
- Seeds on disk: 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
- Valid screen games with no winner (draw/None): 128 (rounds: {15: 128}); counted as non-wins
  **Seed-0 artifact**: seed 0 has 121/320 draws (38%) vs 7/1280 (1%) on the other screen seeds; the shortlist seeds 5-9 have 0/240 draws. All are the engine's 15-round cap. Seed 0 makes the 5-seed screen pessimistic relative to a clean seed set; see the seed-0 sensitivity section.
- Policy: `expert:<deck>` with policy seed 0 (same as `skillloop.run --group scripted`); NPC = `npc.policy.NpcPolicy` greedy, no rotation
- Judging flags: `record_decisions=True`, `record_events=True`, `record_final_state=True` — equipment/event objectives (wagner, timaeus, uncletian) need the decision trace; `record_decisions=False` silently fails them
- Ranking: **three-star rate** first, then win rate, mean stars, mean rounds (fewer is better), then deck name
- Engine lock `fa42fdfec70b`, HP table `0bbae5d8798a`, stage JSON `da3ad153e409`, assets `6b13d08f6419`
- Wall time: screen 1600 games in 852.2 s; shortlist 240 new games in 105.1 s; total 957.3 s (8 workers)
- Best deck overall (5-seed screen): **electrocharged_moon** — 3★ 55/80 (69%), win 78/80 (98%)
- Worst deck overall (5-seed screen): **skirk_ayaka_navia** — 3★ 6/80 (8%), win 66/80 (82%)

The final table uses the 10-seed shortlist extension; the screen/top-5 table uses the 5-seed screen so all 20 decks are compared on the same seeds.

## 1. Fixed deck per stage (final recommendation)

Shortlist = top 3 decks from the 5-seed screen among decks that satisfy the stage's card-type objective. The final pick re-ranks that shortlist on all 10 seeds. Counts are `achieved/games`; the margin is the three-star rate gap to the runner-up.

| stage | recommended deck | win rate | three-star rate | satisfies card-type objective? | runner-up deck | margin (3★) |
|---|---|---|---|---|---|---|
| blanche | **electro_manifestation_nuke** | 10/10 (100%) | 5/10 (50%) | n/a (no card objective) | electrocharged_moon | 0pp (5/10 (50%) vs 5/10 (50%)) |
| buyun | **mualani_double_pyro** | 10/10 (100%) | 8/10 (80%) | n/a (no card objective) | superconduct_aggro | 0pp (8/10 (80%) vs 8/10 (80%)) |
| chefmao | **electrocharged_moon** | 10/10 (100%) | 9/10 (90%) | n/a (no card objective) | mualani_double_pyro | 10pp (9/10 (90%) vs 8/10 (80%)) |
| ellin | **shenhe_double_swirl** | 10/10 (100%) | 9/10 (90%) | n/a (no card objective) | aywolfskirk | 0pp (9/10 (90%) vs 9/10 (90%)) |
| irontonguetian | **mualani_double_pyro** | 9/10 (90%) | 8/10 (80%) | n/a (no card objective) | dual_mualani_stacks | 30pp (8/10 (80%) vs 5/10 (50%)) |
| jifang | **electro_manifestation_nuke** | 10/10 (100%) | 10/10 (100%) | n/a (no card objective) | superconduct_aggro | 0pp (10/10 (100%) vs 10/10 (100%)) |
| lan | **electro_manifestation_nuke** | 10/10 (100%) | 10/10 (100%) | n/a (no card objective) | electrocharged_moon | 20pp (10/10 (100%) vs 8/10 (80%)) |
| liusu | **mualani_double_pyro** | 9/10 (90%) | 7/10 (70%) | n/a (no card objective) | dual_mualani_stacks | 10pp (7/10 (70%) vs 6/10 (60%)) |
| marjorie | **electrocharged_moon** | 10/10 (100%) | 9/10 (90%) | n/a (no card objective) | shenhe_double_swirl | 10pp (9/10 (90%) vs 8/10 (80%)) |
| patton | **electro_manifestation_nuke** | 10/10 (100%) | 10/10 (100%) | n/a (no card objective) | superconduct_aggro | 10pp (10/10 (100%) vs 9/10 (90%)) |
| shitou | **electro_manifestation_nuke** | 10/10 (100%) | 7/10 (70%) | n/a (no card objective) | electrocharged_moon | 0pp (7/10 (70%) vs 7/10 (70%)) |
| swan | **electrocharged_moon** | 10/10 (100%) | 10/10 (100%) | n/a (no card objective) | electro_manifestation_nuke | 0pp (10/10 (100%) vs 10/10 (100%)) |
| timaeus | **electro_manifestation_nuke** | 10/10 (100%) | 6/10 (60%) | yes (artifact 4/1) | electrocharged_moon | 10pp (6/10 (60%) vs 5/10 (50%)) |
| timmie | **electro_manifestation_nuke** | 10/10 (100%) | 9/10 (90%) | n/a (no card objective) | shenhe_double_swirl | 0pp (9/10 (90%) vs 9/10 (90%)) |
| uncletian | **dvalin_bonk** | 10/10 (100%) | 10/10 (100%) | yes (event 19/2) | electro_manifestation_nuke | 20pp (10/10 (100%) vs 8/10 (80%)) |
| wagner | **superconduct_mika_attack** | 10/10 (100%) | 9/10 (90%) | yes (weapon 6/1) | nahida_kokomi_raiden | 30pp (9/10 (90%) vs 6/10 (60%)) |

A `0pp` margin means the top two had the same three-star count; win rate, mean stars, mean rounds and name decided the pick. Read the counts, not just the rounded percentages.

### Coin-flip picks (3★ and win rate both tied)

These picks are not separated by the two primary metrics. Mean stars, mean rounds or the deck name decided them; treat them as arbitrary between the two rows.

| stage | pick | runner-up |
|---|---|---|
| ellin | shenhe_double_swirl (3★ 9/10 (90%), win 10/10 (100%), stars 2.90, rounds 3.8) | aywolfskirk (3★ 9/10 (90%), win 10/10 (100%), stars 2.90, rounds 4.9) |
| jifang | electro_manifestation_nuke (3★ 10/10 (100%), win 10/10 (100%), stars 3.00, rounds 2.4) | superconduct_aggro (3★ 10/10 (100%), win 10/10 (100%), stars 3.00, rounds 3.3) |
| swan | electrocharged_moon (3★ 10/10 (100%), win 10/10 (100%), stars 2.00, rounds 3.6) | electro_manifestation_nuke (3★ 10/10 (100%), win 10/10 (100%), stars 2.00, rounds 4.0) |
| timmie | electro_manifestation_nuke (3★ 9/10 (90%), win 10/10 (100%), stars 2.90, rounds 4.1) | shenhe_double_swirl (3★ 9/10 (90%), win 10/10 (100%), stars 2.90, rounds 4.3) |

### Shortlist ranking (all three measured decks, 10 seeds)

The final pick is the first cell; the other two are the measured alternatives.

| stage | #1 | #2 | #3 |
|---|---|---|---|
| blanche | electro_manifestation_nuke (3★ 5/10 (50%), win 10/10 (100%)) | electrocharged_moon (3★ 5/10 (50%), win 9/10 (90%)) | dual_mualani_stacks (3★ 4/10 (40%), win 9/10 (90%)) |
| buyun | mualani_double_pyro (3★ 8/10 (80%), win 10/10 (100%)) | superconduct_aggro (3★ 8/10 (80%), win 9/10 (90%)) | electrocharged_moon (3★ 7/10 (70%), win 10/10 (100%)) |
| chefmao | electrocharged_moon (3★ 9/10 (90%), win 10/10 (100%)) | mualani_double_pyro (3★ 8/10 (80%), win 10/10 (100%)) | dual_mualani_stacks (3★ 7/10 (70%), win 9/10 (90%)) |
| ellin | shenhe_double_swirl (3★ 9/10 (90%), win 10/10 (100%)) | aywolfskirk (3★ 9/10 (90%), win 10/10 (100%)) | double_geo_navia (3★ 7/10 (70%), win 10/10 (100%)) |
| irontonguetian | mualani_double_pyro (3★ 8/10 (80%), win 9/10 (90%)) | dual_mualani_stacks (3★ 5/10 (50%), win 9/10 (90%)) | trial_skirk_ayaka_navia (3★ 5/10 (50%), win 9/10 (90%)) |
| jifang | electro_manifestation_nuke (3★ 10/10 (100%), win 10/10 (100%)) | superconduct_aggro (3★ 10/10 (100%), win 10/10 (100%)) | superconduct_mika_attack (3★ 10/10 (100%), win 10/10 (100%)) |
| lan | electro_manifestation_nuke (3★ 10/10 (100%), win 10/10 (100%)) | electrocharged_moon (3★ 8/10 (80%), win 10/10 (100%)) | double_geo_navia (3★ 8/10 (80%), win 10/10 (100%)) |
| liusu | mualani_double_pyro (3★ 7/10 (70%), win 9/10 (90%)) | dual_mualani_stacks (3★ 6/10 (60%), win 9/10 (90%)) | electrocharged_moon (3★ 5/10 (50%), win 10/10 (100%)) |
| marjorie | electrocharged_moon (3★ 9/10 (90%), win 10/10 (100%)) | shenhe_double_swirl (3★ 8/10 (80%), win 10/10 (100%)) | electro_manifestation_nuke (3★ 8/10 (80%), win 10/10 (100%)) |
| patton | electro_manifestation_nuke (3★ 10/10 (100%), win 10/10 (100%)) | superconduct_aggro (3★ 9/10 (90%), win 10/10 (100%)) | shenhe_double_swirl (3★ 8/10 (80%), win 10/10 (100%)) |
| shitou | electro_manifestation_nuke (3★ 7/10 (70%), win 10/10 (100%)) | electrocharged_moon (3★ 7/10 (70%), win 9/10 (90%)) | shenhe_double_swirl (3★ 5/10 (50%), win 10/10 (100%)) |
| swan | electrocharged_moon (3★ 10/10 (100%), win 10/10 (100%)) | electro_manifestation_nuke (3★ 10/10 (100%), win 10/10 (100%)) | shenhe_double_swirl (3★ 10/10 (100%), win 10/10 (100%)) |
| timaeus | electro_manifestation_nuke (3★ 6/10 (60%), win 10/10 (100%)) | electrocharged_moon (3★ 5/10 (50%), win 10/10 (100%)) | trial_skirk_ayaka_navia (3★ 1/10 (10%), win 9/10 (90%)) |
| timmie | electro_manifestation_nuke (3★ 9/10 (90%), win 10/10 (100%)) | shenhe_double_swirl (3★ 9/10 (90%), win 10/10 (100%)) | superconduct_mika_attack (3★ 9/10 (90%), win 10/10 (100%)) |
| uncletian | dvalin_bonk (3★ 10/10 (100%), win 10/10 (100%)) | electro_manifestation_nuke (3★ 8/10 (80%), win 10/10 (100%)) | trial_skirk_ayaka_navia (3★ 8/10 (80%), win 9/10 (90%)) |
| wagner | superconduct_mika_attack (3★ 9/10 (90%), win 10/10 (100%)) | nahida_kokomi_raiden (3★ 6/10 (60%), win 10/10 (100%)) | electrocharged_moon (3★ 4/10 (40%), win 10/10 (100%)) |

### Wagner: cost of requiring a weapon card

| role | deck | 3★ | win rate | sample |
|---|---|---|---|---|
| best overall | superconduct_mika_attack | 9/10 (90%) | 10/10 (100%) | 10-seed shortlist |
| best weapon-carrying (recommended) | superconduct_mika_attack | 9/10 (90%) | 10/10 (100%) | 10-seed shortlist |

The best overall deck is already a weapon deck, so satisfying the wagner objective costs **0pp** on both metrics.

**All card-type stages:**

| stage | requirement | best overall (5-seed) | satisfies? | final pick | win-rate cost |
|---|---|---|---|---|---|
| timaeus | artifact x1 | electro_manifestation_nuke (4/5 (80%) 3★, 5/5 (100%) win) | yes | electro_manifestation_nuke (6/10 (60%) 3★, 10/10 (100%) win) | 0pp |
| uncletian | event x2 | dvalin_bonk (5/5 (100%) 3★, 5/5 (100%) win) | yes | dvalin_bonk (10/10 (100%) 3★, 10/10 (100%) win) | 0pp |
| wagner | weapon x1 | superconduct_mika_attack (4/5 (80%) 3★, 5/5 (100%) win) | yes | superconduct_mika_attack (9/10 (90%) 3★, 10/10 (100%) win) | 0pp |

No card-type stage has a win-rate/constraint conflict in this sweep: the unconstrained best deck already satisfies the requirement on all three stages, so the constraint costs 0pp. The samples differ (5 vs 10 seeds), so read the counts.

The top-5 table shows why composition alone is not enough. On wagner, non-weapon decks win 5/5 but score 0/5 3★ because the weapon objective is unreachable; the shortlist's weapon decks score 2-4/5 on the screen. On timaeus, two artifact-carrying decks also score 0/5 3★ (the policy does not play the artifact), so the shortlist is ranked by realized 3★, not just by carrying the card type.

## 2. 5-seed vs 10-seed shortlist: did the pick change?

| stage | 5-seed pick | 5-seed stats | same pick on 10 seeds | 10-seed pick | 10-seed stats | why |
|---|---|---|---|---|---|---|
| blanche | dual_mualani_stacks | 3★ 3/5 (60%), win 4/5 (80%) | 3★ 4/10 (40%), win 9/10 (90%) | electro_manifestation_nuke | 3★ 5/10 (50%), win 10/10 (100%) | 10-seed 3★ 5/10 (50%) vs 4/10 (40%) |
| buyun | electrocharged_moon | 3★ 4/5 (80%), win 5/5 (100%) | 3★ 7/10 (70%), win 10/10 (100%) | mualani_double_pyro | 3★ 8/10 (80%), win 10/10 (100%) | 10-seed 3★ 8/10 (80%) vs 7/10 (70%) |
| chefmao | mualani_double_pyro | 3★ 4/5 (80%), win 5/5 (100%) | 3★ 8/10 (80%), win 10/10 (100%) | electrocharged_moon | 3★ 9/10 (90%), win 10/10 (100%) | 10-seed 3★ 9/10 (90%) vs 8/10 (80%) |
| ellin | aywolfskirk | 3★ 5/5 (100%), win 5/5 (100%) | 3★ 9/10 (90%), win 10/10 (100%) | shenhe_double_swirl | 3★ 9/10 (90%), win 10/10 (100%) | 3★ tied on 10 seeds; tie-break decided |
| liusu | electrocharged_moon | 3★ 3/5 (60%), win 5/5 (100%) | 3★ 5/10 (50%), win 10/10 (100%) | mualani_double_pyro | 3★ 7/10 (70%), win 9/10 (90%) | 10-seed 3★ 7/10 (70%) vs 5/10 (50%) |

These are the stages where the 5-seed pick is not the 10-seed pick. The 10-seed pick is the one used in section 1. Note that the screen contains the pathological seed 0 while the extension adds clean seeds 5-9, so part of the shift is seed composition, not only sample size.

### Seed-0 sensitivity (re-rank without seed 0)

Excluding seed 0 changes the pick on these stages. The published picks keep seeds 0-9 (the benchmark's seed set); this table flags where the choice is sensitive to the seed-0 15-round artifact.

| stage | 0-9 pick | 0-9 3★ | same pick, no seed 0 | no-seed-0 pick | no-seed-0 3★ |
|---|---|---|---|---|---|
| blanche | electro_manifestation_nuke | 5/10 (50%) | 5/9 (56%) | electrocharged_moon | 5/9 (56%) |
| buyun | mualani_double_pyro | 8/10 (80%) | 8/9 (89%) | superconduct_aggro | 8/9 (89%) |
| shitou | electro_manifestation_nuke | 7/10 (70%) | 6/9 (67%) | electrocharged_moon | 7/9 (78%) |

## 3. Per-stage top 5 (5-seed screen)

Top 5 by the ranking above on the screen seeds. `shortlisted` marks the top-3 decks that were extended to 10 seeds; `meets req` is the stage's card-type constraint.

| stage | rank | deck | three-star | win | mean stars | mean rounds | meets req | shortlisted |
|---|---|---|---|---|---|---|---|---|
| blanche | 1 | dual_mualani_stacks | 3/5 (60%) | 4/5 (80%) | 2.20 | 7.6 | n/a | yes |
| blanche | 2 | electro_manifestation_nuke | 2/5 (40%) | 5/5 (100%) | 2.00 | 5.6 | n/a | yes |
| blanche | 3 | electrocharged_moon | 2/5 (40%) | 4/5 (80%) | 2.00 | 6.6 | n/a | yes |
| blanche | 4 | yelan_clorinde_ororon | 1/5 (20%) | 4/5 (80%) | 1.80 | 8.0 | n/a | — |
| blanche | 5 | klee_mavuika_mualani | 1/5 (20%) | 4/5 (80%) | 1.80 | 8.2 | n/a | — |
| buyun | 1 | electrocharged_moon | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.8 | n/a | yes |
| buyun | 2 | mualani_double_pyro | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.2 | n/a | yes |
| buyun | 3 | superconduct_aggro | 4/5 (80%) | 4/5 (80%) | 2.60 | 4.2 | n/a | yes |
| buyun | 4 | dvalin_bonk | 4/5 (80%) | 4/5 (80%) | 2.60 | 4.4 | n/a | — |
| buyun | 5 | double_geo_navia | 4/5 (80%) | 4/5 (80%) | 2.40 | 4.6 | n/a | — |
| chefmao | 1 | mualani_double_pyro | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.0 | n/a | yes |
| chefmao | 2 | electrocharged_moon | 4/5 (80%) | 5/5 (100%) | 2.80 | 6.2 | n/a | yes |
| chefmao | 3 | dual_mualani_stacks | 4/5 (80%) | 4/5 (80%) | 2.40 | 5.2 | n/a | yes |
| chefmao | 4 | trial_skirk_ayaka_navia | 4/5 (80%) | 4/5 (80%) | 2.40 | 6.6 | n/a | — |
| chefmao | 5 | shenhe_double_swirl | 3/5 (60%) | 5/5 (100%) | 2.60 | 5.0 | n/a | — |
| ellin | 1 | aywolfskirk | 5/5 (100%) | 5/5 (100%) | 3.00 | 4.8 | n/a | yes |
| ellin | 2 | shenhe_double_swirl | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.0 | n/a | yes |
| ellin | 3 | double_geo_navia | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.8 | n/a | yes |
| ellin | 4 | electrocharged_moon | 4/5 (80%) | 5/5 (100%) | 2.80 | 6.0 | n/a | — |
| ellin | 5 | klee_mavuika_mualani | 4/5 (80%) | 4/5 (80%) | 2.40 | 6.2 | n/a | — |
| irontonguetian | 1 | mualani_double_pyro | 4/5 (80%) | 4/5 (80%) | 2.60 | 4.8 | n/a | yes |
| irontonguetian | 2 | dual_mualani_stacks | 4/5 (80%) | 4/5 (80%) | 2.60 | 5.2 | n/a | yes |
| irontonguetian | 3 | trial_skirk_ayaka_navia | 4/5 (80%) | 4/5 (80%) | 2.40 | 6.8 | n/a | yes |
| irontonguetian | 4 | shenhe_double_swirl | 3/5 (60%) | 5/5 (100%) | 2.60 | 5.0 | n/a | — |
| irontonguetian | 5 | electrocharged_moon | 3/5 (60%) | 5/5 (100%) | 2.60 | 6.2 | n/a | — |
| jifang | 1 | electro_manifestation_nuke | 5/5 (100%) | 5/5 (100%) | 3.00 | 2.4 | n/a | yes |
| jifang | 2 | superconduct_aggro | 5/5 (100%) | 5/5 (100%) | 3.00 | 3.4 | n/a | yes |
| jifang | 3 | superconduct_mika_attack | 5/5 (100%) | 5/5 (100%) | 3.00 | 5.0 | n/a | yes |
| jifang | 4 | shenhe_double_swirl | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.2 | n/a | — |
| jifang | 5 | double_geo_navia | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.6 | n/a | — |
| lan | 1 | electro_manifestation_nuke | 5/5 (100%) | 5/5 (100%) | 3.00 | 4.0 | n/a | yes |
| lan | 2 | electrocharged_moon | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.2 | n/a | yes |
| lan | 3 | double_geo_navia | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.2 | n/a | yes |
| lan | 4 | superconduct_aggro | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.2 | n/a | — |
| lan | 5 | trial_skirk_ayaka_navia | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.2 | n/a | — |
| liusu | 1 | electrocharged_moon | 3/5 (60%) | 5/5 (100%) | 2.60 | 6.2 | n/a | yes |
| liusu | 2 | dual_mualani_stacks | 3/5 (60%) | 4/5 (80%) | 2.20 | 6.6 | n/a | yes |
| liusu | 3 | mualani_double_pyro | 3/5 (60%) | 4/5 (80%) | 2.20 | 6.6 | n/a | yes |
| liusu | 4 | trial_skirk_ayaka_navia | 3/5 (60%) | 4/5 (80%) | 2.20 | 6.8 | n/a | — |
| liusu | 5 | shenhe_double_swirl | 2/5 (40%) | 5/5 (100%) | 2.40 | 5.0 | n/a | — |
| marjorie | 1 | electrocharged_moon | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.4 | n/a | yes |
| marjorie | 2 | shenhe_double_swirl | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.6 | n/a | yes |
| marjorie | 3 | electro_manifestation_nuke | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.0 | n/a | yes |
| marjorie | 4 | dvalin_bonk | 4/5 (80%) | 5/5 (100%) | 2.60 | 6.2 | n/a | — |
| marjorie | 5 | yelan_clorinde_ororon | 4/5 (80%) | 4/5 (80%) | 2.40 | 6.2 | n/a | — |
| patton | 1 | electro_manifestation_nuke | 5/5 (100%) | 5/5 (100%) | 3.00 | 4.4 | n/a | yes |
| patton | 2 | superconduct_aggro | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.0 | n/a | yes |
| patton | 3 | shenhe_double_swirl | 4/5 (80%) | 5/5 (100%) | 2.60 | 4.6 | n/a | yes |
| patton | 4 | dvalin_bonk | 4/5 (80%) | 5/5 (100%) | 2.60 | 5.0 | n/a | — |
| patton | 5 | double_geo_navia | 4/5 (80%) | 5/5 (100%) | 2.60 | 5.2 | n/a | — |
| shitou | 1 | electro_manifestation_nuke | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.0 | n/a | yes |
| shitou | 2 | electrocharged_moon | 4/5 (80%) | 4/5 (80%) | 2.40 | 6.4 | n/a | yes |
| shitou | 3 | shenhe_double_swirl | 3/5 (60%) | 5/5 (100%) | 2.40 | 5.0 | n/a | yes |
| shitou | 4 | dual_mualani_stacks | 3/5 (60%) | 4/5 (80%) | 2.20 | 7.2 | n/a | — |
| shitou | 5 | klee_mavuika_mualani | 3/5 (60%) | 4/5 (80%) | 2.00 | 7.2 | n/a | — |
| swan | 1 | electrocharged_moon | 5/5 (100%) | 5/5 (100%) | 2.00 | 3.6 | n/a | yes |
| swan | 2 | electro_manifestation_nuke | 5/5 (100%) | 5/5 (100%) | 2.00 | 3.8 | n/a | yes |
| swan | 3 | shenhe_double_swirl | 5/5 (100%) | 5/5 (100%) | 2.00 | 4.0 | n/a | yes |
| swan | 4 | superconduct_aggro | 5/5 (100%) | 5/5 (100%) | 2.00 | 4.6 | n/a | — |
| swan | 5 | superconduct_mika_attack | 5/5 (100%) | 5/5 (100%) | 2.00 | 4.6 | n/a | — |
| timaeus | 1 | electro_manifestation_nuke | 4/5 (80%) | 5/5 (100%) | 2.80 | 3.8 | yes | yes |
| timaeus | 2 | electrocharged_moon | 3/5 (60%) | 5/5 (100%) | 2.60 | 5.6 | yes | yes |
| timaeus | 3 | trial_skirk_ayaka_navia | 1/5 (20%) | 4/5 (80%) | 1.60 | 6.4 | yes | yes |
| timaeus | 4 | aywolfskirk | 0/5 (0%) | 5/5 (100%) | 2.00 | 8.6 | yes | — |
| timaeus | 5 | superconduct_aggro | 0/5 (0%) | 5/5 (100%) | 1.80 | 5.0 | yes | — |
| timmie | 1 | electro_manifestation_nuke | 5/5 (100%) | 5/5 (100%) | 3.00 | 3.8 | n/a | yes |
| timmie | 2 | shenhe_double_swirl | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.0 | n/a | yes |
| timmie | 3 | superconduct_mika_attack | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.6 | n/a | yes |
| timmie | 4 | double_geo_navia | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.8 | n/a | — |
| timmie | 5 | dvalin_bonk | 4/5 (80%) | 5/5 (100%) | 2.80 | 4.8 | n/a | — |
| uncletian | 1 | dvalin_bonk | 5/5 (100%) | 5/5 (100%) | 3.00 | 4.4 | yes | yes |
| uncletian | 2 | trial_skirk_ayaka_navia | 4/5 (80%) | 4/5 (80%) | 2.60 | 4.2 | yes | yes |
| uncletian | 3 | electro_manifestation_nuke | 3/5 (60%) | 5/5 (100%) | 2.60 | 4.0 | yes | yes |
| uncletian | 4 | skirk_chasca_freeze | 3/5 (60%) | 5/5 (100%) | 2.60 | 5.0 | yes | — |
| uncletian | 5 | electrocharged_moon | 3/5 (60%) | 5/5 (100%) | 2.60 | 6.0 | yes | — |
| wagner | 1 | superconduct_mika_attack | 4/5 (80%) | 5/5 (100%) | 2.80 | 5.0 | yes | yes |
| wagner | 2 | nahida_kokomi_raiden | 3/5 (60%) | 5/5 (100%) | 2.60 | 5.2 | yes | yes |
| wagner | 3 | electrocharged_moon | 2/5 (40%) | 5/5 (100%) | 2.20 | 5.4 | yes | yes |
| wagner | 4 | electro_manifestation_nuke | 0/5 (0%) | 5/5 (100%) | 2.00 | 2.2 | no | — |
| wagner | 5 | superconduct_aggro | 0/5 (0%) | 5/5 (100%) | 2.00 | 2.2 | no | — |

The full 16 x 20 grid (every deck on every stage, all 5 screen seeds) is in `data/runs/deck_matrix.jsonl`; this table keeps the top 5 per stage for readability. Shortlisted cells have 10 seeds in the JSONL.

## 4. Overall deck ranking (5-seed screen)

Pooled over all 16 stages x 5 screen seeds for every deck, so the comparison is fair despite the shortlist extension.

| rank | deck | 3★ | win | mean stars | mean rounds | mean stage 3★ |
|---|---|---|---|---|---|---|
| 1 | electrocharged_moon | 55/80 (69%) | 78/80 (98%) | 2.56 | 5.5 | 69% |
| 2 | electro_manifestation_nuke | 53/80 (66%) | 78/80 (98%) | 2.48 | 4.6 | 66% |
| 3 | trial_skirk_ayaka_navia | 45/80 (56%) | 65/80 (81%) | 2.12 | 6.2 | 56% |
| 4 | shenhe_double_swirl | 44/80 (55%) | 78/80 (98%) | 2.42 | 4.8 | 55% |
| 5 | dvalin_bonk | 41/80 (51%) | 75/80 (94%) | 2.20 | 5.4 | 51% |
| 6 | dual_mualani_stacks | 39/80 (49%) | 67/80 (84%) | 2.09 | 6.5 | 49% |
| 7 | double_geo_navia | 36/80 (45%) | 75/80 (94%) | 2.20 | 5.7 | 45% |
| 8 | superconduct_aggro | 35/80 (44%) | 74/80 (92%) | 2.19 | 5.3 | 44% |
| 9 | mualani_double_pyro | 34/80 (42%) | 65/80 (81%) | 1.99 | 6.8 | 42% |
| 10 | yelan_clorinde_ororon | 31/80 (39%) | 64/80 (80%) | 1.93 | 6.8 | 39% |
| 11 | klee_mavuika_mualani | 29/80 (36%) | 58/80 (72%) | 1.75 | 6.7 | 36% |
| 12 | nahida_kokomi_raiden | 28/80 (35%) | 71/80 (89%) | 2.08 | 6.7 | 35% |
| 13 | aywolfskirk | 27/80 (34%) | 78/80 (98%) | 2.16 | 6.2 | 34% |
| 14 | superconduct_mika_attack | 27/80 (34%) | 76/80 (95%) | 2.19 | 6.1 | 34% |
| 15 | unyielding_geo | 24/80 (30%) | 67/80 (84%) | 1.84 | 7.5 | 30% |
| 16 | ice_water_battleship | 24/80 (30%) | 61/80 (76%) | 1.79 | 7.2 | 30% |
| 17 | skirk_chasca_freeze | 19/80 (24%) | 65/80 (81%) | 1.79 | 7.2 | 24% |
| 18 | beidou_skirk_ayaka | 14/80 (18%) | 48/80 (60%) | 1.38 | 8.5 | 18% |
| 19 | natlan_battleship | 11/80 (14%) | 58/80 (72%) | 1.56 | 9.1 | 14% |
| 20 | skirk_ayaka_navia | 6/80 (8%) | 66/80 (82%) | 1.68 | 8.0 | 8% |

## 5. Dominated decks and `superconduct_aggro`

- Never won a screen game: none.
- Never three-starred in the screen: none.
- Dominated (neither won nor three-starred): none.

`superconduct_aggro` ranks **8/20** on the screen with 3★ 35/80 (44%) and win 74/80 (92%).
It is the final recommendation on: no stage.
Within 10pp of the final pick's 3★ rate on 5/16 stages: buyun, jifang, patton, swan, timmie.
Verdict: a high-win-rate / low-3★ deck — it rarely loses but leaves objectives on the table, and it is never the fixed pick. It is not competitive for the three-star metric.

## 6. Card composition

Counts are over the 30-card list (duplicates included). `W` = `GCG_CARD_MODIFY` + `GCG_TAG_WEAPON`, `A` = `GCG_CARD_MODIFY` + `GCG_TAG_ARTIFACT`, `E` = `GCG_CARD_EVENT` (excluding talent-tagged cards, which `npc.objectives` records as `talent`), `S` = `GCG_CARD_ASSIST`. `T` / `X` are talent cards and other equipment (e.g. vehicles).

| code | deck | W | A | E | S | T | X | total | wagner (W>=1) | timaeus (A>=1) | uncletian (E>=2) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| D01 | natlan_battleship | 2 | 2 | 17 | 5 | 4 | 0 | 30 | yes | yes | yes |
| D02 | klee_mavuika_mualani | 2 | 2 | 19 | 3 | 2 | 2 | 30 | yes | yes | yes |
| D03 | superconduct_mika_attack | 6 | 10 | 11 | 1 | 2 | 0 | 30 | yes | yes | yes |
| D04 | dual_mualani_stacks | 0 | 1 | 22 | 5 | 2 | 0 | 30 | no | yes | yes |
| D05 | superconduct_aggro | 0 | 6 | 17 | 7 | 0 | 0 | 30 | no | yes | yes |
| D06 | mualani_double_pyro | 0 | 1 | 21 | 6 | 2 | 0 | 30 | no | yes | yes |
| D07 | yelan_clorinde_ororon | 0 | 9 | 15 | 6 | 0 | 0 | 30 | no | yes | yes |
| D08 | electrocharged_moon | 2 | 3 | 15 | 8 | 2 | 0 | 30 | yes | yes | yes |
| D09 | aywolfskirk | 1 | 2 | 12 | 12 | 3 | 0 | 30 | yes | yes | yes |
| D10 | dvalin_bonk | 0 | 2 | 19 | 6 | 3 | 0 | 30 | no | yes | yes |
| D11 | shenhe_double_swirl | 0 | 2 | 20 | 5 | 3 | 0 | 30 | no | yes | yes |
| D12 | electro_manifestation_nuke | 0 | 4 | 15 | 11 | 0 | 0 | 30 | no | yes | yes |
| D13 | double_geo_navia | 0 | 0 | 18 | 8 | 4 | 0 | 30 | no | no | yes |
| D14 | unyielding_geo | 1 | 3 | 14 | 9 | 2 | 1 | 30 | yes | yes | yes |
| D15 | beidou_skirk_ayaka | 0 | 10 | 13 | 5 | 2 | 0 | 30 | no | yes | yes |
| D16 | skirk_chasca_freeze | 0 | 6 | 9 | 8 | 5 | 2 | 30 | no | yes | yes |
| D17 | skirk_ayaka_navia | 0 | 2 | 19 | 6 | 3 | 0 | 30 | no | yes | yes |
| D18 | ice_water_battleship | 0 | 4 | 15 | 6 | 3 | 2 | 30 | no | yes | yes |
| D19 | nahida_kokomi_raiden | 4 | 2 | 7 | 15 | 2 | 0 | 30 | yes | yes | yes |
| D20 | trial_skirk_ayaka_navia | 2 | 1 | 16 | 8 | 3 | 0 | 30 | yes | yes | yes |

Note: 8/20 decks carry a weapon card, so wagner is the restrictive stage; 19/20 carry an artifact (missing: double_geo_navia); 20/20 have at least 2 event cards.

## 7. Benchmark defects / weak stages

No stage has zero wins across all 20 decks; every stage is winnable by some deck.

### Draw / 15-round-cap rate by stage (5-seed screen)

A draw is `winner` not in `{0, 1}` with no error/truncation; all observed draws end at round 15. They are counted as non-wins above.

| stage | draws | valid games | draw rate |
|---|---|---|---|
| blanche | 14 | 100 | 14% |
| buyun | 3 | 100 | 3% |
| chefmao | 10 | 100 | 10% |
| ellin | 12 | 100 | 12% |
| irontonguetian | 9 | 100 | 9% |
| jifang | 6 | 100 | 6% |
| lan | 10 | 100 | 10% |
| liusu | 12 | 100 | 12% |
| marjorie | 9 | 100 | 9% |
| patton | 5 | 100 | 5% |
| shitou | 12 | 100 | 12% |
| swan | 0 | 100 | 0% |
| timaeus | 2 | 100 | 2% |
| timmie | 11 | 100 | 11% |
| uncletian | 7 | 100 | 7% |
| wagner | 6 | 100 | 6% |

Method caveat: the screen has only 5 samples per cell, so one game moves a 3★ rate by 20pp. The shortlist finalists have 10 samples; their Wilson 95% CI is in the JSON notes. Seed 0 is not exchangeable with the others (38% draws), so the 5-seed screen is conservative. Treat near-ties (and the 3★ tie-break) as unresolved rather than real.

## 8. Ready-to-paste `configs/stage_decks.json` (not written by this script)

Notes carry the raw counts, seed count, shortlist origin and Wilson 95% CI for the three-star rate. This file is intentionally only shown here; another agent owns `configs/stage_decks.json`.

```json
{
  "stages": {
    "blanche": {
      "deck": "electro_manifestation_nuke",
      "notes": "3-star 5/10 (50%); win 10/10 (100%); mean stars 2.30; mean rounds 5.6; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up electrocharged_moon 5/10 (50%); 3-star 95% CI 24%-76%"
    },
    "buyun": {
      "deck": "mualani_double_pyro",
      "notes": "3-star 8/10 (80%); win 10/10 (100%); mean stars 2.80; mean rounds 5.1; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up superconduct_aggro 8/10 (80%); 3-star 95% CI 49%-94%"
    },
    "chefmao": {
      "deck": "electrocharged_moon",
      "notes": "3-star 9/10 (90%); win 10/10 (100%); mean stars 2.90; mean rounds 5.4; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up mualani_double_pyro 8/10 (80%); 3-star 95% CI 60%-98%"
    },
    "ellin": {
      "deck": "shenhe_double_swirl",
      "notes": "3-star 9/10 (90%); win 10/10 (100%); mean stars 2.90; mean rounds 3.8; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up aywolfskirk 9/10 (90%); 3-star 95% CI 60%-98%"
    },
    "irontonguetian": {
      "deck": "mualani_double_pyro",
      "notes": "3-star 8/10 (80%); win 9/10 (90%); mean stars 2.70; mean rounds 4.8; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up dual_mualani_stacks 5/10 (50%); 3-star 95% CI 49%-94%"
    },
    "jifang": {
      "deck": "electro_manifestation_nuke",
      "notes": "3-star 10/10 (100%); win 10/10 (100%); mean stars 3.00; mean rounds 2.4; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up superconduct_aggro 10/10 (100%); 3-star 95% CI 72%-100%"
    },
    "lan": {
      "deck": "electro_manifestation_nuke",
      "notes": "3-star 10/10 (100%); win 10/10 (100%); mean stars 3.00; mean rounds 3.9; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up electrocharged_moon 8/10 (80%); 3-star 95% CI 72%-100%"
    },
    "liusu": {
      "deck": "mualani_double_pyro",
      "notes": "3-star 7/10 (70%); win 9/10 (90%); mean stars 2.50; mean rounds 5.6; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up dual_mualani_stacks 6/10 (60%); 3-star 95% CI 40%-89%"
    },
    "marjorie": {
      "deck": "electrocharged_moon",
      "notes": "3-star 9/10 (90%); win 10/10 (100%); mean stars 2.90; mean rounds 4.5; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up shenhe_double_swirl 8/10 (80%); 3-star 95% CI 60%-98%"
    },
    "patton": {
      "deck": "electro_manifestation_nuke",
      "notes": "3-star 10/10 (100%); win 10/10 (100%); mean stars 3.00; mean rounds 4.3; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up superconduct_aggro 9/10 (90%); 3-star 95% CI 72%-100%"
    },
    "shitou": {
      "deck": "electro_manifestation_nuke",
      "notes": "3-star 7/10 (70%); win 10/10 (100%); mean stars 2.60; mean rounds 4.9; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up electrocharged_moon 7/10 (70%); 3-star 95% CI 40%-89%"
    },
    "swan": {
      "deck": "electrocharged_moon",
      "notes": "3-star 10/10 (100%); win 10/10 (100%); mean stars 2.00; mean rounds 3.6; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up electro_manifestation_nuke 10/10 (100%); 3-star 95% CI 72%-100%"
    },
    "timaeus": {
      "deck": "electro_manifestation_nuke",
      "notes": "3-star 6/10 (60%); win 10/10 (100%); mean stars 2.60; mean rounds 4.2; 10-seed shortlist (top 3 from 5-seed screen); satisfies artifact x1 objective (artifact 4/1); runner-up electrocharged_moon 5/10 (50%); 3-star 95% CI 31%-83%"
    },
    "timmie": {
      "deck": "electro_manifestation_nuke",
      "notes": "3-star 9/10 (90%); win 10/10 (100%); mean stars 2.90; mean rounds 4.1; 10-seed shortlist (top 3 from 5-seed screen); no card-type objective; runner-up shenhe_double_swirl 9/10 (90%); 3-star 95% CI 60%-98%"
    },
    "uncletian": {
      "deck": "dvalin_bonk",
      "notes": "3-star 10/10 (100%); win 10/10 (100%); mean stars 3.00; mean rounds 4.3; 10-seed shortlist (top 3 from 5-seed screen); satisfies event x2 objective (event 19/2); runner-up electro_manifestation_nuke 8/10 (80%); 3-star 95% CI 72%-100%"
    },
    "wagner": {
      "deck": "superconduct_mika_attack",
      "notes": "3-star 9/10 (90%); win 10/10 (100%); mean stars 2.90; mean rounds 4.5; 10-seed shortlist (top 3 from 5-seed screen); satisfies weapon x1 objective (weapon 6/1); runner-up nahida_kokomi_raiden 6/10 (60%); 3-star 95% CI 60%-98%"
    }
  }
}
```

## 9. Reproduce

```bash
PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
cd /home/ubuntuaaa/projects/gitcg-skills
# phase 1: full 5-seed screen (16 x 20 x 5 = 1600 games)
"$PY" -m scripts.deck_matrix --seeds 0,1,2,3,4 --workers 8
# phase 2: extend the top 3 per stage to 10 seeds (240 new games)
"$PY" -m scripts.deck_matrix --shortlist-top 3 --shortlist-seeds 0,1,2,3,4,5,6,7,8,9 --screen-seeds 0,1,2,3,4 --workers 8
# "$PY" -m scripts.deck_matrix --analyze-only   # rebuild this report from the JSONL
# clean rerun: rm data/runs/deck_matrix.jsonl data/runs/deck_matrix.jsonl.meta.json
```

Rows are appended to `data/runs/deck_matrix.jsonl` (gitignored) and keyed by `(stage, deck, seed)`, so adding seeds resumes instead of replaying. The fingerprint sidecar `data/runs/deck_matrix.jsonl.meta.json` pins the engine lock, HP table, stage JSON, assets, deck list and judging flags (not the seed list, so the seed set and shortlist can be extended).

