# Why era-matrix games stall: the 15-round draws and the 10+ round tail

- Generated: 2026-11-02
- Engine: `gitcg 0.21.0`, data version `v3.3.0` (`CreateParam(version="v3.3.0")`)
- Input matrix: `data/runs/era_matrix.jsonl` — 16 stages x 5 decks x seeds 1-5 = **400 games**, 0 errors / 0 truncated
- Policies: player = `expert:<era deck>` (`scripts/era_decks.py::_scripted_policy`, synthetic `style="era"` profile, policy seed 0); NPC = `npc.policy.NpcPolicy` greedy (never plays cards, never switches)
- Instrumented replay: `scripts/probe_stall.py` (`record_decisions=True`, `record_events=True`, `record_final_state=True`, plus the public mutation stream)
- Raw traces: `data/runs/stall_probe.jsonl` (the five draws), `data/runs/stall_probe_10plus.jsonl` (all 33 games with `rounds >= 10`)

All 33 long games were replayed individually — not sampled. Every replay reproduced the matrix row exactly (`rounds` and `winner` identical, 0 mismatches).

## TL;DR

The draws are **not** a policy that refuses to attack, not a deck-out rule, and not an engine bug. They are the intended enemy **elemental ward / elemental-lifeform** rules meeting a policy that never switches to the character whose element can break them. The player attacks every round; the attacks simply land for 0 because the last NPC character is immune to the only damage element the player still has on the field.

The 10+ round tail has two independent causes, and they explain **all 33 games**:

1. **Ward/immunity stalls** (19 games, on `buyun`, `lan`, `uncletian`): 15 of them show a >=4-round zero-damage streak for one side. 5 stall all the way to the cap (the draws). The expert keeps attacking with the blocked element and never brings the counter-element character (Xingqiu/Hydro, Keqing/Electro, Fischl/physical).
2. **High-HP + damage-soak stages** (14 games, on `blanche` 30 HP, `timaeus`/`shitou` 26 HP, `ellin` 22 HP): no stall, both sides trade damage, but 22-30 HP at ~2.5-3.5 damage/round needs 8-12 rounds. These are genuinely long, and the 5-round objective is arithmetically unreachable there.

The other 9 stages produce **zero** games at 10+ rounds. The 15-round cap is the engine default (`getDefaultGameConfig().maxRoundsCount = 15`); at the cap the engine ends in `gameEnd` with `winner = null` — it does not compare HP or survivors. It is configurable (`ATTR_STATE_CONFIG_MAX_ROUNDS_COUNT`, attr 105; verified 15 -> 21), but `run_match` never sets it.

---

## 1. The five 15-round draws

### 1.1 Reproduction

All five matrix rows reproduce exactly: `winner=None`, `rounds=15`, `error=None`, `truncated=False`, `phase=game_end`.

| game (stage / deck / seed) | P0 survivors (HP) | P1 survivors (HP) | damage dealt P0/P1 | last 3 rounds, dealt P0 | last 3 rounds, dealt P1 | P0 hand/pile | P1 hand/pile | `declare_end` P0/P1 |
|---|---|---|---|---|---|---|---|---|
| buyun / keqing_jihua / 5 | Fischl 3, Keqing 4 (Collei dead) | Electro Slime 3 (samachurl, cryo mage dead) | 15 / 25 | 0, 0, 0 | 0, 1, 2 | 5 / 0 | 10 / 0 | 14 / 14 |
| buyun / linghua_yongdong / 3 | Ayaka 2, Xingqiu 3, Kaeya 10 | Cryo Mage 2, Electro Slime 6 (samachurl dead) | 10 / 30 | 0, 0, 4 | 3, 0, 4 | 0 / 0 | 10 / 0 | 14 / 14 |
| lan / keqing_shuanghuo / 3 | Fatui Pyro Agent 7, Keqing 10 (Bennett dead) | Pyro Mage 6, Electro Shooter 6 (dendro samachurl dead) | 6 / 27 | 0, 0, 0 | 0, 8, 0 | 4 / 0 | 10 / 0 | 14 / 14 |
| uncletian / keqing_shuanghuo / 3 | Fatui Pyro Agent 6, Keqing 10 (Bennett dead) | Pyro Mage 6 (cryo mage dead) | 6 / 27 | 0, 0, 0 | 0, 8, 0 | 4 / 0 | 10 / 0 | 14 / 14 |
| uncletian / linghua_yongdong / 3 | Ayaka 2, Xingqiu 3, Kaeya 10 | Cryo Mage 2, Pyro Mage 6 | 4 / 29 | 0, 0, 4 | 3, 0, 4 | 0 / 0 | 10 / 0 | 14 / 14 |

