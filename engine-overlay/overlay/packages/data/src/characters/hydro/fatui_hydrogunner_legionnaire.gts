// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3203
// "Fatui Skirmisher - Hydrogunner Legionnaire".
// Values mirror configs/npc/npc_enemies_all.json.
//
// Deviation (documented): "Hydro Elemental Equipment" also grants "+1 Healing";
// the heal bonus is not modelled (engine raises no heal-amount event here), the
// -1 DMG and 2 Hydro Applications are.

import { $, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 132031
 * @name 元素武装·水
 * @description
 * 所附属角色所受伤害-1，造成的治疗+1。
 * 此状态会提供2次水元素附着（可被元素反应消耗），耗尽后移除此状态。
 *
 * Deviations (documented): the "+1 Healing" bonus and the "skill lock until end
 * of round on shatter" clause are not modelled.
 */
define status {
  id 132031 as HydroElementalEquipment;
  on selfEnter {
    :apply(DamageType.Hydro, :self.master);
  };
  on decreaseDamaged {
    :e.decreaseDamage(1);
  };
  on modifyReaction {
    listenTo all;
    when :( :e.target.id === :self.master.id );
    usage 2;
    :apply(DamageType.Hydro, :self.master);
  };
};

/**
 * @id 32031
 * @name 战术冲撞
 * @cost 1*Hydro, 2*Void
 * @description
 * 造成2点物理伤害。
 */
define skill {
  id 32031 as SkilledCharge;
  skillType normal;
  cost DiceType.Hydro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 32032
 * @name 疗愈弹
 * @cost 3*Hydro
 * @description
 * 造成2点水元素伤害，治疗2个我方后台角色1点。
 */
define skill {
  id 32032 as CurativeBullet;
  skillType elemental;
  cost DiceType.Hydro, 3;
  :damage(DamageType.Hydro, 2);
  :heal(1, $.my.standby);
};

/**
 * @id 32033
 * @name 元素增幅·水
 * @cost 3*Hydro, 3*Energy
 * @description
 * 造成3点水元素伤害，本角色附属元素武装·水。
 */
define skill {
  id 32033 as ElementalBuffHydro;
  skillType burst;
  cost DiceType.Hydro, 3;
  cost DiceType.Energy, 3;
  :damage(DamageType.Hydro, 3);
  :characterStatus(HydroElementalEquipment);
};

/**
 * @id 32034
 * @name 战备补给
 * @cost
 * @description
 * 【被动】战斗开始时，本角色获得2点充能。
 */
define skill {
  id 32034 as CombatResupply;
  skillType passive {
    on battleBegin {
      :gainEnergy(2, :self);
    };
  };
};

/**
 * @id 3203
 * @name 愚人众先遣队·水铳重卫士
 * @hp 12
 * @energy 3
 * @description
 * 愚人众先遣队的水铳重卫士。
 */
define character {
  id 3203 as FatuiHydrogunnerLegionnaire;
  since "v3.3.0";
  tags hydro, fatui;
  health 12;
  energy 3;
  skills SkilledCharge, CurativeBullet, ElementalBuffHydro, CombatResupply;
};
