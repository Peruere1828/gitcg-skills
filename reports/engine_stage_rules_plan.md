# Engine stage-rule support cards — implementation plan

**Date:** 2026-11-02  
**Scope:** PvE Adventure Challenge special rules. Primary targets: `blanche`, `timaeus`, `chefmao`, `buyun`. The same mechanism covers the other stages with starting supports.  
**Status:** design + cheap PoC only. No engine rebuild, no changes to `build/engine`, no changes to installed `site-packages`.

**Calibration note (do not change):** the user hand-verified that all stages are 3-star-able within 5 rounds with the Keqing double-pyro deck. The `win_within_rounds = 5` threshold is therefore confirmed achievable and must **not** be relaxed or re-calibrated in this work.

---

## 0. Supplied rule data — important reconciliation

A new untracked file, `configs/npc/special_rules.json` (generated 2026-11-02), is the authoritative rule data for all 16 stages. It supersedes several strings in the older `configs/npc/*.json` files and in the earlier task description.

### 0.1 Starting supports in the supplied data

| Stage | Starting support | Card id | `removable` in data |
|---|---|---|---|
| `timmie` | 提米 | 322007 | true (unverified) |
| `wagner` | 瓦格纳 | 322004 | true (unverified) |
| `timaeus` | 蒂玛乌斯 | **322003** | true (unverified) |
| `ellin` | 艾琳 | 322010 | true (unverified) |
| `buyun` | 群玉阁 | **321003** | true (unverified) |
| `liusu` | 刘苏 | 322012 | true (unverified) |
| `irontonguetian` | 田铁嘴 | 322011 | true (unverified) |
| `chefmao` | 卯师傅 | **322005** | true (unverified) |
| `uncletian` | 璃月港口 | 321001 | true (unverified) |
| `lan` | 凯瑟琳 | 322002 | true (unverified) |

All ten are real, playable support cards in the current data. The supplied file explicitly says `removable_verified: false` for this batch: all are inferred to be ordinary removable supports, and no in-game overflow test was done. The engine must still support `removable: false` because the user confirmed that some rule cards (e.g. **地脉镇石 / Ley Line Monolith**) are irremovable; that card is not in the current asset dump, so it needs a future custom definition.

### 0.2 Effects in the supplied data

| Stage | Effect `kind` | Details |
|---|---|---|
| `blanche` | `initial_energy` | +2, target `friendly_all`, timing `battle_start` |
| `patton` | `element_application` | Hydro, target `enemy_active`, timing `action_phase_start` |
| `liusu` | `element_application` | Hydro, target `both_active`, timing `action_phase_start` |
| `shitou` | `element_application` | Hydro, target `both_active`, timing `action_phase_start` |
| `chefmao` | `element_application` | Pyro, target `both_active`, timing `action_phase_start` |
| `wagner` | `weapon_free_equip` | via support 322004, once per round, uses 锻造原胚 |
| `timaeus` | `artifact_free_equip` | via support 322003, once per round, uses 合成材料 |

`timaeus` is the biggest reconciliation point: the supplied file lists `artifact_cards_free` only under `legacy_special_rules` and explicitly notes that it was an approximation. The real rule is “Timaeus is in the support zone; its normal card effect lets you pay materials to equip an artifact for free once per round”, not “all artifacts cost 0 unconditionally”. The plan below therefore **does not** zero artifact costs by default; it injects the real 322003 and lets its card skills run. The explicit `artifact_cards_free` hook from the earlier draft is kept as an optional effect key only if the user confirms that simplification.

`blanche` has **no starting support** in the supplied data — only the `initial_energy` effect. We therefore do not invent a Blanche support card. If the user wants a visible rule card for Blanche, that is a separate synthetic-card decision.

`chefmao` injects the **real 322005**, so its normal food-support skills remain active, and the Pyro rule is added as a stage-rule effect. We do not use a custom Chef Mao variant that would drop the food effects.

---

## 1. TL;DR / recommendation

- Model the rule as a **real support entity placed into `player.supports` at game start**. The placement uses the **normal support-play path** (`setSupportPlayAction` → `insertEntityOnStage`) so the card's `on staged`/continuous triggers work; see §2.1.1 and §3.4. It is not part of the 30-card deck, never enters `initialPile`/`pile`/`hands` after placement, and is exposed through the existing `support` array (`packages/core/src/io.ts:640-669`, line 661).
- **The rule card counts toward the support cap (4).** It is a normal entry in `player.supports`; with a rule card present the player has at most 3 more supports. There is no cap-exemption special case.
- **`removable` is a per-entity flag** (default `true`). `getPlayCardTargetCandidates` (`utils.ts:811-828`) returns only `removable !== false` supports when the zone is full; if all supports are irremovable the list is empty and the support card becomes `NO_TARGET`. The cap count itself is unchanged.
- **Irremovable supports also need disposal guards** (`SkillContext.dispose` and the `removeEntity` mutation), otherwise card effects can still delete them.
- **Do not clone definitions in memory.** State JSON stores definitions as `{ $$: "entities", id }` (`log.ts:32-91`, `169-172`); a runtime clone loses its custom skills after `State.fromJson`/replay. For the current data, use the **real card definitions**; future custom rule cards (Ley Line Monolith, optional Blanche card) must be data-backed.
- **Data source:** `configs/npc/special_rules.json` as supplied. Its normalized per-stage view is `{stage, support_card_id, removable, effects:[...]}`; for the current 16 stages each has at most one starting support. The loader also accepts multiple initial supports per stage.
- **Effect implementation:**
  - `initial_support`: place the real card via the normal support-play path.
  - `initial_energy`: apply +2 at init (capped at max energy).
  - `element_application` at `action_phase_start`: generic stage-rule triggered skill, including `chefmao` Pyro, `liusu`/`shitou` Hydro both actives, and `patton` Hydro enemy active.
  - `artifact_free_equip` / `weapon_free_equip`: the real Timaeus/Wagner card skills handle these; no core cost hook.
  - Optional `artifact_cards_free`: keep the core cost hook only if the user overrides the supplied data.