"dealt P0/P1" = total effective HP lost by the opponent, from the `damage` mutations (`old_health - new_health`, heals excluded). `declare_end` = number of `action_declare_end` decisions (one per round, as normal). Both sides keep attacking: P0 used skills in 14-15 of the 15 played rounds, P1 in 11-14.

### 1.2 Mechanism per game

**buyun / keqing_jihua / 5 — electro slime immunity.**
The last NPC character is the Electro Slime (3/6 HP). Its passive `ElementalLifeformElectro` (`build/engine/packages/data/src/characters/electro/electro_slime.gts:15-30`) makes it **immune to Electro DMG** and permanently Electro-applied. P0's surviving characters are Fischl and Keqing, both Electro (Keqing also carries `114034` Electro Infusion, which converts even her normal attack to Electro). P0's only Dendro character, Collei, died in round 8. From round 10 to 14 every P0 skill is an Electro skill dealing `value 0` (e.g. Keqing normal 14031, elemental 14032 and burst 14033 all report `value=0, old_health=3, new_health=3`). P0 dealt 15 of the 18 required damage; the slime survived at 3 HP.

This is not literally unwinnable: Fischl's normal attack (`14011`, 2 **Physical** DMG) would hit the slime. But Fischl sat at 3 HP and was never made active — see §3.5 for the exact policy guards.

**buyun / linghua_yongdong / 3 — Cryo Ward vs a Cryo carry.**
`3103 Cryo Abyss Mage` carries `131031 Cryo Ward`: **all damage to the holder is reduced to 0**, and the ward has `usage 2` that is consumed only by an **elemental reaction** on the holder (`cryo_abyss_mage.gts:25-37`). A Cryo attack onto the ward's Cryo aura produces no reaction, so it never consumes a usage. P0's active is Ayaka (Cryo), and Ayaka is the opener/carry; P0 attacked for 0 damage in rounds 2-13 (12 consecutive rounds). The only Hydro character, Xingqiu, stayed on the bench until round 13, when HP pressure forced a switch; his Hydro skill `12022` triggered Freeze (one reaction, still 0 damage) and the ward broke only around round 13-14. Kaeya's physical normal attacks then dealt 2+2 = 4 damage in round 14 — 4 short of the 6 HP mage when the cap hit. P0 was also fully decked out (hand 0, pile 0) by then.

**lan / keqing_shuanghuo / 3 — Pyro Ward vs a Pyro carry, mutual 0-damage endgame.**
`3301 Pyro Abyss Mage` carries `133011 Pyro Ward` (same rule: 0 damage, 2 reaction usages). P0's active is the Fatui Pyro Agent (Pyro) and Bennett (Pyro) was the second carry; every P0 attack from round 3 to 14 was Pyro and dealt 0. Keqing (Electro) sat at 10/10 HP the whole game and never acted; her Electro attacks would trigger Overload and consume the ward. P0 dealt 6 damage total (only the Dendro Samachurl died). On the NPC side, the mage's 1-damage normal attacks were absorbed by the Fatui's `123012 Stealth` (-1 damage per hit, `fatui_pyro_agent.gts:19-34`), so the final played round (14) is a **fully frozen board: 0 damage both ways**. The mage's 7-damage burst in round 13 was the last real event (it killed Bennett).

**uncletian / keqing_shuanghuo / 3 — same as lan, one fewer enemy.**
P0 killed the Cryo Mage in rounds 3-4, then hit the Pyro Ward and dealt 0 for 10 consecutive rounds (rounds 5-14). Keqing never left the bench. Identical endgame: 0 damage both ways in the final played round (14); the mage's round-13 burst was the last damage.

**uncletian / linghua_yongdong / 3 — same as buyun/linghua.**
Cryo Ward on the first mage; P0 dealt 0 in rounds 0-13 (14 consecutive rounds), broke the ward at round 14 with Xingqiu's Hydro + Kaeya's physical attacks (4 damage), and the cap arrived with the mage at 2/6.

### 1.3 Engine rules involved (exact)

