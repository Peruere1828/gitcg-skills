# Era-legal (v3.3.0) deck pool for the NPC PvE benchmark

The 20 preset expert decks are built from post-v3.3 card sets and saturate the 16-stage benchmark (best deck wins 100% of screen games on all 16 stages). This report uses **official v3.3-era share-code decks**, decoded and engine-validated at `version="v3.3.0"`.

- Generated: 2026-11-02 08:23
- Era version: `v3.3.0` (`CreateParam(version=...)`, engine-enforced)
- Deck source: **share_code** (`/home/ubuntuaaa/projects/gitcg-skills/configs/era_decks.json`)
- Input list: `/home/ubuntuaaa/projects/gitcg-skills/data/era_decks.txt`
- Matrix rows: `/home/ubuntuaaa/projects/gitcg-skills/data/runs/era_matrix.jsonl`
- Matrix: 16 stages x 5 decks x 5 seeds = 400 planned games (seeds 1, 2, 3, 4, 5)
- Seed 0 is **not** used: the finished post-3.3 matrix showed 121/320 seed-0 draws (15-round cap) vs 0-3/320 on seeds 1-4, a deterministic artifact concentrated in marginal decks.
- Valid matrix rows: 400 (errors/truncated: 0)
- Policy: `expert:<era deck>` (synthetic `ExpertRuleAgent` profile, policy seed 0, the same convention as `scripts/deck_matrix.py`); NPC = `npc.policy.NpcPolicy` greedy.
- Judging: `record_decisions=True`, `record_events=True`, `record_final_state=True` (required by the equipment/event/reaction objectives).
- Engine lock `fa42fdfec70b`, HP table `0bbae5d8798a`, stage JSON `da3ad153e409`, assets `6b13d08f6419`
- Ranking: three-star rate first, then win rate, mean stars, mean rounds (fewer is better), deck name.

> **Pool size: 5 deck(s).** The original target was 8-12; this run uses every valid code currently in `data/era_decks.txt`. Add more codes and re-run `--all` if the article provides them.

## 1. The legal v3.3.0 pool

Candidates from `data/assets/expert_system_assets_chs_latest.json` with `sinceVersion <= v3.3.0`, then **validated one by one against the engine** (`CreateParam(version="v3.3.0")` + `Game(...)`; a failure is illegal). Counts are engine-verified, not asset-tag guesses.

### 1.1 Characters

- 27 asset candidates -> **27 engine-legal character cards** -> **27 playable characters** (asset `category == "characters"` and `obtainable != false`).
- At v3.3.0 there is **no NPC-only split**: Oceanid (2201) and Fatui Pyro Agent (2301) are playable character cards, so all 27 are in the pool.

| element | n | characters |
|---|---|---|
| CRYO | 5 | 凯亚, 甘雨, 神里绫华, 迪奥娜, 重云 |
| HYDRO | 5 | 愚人众·藏镜仕女, 纯水精灵·洛蒂娅, 芭芭拉, 莫娜, 行秋 |
| PYRO | 5 | 宵宫, 愚人众·火之债务处理人, 班尼特, 迪卢克, 香菱 |
| ELECTRO | 4 | 刻晴, 菲谢尔, 赛诺, 雷泽 |
| ANEMO | 3 | 琴, 砂糖, 魔偶剑鬼 |
| GEO | 3 | 丘丘岩盔王, 凝光, 诺艾尔 |
| DENDRO | 2 | 柯莱, 翠翎恐蕈 |

Sanity checks: Diluc / Kaeya / Fischl load at v3.3.0; Nahida / Kokomi / Raiden are rejected by the engine with `Unknown character id ...`.

### 1.2 Action cards

- 127 asset candidates -> **120 engine-legal cards** -> **119 deck-buildable cards**.
- 7 candidates are rejected by the engine: internal "elemental infusion" state entries (ids 81-87) that the asset dump types like a `GCG_CARD_MODIFY` card. They are not TCG cards.
- Deck-buildable drops only the unobtainable Keqing alternate talent (114031). The six 2xxx-character talents are **included** because those characters are playable at v3.3.0.