- **Phase 1 is worth doing now.** The supplied data makes the starting-support placement generic across 10 stages, so the work is one loader + one `Game.start` placement helper + the cap/replacement flag. Phase 1 (all starting supports + Blanche energy + removable machinery) is roughly **2–3 dev-days**. Phase 2 (generic element application, 4 stages) is roughly **0.5–1 dev-day**. The old “artifact cost 0” work is not needed for the supplied data and should not be done by default.

---

## 2. Current engine facts

### 2.1 Initial state and supports

`Game.createInitialState` (`packages/core/src/game.ts:280-321`) builds both players via `initPlayerState` (`game.ts:126-215`). `initPlayerState`:

- builds `initialPile` from `deck.cards` and `pile` from it;
- builds `characters` from `deck.characters`, variables from `definition.varConfigs`;
- returns `supports: []` at `game.ts:203`.

There is no cap check at init and no `onEnter` event for initial entities. A **direct-push fallback** is therefore possible: construct an `EntityState` with `{ [StateSymbol]: "entity", id: idIter.id, definition, variables, attachments: [], stageRule: { stage, effects }, removable }` and put it in `supports`. It counts toward the cap, but it skips `on staged`; the preferred normal-path placement is in §2.1.1/§3.4. `IdIter` is the same allocator as characters and pile cards (`game.ts:109-124`).

The support is not in `initialPile`, so it is never shuffled or drawn. This satisfies “not part of the 30-card deck”.

`exposeState` already exposes supports: `io.ts:640-669`, line 661; the UI consumes them in `packages/web-ui-core/src/components/Chessboard.tsx:585-590`. No protobuf change is needed.

### 2.1.1 Can injection reuse the normal support-creation path?

**Yes, but not from `createInitialState`.** The normal path is the card play skill:

- `setSupportPlayAction` (`packages/core/src/gts/vm_impl/card.ts:158-181`) captures `stagedOperations` from the card's `on staged` triggers, moves the card into `supports` via `c.moveEntity(..., "createSupport")`, then runs those staged operations.
- `c.moveEntity` calls `StateMutator.insertEntityOnStage` (`runtime/skill_context.ts:1245-1263`, `mutator.ts:989-1170`), which enforces the support cap and returns `onEnter` events.
- For supports/equipment, GTS explicitly says to use `on staged` instead of `selfEnter` (`gts/vm_impl/entity.ts:1103`). Timaeus 322003 and Wagner 322004 both use `on staged` for their one-time “draw an artifact/weapon” text (`ally.gts:104-111`, `142-150`).

`createInitialState` is a pure static function and has no `StateMutator`/`SkillExecutor`, so it cannot run the play skill or `on staged`. The clean place is `Game.start()` (`game.ts:404-426`), **before** the first `notifyAndPause` and before `initHands`:

```ts
async start() {
  // ... finishResolvers setup ...
  await this.placeInitialSupports();   // new: normal support-play path
  runWithAsyncContext(... phase loop ...);
}
```

`placeInitialSupport` creates the card in the hand (`mutator.createHandCard`), builds a `PlayCardSkillInfo` with `playSkillOfCard(definition)`, and calls `this.executeSkill(skillInfo, { targets: [] })`. That executes `setSupportPlayAction`, moves the card into `supports`, and runs `on staged`. It bypasses cost because it is not an action, which is exactly what a free initial placement needs. The spec list must therefore be stored on `GameState` (e.g. `initialSupportSpecs`) by `createInitialState`; `Game.start` consumes it and skips a spec whose support is already present (state-from-JSON/replay).

**Cheaper fallback:** construct the `EntityState` directly in `initPlayerState` (push to `supports`). Continuous/triggered support skills (Timaeus/Wagner `on deductAllDiceCard`, Chef Mao food, Liu Su switch) still work because `allSkills` includes all entities, but `on staged` one-time effects (Timaeus/Wagner draw) do not. For the stage rules themselves, the free-equip effect is continuous, so the fallback is functionally sufficient; it is not fully card-faithful. Use the normal path if “the card's own triggers work” is a hard requirement.

**Recommendation:** use the normal path (`Game.start`) for phase 1. It costs about 0.5–1 extra day but avoids a known card-behaviour gap and matches the user's expectation.

### 2.2 Where the support cap is enforced and where the replace list is built

| File:line | Code | Meaning |
|---|---|---|
| `packages/core/src/utils.ts:811-828` | `getPlayCardTargetCandidates` | **the “choose a support to replace” list** when a support card is played at cap |
| `packages/core/src/mutator.ts:1080-1085` | `insertEntityOnStage` | rejects insertion when `supports.length >= maxSupportsCount` |
| `packages/core/src/runtime/skill_context.ts:802-806` | `remainingSupportCount` | free support slots for card effects |
| `packages/core/src/base/state.ts:65` | `GameConfig.maxSupportsCount` | default **4** (`state.ts:104`) |

Supports are stored as an array (`base/state.ts:183`); removal is by entity id/target, not index. Support play disposes the chosen target and moves itself in (`gts/vm_impl/card.ts:158-181`). `shouldEnterOverride` returns `null` for supports (`utils.ts:614-625`), so two supports with the same definition id do not merge.

**Corrected semantics:** the rule card counts. Only the replacement list changes:

```ts
export function isIrremovableSupport(e: AnyState): boolean {
  return e.definition.type === "support" && e.removable === false;
}

export function getPlayCardTargetCandidates(...) {
  const player = state.players[who];
  if (
    card.definition.type === "support" &&
    player.supports.length >= state.config.maxSupportsCount
  ) {
    const replaceable = player.supports.filter((s) => !isIrremovableSupport(s));
    return replaceable.map((support) => ({ targets: [support] }));
  }
  return (0, skillInfo.definition.initiativeSkillConfig.getTarget)(state, skillInfo);
}
```

- With 1 irremovable rule card + 3 normal supports (total 4), another support returns the 3 normal supports; the rule card cannot be chosen.
- With 4 irremovable supports, the list is empty; `Game.availableActions` marks the card `NO_TARGET` (`game.ts:1066-1082`).
- `insertEntityOnStage` and `remainingSupportCount` remain unchanged: the rule card occupies a slot.
- For removable rule cards (`buyun`/Jade Chamber, and all current 16 stages per the supplied data), the rule card appears in the replacement list and behaves like a normal support.