| rule | where | effect |
|---|---|---|
| win condition | `build/engine/packages/core/src/runtime/skill_context.ts:534-560` | A player loses only when `failedPlayers` (characters with 0 HP) kills the whole lineup. One failed player -> winner = other; **two failed players -> `gameEnd` with winner null**. |
| round cap | `build/engine/packages/core/src/game.ts:981-984` | At the end phase, `stepRound` then `if (roundNumber >= maxRoundsCount) return "gameEnd"`. The winner is left untouched, so the game ends **with `winner = null`** if both sides are alive. |
| default cap | `build/engine/packages/core/src/base/state.ts:102` | `maxRoundsCount: 15` in `getDefaultGameConfig()`. |
| cap configurable? | `gitcg.low_level.ATTR_STATE_CONFIG_MAX_ROUNDS_COUNT` = **105** | Yes. `CreateParam.set_attr(105, 21)` produces `maxRoundsCount: 21` in the state config (verified by `python -m scripts.probe_stall --check-round-cap`). `envs.match.run_match` never sets it, so the benchmark uses 15. |
| deck-out | `build/engine/packages/core/src/mutator.ts:827-843` (`drawCardsPlain`) | Drawing from an empty pile is a **no-op** (`if (!card) continue`). There is no fatigue damage and no deck-out loss. |
| cap = 14 played rounds | `game.ts:617-621` + `981-984` | Round 0 -> 1 at battle begin; after round 14's end phase the counter becomes 15 and the game ends. So a draw is reported as `rounds=15` after **14 played rounds**; round 15 has no decisions. |
| Cryo/Pyro Ward | `build/engine/packages/data/src/characters/cryo/cryo_abyss_mage.gts:25-37`, `.../pyro/pyro_abyss_mage.gts:25-37` | `on decreaseDamaged: decreaseDamage(value)` -> **all damage 0**; `on modifyReaction usage 2` -> only 2 **reactions** remove it. Same-element attacks do not react -> permanent immunity. |
| Electro Slime | `.../electro/electro_slime.gts:15-30` | Electro DMG reduced to 0; always Electro-applied. Non-Electro damage works. |
| Hoarfrost Greatshield | `.../cryo/ice_shieldwall_mitachurl.gts:19-37` | -2 damage per hit (not immunity). |
| Ice Lotus | `.../cryo/ganyu.gts:36-45` | Player-side combat status: -1 damage per hit, 2 usages. |
| Fatui Stealth | `.../pyro/fatui_pyro_agent.gts:19-34` | -1 damage per hit, 3 usages; converts his physical damage to Pyro. |

### 1.4 Classification of the five

All five are **class (b): one side unable to damage (shield/immunity/passive)**. None is (a) genuine, (c) deck-out, (d) policy loop, or (e) engine anomaly:

- (a) is excluded: the player dealt 0 in at least 5 consecutive rounds in every draw, and 0 in the last three rounds of all five.
- (c) is excluded: only two draws end with hand 0 / pile 0, but the player still had legal skills every round and the engine has no deck-out loss. Deck-out is a consequence of playing 30 cards, not the reason nobody won.
- (d) is excluded: the expert chose an attack skill in every round (14-15 of 15) and `declare_end` once per round (14/14); the NPC likewise (11-14 attack rounds, 14/14 ends). There is no pass loop and no support/switch loop.
- (e) is excluded: `winner` matches the alive counts in every game; no side was eliminated while the game continued.

The policy contribution is real but it is a **positioning** failure, not a refusal to act (see §3.5).

---

## 2. All games with `rounds >= 10`

### 2.1 Population

`rounds` histogram over all 400 games:

| rounds | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| games | 30 | 55 | 71 | 83 | 83 | 32 | 13 | 11 | 9 | 4 | 2 | 2 | 5 |
| cumulative | 30 | 85 | 156 | 239 | 322 | 354 | 367 | 378 | 387 | 391 | 393 | 395 | 400 |

- **33 / 400 games (8.25%) reach 10+ rounds**. 367 / 400 (91.75%) finish by round 9; median 6, mean 6.26.
- Winners among the 33: P0 (player) 25, P1 (NPC) 3, draw 5.
- By stage: `blanche` 8, `buyun` 7, `uncletian` 7, `lan` 5, `timaeus` 3, `ellin` 2, `shitou` 1. **The other 9 stages have 0.**
- By deck: `linghua_yongdong` 13, `ganyu_shuanglei` 7, `keqing_shuanghuo` 7, `nvpu_shuangyan_era` 4, `keqing_jihua` 2.
- By seed: 1 -> 5, 2 -> 6, 3 -> 9, 4 -> 5, 5 -> 8. No seed-specific artefact.

### 2.2 Stage x deck (count of long games, n/5 seeds)

| stage | ganyu_shuanglei | keqing_jihua | keqing_shuanghuo | linghua_yongdong | nvpu_shuangyan_era |
|---|---|---|---|---|---|
| blanche | 3 | 0 | 1 | 3 | 1 |
| timaeus | 0 | 0 | 0 | 3 | 0 |
| ellin | 2 | 0 | 0 | 0 | 0 |
| buyun | 2 | 2 | 0 | 3 | 0 |
| shitou | 0 | 0 | 0 | 0 | 1 |
| uncletian | 0 | 0 | 3 | 4 | 0 |
| lan | 0 | 0 | 3 | 0 | 2 |

