// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3103
// "Cryo Abyss Mage". Values mirror configs/npc/npc_enemies_all.json.

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 131031
 * @name 冰元素护罩
 * @description
 * 所附属角色免疫所有伤害，并且不会被冻结。
 * 此状态提供2次冰元素附着（可被元素反应消耗）：耗尽后移除此状态。
 *
 * Deviation (documented): the original also disables the character's skills
 * until the end of the round once the ward shatters; that clause is not modelled
 * (the `on selfDispose` hook cannot resolve its master safely), so the ward is
 * removed without the temporary skill lock.
 */
define status {
  id 131031 as CryoWard;
  tags immuneControl;
  on selfEnter {
    :apply(DamageType.Cryo, :self.master);
  };
  on decreaseDamaged {
    :e.decreaseDamage(:e.value);
  };
  on modifyReaction {
    listenTo all;
    when :( :e.target.id === :self.master.id );
    usage 2;
    :apply(DamageType.Cryo, :self.master);
  };
};

/**
 * @id 31031
 * @name 冰刺
 * @cost 1*Cryo, 2*Void
 * @description
 * 造成1点冰元素伤害。
 */
define skill {
  id 31031 as CryoThorns;
  skillType normal;
  cost DiceType.Cryo, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Cryo, 1);
};

/**
 * @id 31032
 * @name 凝华的祭法
 * @cost 3*Cryo
 * @description
 * 造成3点冰元素伤害。
 */
define skill {
  id 31032 as FrostdriftOffering;
  skillType elemental;
  cost DiceType.Cryo, 3;
  :damage(DamageType.Cryo, 3);
};

/**
 * @id 31033
 * @name 霜寒迂旋之仪
 * @cost 3*Cryo, 2*Energy
 * @description
 * 造成5点冰元素伤害。
 */
define skill {
  id 31033 as RimestormRite;
  skillType burst;
  cost DiceType.Cryo, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Cryo, 5);
};

/**
 * @id 31034
 * @name 冰元素护罩
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属冰元素护罩。
 */
define skill {
  id 31034 as CryoWardPassive;
  skillType passive {
    on battleBegin {
      :characterStatus(CryoWard);
    };
  };
};

/**
 * @id 3103
 * @name 冰深渊法师
 * @hp 6
 * @energy 2
 * @description
 * 乘坐冰元素护罩的深渊法师。
 */
define character {
  id 3103 as CryoAbyssMage;
  since "v3.3.0";
  tags cryo, monster;
  health 6;
  energy 2;
  skills CryoThorns, FrostdriftOffering, RimestormRite, CryoWardPassive;
};