**Initial placement and the cap:** the normal placement path inserts the support through `insertEntityOnStage`, so `supports.length` becomes 1 and every count check sees it. Add a defensive `supports.length <= maxSupportsCount` check; a stage with too many rule cards fails loudly instead of creating an over-cap state.

**Direct removal guards:** card effects go through `SkillContext.dispose` (`runtime/skill_context.ts:1463-1495`); add an early return for `support && removable === false` before `onDispose`. Add a last-resort skip in the `removeEntity` mutation (`base/mutation.ts:438-445`). `moveEntity` uses the internal `removeEntity` helper (`utils.ts:512`) and is not covered; rule cards should not be moved.

### 2.3 Card cost (only relevant for the optional `artifact_cards_free` override)

`applyAttachmentModifications` (`utils.ts:951-1034`) computes `requiredCost`; `diceCostSizeOfCard` (`utils.ts:876-890`) has a fast path used by effects/queries. `Game.availableActions` (`game.ts:1055-1065`) uses the former, payment uses `actionInfo.cost` (`game.ts:757`, `806-825`).

The supplied data uses the real Timaeus card’s material-based free-equip effect, so **no cost override is needed by default**. If the user explicitly wants unconditional 0-cost artifacts, add an `artifact_cards_free` effect key and patch both functions; this is isolated and optional.

### 2.4 `CreateParam` attribute flow — adding one attribute

1. `gitcg/create_param.py:73-74` — `CreateParam.set_attr`.
2. `gitcg/low_level.py:121-132` — int or string C entry point.
3. `packages/cbinding/src/api.cpp:78-92` / `96-110` — string/int setter.
4. `packages/cbinding/src/state_createparam.cpp:21-50` — converts and calls JS `setAttribute`.
5. `packages/cbinding/js/main.ts:85-149` — `GameCreateParameter.setAttribute`.
6. `packages/cbinding/js/main.ts:151-167` — `createState()` passes to `InternalGame.createInitialState`.

To add `GITCG_ATTR_CREATEPARAM_STAGE_RULE_SUPPORTS = 2002`:

- **`packages/cbinding/js/constant.ts:26-27`** — add the macro. `prepare.ts:99-122` regenerates `packages/cbinding/include/gitcg/gitcg.h` during `pnpm build`; **no C++ change**.
- **`packages/cbinding/js/main.ts:85-149`** — add a case; the string path already exists:
  ```ts
  case c.GITCG_ATTR_CREATEPARAM_STAGE_RULE_SUPPORTS: {
    if (typeof value !== "string") throw new Error("stage rule supports must be a JSON string");
    const parsed = JSON.parse(value);
    // { v: 1, supports: [{ who, definitionId, removable, effects, stage }],
    //   effects: [{ who, effect, supportDefinitionId?, stage }] }
    this.stageRuleSupports = parsed.supports;
    this.stageRuleEffects = parsed.effects;
    break;
  }
  ```
- **`packages/cbinding/js/main.ts:151-167`** — pass `stageRuleSupports`.
- **Python:** the new macro is **not** available as `C.GITCG_ATTR_...` in the installed wheel. `gitcg/_gitcg_cffi.py` is a precompiled ABI-mode CFFI module with a fixed `_globals` tuple; `low_level.py:53-83` reads those macros at import. Since we must not edit `site-packages`, define the numeric value locally:
  ```python
  # envs/match.py (or envs/engine_attrs.py)
  ATTR_CREATEPARAM_STAGE_RULE_SUPPORTS = 2002
  ```
  `CreateParam.set_attr(2002, json_string)` already works (`set_attr` accepts `int | str`). Do **not** add a `C.GITCG_ATTR_...` lookup to `low_level.py`; it would raise `AttributeError` on the installed wheel. If the binding is regenerated from the new header later, the constant can move to `low_level.py`.
- The C ABI is unchanged: no new symbols, only a new integer key understood by JS. A `.so` swap is safe.
- `packages/cbinding/scripts/prepare.ts` needs no edit; it is the generator.

### 2.5 Rebuild, install, rollback

Overlay discipline: files under `engine-overlay/overlay/` mirror the engine repo root and are copied into `build/engine/` by `apply-overlay.sh`; `build/engine` is gitignored.

**Incremental rebuild:**

```sh
cd ~/projects/gitcg-skills
setsid env SKIP_IMAGE=1 SKIP_MATERIALIZE=1 \
  bash engine-overlay/scripts/build-in-docker.sh 2>&1 | tee build/engine-build.log
```

- `apply-overlay.sh` copies files and calls `register-data-imports.py` for any new `.gts`.
- `build-inner.sh` runs pnpm install/build then cmake/Ninja; V8 is not recompiled because `build/engine-build` and `build/v8-cmake-src` exist.
- Previous incremental build: **29 s** (`engine-overlay/README.md:183-186`); expect **30–90 s** with core changes.
- Full build only if sources/image are missing: **19 min 16 s** for V8 (`README.md:154-166`).

**Install:**

```sh
cd ~/projects/gitcg-skills
cp -a build/artifacts/libgitcg.so build/artifacts/libgitcg.so.prev   # keep the B1 build
CONDA_ENV=gitcg-skill bash engine-overlay/scripts/install-lib.sh
sha256sum build/artifacts/libgitcg.so \
  /home/ubuntuaaa/miniconda3/envs/gitcg-skill/lib/python3.12/site-packages/gitcg/libgitcg.so
```

`install-lib.sh` defaults to `CONDA_ENV=gitcg`; this repo is **`gitcg-skill`**.

**Rollback:**

```sh
# preferred: previous B1 overlay build
cp -a build/artifacts/libgitcg.so.prev \
  /home/ubuntuaaa/miniconda3/envs/gitcg-skill/lib/python3.12/site-packages/gitcg/libgitcg.so

# last resort: wheel-original .so (loses the 17 NPC enemy .gts)
CONDA_ENV=gitcg-skill bash engine-overlay/scripts/install-lib.sh --restore
```