The interaction is **deck element vs enemy ward element**, not a deck being globally slow: `linghua_yongdong` is Cryo/Hydro and struggles only on the Cryo-Ward stage (`buyun`, `uncletian`); `keqing_shuanghuo` is Pyro/Electro and struggles only on the Pyro-Ward stages (`lan`, `uncletian`); `ganyu_shuanglei`/`nvpu_shuangyan_era` are the decks whose elements fare best against the wards, and their long games are mostly the 26-30 HP stages (plus 2 class-a `lan` games for `nvpu`).

### 2.3 Stage summary

| stage | NPC lineup (HP x count) | total HP | long >=10 | mean rounds (all 25) | class a | class b |
|---|---|---|---|---|---|---|
| blanche | blazing axe x2 (10), iceshield wall x1 (10) | 30 | 8/25 | 8.5 | 8 | 0 |
| timaeus | cryo shooter (6), iceshield wall (10), blazing axe (10) | 26 | 3/25 | 6.9 | 3 | 0 |
| ellin | cryo shooter (6), blazing axe (10), electro slime (6) | 22 | 2/25 | 6.3 | 2 | 0 |
| buyun | anemo samachurl (6), cryo abyss mage (6), electro slime (6) | 18 | 7/25 | 8.2 | 2 | 5 |
| shitou | crackling axe (10), cryo shooter (6), blazing axe (10) | 26 | 1/25 | 6.7 | 1 | 0 |
| uncletian | cryo abyss mage (6), pyro abyss mage (6) | 12 | 7/25 | 7.5 | 0 | 7 |
| lan | dendro samachurl (6), pyro abyss mage (6), electro shooter (6) | 18 | 5/25 | 7.3 | 2 | 3 |

Note `uncletian`: only 12 HP, yet 7/25 games reach 10+ — the clearest proof that HP is not the driver; the two wards are.

### 2.4 Per-game trace summary (all 33)

`P0/P1 final HP` lists the three (or two) character slots in lineup order; `dmg` = total effective damage **dealt** by each side; `last2` = damage dealt in the last two *played* rounds; `ends` = `action_declare_end` decisions.