| kind | n | breakdown |
|---|---|---|
| talent | 27 | ANEMO 3, CRYO 5, DENDRO 2, ELECTRO 4, GEO 3, HYDRO 5, PYRO 5 |
| weapon | 15 | BOW 3, CATALYST 3, CLAYMORE 3, POLE 3, SWORD 3 |
| artifact | 20 | ANEMO 2, CRYO 2, DENDRO 2, ELECTRO 2, GEO 2, HYDRO 2, PYRO 2, neutral 6 |
| support | 20 | neutral |
| event | 37 | ANEMO 2, CRYO 2, DENDRO 2, ELECTRO 2, GEO 2, HYDRO 2, PYRO 2, neutral 23 |

The `event` row includes the 14 elemental-resonance events (2 per element); the rest are neutral die/draw/heal/damage/switch events and 8 food cards. Weapons come in exactly 3 per weapon type (catalyst/bow/claymore/pole/sword).

Engine-rejected candidates:

| id | name | sinceVersion | engine message |
|---|---|---|---|
| 81 | 元素附魔：冰 | v3.3.0 | `gitcg_state_new: Uncaught Error: Unknown card id 81 | AssertionError:` |
| 82 | 元素附魔：水 | v3.3.0 | `gitcg_state_new: Uncaught Error: Unknown card id 82 | AssertionError:` |
| 83 | 元素附魔：火 | v3.3.0 | `gitcg_state_new: Uncaught Error: Unknown card id 83 | AssertionError:` |
| 84 | 元素附魔：雷 | v3.3.0 | `gitcg_state_new: Uncaught Error: Unknown card id 84 | AssertionError:` |
| 85 | 元素附魔：风 | v3.3.0 | `gitcg_state_new: Uncaught Error: Unknown card id 85 | AssertionError:` |
| 86 | 元素附魔：岩 | v3.3.0 | `gitcg_state_new: Uncaught Error: Unknown card id 86 | AssertionError:` |
| 87 | 元素附魔：草 | v3.3.0 | `gitcg_state_new: Uncaught Error: Unknown card id 87 | AssertionError:` |

### 1.3 Current `superconduct_aggro` at v3.3.0

`superconduct_aggro` does **not** load at v3.3.0: characters [1403, 1407, 1101], illegal characters [1407] (Raiden Shogun is v3.7.0), and 13/30 cards outside the launch pool. This is the concrete evidence that the current benchmark decks are post-3.3 decks.

Engine message:

```text
gitcg_state_new: Uncaught Error: Unknown card id 312007 | AssertionError:
```

Illegal card ids (per-card engine probe): `312007`, `312041`, `321011`, `321016`, `323004`, `330006`, `330012`, `331004`, `331802`, `333020`.

### 1.4 NPC stages at v3.3.0

All **16/16** stage lineups load at v3.3.0 (the NPC enemies are launch-era monster cards). No stage is silently excluded.

## 2. The era deck pool

Decks come from the configured pool: official share codes by default, or the synthetic fallback only when `--allow-fallback` is passed. The importer re-derives card kinds, element/weapon metadata, composition and objective coverage from the asset dump.

| deck | archetype | source | characters (element / weapon) | T | W | A | S | E | total |
|---|---|---|---|---|---|---|---|---|---|
| keqing_jihua | quicken | share_code | 菲谢尔 (ELECTRO/BOW), 柯莱 (DENDRO/BOW), 刻晴 (ELECTRO/SWORD) | 3 | 0 | 2 | 5 | 20 | 30 |
| ganyu_shuanglei | double-electro Ganyu | share_code | 菲谢尔 (ELECTRO/BOW), 甘雨 (CRYO/BOW), 刻晴 (ELECTRO/SWORD) | 2 | 0 | 2 | 8 | 18 | 30 |
| linghua_yongdong | Ayaka freeze | share_code | 神里绫华 (CRYO/SWORD), 行秋 (HYDRO/SWORD), 凯亚 (CRYO/SWORD) | 2 | 2 | 1 | 6 | 19 | 30 |
| keqing_shuanghuo | double-pyro Keqing | share_code | 愚人众·火之债务处理人 (PYRO/NONE), 班尼特 (PYRO/SWORD), 刻晴 (ELECTRO/SWORD) | 3 | 1 | 3 | 5 | 18 | 30 |
| nvpu_shuangyan_era | double-geo Noelle | share_code_variant | 诺艾尔 (GEO/CLAYMORE), 凝光 (GEO/CATALYST), 行秋 (HYDRO/SWORD) | 1 | 2 | 1 | 7 | 19 | 30 |