Current artefact: `sha256 = 62a0ac61591c0800b950612b33844793f8c0d862252b492a8165d76b511b665a`; wheel backup `45ba6ddd…`.

---

## 3. Proposed design

### 3.1 Entity marker and `removable`

Add to `EntityState` (`base/state.ts:223-229`) and `GameState` (`base/state.ts:159-172`):

```ts
export interface StageRuleInstance {
  readonly stage: string;
  readonly effects: readonly StageRuleEffect[];
}
export interface EntityState<Ty extends EntityType = EntityType> {
  // existing fields...
  readonly stageRule?: StageRuleInstance;
  /** Defaults to true. false = cannot be replaced or disposed. */
  readonly removable?: boolean;
}
export interface GameState {
  // existing fields...
  /** Specs consumed by Game.start(); not a source of support entities. */
  readonly initialSupportSpecs?: readonly StageRuleSupportSpec[];
  /** Effects not attached to a support (Blanche/Patton/Shitou). */
  readonly initialRuleEffects?: readonly StageRuleEffectSpec[];
}
```

`CreateInitialStateConfig` (`game.ts:218-224`) gains:

```ts
export interface StageRuleSupportSpec {
  readonly who: 0 | 1;
  readonly definitionId: number;
  readonly removable: boolean;
  readonly effects: readonly StageRuleEffect[];
  readonly stage: string;
}
export interface StageRuleEffectSpec {
  readonly who: 0 | 1;
  readonly effect: StageRuleEffect;
  /** If set, active only while this support is present. */
  readonly supportDefinitionId?: number;
  readonly stage: string;
}
readonly stageRuleSupports?: readonly StageRuleSupportSpec[];
readonly stageRuleEffects?: readonly StageRuleEffectSpec[];
```

`StageRuleEffect` is a discriminated union derived from the supplied JSON:

```ts
export type StageRuleEffect =
  | { kind: "initial_energy"; value: number; target: "friendly_all"; timing: "battle_start" }
  | { kind: "element_application"; element: "pyro" | "hydro" | "cryo" | "electro" | "anemo" | "geo" | "dendro"; target: "enemy_active" | "both_active" | "friendly_active"; timing: "action_phase_start" }
  | { kind: "artifact_free_equip" | "weapon_free_equip"; /* audit only; card skill does the work */ }
  | { kind: "artifact_cards_free" }; // optional override, not used by current data
```

Unknown `kind`/`target`/`timing` values are rejected by the loader.

### 3.2 Support definitions

- For the current 16 stages, **reuse the real cards** listed in §0.1. No custom card data is needed.
- `timaeus` 322003: real card skills handle `artifact_free_equip` (materials, once per round). Do not zero costs.
- `wagner` 322004: real card skills handle `weapon_free_equip`.
- `chefmao` 322005: real food-support skills stay active; Pyro is added by the stage-rule effect.
- `buyun` 321003: real Jade Chamber, removable; it may self-dispose via its own `on actionPhase` skill, which is correct.
- `blanche`: no support card in supplied data; only `initial_energy`.
- Future irremovable cards (Ley Line Monolith), and an optional Blanche rule card if the user insists, are **data-backed custom definitions** in `engine-overlay/overlay/packages/data/src/cards/support/stage_rules.gts` with reserved ids (e.g. `390001+`), `undiscoverable`, and `removable` supplied by the data. Do not create in-memory clones: definitions are serialized by id (`log.ts:74-80`, deserialized via `data.entities.get(id)` at `169-172`).

### 3.3 Data format and loading

Authoritative input: `configs/npc/special_rules.json` as supplied. It already has:

```json
{
  "stage": "buyun",
  "initial_supports": [
    { "name": "群玉阁", "card_id": 321003, "removable": true, ... }
  ],
  "effects": []
}
```

The loader normalizes each support to `{stage, support_card_id, removable, effects}` and keeps stage-level effects separately:

```json
{
  "stage": "chefmao",
  "support_card_id": 322005,
  "removable": true,
  "effects": [
    { "kind": "element_application", "element": "pyro", "target": "both_active", "timing": "action_phase_start" }
  ]
}
```

For stages with no starting support (Blanche/Patton/Shitou), `support_card_id` is absent and the effects are emitted as `StageRuleEffectSpec` records instead.

`npc/stage_rules.py` (new) loads/validates the supplied file:

- stage must exist in `npc.spec.load_stages()`; card id must exist in the asset/card data;
- `removable` must be bool; effects must be known kinds and valid targets/timings;
- one support record per `initial_supports` entry; duplicate stage cards are allowed (all are distinct);
- stage effects are attached to the support record when a support exists (`supportDefinitionId` set), so Liusu/ChefMao effects stop if the support is removed; effects on no-support stages become global `stageRuleEffects` (Blanche/Patton/Shitou);
- `artifact_free_equip` / `weapon_free_equip` are audit-only (the card skill does the work); `initial_energy` and `element_application` are core-handled;
- unknown keys/kinds raise rather than silently doing nothing.

Keep `configs/npc/*.json` `special_rules` as display/legacy text; `configs/npc/special_rules.json` is the engine-authoritative source. `legacy_special_rules` is not read by the engine.

### 3.4 Placement at game start (counts against the cap)

`createInitialState` does **not** push support entities. It stores the validated specs on the state and applies the `initial_energy` effect to characters:

```ts
// CreateInitialStateConfig
readonly stageRuleSupports?: readonly StageRuleSupportSpec[];
readonly stageRuleEffects?: readonly StageRuleEffectSpec[];

// createInitialState
const {
  decks, data, versionBehavior = CURRENT_VERSION,
  stageRuleSupports = [], stageRuleEffects = [], ...partialConfig
} = opt;
const state: GameState = {
  // ...
  initialSupportSpecs: stageRuleSupports,
  initialRuleEffects: stageRuleEffects,
  // ...
};
applyInitialEnergy(state, stageRuleEffects); // Blanche +2, capped
return state;
```

Then `Game.start()` places them through the **normal support-play path** before the first notification/`initHands`:

