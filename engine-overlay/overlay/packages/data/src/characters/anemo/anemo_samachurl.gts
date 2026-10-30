// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3501
// "Anemo Samachurl". Values mirror configs/npc/npc_enemies_all.json.

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 135011
 * @name 旋风
 * @description
 * 结束阶段：造成1点风元素伤害。
 * 可用次数：2
 */
define summon {
  id 135011 as Whirlwind;
  since "v3.3.0";
  hint DamageType.Anemo, 1;
  on endPhase {
    usage 2;
    :damage(DamageType.Anemo, 1);
  };
};

/**
 * @id 35011
 * @name 杖击
 * @cost 2*Void
 * @description
 * 造成1点物理伤害。
 */
define skill {
  id 35011 as StaffStrike;
  skillType normal;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 1);
};

/**
 * @id 35012
 * @name 祈风术
 * @cost 3*Anemo
 * @description
 * 造成2点风元素伤害，召唤旋风。
 */
define skill {
  id 35012 as PrayerToTheWinds;
  skillType elemental;
  cost DiceType.Anemo, 3;
  :damage(DamageType.Anemo, 2);
  :summon(Whirlwind);
};

/**
 * @id 35013
 * @name 风波冲击
 * @cost 3*Anemo, 2*Energy
 * @description
 * 造成5点风元素伤害。
 */
define skill {
  id 35013 as GaleforceShock;
  skillType burst;
  cost DiceType.Anemo, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Anemo, 5);
};

/**
 * @id 3501
 * @name 风丘丘萨满
 * @hp 6
 * @energy 2
 * @description
 * 驱使风元素的丘丘萨满，能召唤旋风。
 */
define character {
  id 3501 as AnemoSamachurl;
  since "v3.3.0";
  tags anemo, monster, hilichurl;
  health 6;
  energy 2;
  skills StaffStrike, PrayerToTheWinds, GaleforceShock;
};