T/W/A/S/E = talent / weapon / artifact / support / event cards in the 30-card list (duplicates included).

**Archetypes represented:** quicken (`keqing_jihua`), double-electro Ganyu (`ganyu_shuanglei`), Ayaka freeze (`linghua_yongdong`), double-pyro Keqing (`keqing_shuanghuo`), double-geo Noelle (`nvpu_shuangyan_era`).

`nvpu_shuangyan_era` is a documented era variant of `nvpu_shuangyan`: 2x `333014` 松茸酿肉卷 (v4.4.0) were replaced with 2x `333006` 蒙德土豆饼 (v3.3.0). Both are `GCG_CARD_EVENT` food cards, so the composition and coverage counters are **unchanged** (1 talent / 2 weapons / 1 artifact / 7 supports / 19 events; weapon 2 / artifact 1 / event 19). The original share code is in the deck's `notes` and `source_share_code`.

- Input entries **not** in the config: `nvpu_shuangyan`. Run `scripts.import_deck` to see the rejection reason (out-of-era, non-playable character, or a deck-building rule).

### 2.1 Card-type objective coverage

`wagner` needs 1 weapon, `timaeus` needs 1 artifact, `uncletian` needs 2 events. The per-stage recommendation filters on these counts, so a stronger deck that cannot satisfy the stage objective is never picked.

| deck | wagner (weapon >= 1) | timaeus (artifact >= 1) | uncletian (event >= 2) | all three? |
|---|---|---|---|---|
| keqing_jihua | 0 | 2 | 20 | no |
| ganyu_shuanglei | 0 | 2 | 18 | no |
| linghua_yongdong | 2 | 1 | 19 | yes |
| keqing_shuanghuo | 1 | 3 | 18 | yes |
| nvpu_shuangyan_era | 2 | 1 | 19 | yes |

- Decks with **>= 2 weapon cards**: linghua_yongdong, nvpu_shuangyan_era.
- Decks with **>= 2 artifacts and >= 6 events**: keqing_jihua, ganyu_shuanglei, keqing_shuanghuo.
- Decks covering **all three** objectives: linghua_yongdong, keqing_shuanghuo, nvpu_shuangyan_era.
- Weapon usability: `linghua_yongdong` has **three sword characters** (Ayaka/Xingqiu/Kaeya) and 2x Aquila Favonia, so both copies are usable; `nvpu_shuangyan_era` has 2x Wolf's Gravestone but only Noelle is a claymore user, so one copy is redundant; `keqing_shuanghuo` carries one sword (Bennett/Keqing can use it) and still satisfies `wagner`.
- Realized vs carried: `wagner` is realized (linghua_yongdong, keqing_shuanghuo, nvpu_shuangyan_era each score 3/5 three-star); `uncletian` is realized (keqing_jihua 5/5, ganyu_shuanglei 4/5, nvpu_shuangyan_era 2/5); `timaeus` is **not** realized by any deck (0/5 three-star on all five) even though every deck carries >=1 artifact.
- **Single-deck answer:** `keqing_shuanghuo` is the strongest deck that covers all three objectives; every deck in the all-three list above also does. If the pool is thin, that is the safest single fixed choice, but the per-stage pick still optimizes three-star rate per stage.

## 3. Matrix (win / three-star), seeds 1, 2, 3, 4, 5

Cells are `wins/5 · three-stars/5` over the 5 matrix seeds. Counts, not percentages, so a 4/5 vs 5/5 difference is visible.

