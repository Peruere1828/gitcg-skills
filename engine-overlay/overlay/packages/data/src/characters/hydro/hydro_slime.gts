// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3204
// "Hydro Slime". Values mirror configs/npc/npc_enemies_all.json.
//
// The passive status "Elemental Lifeform: Hydro" (222062) is the one already
// defined for Hydro Tulpa in the upstream data package; it is imported instead
// of redefined (id collision).

import { DamageType, DiceType } from "@gi-tcg/core/data";
import { ElementalLifeformHydro } from "./hydro_tulpa.gts";

/**
 * @id 32041
 * @name 弹跳
 * @cost 1*Hydro, 2*Void
 * @description
 * 造成1点水元素伤害。
 */
define skill {
  id 32041 as Bounce;
  skillType normal;
  cost DiceType.Hydro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Hydro, 1);
};

/**
 * @id 32042
 * @name 重重下落
 * @cost 3*Hydro, 2*Energy
 * @description
 * 造成4点水元素伤害。
 */
define skill {
  id 32042 as Slam;
  skillType burst;
  cost DiceType.Hydro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Hydro, 4);
};

/**
 * @id 32043
 * @name 元素沉积·水
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属元素生命·水。
 */
define skill {
  id 32043 as ElementalSedimentationHydro;
  skillType passive {
    on battleBegin {
      :characterStatus(ElementalLifeformHydro);
    };
  };
};

/**
 * @id 3204
 * @name 水史莱姆
 * @hp 6
 * @energy 2
 * @description
 * 由水元素凝聚成的魔物，总是附着水元素。
 */
define character {
  id 3204 as HydroSlime;
  since "v3.3.0";
  tags hydro, monster;
  health 6;
  energy 2;
  skills Bounce, Slam, ElementalSedimentationHydro;
};