| # | stage | deck | seed | rounds | winner | P0 final HP | P1 final HP | dmg P0/P1 | last2 P0/P1 | ends P0/P1 | class |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | buyun | keqing_jihua | 5 | 15 | None | 3 0 4 | 0 0 3 | 15/25 | [0,0]/[1,2] | 14/14 | b |
| 2 | buyun | linghua_yongdong | 3 | 15 | None | 2 3 10 | 0 2 6 | 10/30 | [0,4]/[0,4] | 14/14 | b |
| 3 | lan | keqing_shuanghuo | 3 | 15 | None | 7 0 10 | 0 6 6 | 6/27 | [0,0]/[8,0] | 14/14 | b |
| 4 | uncletian | keqing_shuanghuo | 3 | 15 | None | 6 0 10 | 0 6 | 6/27 | [0,0]/[8,0] | 14/14 | b |
| 5 | uncletian | linghua_yongdong | 3 | 15 | None | 2 3 10 | 2 6 | 4/29 | [0,4]/[0,4] | 14/14 | b |
| 6 | buyun | linghua_yongdong | 2 | 14 | 1 | 0 0 0 | 0 2 6 | 10/38 | [2,2]/[0,4] | 14/13 | b |
| 7 | lan | keqing_shuanghuo | 5 | 14 | 0 | 3 4 4 | 0 0 0 | 18/26 | [5,7]/[4,1] | 13/14 | b |
| 8 | timaeus | linghua_yongdong | 4 | 13 | 0 | 0 0 2 | 0 0 0 | 26/32 | [2,1]/[2,0] | 12/12 | a |
| 9 | uncletian | keqing_shuanghuo | 5 | 13 | 0 | 4 4 9 | 0 0 | 12/23 | [0,6]/[1,1] | 12/13 | b |
| 10 | blanche | linghua_yongdong | 3 | 12 | 0 | 3 4 2 | 0 0 0 | 30/31 | [2,6]/[4,4] | 11/11 | a |
| 11 | ellin | ganyu_shuanglei | 1 | 12 | 1 | 0 0 0 | 0 0 1 | 21/34 | [2,0]/[2,1] | 11/11 | a |
| 12 | lan | keqing_shuanghuo | 1 | 12 | 0 | 0 0 3 | 0 0 0 | 18/30 | [6,3]/[3,2] | 11/11 | b |
| 13 | uncletian | keqing_shuanghuo | 2 | 12 | 0 | 4 4 6 | 0 0 | 12/24 | [5,1]/[3,1] | 11/11 | b |
| 14 | blanche | linghua_yongdong | 2 | 11 | 0 | 0 2 2 | 0 0 0 | 30/29 | [4,2]/[2,2] | 10/10 | a |
| 15 | buyun | ganyu_shuanglei | 3 | 11 | 0 | 5 4 10 | 0 0 0 | 18/15 | [1,5]/[1,1] | 11/11 | b |
| 16 | buyun | keqing_jihua | 3 | 11 | 0 | 0 1 0 | 0 0 0 | 18/31 | [0,3]/[5,5] | 10/10 | a |
| 17 | buyun | linghua_yongdong | 5 | 11 | 0 | 2 6 10 | 0 0 0 | 18/23 | [3,6]/[1,0] | 10/10 | b |
| 18 | ellin | ganyu_shuanglei | 2 | 11 | 0 | 0 1 1 | 0 0 0 | 22/32 | [2,1]/[1,1] | 10/10 | a |
| 19 | timaeus | linghua_yongdong | 1 | 11 | 0 | 3 4 5 | 0 0 0 | 26/25 | [2,1]/[2,0] | 10/10 | a |
| 20 | timaeus | linghua_yongdong | 2 | 11 | 1 | 0 0 0 | 0 0 2 | 24/32 | [7,1]/[3,2] | 10/10 | a |
| 21 | uncletian | linghua_yongdong | 1 | 11 | 0 | 4 4 7 | 0 0 | 12/18 | [5,1]/[2,0] | 10/10 | b |
| 22 | uncletian | linghua_yongdong | 4 | 11 | 0 | 0 5 10 | 0 0 | 12/24 | [2,6]/[3,0] | 10/11 | b |
| 23 | blanche | ganyu_shuanglei | 2 | 10 | 0 | 0 0 5 | 0 0 0 | 30/29 | [0,2]/[3,4] | 9/9 | a |
| 24 | blanche | ganyu_shuanglei | 3 | 10 | 0 | 3 0 2 | 0 0 0 | 30/29 | [2,1]/[5,2] | 9/9 | a |
| 25 | blanche | ganyu_shuanglei | 5 | 10 | 0 | 2 3 2 | 0 0 0 | 30/27 | [0,2]/[3,2] | 9/9 | a |
| 26 | blanche | keqing_shuanghuo | 3 | 10 | 0 | 0 5 2 | 0 0 0 | 30/29 | [1,6]/[4,0] | 9/9 | a |
| 27 | blanche | linghua_yongdong | 4 | 10 | 0 | 3 3 2 | 0 0 0 | 30/26 | [3,3]/[2,0] | 9/10 | a |
| 28 | blanche | nvpu_shuangyan_era | 4 | 10 | 0 | 3 4 1 | 0 0 0 | 30/30 | [1,9]/[0,0] | 9/10 | a |
| 29 | buyun | ganyu_shuanglei | 5 | 10 | 0 | 2 1 1 | 0 0 0 | 18/30 | [4,1]/[5,2] | 9/9 | a |
| 30 | lan | nvpu_shuangyan_era | 4 | 10 | 0 | 4 4 6 | 0 0 0 | 18/24 | [2,1]/[4,0] | 9/9 | a |
| 31 | lan | nvpu_shuangyan_era | 5 | 10 | 0 | 2 2 2 | 0 0 0 | 18/32 | [2,1]/[8,0] | 9/9 | a |
| 32 | shitou | nvpu_shuangyan_era | 1 | 10 | 0 | 3 3 6 | 0 0 0 | 26/26 | [0,4]/[3,1] | 9/9 | a |
| 33 | uncletian | linghua_yongdong | 5 | 10 | 0 | 2 4 8 | 0 0 | 12/23 | [2,6]/[3,2] | 9/9 | b |

### 2.5 Classification