| stage | keqing_jihua | ganyu_shuanglei | linghua_yongdong | keqing_shuanghuo | nvpu_shuangyan_era |
|---|---|---|---|---|---|
| blanche | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 4/5 (80%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) |
| buyun | 3/5 (60%) · 1/5 (20%) | 5/5 (100%) · 1/5 (20%) | 3/5 (60%) · 0/5 (0%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 2/5 (40%) |
| chefmao | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 1/5 (20%) |
| ellin | 5/5 (100%) · 4/5 (80%) | 4/5 (80%) · 2/5 (40%) | 5/5 (100%) · 2/5 (40%) | 5/5 (100%) · 3/5 (60%) | 5/5 (100%) · 0/5 (0%) |
| irontonguetian | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 1/5 (20%) |
| jifang | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 5/5 (100%) | 4/5 (80%) · 3/5 (60%) | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 2/5 (40%) |
| lan | 5/5 (100%) · 4/5 (80%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 2/5 (40%) | 4/5 (80%) · 0/5 (0%) | 5/5 (100%) · 1/5 (20%) |
| liusu | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 0/5 (0%) |
| marjorie | 5/5 (100%) · 1/5 (20%) | 4/5 (80%) · 2/5 (40%) | 4/5 (80%) · 3/5 (60%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 1/5 (20%) |
| patton | 5/5 (100%) · 4/5 (80%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) |
| shitou | 5/5 (100%) · 3/5 (60%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) |
| swan | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 4/5 (80%) | 5/5 (100%) · 3/5 (60%) | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 2/5 (40%) |
| timaeus | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 4/5 (80%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) |
| timmie | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 1/5 (20%) | 5/5 (100%) · 4/5 (80%) | 5/5 (100%) · 2/5 (40%) |
| uncletian | 5/5 (100%) · 5/5 (100%) | 5/5 (100%) · 4/5 (80%) | 4/5 (80%) · 0/5 (0%) | 4/5 (80%) · 0/5 (0%) | 5/5 (100%) · 2/5 (40%) |
| wagner | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 0/5 (0%) | 5/5 (100%) · 3/5 (60%) | 5/5 (100%) · 3/5 (60%) | 5/5 (100%) · 3/5 (60%) |

### 3.1 Per-deck aggregate

Pooled over all 16 stages x 5 matrix seeds. `mean stage 3★` averages the per-stage rates (each stage weighs equally).

| rank | deck | 3★ | win | mean stars | mean rounds | mean stage 3★ |
|---|---|---|---|---|---|---|
| 1 | keqing_jihua | 52/80 (65%) | 78/80 (98%) | 2.54 | 4.8 | 65% |
| 2 | keqing_shuanghuo | 24/80 (30%) | 78/80 (98%) | 2.08 | 6.6 | 30% |
| 3 | linghua_yongdong | 20/80 (25%) | 73/80 (91%) | 1.98 | 6.8 | 25% |
| 4 | ganyu_shuanglei | 19/80 (24%) | 78/80 (98%) | 2.05 | 6.4 | 24% |
| 5 | nvpu_shuangyan_era | 17/80 (21%) | 80/80 (100%) | 2.02 | 6.6 | 21% |

## 4. Rounds diagnostic

A normal game should finish well under 10 rounds. The engine caps at 15 rounds; a game that reaches 15 is flagged as a probable stall (win or draw), not fixed here.

- Games reaching **>= 10 rounds**: 33/400 (8%).
- Games reaching **>= 15 rounds (cap)**: 5/400 (1%).

| rounds | games | share |
|---|---|---|
| 3 | 30 | 8% |
| 4 | 55 | 14% |
| 5 | 71 | 18% |
| 6 | 83 | 21% |
| 7 | 83 | 21% |
| 8 | 32 | 8% |
| 9 | 13 | 3% |
| 10 | 11 | 3% |
| 11 | 9 | 2% |
| 12 | 4 | 1% |
| 13 | 2 | 0% |
| 14 | 2 | 0% |
| 15 | 5 | 1% |

**Flagged 15-round games (probable stalls):**

| stage | deck | seed | winner | 3★ |
|---|---|---|---|---|
| buyun | keqing_jihua | 5 | None | False |
| buyun | linghua_yongdong | 3 | None | False |
| lan | keqing_shuanghuo | 3 | None | False |
| uncletian | keqing_shuanghuo | 3 | None | False |
| uncletian | linghua_yongdong | 3 | None | False |

## 5. Per-stage recommendation

The pick is the best deck **among decks that satisfy the stage's card-type objective** (ranking as above). A deck that cannot satisfy the objective is never recommended, even if it is stronger. The last column flags a deliberate difficulty trade-off when the strength-only pick and the satisfying pick differ by more than 15pp win rate.

| stage | recommended deck | win | 3★ | mean rounds | objective coverage | strength-only pick | win-rate gap | trade-off? |
|---|---|---|---|---|---|---|---|---|
| blanche | **keqing_jihua** | 5/5 (100%) | 0/5 (0%) | 7.2 | n/a | keqing_jihua | 0pp | no (same deck) |
| buyun | **nvpu_shuangyan_era** | 5/5 (100%) | 2/5 (40%) | 6.4 | n/a | nvpu_shuangyan_era | 0pp | no (same deck) |
| chefmao | **keqing_jihua** | 5/5 (100%) | 5/5 (100%) | 4.2 | n/a | keqing_jihua | 0pp | no (same deck) |
| ellin | **keqing_jihua** | 5/5 (100%) | 4/5 (80%) | 5.0 | n/a | keqing_jihua | 0pp | no (same deck) |
| irontonguetian | **keqing_jihua** | 5/5 (100%) | 5/5 (100%) | 4.2 | n/a | keqing_jihua | 0pp | no (same deck) |
| jifang | **keqing_jihua** | 5/5 (100%) | 5/5 (100%) | 3.2 | n/a | keqing_jihua | 0pp | no (same deck) |
| lan | **keqing_jihua** | 5/5 (100%) | 4/5 (80%) | 4.8 | n/a | keqing_jihua | 0pp | no (same deck) |
| liusu | **keqing_jihua** | 5/5 (100%) | 5/5 (100%) | 4.2 | n/a | keqing_jihua | 0pp | no (same deck) |
| marjorie | **linghua_yongdong** | 4/5 (80%) | 3/5 (60%) | 6.0 | n/a | linghua_yongdong | 0pp | no (same deck) |
| patton | **keqing_jihua** | 5/5 (100%) | 4/5 (80%) | 5.0 | n/a | keqing_jihua | 0pp | no (same deck) |
| shitou | **keqing_jihua** | 5/5 (100%) | 3/5 (60%) | 5.6 | n/a | keqing_jihua | 0pp | no (same deck) |
| swan | **keqing_jihua** | 5/5 (100%) | 5/5 (100%) | 3.4 | n/a | keqing_jihua | 0pp | no (same deck) |
| timaeus | **keqing_jihua** | 5/5 (100%) | 0/5 (0%) | 5.4 | artifact 2/1 | keqing_jihua | 0pp | no (same deck) |
| timmie | **keqing_jihua** | 5/5 (100%) | 5/5 (100%) | 3.8 | n/a | keqing_jihua | 0pp | no (same deck) |
| uncletian | **keqing_jihua** | 5/5 (100%) | 5/5 (100%) | 3.8 | event 20/2 | keqing_jihua | 0pp | no (same deck) |
| wagner | **linghua_yongdong** | 5/5 (100%) | 3/5 (60%) | 3.6 | weapon 2/1 | linghua_yongdong | 0pp | no (same deck) |

No stage has a >15pp win-rate gap between the best deck and the best objective-satisfying deck on this pool.

## 6. Saturation: era pool vs the post-3.3 matrix

Post-3.3 evidence is the finished 20-deck matrix, read-only and **not** used for deck selection. Seed 0 is excluded from this comparison too (121/320 draws there vs 0-3/320 on seeds 1-4), so the post-3.3 numbers use seeds 1-4. Era numbers are the best era deck per stage on the 5 era seeds. The seed sets still differ, so read this as a pool-level saturation check, not a paired seed comparison. `era best` here is the highest win rate per stage, not the objective-eligible pick from §5.

| stage | post-3.3 best deck | post-3.3 win | era best deck | era win | Δ (era - post) |
|---|---|---|---|---|---|
| blanche | dual_mualani_stacks | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| buyun | superconduct_aggro | 4/4 (100%) | nvpu_shuangyan_era | 5/5 (100%) | +0pp |
| chefmao | dual_mualani_stacks | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| ellin | klee_mavuika_mualani | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| irontonguetian | dual_mualani_stacks | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| jifang | superconduct_mika_attack | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| lan | electrocharged_moon | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| liusu | dual_mualani_stacks | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| marjorie | yelan_clorinde_ororon | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| patton | dvalin_bonk | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| shitou | electrocharged_moon | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| swan | superconduct_mika_attack | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| timaeus | electro_manifestation_nuke | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| timmie | klee_mavuika_mualani | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| uncletian | dvalin_bonk | 4/4 (100%) | keqing_jihua | 5/5 (100%) | +0pp |
| wagner | superconduct_mika_attack | 4/4 (100%) | linghua_yongdong | 5/5 (100%) | +0pp |

**Saturation check:** the post-3.3 best deck wins 100% on 16/16 stages; the era best deck wins 100% on 16/16 stages. Pooled, the post-3.3 best deck is 64/64 (100%) win / 52/64 (81%) 3★ (`electrocharged_moon`); the era best deck is 78/80 (98%) win / 52/80 (65%) 3★ (`keqing_jihua`).
**Win-rate saturation largely persists in this pool** (era 98% vs post 100%).
The era pool is harder on the three-star metric (era 65% vs post 81%), so the objective ceiling has more headroom, but the win rate is still saturated. If the goal is win-rate de-saturation, the card-pool restriction alone is insufficient; NPC difficulty / policy is the remaining bottleneck.
Read the 3★ column, not only wins; this is a 5-deck pool.
The post-3.3 matrix contains 1840 rows (1840 valid); its per-deck screen numbers are in `reports/deck_matrix.md`.

## 7. Ready-to-paste `configs/stage_decks.json` block (not written here)

Era deck ids are defined in `configs/era_decks.json`; the consumer must resolve them from there (they are **not** in `envs.decks.expert_deck_specs()`, which only knows the 20 post-3.3 expert decks). Notes carry raw counts, the 5-seed matrix, objective coverage and the 95% Wilson interval.

```json
{
  "stages": {
    "blanche": {
      "deck": "keqing_jihua",
      "notes": "3-star 0/5 (0%); win 5/5 (100%); mean rounds 7.2; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up nvpu_shuangyan_era 0/5 (0%); 3-star 95% CI 0%-43%"
    },
    "buyun": {
      "deck": "nvpu_shuangyan_era",
      "notes": "3-star 2/5 (40%); win 5/5 (100%); mean rounds 6.4; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up keqing_shuanghuo 1/5 (20%); 3-star 95% CI 12%-77%"
    },
    "chefmao": {
      "deck": "keqing_jihua",
      "notes": "3-star 5/5 (100%); win 5/5 (100%); mean rounds 4.2; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up keqing_shuanghuo 1/5 (20%); 3-star 95% CI 57%-100%"
    },
    "ellin": {
      "deck": "keqing_jihua",
      "notes": "3-star 4/5 (80%); win 5/5 (100%); mean rounds 5.0; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up keqing_shuanghuo 3/5 (60%); 3-star 95% CI 38%-96%"
    },
    "irontonguetian": {
      "deck": "keqing_jihua",
      "notes": "3-star 5/5 (100%); win 5/5 (100%); mean rounds 4.2; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up keqing_shuanghuo 1/5 (20%); 3-star 95% CI 57%-100%"
    },
    "jifang": {
      "deck": "keqing_jihua",
      "notes": "3-star 5/5 (100%); win 5/5 (100%); mean rounds 3.2; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up ganyu_shuanglei 5/5 (100%); 3-star 95% CI 57%-100%"
    },
    "lan": {
      "deck": "keqing_jihua",
      "notes": "3-star 4/5 (80%); win 5/5 (100%); mean rounds 4.8; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up linghua_yongdong 2/5 (40%); 3-star 95% CI 38%-96%"
    },
    "liusu": {
      "deck": "keqing_jihua",
      "notes": "3-star 5/5 (100%); win 5/5 (100%); mean rounds 4.2; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up keqing_shuanghuo 1/5 (20%); 3-star 95% CI 57%-100%"
    },
    "marjorie": {
      "deck": "linghua_yongdong",
      "notes": "3-star 3/5 (60%); win 4/5 (80%); mean rounds 6.0; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up ganyu_shuanglei 2/5 (40%); 3-star 95% CI 23%-88%"
    },
    "patton": {
      "deck": "keqing_jihua",
      "notes": "3-star 4/5 (80%); win 5/5 (100%); mean rounds 5.0; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up ganyu_shuanglei 0/5 (0%); 3-star 95% CI 38%-96%"
    },
    "shitou": {
      "deck": "keqing_jihua",
      "notes": "3-star 3/5 (60%); win 5/5 (100%); mean rounds 5.6; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up ganyu_shuanglei 0/5 (0%); 3-star 95% CI 23%-88%"
    },
    "swan": {
      "deck": "keqing_jihua",
      "notes": "3-star 5/5 (100%); win 5/5 (100%); mean rounds 3.4; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up keqing_shuanghuo 5/5 (100%); 3-star 95% CI 57%-100%"
    },
    "timaeus": {
      "deck": "keqing_jihua",
      "notes": "3-star 0/5 (0%); win 5/5 (100%); mean rounds 5.4; 5-seed era matrix (seeds 1,2,3,4,5); satisfies artifact x1 (artifact 2/1); runner-up nvpu_shuangyan_era 0/5 (0%); 3-star 95% CI 0%-43%"
    },
    "timmie": {
      "deck": "keqing_jihua",
      "notes": "3-star 5/5 (100%); win 5/5 (100%); mean rounds 3.8; 5-seed era matrix (seeds 1,2,3,4,5); no card-type objective; runner-up keqing_shuanghuo 4/5 (80%); 3-star 95% CI 57%-100%"
    },
    "uncletian": {
      "deck": "keqing_jihua",
      "notes": "3-star 5/5 (100%); win 5/5 (100%); mean rounds 3.8; 5-seed era matrix (seeds 1,2,3,4,5); satisfies event x2 (event 20/2); runner-up ganyu_shuanglei 4/5 (80%); 3-star 95% CI 57%-100%"
    },
    "wagner": {
      "deck": "linghua_yongdong",
      "notes": "3-star 3/5 (60%); win 5/5 (100%); mean rounds 3.6; 5-seed era matrix (seeds 1,2,3,4,5); satisfies weapon x1 (weapon 2/1); runner-up nvpu_shuangyan_era 3/5 (60%); 3-star 95% CI 23%-88%"
    }
  }
}
```

## 8. Reproduce

Rows are in `/home/ubuntuaaa/projects/gitcg-skills/data/runs/era_matrix.jsonl` (the default run path for the final pool).

```bash
PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
cd /home/ubuntuaaa/projects/gitcg-skills
"$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run
"$PY" -m scripts.import_deck --file data/era_decks.txt --write
"$PY" -m scripts.era_decks --build
"$PY" -m scripts.era_decks --matrix --seeds 1,2,3,4,5 --workers 8
"$PY" -m scripts.era_decks --report
# clean rerun: rm data/runs/era_matrix.jsonl data/runs/era_matrix.jsonl.meta.json
```

Rows are appended to `data/runs/era_matrix.jsonl` (gitignored) and keyed by `(stage, deck, seed)`, so adding seeds resumes instead of replaying. The fingerprint sidecar pins the version, stage/deck list, judging flags, engine lock and assets hash.

### Validation (one full game per deck at v3.3.0)

Stage `timmie`, seed 1; every deck loaded and played to completion.

| deck | winner | rounds | stars | 3★ | error |
|---|---|---|---|---|---|
| keqing_jihua | 0 | 4 | 3 | True | — |
| ganyu_shuanglei | 0 | 7 | 2 | False | — |
| linghua_yongdong | 0 | 7 | 2 | False | — |
| keqing_shuanghuo | 0 | 4 | 3 | True | — |
| nvpu_shuangyan_era | 0 | 6 | 2 | False | — |