```ts
private async placeInitialSupports(): Promise<void> {
  for (const spec of this.state.initialSupportSpecs ?? []) {
    const already = this.state.players[spec.who].supports.some(
      (s) => s.stageRule?.stage === spec.stage &&
             s.definition.id === spec.definitionId,
    );
    if (already) continue; // state-from-JSON / replay
    const definition = this.state.data.entities.get(spec.definitionId);
    if (!definition || definition.type !== "support") {
      throw new GiTcgDataError(`Unknown stage support ${spec.definitionId}`);
    }
    const { state: card } = this.mutator.createHandCard(spec.who, definition, {
      noOverflow: true,
      stageRule: { stage: spec.stage, effects: spec.effects },
      removable: spec.removable,
    });
    const skillDef = playSkillOfCard(definition);
    if (!skillDef) throw new GiTcgDataError(`Support ${spec.definitionId} has no play skill`);
    const skillInfo = defineSkillInfo({ caller: card, definition: skillDef });
    await this.executeSkill(skillInfo, { targets: [] });
  }
}
```

- `setSupportPlayAction` (`gts/vm_impl/card.ts:158-181`) moves the card into `supports` via `insertEntityOnStage` and runs `on staged`; this is what makes Timaeus/Wagner’s one-time draw and any other `on staged` text work.
- `insertEntityOnStage` enforces `supports.length >= maxSupportsCount`; the starting support occupies a real slot. A stage with more rule cards than the cap fails.
- The card is in `hands` only transiently during placement; it is removed by the move. It is never in `initialPile` or `pile`, and the 30-card deck is unchanged.
- `stageRule`/`removable` are passed into `createHandCard` as optional entity fields (extend `InsertHandCardOption`), so they survive the move into `supports`; no post-annotation mutation is needed.
- `initialSupportSpecs` remains on `GameState` for serialization/audit; `placeInitialSupports` skips specs whose support is already present, so replay/state-from-JSON does not duplicate.
- `initial_energy` is applied in `createInitialState` because it does not depend on the support entity; if preferred, move it to `Game.start` after placement. Either way it must happen before the first roll and be capped.

**Alternative if the placement code proves too invasive:** push the entity directly in `initPlayerState` (as in the earlier draft). Continuous support skills work, but `on staged` one-time effects do not. For the supplied stage rules (Timaeus/Wagner free-equip is continuous), this is functionally sufficient; it is not fully card-faithful.

### 3.5 Cap, replacement, removal

- `insertEntityOnStage` (`mutator.ts:1080-1085`) unchanged (`supports.length >= maxSupportsCount`).
- `remainingSupportCount` (`skill_context.ts:802-806`) unchanged.
- `getPlayCardTargetCandidates` (`utils.ts:811-828`): condition stays `supports.length >= max`; returned targets filtered by `!isIrremovableSupport`.
- Empty replacement list -> `Game.availableActions` `NO_TARGET` (`game.ts:1066-1082`).
- Removal guards: `SkillContext.dispose` early-return for `support && removable === false`; `removeEntity` mutation last-resort skip.
- `shouldEnterOverride` no change (supports never override).
- UI `getEntityPos` hard-caps at 4 (`web-ui-core/src/layout.ts:100-127`). This is correct: rule card + 3 normal supports = 4; no fifth card can exist.

### 3.6 Effect engine

Only two effects need engine code for the current data; the rest are card behavior.

1. **`initial_energy` (Blanche).** Apply at init to every character:
   ```ts
   const energyVar = definition.specialEnergy?.variableName ?? "energy";
   const maxEnergy = definition.specialEnergy?.slotSize
     ?? definition.varConfigs.maxEnergy.initialValue;
   variables[energyVar] = Math.min(maxEnergy, (variables[energyVar] ?? 0) + value);
   ```
   Capped at max; handle `specialEnergy`. No support card is created for Blanche with the supplied data.

2. **`element_application` at `action_phase_start` (patton/liusu/shitou/chefmao).** Implement as a **generic synthetic triggered skill**, rather than a core `actionPhase` hook:
   - In `allSkills(state, triggerOn)` (`utils.ts:416-445`), after collecting normal entity skills, append stage-rule skills for effects with `kind: "element_application"`, `timing: "action_phase_start"`, and `triggerOn === "onActionPhase"`.
   - The skill caller is:
     - the rule support entity when `supportDefinitionId` is set (Liusu/ChefMao); if the support is absent (removed/replaced), the effect is skipped;
     - a live player-0 active character for global effects (Patton/Shitou, no support).
   - The synthetic `TriggeredSkillDefinition` uses `SkillContext.encapsulate(SkillContextOptions.plain, (c) => { ... })`; the action applies the element to `$.opp.active`, both actives, or `$.my.active` depending on `target`.
   - The skill is derived from state on every event, so it needs no serialization; real card skills remain untouched.
   - `SkillExecutor` handles reactions and defeat switching, avoiding a hand-written action-phase reaction/defeat path.
   - `allSkills` is called for `onActionPhase`; `game.ts:666-669` fires it once per round when `prevPhase === "roll"`, matching “行动阶段开始时”.
   - `filter` checks target alive; `usagePerRoundVariableName: null`; id reserved negative (e.g. `-910001`).
   - This one generic builder covers all four stages and future element rules.

3. **`artifact_free_equip` / `weapon_free_equip`:** no engine code; real card skills. The loader keeps the effect object for audit.

4. **Optional `artifact_cards_free`:** only if the user overrides the supplied data; then use the old `isArtifactCardCostFree` + both cost-path patches.

### 3.7 Why not a hidden status / policy branch

The rule is a real support in `player.supports`, exposed in `PbPlayerState`; a status or policy branch would not satisfy “renderable/inspectable card”. The engine hooks are keyed by the entity’s `stageRule.effects`, not by Python stage names, so the policy still sees only `notification.state`/`ActionRequest`.

---

## 4. Change-point table

Overlay = add under `engine-overlay/overlay/` (full file copy); `build/engine` is gitignored.

