// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3502
// "Fatui Skirmisher - Anemoboxer Vanguard".
// Values mirror configs/npc/npc_enemies_all.json.
//
// Deviation (documented): "Stormwind Barrier" grants 2 Shield points in the
// original; here it is approximated as "-1 DMG, 2 times".  The barrier's
// damage-type conversion / +1 DMG clause for Avenging Squall is not modelled.

import { $, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 135021
 * @name 聚风屏障
 * @description
 * 本角色将在下次行动时，直接使用技能：逆风奉还拳。
 * 准备逆风奉还拳期间：提供2点护盾，保护所附属角色。
 */
define status {
  id 135021 as StormwindBarrier;
  prepare AvengingSquall;
  on decreaseDamaged {
    usage 2;
    :e.decreaseDamage(1);
  };
};

/**
 * @id 35021
 * @name 疾风刺拳
 * @cost 1*Anemo, 2*Void
 * @description
 * 造成2点物理伤害。
 */
define skill {
  id 35021 as HurricaneJab;
  skillType normal;
  cost DiceType.Anemo, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 35022
 * @name 聚风蓄力
 * @cost 3*Anemo
 * @description
 * 本角色附属聚风屏障并准备技能：逆风奉还拳。
 */
define skill {
  id 35022 as GatheringTheWinds;
  skillType elemental;
  cost DiceType.Anemo, 3;
  :characterStatus(StormwindBarrier);
};

/**
 * @id 35023
 * @name 激风鼓动
 * @cost 3*Anemo, 2*Energy
 * @description
 * 造成3点风元素伤害，其他我方「愚人众」角色获得1点充能。
 */
define skill {
  id 35023 as GaleforceAgitation;
  skillType burst;
  cost DiceType.Anemo, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Anemo, 3);
  for (const ch of :queryAll($.my.character)) {
    if (ch.id !== :self.id && ch.definition.tags.includes("fatui")) {
      :gainEnergy(1, ch);
    }
  }
};

/**
 * @id 35025
 * @name 逆风奉还拳
 * @cost
 * @description
 * （需准备1个行动轮）
 * 造成2点物理伤害。
 */
define skill {
  id 35025 as AvengingSquall;
  skillType elemental;
  prepared;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 3502
 * @name 愚人众先遣队·风拳前锋军
 * @hp 12
 * @energy 2
 * @description
 * 愚人众先遣队的风拳前锋军。
 */
define character {
  id 3502 as FatuiAnemoboxerVanguard;
  since "v3.3.0";
  tags anemo, fatui;
  health 12;
  energy 2;
  skills HurricaneJab, GatheringTheWinds, GaleforceAgitation, AvengingSquall;
};
