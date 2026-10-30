// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3107
// "Cryo Slime". Values mirror configs/npc/npc_enemies_all.json.

import { Aura, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 131071
 * @name 元素生命·冰
 * @description
 * 角色总是附着冰元素，并且免疫冰元素伤害和冻结。
 */
define status {
  id 131071 as ElementalLifeformCryo;
  tags immuneControl;
  variable enableModification, 0;
  on selfEnter {
    :apply(DamageType.Cryo, :self.master);
    :apply(DamageType.Cryo, :self.master);
    :setVariable("enableModification", +(:self.master.aura === Aura.Cryo));
  };
  on modifyReaction {
    when :( :getVariable("enableModification") );
    :apply(DamageType.Cryo, :self.master);
  };
  on decreaseDamaged {
    when :( :e.type === DamageType.Cryo );
    :e.decreaseDamage(:e.value);
  };
};

/**
 * @id 31071
 * @name 冲撞
 * @cost 1*Cryo, 2*Void
 * @description
 * 造成1点冰元素伤害。
 */
define skill {
  id 31071 as Ram;
  skillType normal;
  cost DiceType.Cryo, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Cryo, 1);
};

/**
 * @id 31072
 * @name 跳跃冲击
 * @cost 3*Cryo, 2*Energy
 * @description
 * 造成4点冰元素伤害。
 */
define skill {
  id 31072 as JumpingSlam;
  skillType burst;
  cost DiceType.Cryo, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Cryo, 4);
};

/**
 * @id 31073
 * @name 元素沉积·冰
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属元素生命·冰。
 */
define skill {
  id 31073 as ElementalSedimentationCryo;
  skillType passive {
    on battleBegin {
      :characterStatus(ElementalLifeformCryo);
    };
  };
};

/**
 * @id 3107
 * @name 冰史莱姆
 * @hp 6
 * @energy 2
 * @description
 * 由冰元素凝聚成的魔物，总是附着冰元素且免疫冻结。
 */
define character {
  id 3107 as CryoSlime;
  since "v3.3.0";
  tags cryo, monster;
  health 6;
  energy 2;
  skills Ram, JumpingSlam, ElementalSedimentationCryo;
};