| # | Overlay / repo file | Upstream anchor | Change |
|---|---|---|---|
| 1 | `overlay/packages/core/src/base/state.ts` | `state.ts:159-172`, `223-229` | `StageRuleInstance`, `StageRuleEffect`, `GameState.initialSupportSpecs?`/`initialRuleEffects?`, `EntityState.stageRule?`, `EntityState.removable?` |
| 2 | `overlay/packages/core/src/game.ts` | `game.ts:218-224`, `280-321` | `CreateInitialStateConfig.stageRuleSupports`/`stageRuleEffects`; store specs; apply `initial_energy`; no direct support push |
| 3 | `overlay/packages/core/src/game.ts` | `game.ts:404-426` (`start`) | New `placeInitialSupports()` before first `notifyAndPause`/`initHands`; normal card-play path with `createHandCard` + `executeSkill` |
| 4 | `overlay/packages/core/src/mutator.ts` | `mutator.ts:185-188`, `847-887` (`createHandCard`) | Optional `stageRule`/`removable` fields on `InsertHandCardOption`, passed through the hand card into `supports` |
| 5 | `overlay/packages/core/src/utils.ts` | `utils.ts:811-828` | Replacement list filtered by `isIrremovableSupport`; keep `>= max` |
| 6 | same | `utils.ts:416-445` (`allSkills`) | Append synthetic `onActionPhase` skills for `element_application` effects (support-linked or global) |
| 7 | same | `utils.ts` near 614-625 | Helpers `isIrremovableSupport`, `hasStageRuleEffect`, optional `isArtifactCardCostFree` |
| 8 | `overlay/packages/core/src/stage_rule_effects.ts` | new file | `buildStageRuleTriggeredSkills(state, triggerOn)`; `SkillContext.encapsulate` element application; effect union/validation |
| 9 | `overlay/packages/core/src/runtime/skill_context.ts` | `skill_context.ts:1463-1495` | `dispose` early-return for `support && removable === false` |
| 10 | `overlay/packages/core/src/base/mutation.ts` | `mutation.ts:438-445` | Last-resort `removeEntity` skip for irremovable supports |
| 11 | `overlay/packages/cbinding/js/constant.ts` | `constant.ts:26-27` | `GITCG_ATTR_CREATEPARAM_STAGE_RULE_SUPPORTS = 2002` |
| 12 | `overlay/packages/cbinding/js/main.ts` | `main.ts:85-149` | Parse the JSON string into `stageRuleSupports` |
| 13 | same | `main.ts:151-167` | Pass `stageRuleSupports` into `createInitialState` |
| 14 | `envs/match.py` | `match.py:539-563` | `ATTR_CREATEPARAM_STAGE_RULE_SUPPORTS = 2002`; `_build_create_param(..., stage_rule_supports, stage_rule_effects)` sets the JSON attr (`{v, supports, effects}`) |
| 15 | same | `match.py:575-612` | `run_match(..., stage_rule_supports=None, stage_rule_effects=None)`; pass through |
| 16 | `npc/stage_rules.py` | new file | Load/validate `configs/npc/special_rules.json`; emit support specs + global effect specs; unknown kind/target raises |
| 17 | `npc/eval.py` | `eval.py:151-160` | Pass `stage_rule_supports(stage)` and `stage_rule_effects(stage)` to `run_match` |
| 18 | same | `eval.py:163-195` | Add `stage_rules` to per-match row |
| 19 | same | `eval.py:292-309` | Add stage-rule specs to `_spec_fingerprint` |
| 20 | `scripts/run_npc_gate.py` | lines 397-398 | Replace “rules not simulated” with the new mechanism; note card-based effects |
| 21 | `skillloop/render.py` | lines 226-230 | Render rule-stage support names from the asset data; no custom ids for current cards |
| 22 | `docs/PROVENANCE.md` | §3 table, lines 24-30 | Register core/cbinding/`envs/match.py`/`npc` changes |
| 23 | `engine-overlay/README.md` | build record | Overlay hash, `.so` sha/size, incremental duration, tests |
| 24 | `configs/engine.lock` | optional `[overlay]` | Record `overlay_tree_sha256` and `.so` sha |
| 25 | `overlay/packages/data/src/cards/support/stage_rules.gts` | **not needed for current 16 stages** | Only for future irremovable/Ley Line or optional Blanche card; custom ids, data-backed |
| 26 | `overlay/packages/core/src/utils.ts` | 876-890 / 951-1034 | **optional** `artifact_cards_free` cost hook if user overrides supplied data |

No C++ changes. `gitcg.h` is regenerated from `constant.ts` by `prepare.ts`.

---

## 5. Per-rule implementation

### 5.1 `timaeus` — real Timaeus support, artifact free-equip

- Inject real **322003**, `removable` from data (`true` for this batch).
- The card is implemented: `packages/data/src/cards/support/ally.gts:90-125`. It has `variable material, 2` (line 103), `on staged` draw for ≥6 artifacts in the initial pile (104-111), `on endPhase` material +1 (112-114), and the free-equip hook `on deductAllDiceCard` (115-123) which pays materials equal to the artifact's dice cost and calls `e.deductAllCost()`.
- With the normal placement path (§3.4), `on staged` runs and the card is fully faithful. With the direct-push fallback, `on staged` does not run but the free-equip hook still works.
- Do **not** zero artifact costs by default. The supplied data explicitly marks the old `artifact_cards_free` as legacy/approximate. If the user insists on unconditional 0-cost, add the optional effect key and cost hook; otherwise no core cost change.
- Wagner 322004 is the same pattern (`ally.gts:128-163`), with `on staged` weapon draw and `on deductAllDiceCard` free equip.

### 5.2 `blanche` — +2 energy, no starting support

- Supplied data: `initial_supports: []`, `effects: [{kind: "initial_energy", value: 2, target: "friendly_all", timing: "battle_start"}]`.
- Apply at init to all characters, capped at max energy; handle `specialEnergy`.
- No synthetic Blanche support card. If the user wants a visible rule card, add a data-backed custom card later; do not make one up now.
- `on battleBegin` cannot be used on a support: `allSkills` collects `onBattleBegin` from characters only (`utils.ts:435-436`).

### 5.3 `chefmao` — real Chef Mao + Pyro at action phase start