| class | count | members | evidence |
|---|---|---|---|
| **a** genuine long game (both sides trading damage, decided normally) | **18** | 8x blanche, 3x timaeus, 2x ellin, 1x shitou, 2x buyun, 2x lan (see table) | No side had a >=4-round zero-damage streak; both sides used skills in >=90% of rounds. Winner = elimination (loser at 0 alive). 5 are close (winner left with 1 character), 13 are 2-3 character wins. |
| **b** one side unable to damage (ward / immunity / damage reduction) | **15** | all `buyun`/`lan`/`uncletian` except 4; the 5 draws are in this class | At least one side attacked in >=60% of rounds but dealt **0 effective damage for >=4 consecutive rounds**. 14 cases = player locked by an NPC ward/immunity; 1 case (`buyun/ganyu_shuanglei/3`) = NPC locked by the player's `Ice Lotus` (-1 per hit) + heals. |
| **c** deck-out / no win condition | **0** | — | No game was decided or stalled by deck-out. `drawCardsPlain` makes empty-pile draws a no-op, and two stalled draws still had legal skills with 0 cards. Hand 0 / pile 0 appears at the end of 5 games (including wins), always as a consequence. |
| **d** policy non-progress (passing / support loop / never ends) | **0** | — | The expert used at least one skill in every played round of every game (worst case: 11 of 15 rounds for the NPC in `buyun/keqing_jihua/5`; the player is 14-15 of 15). `declare_end` was chosen once per round by both sides in every game (9-14 times; >=90% of rounds). There is no "keep playing supports" loop. |
| **e** engine / rule anomaly | **0** | — | Alive counts match winners in all 33 (`winner=0` => P1 alive 0; `winner=1` => P0 alive 0; `null` => both >0). No game continued after an elimination, no game ended before round 15 without a winner. |

**By stage:** `blanche` 8a, `timaeus` 3a, `ellin` 2a, `shitou` 1a, `buyun` 2a + 5b, `lan` 2a + 3b, `uncletian` 0a + 7b.

**By stage family:** all 14 high-HP-stage long games are class (a); all 15 class-(b) games are on the three ward stages. The two families do not overlap.

---

## 3. Common mechanism

### 3.1 Is it the NPC policy?

No for the draws and no for the long game length. `NpcPolicy` (`npc/policy.py`) always picks the highest-damage affordable skill (`_choose_action` -> `_select_skill` -> `_max_damage`, lines 97-159) and only declares end when no skill is legal (`_declare_end`, lines 164-168). It never plays cards, never switches, never heals, never passes: the traces show `supports=[]` for P1 in every game and P1 using skills in at least 73% of the played rounds (worst: 11/15). Its damage output is modest (1-2 skills/round from the 8-dice economy), and its 1-damage normals are nullified by the player's -1 mitigation (`Ice Lotus`, `Stealth`) — that is why the three NPC wins took 11-14 rounds, but it is not what makes the player slow.

### 3.2 Is it the expert refusing to attack / spamming supports?

No. The expert used at least one attack skill in every played round; `declare_end` was chosen once per round. It does spend the first `setup_rounds=2` rounds on supports/equipment (`_rule_play_setup_support`, `agents/scripted/agent.py:440-467`), but that is at most 2 rounds and cannot explain a 10-15 round game. The problem is not *whether* it attacks, it is *with which element* and *from which character*.

### 3.3 Is it a specific card (healing food / shields) spammed by both sides?

No. The NPC never plays a card. The player heals 0-8 HP per game (food + `321005`/`322009` supports), which extends some games but cannot be the main driver: the 14 high-HP games are decided by elimination, not by healing (12 player wins, 2 NPC wins). The card-level culprits are the **enemy-side damage soaks**:

- `131031 Cryo Ward` / `133011 Pyro Ward`: 0 damage until 2 reactions;
- `134061 Elemental Lifeform: Electro`: 0 Electro damage;
- `131011 Hoarfrost Greatshield`: -2 damage per hit;
- plus player-side `111012 Ice Lotus` and `123012 Stealth` (-1 per hit), which nullify the NPC's small hits.

### 3.4 So why 10+ rounds?

Two mechanisms, cleanly separated by stage:

**(A) Ward/immunity stalls — 19 games on `buyun`/`lan`/`uncletian`, 15 of them class (b).**
The NPC's second/third character is immune to all damage (Cryo/Pyro Ward) or to the deck's element (Electro Slime). Breaking a ward needs **two elemental reactions on that character**; the expert's active carry is the same element as the ward, and same-element attacks do not react. The expert keeps attacking for 0 for 4-14 rounds and only switches to the counter-element character under HP pressure (or never). Examples: `lan/keqing_shuanghuo/3` has Keqing (Electro) at 10/10 HP for 15 rounds while the Pyro Agent attacks the Pyro Ward for 0; `buyun/linghua_yongdong/3` has Xingqiu (Hydro) enter at round 13 and break the ward at round 14.

**(B) High-HP + damage-soak arithmetic — 14 games on `blanche` (30 HP), `timaeus`/`shitou` (26), `ellin` (22).**
Three 10-HP characters (or two 10-HP + one 6-HP) at ~2.5-3.5 effective damage per round means 8-12 rounds. `blanche` is the extreme: P0 deals exactly 30 damage (the full NPC HP) in every one of its 8 long games, and P1 deals 26-31 — real trades, not a stall. The `Hoarfrost Greatshield` (-2/hit) makes small hits worthless and pushes the required number of attacks up further.

