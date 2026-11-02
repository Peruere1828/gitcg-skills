// Copyright (C) 2026 Piovium Labs
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as
// published by the Free Software Foundation, either version 3 of the
// License, or (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

import { DamageType } from "@gi-tcg/typings";

import {
  SkillContextOptions,
  type TriggeredSkillDefinition,
} from "./base/skill";
import type {
  CharacterState,
  EntityState,
  GameState,
  StageRuleEffect,
  StageRuleElement,
} from "./base/state";
import type { AppliableDamageType } from "./data/type";
import { $ } from "./query";
import { SkillContext } from "./runtime/skill_context";
import type { CallerAndTriggeredSkill } from "./utils";

/** Reserved (negative) skill ids so stage-rule skills never collide with data. */
const STAGE_RULE_SKILL_ID_BASE = -910000;

const DAMAGE_TYPE_OF: Record<StageRuleElement, AppliableDamageType> = {
  CRYO: DamageType.Cryo,
  HYDRO: DamageType.Hydro,
  PYRO: DamageType.Pyro,
  ELECTRO: DamageType.Electro,
  GEO: DamageType.Geo,
  DENDRO: DamageType.Dendro,
};

function activeCharacter(
  state: GameState,
  who: 0 | 1,
): CharacterState | null {
  const player = state.players[who];
  return (
    player.characters.find((ch) => ch.id === player.activeCharacterId) ?? null
  );
}

function findSupport(
  state: GameState,
  who: 0 | 1,
  definitionId: number,
): EntityState | null {
  return (
    state.players[who].supports.find(
      (support) => support.definition.id === definitionId,
    ) ?? null
  );
}

function buildElementApplicationSkill(
  id: number,
  caller: CharacterState | EntityState,
  effect: Extract<StageRuleEffect, { kind: "element_application" }>,
): TriggeredSkillDefinition {
  const damageType = DAMAGE_TYPE_OF[effect.element];
  const target = effect.target;
  return {
    type: "skill",
    id,
    ownerType: caller.definition.type,
    skillType: null,
    triggerOn: "onActionPhase",
    initiativeSkillConfig: null,
    usagePerRoundVariableName: null,
    filter: () => true,
    action: SkillContext.encapsulate(
      SkillContextOptions.plain,
      (context: SkillContext<any>) => {
        // Resolve both actives before applying: a reaction on the first one can
        // defeat it and switch in a new active, which must not be hit.
        const myActive = context.query($.my.active);
        const oppActive = context.query($.opp.active);
        if (target !== "enemy_active" && myActive) {
          context.apply(damageType, myActive);
        }
        if (target !== "friendly_active" && oppActive) {
          context.apply(damageType, oppActive);
        }
      },
    ),
  };
}

/**
 * Adventure Challenge `element_application` effects fire at the start of every
 * action phase. They are synthetic triggered skills, built from state on each
 * event, so they need no serialization and do not touch real card data.
 */
export function buildStageRuleTriggeredSkills(
  state: GameState,
  triggerOn: "onActionPhase",
): CallerAndTriggeredSkill[] {
  if (triggerOn !== "onActionPhase") {
    return [];
  }
  const specs = state.initialRuleEffects;
  if (!specs || specs.length === 0) {
    return [];
  }
  const result: CallerAndTriggeredSkill[] = [];
  let index = 0;
  for (const spec of specs) {
    const effect = spec.effect;
    if (effect.kind !== "element_application") {
      continue;
    }
    let caller: CharacterState | EntityState | null;
    if (spec.supportDefinitionId !== void 0) {
      // Liu Su / Chef Mao: the effect lives as long as their support does.
      caller = findSupport(state, spec.who, spec.supportDefinitionId);
    } else {
      // Patton / Shitou / Blanche: no support entity is involved.
      caller = activeCharacter(state, spec.who);
    }
    if (!caller) {
      continue;
    }
    result.push({
      caller,
      skill: buildElementApplicationSkill(
        STAGE_RULE_SKILL_ID_BASE - index,
        caller,
        effect,
      ),
    });
    index++;
  }
  return result;
}