- Inject real **322005**, `removable: true` per data; food-support skills remain active.
- **The card data does not implement the Pyro application.** `ally.gts:166-192` only has the two food triggers (random die / draw food). Support injection alone therefore does **not** cover the stage rule; the `element_application` effect is new engine code.
- Add `element_application` (`pyro`, `both_active`, `action_phase_start`) via the generic synthetic skill in §3.6.
- This preserves the real card text while adding the stage rule; no custom Chef Mao variant.
- Risk: reactions/defeats at round start; the synthetic skill goes through `SkillExecutor`, so defeat switching is handled, but test both actives, existing auras, and no-rule baseline.
- If the user prefers an invisible/no-food rule card, a data-backed variant is possible, but it would drop the real card effects and is not the supplied data.

**Liusu 322012 has the same issue:** `ally.gts:342-361` only grants energy on switching to a character with 0 energy; it does not apply Hydro. The Hydro both-active effect is new engine code on top of the injected support.

### 5.4 `buyun` — real Jade Chamber

- Inject real **321003**, `removable: true`, no effects.
- It behaves exactly like a normal starting support, including its own `on roll` and self-dispose `on actionPhase` skills.
- Verify replacement: with Jade Chamber + 3 other supports, all 4 appear in the replacement list (it is removable).

### 5.5 Other starting supports / effects

- `timmie` 322007, `wagner` 322004, `ellin` 322010, `liusu` 322012, `irontonguetian` 322011, `uncletian` 321001, `lan` 322002: inject real cards, `removable` from data.
- `wagner` `weapon_free_equip` is card behavior; `timaeus` `artifact_free_equip` is card behavior.
- `patton` Hydro enemy active; `liusu`/`shitou` Hydro both actives; `chefmao` Pyro both actives: generic `element_application`.
- Future `Ley Line Monolith`: custom data-backed support with `removable:false`; engine/replacement machinery already supports it.

### 5.6 Direct answers to the scope questions

**Can support injection reuse the normal support-creation path?**
Yes, but the call site is `Game.start()`, not `createInitialState`. `setSupportPlayAction` (`gts/vm_impl/card.ts:158-181`) is the normal path: it moves the card into `supports` via `insertEntityOnStage` and runs `on staged`. `createInitialState` has no `StateMutator`/`SkillExecutor`, so it can only store `initialSupportSpecs`; `Game.start()` consumes them before `initHands`. Directly pushing an `EntityState` in `initPlayerState` works for continuous triggers but skips `on staged` (Timaeus/Wagner one-time draws).

**Is there an existing “start of action phase” hook?**
Yes: `Game.actionPhase` fires `onActionPhase` once per round when `prevPhase === "roll"` (`game.ts:666-669`), and GTS supports `on actionPhase` skills (e.g. `place.gts:96-100` for Jade Chamber). However, the real 322005 (Chef Mao) and 322012 (Liu Su) card data do **not** apply elements:
- `322005` only has food triggers (`ally.gts:166-192`);
- `322012` only grants energy on switching (`ally.gts:342-361`).
So injection alone does not implement the Pyro/Hydro stage effects; they need the generic synthetic `onActionPhase` skill in §3.6.

**Which of the 5 effects need new engine code?**

| Effect | Card data already implements it? | Needed work |
|---|---|---|
| Blanche +2 energy | No card (no support) | New core init code |
| Patton Hydro enemy active | No card | Generic `element_application` builder |
| Liusu Hydro both + support | Support card exists, but it only grants energy | Injection + generic `element_application` |
| Shitou Hydro both | No card | Generic `element_application` builder |
| Chef Mao Pyro both + support | Support card exists, but only food triggers | Injection + generic `element_application` |
| Timaeus artifact free-equip | **Yes** (322003 `on deductAllDiceCard`) | Injection via normal path; no cost hook |
| Wagner weapon free-equip | **Yes** (322004 `on deductAllDiceCard`) | Injection via normal path |
| buyun Jade Chamber | **Yes** (321003 roll/action skills) | Injection only |


---

## 6. Phased plan

### Phase 1 — generic starting supports + energy + cap/removable (2–3 days)

1. Add the new attribute end-to-end with a no-op payload; rebuild and verify no behavior change.
2. Add `EntityState.stageRule`/`removable`, `GameState.initialSupportSpecs`/`initialRuleEffects`, and `Game.start().placeInitialSupports()` using the normal card-play path; defensive cap check.
3. Implement replacement filtering + disposal guards; unit-test with a synthetic `removable:false` support (no data needed).
4. Load `configs/npc/special_rules.json`; place all 10 starting supports; `removable` from data (all true currently).
5. Apply Blanche `initial_energy`; no support card for Blanche.
6. Rebuild/install; one match each for `blanche`, `buyun`, `timaeus`, `timmie`, `wagner`; assert supports/energy and no deck-size change; state JSON round-trip.
7. Gate smoke; old rates for these stages are no longer strictly comparable because starting supports are now modeled.

### Phase 2 — generic element application (0.5–1 day)

1. Add `stage_rule_effects.ts` synthetic `onActionPhase` skills for `element_application`.
2. Cover `patton` (enemy active Hydro), `liusu`/`shitou` (both Hydro), `chefmao` (both Pyro).
3. Tests: no aura, existing aura/reaction, defeat switching, no-rule baseline.
4. Re-run the four stages with paired seeds; record old/new rates.

### Phase 3 — optional / future (S–M)

- Unconditional `artifact_cards_free` only if the user overrides the supplied data; use the old cost hook.
- UI art/name aliases only if a custom rule card is introduced; current real cards need none.
- Future irremovable custom cards (Ley Line Monolith) and optional Blanche rule card; data-backed definitions, no engine change beyond what Phase 1 provides.
- Verify `on staged` draws for Timaeus/Wagner and other starting supports; no synthetic `onEnter` should be needed with the normal path.

---

## 7. Rebuild / verification checklist

```sh
cd ~/projects/gitcg-skills
setsid env SKIP_IMAGE=1 SKIP_MATERIALIZE=1 \
  bash engine-overlay/scripts/build-in-docker.sh 2>&1 | tee build/engine-build.log
cp -a build/artifacts/libgitcg.so build/artifacts/libgitcg.so.prev
CONDA_ENV=gitcg-skill bash engine-overlay/scripts/install-lib.sh

/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python -m pytest -q -m "not slow"
/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python -m pytest -q \
  tests/test_env_smoke.py tests/test_replay_consistency.py
/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python -m npc.eval \
  --stages blanche,buyun,timaeus,chefmao --deck superconduct_aggro \
  --policy expert:superconduct_aggro --seeds 1 --workers 2 --tag stage_rules_smoke
```