The remaining 9 stages (18-25 HP, no ward) never reach 10 rounds. That is the strongest evidence that the tail is structural, not random.

### 3.5 Exact policy gap (for the ward stalls)

The expert has no rule of the form "my damage is being absorbed -> switch to the character whose element reacts". The existing switch rules all miss:

1. `_rule_switch_to_preferred` (`agents/scripted/agent.py:531-534`) returns `None` immediately when the active character is the opener or in `carry[:2]`. For era decks, the opener *is* the carry (`scripts/era_decks.py:868-870` sets `opener=names[0]`, `carry=names[:2]`), so the carry is never swapped out while healthy. This is why Ayaka/the Pyro Agent/Keqing stay active for 14 rounds against a ward that blocks them.
2. `_rule_switch_low_hp` (`:309-318`) only fires when the active is at <= `switch_hp_threshold = 4` HP and the target has at least `active_hp + 2` (`_best_switch_target`, `:892-922`). In `buyun/keqing_jihua/5`, Fischl (the only physical attacker, 3 HP) is rejected because she is not 2 HP above Keqing (4 HP); in `buyun/linghua_yongdong/3`, Xingqiu (the only Hydro character, 3 HP) is rejected for the same reason. Element is not part of the key at all.
3. `_rule_reaction_setup_switch` (`:419-438`) is the only element-aware switch, and it is disabled after round 5 when a skill is legal (`:423-424`). Worse, for the matrix decks it can never fire: `_reaction_switch_targets` (`:1796-1810`) switches on `profile.rule_config.style`, and `scripts/era_decks.py:872` sets `style="era"`, which falls through to `return ((), ())`.
4. `_choose_active` (`:96-114`) ranks by profile order and HP, not by counter-element. After Bennett died in `lan/keqing_shuanghuo/3`, the forced re-choose picked the Pyro opener again instead of the Electro Keqing who could break the Pyro Ward.

This is a policy/positioning failure, not a damage-rule bug: in `lan/keqing_shuanghuo/3`, one switch to Keqing (10/10 HP, never used) would have given the deck its only route to break the ward (Overload), instead of 0 damage for 12 rounds.

### 3.6 Is the 15 cap the engine default, and is it configurable?

Yes. `getDefaultGameConfig()` sets `maxRoundsCount: 15` (`build/engine/packages/core/src/base/state.ts:102`). `envs.match.run_match` only sets `ATTR_STATE_CONFIG_RANDOM_SEED`, the no-shuffle flags and the data version, so every matrix game uses 15. The cap is configurable through `gitcg.low_level.ATTR_STATE_CONFIG_MAX_ROUNDS_COUNT` (attr 105); `scripts/probe_stall.py --check-round-cap` shows the default config and an attr-21 config (`maxRoundsCount: 21`). At the cap the engine does **not** apply a tiebreak: `game.ts:983` returns `gameEnd`, and `gotWinner(state.winner)` is called with the unchanged null.

Raising the cap would not "fix" normal play: 4 of the 5 draws are games the player is losing on the board (one or two characters left at low HP, P1 chipping 1-8 per round); they would become ordinary losses in rounds 16-19, not wins. The fifth pair (`lan`/`uncletian` keqing) is a true frozen endgame — ordinary attacks are 0 both ways, so only the mage's rechargeable burst can make progress, and an unbounded cap could produce an endless game.

---

## 4. Verdict

A game reaches the 15-round cap because **the engine's only win condition is "eliminate all three characters", and the player cannot eliminate the last NPC character**: that character is either immune to the player's remaining damage element (Electro Slime vs an Electro-only board) or protected by a Cryo/Pyro Ward that only two *elemental reactions* can remove, while the expert keeps attacking with the ward's own element and never switches to the Hydro/Electro/physical character that could react. Neither side stops acting — the expert attacks every round and both sides declare end once per round — and deck-out is a no-op in this engine, so the board simply freezes until the cap; the engine then sets `gameEnd` with `winner = null` instead of a tiebreak. The 10+ round tail is the same phenomenon in its milder form (19 games on the three ward stages) plus a second, benign cause: the four high-HP stages (22-30 HP; `blanche`/`timaeus` also carry a -2/hit greatshield) need 8-12 rounds of normal trading, which no policy can compress to 5. In short: **not a pass loop, not a bug, not a deck-out rule — an element-match / policy-positioning problem on 3 stages, plus a benchmark whose 22-30 HP stages are arithmetically incompatible with a 5-round target.**

