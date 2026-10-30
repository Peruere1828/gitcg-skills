// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3201
// "Hydro Abyss Mage". Values mirror configs/npc/npc_enemies_all.json.

import { $, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 132012
 * @name 水元素护罩
 * @description
 * 所附属角色免疫所有伤害。
 * 此状态会提供2次水元素附着（可被元素反应消耗），耗尽后移除此状态。
 *
 * Deviation (documented): the "skill lock until end of round on shatter" clause
 * is not modelled (see cryo_abyss_mage.gts).
 */
define status {
  id 132012 as HydroWard;
  tags immuneControl;
  on selfEnter {
    :apply(DamageType.Hydro, :self.master);
  };
  on decreaseDamaged {
    :e.decreaseDamage(:e.value);
  };
  on modifyReaction {
    listenTo all;
    when :( :e.target.id === :self.master.id );
    usage 2;
    :apply(DamageType.Hydro, :self.master);
  };
};

/**
 * @id 132071
 * @name 咕噜水泡
 * @description
 * 角色无法使用技能。（持续到回合结束）
 */
define status {
  id 132071 as GurglingBubble;
  oneDuration;
  tags disableSkill;
};

/**
 * @id 32011
 * @name 水弹
 * @cost 1*Hydro, 2*Void
 * @description
 * 造成1点水元素伤害。
 */
define skill {
  id 32011 as WaterBall;
  skillType normal;
  cost DiceType.Hydro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Hydro, 1);
};

/**
 * @id 32012
 * @name 泡影的祭法
 * @cost 3*Hydro
 * @description
 * 造成1点水元素伤害，目标角色附属水泡。
 */
define skill {
  id 32012 as BubblingOffering;
  skillType elemental;
  cost DiceType.Hydro, 3;
  :damage(DamageType.Hydro, 1);
  :characterStatus(GurglingBubble, $.opp.active);
};

/**
 * @id 32013
 * @name 浮幻生灭之仪
 * @cost 4*Hydro, 3*Energy
 * @description
 * 造成4点水元素伤害，目标角色附属水泡。
 */
define skill {
  id 32013 as RiteOfMortalWater;
  skillType burst;
  cost DiceType.Hydro, 4;
  cost DiceType.Energy, 3;
  :damage(DamageType.Hydro, 4);
  :characterStatus(GurglingBubble, $.opp.active);
};

/**
 * @id 32014
 * @name 水元素护罩
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属水元素护罩。
 */
define skill {
  id 32014 as HydroWardPassive;
  skillType passive {
    on battleBegin {
      :characterStatus(HydroWard);
    };
  };
};

/**
 * @id 3201
 * @name 水深渊法师
 * @hp 6
 * @energy 3
 * @description
 * 乘坐水元素护罩的深渊法师。
 */
define character {
  id 3201 as HydroAbyssMage;
  since "v3.3.0";
  tags hydro, monster;
  health 6;
  energy 3;
  skills WaterBall, BubblingOffering, RiteOfMortalWater, HydroWardPassive;
};