Success criteria:

- no-rule state for the same seed is byte-identical to the pre-change engine (state JSON hash);
- starting supports are present, count toward 4, and are in `supports` (they are transiently in `hands` during placement, never in `pile`);
- deck length unchanged;
- removable rule can be replaced; irremovable synthetic support cannot be replaced or disposed;
- Blanche energies capped;
- element applications occur once per round at action phase start with correct target/reaction;
- `State.toJson`/`fromJson` preserves `stageRule`/`removable` and card ids;
- same seed + same rules replays identically;
- overlay hash and new `.so` sha recorded.

---

## 8. Cheap PoC (already run; no rebuild)

Because a full rebuild was forbidden, I copied the built `packages/core/dist` bundle to `/tmp/opencode/poc` and patched the copy, not `build/engine` and not `site-packages`. The patch emulated:

- `createInitialState` accepting `stageRuleSupports`;
- injected support entities with `stageRule` and normal cap membership;
- `initial_energy` +2 at init;
- an `artifact_cards_free` check in both cost functions (the optional path; the supplied data does not use it).

Representative result (Node 24, `@gi-tcg/data` v7.1.0, `superconduct_aggro` characters `[1403, 1407, 1101]`):

```
player 0 energy: 1403 2/3, 1407 2/2, 1101 2/3
player 1 energy: 1403 0/3, 1407 0/2, 1101 0/3
player 0 supports: two injected entities with stageRule.effects
artifact 312006: player 0 cost { Aligned: 0 }, player 1 cost 2
after filtering out the artifact-free support: player 0 cost 2
```

This proves direct state injection, cap membership at the state level, energy capping, and the optional cost hook. It did **not** exercise the normal `Game.start` placement path or `on staged`, so it does not prove Timaeus/Wagner one-time triggers. It also does not prove CFFI→C++→V8 attr flow, full-cap replacement with `removable:false`, generic element application, replay through `.so`, or UI rendering.

---

## 9. Effort and risk

| Work item | Effort | Risk | Notes |
|---|---|---|---|
| New `CreateParam` string attribute | S (0.5 d) | Low | No C++; Python constant local |
| Loader/normalizer for supplied JSON | S (0.5 d) | Low | Unknown kind/target raises |
| Normal-path placement (`Game.start` + `createHandCard` options) | M (0.5–1 d) | Medium | `on staged`/card triggers; state-from-JSON skip |
| `stageRule`/`removable` + replacement filtering + disposal guards | S/M (0.5–1 d) | Medium | Cap counts rule card; support cards regression surface |
| Blanche `initial_energy` | S (0.25 d) | Low | Cap/specialEnergy |
| Real Timaeus/Wagner/Jade Chamber placement | S (0.5 d) | Medium | Verify `on staged` draw and free-equip |
| Generic `element_application` skill builder | M (0.5–1 d) | Medium–high | Reactions/defeat; event ordering |
| Optional unconditional `artifact_cards_free` | S (0.5 d) | Medium | Only if user overrides data |
| Tests/docs/provenance/gate smoke | S/M (0.5–1 d) | Low | Pair old/new seeds |
| Future irremovable/Blanche custom card | M (optional) | Medium | Data-backed only |

**Phase 1:** ~2–3 dev-days. **Phase 2:** ~0.5–1 dev-day. **Optional cost override:** ~0.5 day.

---

## 10. What I could not verify without rebuilding

1. `CreateParam.set_attr(2002, json)` through Python CFFI → C++ string → V8 `setAttribute` → `createState`.
2. Header macro generation and the fact that installed CFFI does not need it (we pass numeric key).
3. Loader/normalizer against the supplied JSON in the actual Python package; unknown-key behavior.
4. Full-cap replacement: rule + 3 supports, removable vs irremovable; empty target list => `NO_TARGET`; disposal guards.
5. Real card skills for Timaeus/Wagner/Jade Chamber after normal-path placement, especially `on staged` one-time draws and the free-equip hooks.
6. Generic `element_application` timing, target, reactions, defeat switching, and no double-fire.
7. Blanche energy cap and `specialEnergy` handling.
8. `State.toJson`/`fromJson`/replay with injected supports and rule markers.
9. UI rendering and card name/asset lookup for the starting supports.
10. Exact gate deltas for all affected stages (old rates not comparable after rules are modeled).
11. Whether the supplied `removable: true` inference is correct for each card; the file itself marks it unverified.

---

## 11. Calibration note (explicitly unchanged)

The user hand-verified that **all stages are 3-star-able within 5 rounds with the Keqing double-pyro deck**. The existing `win_within_rounds = 5` threshold is confirmed achievable and is not a threshold artefact. **No objective/threshold change is proposed.** After Phase 1/2, re-run affected stages with paired seeds and report old/new rates, but keep `win_within_rounds = 5` and the existing HP table unless a separate evidence-backed calibration decision is made.

---

## 12. Documentation / provenance updates

- `docs/PROVENANCE.md` §3: overlay core changes (normal-path placement, `stageRule`/`removable`, replacement filtering, disposal guards, generic element-application effects), `envs/match.py` attribute, `npc/stage_rules.py`, `npc/eval.py`.
- Add `configs/npc/special_rules.json` to version control (it is currently untracked) together with `reports/npc_special_rules.md`; the loader depends on it.
- `engine-overlay/README.md`: build record with overlay hash, `.so` size/sha, duration, tests.
- `configs/engine.lock`: optional `[overlay]` tree sha and `.so` sha.
- `docs/STATUS.md`: only after rebuild/verification.
- `scripts/run_npc_gate.py`: update the old “special rules not simulated” issue; list which rules are now modeled and which remain (e.g. verifiability of `removable`).
- Record that `configs/npc/special_rules.json` is the authoritative supplied data and that old `special_rules` strings are legacy/display.
