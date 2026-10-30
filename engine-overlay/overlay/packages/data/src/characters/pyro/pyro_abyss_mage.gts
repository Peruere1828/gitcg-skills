// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3301
// "Pyro Abyss Mage". Values mirror configs/npc/npc_enemies_all.json.

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 133011
 * @name 火元素护罩
 * @description
 * 所附属角色免疫所有伤害。
 * 此状态提供2次火元素附着（可被元素反应消耗）：耗尽后移除此状态。
 *
 * Deviation (documented): the "skill lock until end of round on shatter" clause
 * is not modelled (see cryo_abyss_mage.gts).
 */
define status {
  id 133011 as PyroWard;
  tags immuneControl;
  on selfEnter {
    :apply(DamageType.Pyro, :self.master);
  };
  on decreaseDamaged {
    :e.decreaseDamage(:e.value);
  };
  on modifyReaction {
    listenTo all;
    when :( :e.target.id === :self.master.id );
    usage 2;
    :apply(DamageType.Pyro, :self.master);
  };
};

/**
 * @id 33011
 * @name 炎波
 * @cost 1*Pyro, 2*Void
 * @description
 * 造成1点火元素伤害。
 */
define skill {
  id 33011 as Flamewave;
  skillType normal;
  cost DiceType.Pyro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Pyro, 1);
};

/**
 * @id 33012
 * @name 灼面的祭法
 * @cost 3*Pyro
 * @description
 * 造成3点火元素伤害。
 */
define skill {
  id 33012 as BlazingVisage;
  skillType elemental;
  cost DiceType.Pyro, 3;
  :damage(DamageType.Pyro, 3);
};

/**
 * @id 33013
 * @name 熔焰地涌之仪
 * @cost 4*Pyro, 3*Energy
 * @description
 * 造成7点火元素伤害。
 */
define skill {
  id 33013 as RiteOfTheMoltenEruption;
  skillType burst;
  cost DiceType.Pyro, 4;
  cost DiceType.Energy, 3;
  :damage(DamageType.Pyro, 7);
};

/**
 * @id 33014
 * @name 火元素护罩
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属火元素护罩。
 */
define skill {
  id 33014 as PyroWardPassive;
  skillType passive {
    on battleBegin {
      :characterStatus(PyroWard);
    };
  };
};

/**
 * @id 3301
 * @name 火深渊法师
 * @hp 6
 * @energy 3
 * @description
 * 乘坐火元素护罩的深渊法师。
 */
define character {
  id 3301 as PyroAbyssMage;
  since "v3.3.0";
  tags pyro, monster;
  health 6;
  energy 3;
  skills Flamewave, BlazingVisage, RiteOfTheMoltenEruption, PyroWardPassive;
};
