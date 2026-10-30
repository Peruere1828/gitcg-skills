// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3001
// "Hilichurl Fighter". Values mirror configs/npc/npc_enemies_all.json.
// Physical (element-less) character, so no element directory applies.

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 30011
 * @name 打
 * @cost 2*Void
 * @description
 * 造成1点物理伤害。
 */
define skill {
  id 30011 as Hit;
  skillType normal;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 1);
};

/**
 * @id 30012
 * @name 跳起打
 * @cost 3*Void
 * @description
 * 造成2点物理伤害。
 */
define skill {
  id 30012 as JumpAndHit;
  skillType elemental;
  cost DiceType.Void, 3;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 30013
 * @name 挥来挥去
 * @cost 3*Same, 2*Energy
 * @description
 * 造成4点物理伤害。
 */
define skill {
  id 30013 as SwingThatClub;
  skillType burst;
  cost DiceType.Aligned, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Physical, 4);
};

/**
 * @id 3001
 * @name 打手丘丘人
 * @hp 6
 * @energy 2
 * @description
 * 挥舞着大棒的丘丘人。
 */
define character {
  id 3001 as HilichurlFighter;
  since "v3.3.0";
  tags monster, hilichurl;
  health 6;
  energy 2;
  skills Hit, JumpAndHit, SwingThatClub;
};