---

## 5. Recommendations

**Benchmark (no engine change):**

1. Set `win_within_rounds` per stage from the observed distribution instead of `n=5` everywhere. Currently all 16 stages require a 5-round win (`configs/npc/npc_stages_mond_liyue.json`), but only 156/400 games (39%) finish by round 5; the median is 6 and `blanche`'s mean is 8.5. A 3-star rate dominated by an unreachable round target is not measuring deck strength.
2. Report the `rounds >= 10` rate (and the draw rate) as a benchmark-health metric per stage. The healthy band in this matrix is 3-9 rounds; the tail is concentrated in 7 of 16 stages.
3. Either exclude `buyun`/`lan`/`uncletian` from round-efficiency comparisons or score them on a separate "ward-breaking" axis; their mean rounds (7.3-8.2) are structurally higher than the 13 non-ward stages (3.6-6.9, except `blanche` at 8.5).
4. If the goal is "close in 5-7", restrict the matrix to stages with <=22 total NPC HP and no ward/immunity, or normalize enemy HP. Do not lower the 15-round cap: it would turn slow wins into draws.

**Policy (requires an `agents/` change and a `docs/PROVENANCE.md` §3 entry):**

5. Add an element-aware switch before `_rule_use_elemental_skill` in `_choose_action` (`agents/scripted/agent.py:281-297`): if the opponent's active character has an aura/ward matching the active character's damage element and the last round produced 0 damage, switch to the bench character whose element reacts with it (Hydro vs Cryo Ward, Electro vs Pyro Ward, Physical/non-Electro vs Electro Slime).
6. Remove the "active is opener/carry -> never switch" early return (`:532-534`) at least when the active's damage is being nullified.
7. Give `_rule_switch_low_hp` (`:309-318`) an element/counter overrides so it can switch to a low-HP counter character (Fischl at 3, Xingqiu at 3) instead of requiring `+2` HP.
8. Wire `style="era"` into `_reaction_switch_targets` (`:1796-1810`); right now the only element-aware switch is dead for exactly the decks in this matrix.

**Engine / draw policy (only if a draw must be impossible):**

9. Add a tiebreak at `game.ts:983` (e.g. compare alive characters, then total HP) instead of returning `gameEnd` with `winner=null`. This is an engine rule change and must go through `engine-overlay/` + `docs/PROVENANCE.md` §3. Changing `ATTR_STATE_CONFIG_MAX_ROUNDS_COUNT` only moves the problem.

---

## 6. What I could not determine from the trace

- Why the NPC takes no skill action in some rounds (e.g. `buyun/keqing_jihua/5`, P1 attacks in 11 of 15 rounds). The `reroll_dice` decisions and final dice are recorded, but the engine does not expose the NPC's per-round dice budget before spending; this is an economy detail, not a cause of the draws (the player is the stuck side in all five).
- The exact trigger that consumed the second Cryo Ward usage in `buyun/linghua_yongdong/3` at round 13-14. The trace shows one Hydro reaction (`12022`, Freeze) and the ward gone by round 14; the second consumption is consistent with the ward's `usage 2` + the engine's reaction bookkeeping, but the mutation stream does not label ward usages, so I did not prove which reaction was the second.
- Whether a fixed expert policy (element-aware switch) would actually win these five. That needs an `agents/` change and a re-run; I did not modify the upstream agent layer. The board states say the winning line existed (Keqing at 10/10 in the two Pyro-Ward draws; Fischl's 2-physical normal vs a 3-HP Electro Slime), but I did not execute the counterfactual.

## Appendix: reproduction

```bash
PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
cd /home/ubuntuaaa/projects/gitcg-skills

# the five draws, full mutation trace
"$PY" -m scripts.probe_stall --json-out data/runs/stall_probe.jsonl

# all 33 games with rounds >= 10 (selection straight from data/runs/era_matrix.jsonl)
"$PY" -m scripts.probe_stall --min-rounds 10 --no-mutations \
      --json-out data/runs/stall_probe_10plus.jsonl

# engine round-cap check (default 15, attr 105 -> 21)
"$PY" -m scripts.probe_stall --check-round-cap
```

`scripts/probe_stall.py` replays one `(stage, deck, seed)` through `envs.match.run_match` with `record_decisions=True`, `record_events=True`, `record_final_state=True`; a `PolicyPlayer` subclass captures the public mutation stream (damage values, skills, declared-end flags, round steps, winner) and a policy wrapper snapshots the acting player's view at every decision. It prints the per-round damage/HP table and writes one JSON row per game.
