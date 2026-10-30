// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3406
// "Electro Slime". Values mirror configs/npc/npc_enemies_all.json.

import { Aura, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 134061
 * @name 元素生命·雷
 * @description
 * 角色总是附着雷元素，并且免疫雷元素伤害。
 */
define status {
  id 134061 as ElementalLifeformElectro;
  variable enableModification, 0;
  on selfEnter {
    :apply(DamageType.Electro, :self.master);
    :apply(DamageType.Electro, :self.master);
    :setVariable("enableModification", +(:self.master.aura === Aura.Electro));
  };
  on modifyReaction {
    when :( :getVariable("enableModification") );
    :apply(DamageType.Electro, :self.master);
  };
  on decreaseDamaged {
    when :( :e.type === DamageType.Electro );
    :e.decreaseDamage(:e.value);
  };
};

/**
 * @id 34061
 * @name 弹跳
 * @cost 1*Electro, 2*Void
 * @description
 * 造成1点雷元素伤害。
 */
define skill {
  id 34061 as Bounce;
  skillType normal;
  cost DiceType.Electro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Electro, 1);
};

/**
 * @id 34062
 * @name 重重下落
 * @cost 3*Electro, 2*Energy
 * @description
 * 造成4点雷元素伤害。
 */
define skill {
  id 34062 as Slam;
  skillType burst;
  cost DiceType.Electro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Electro, 4);
};

/**
 * @id 34063
 * @name 元素沉积·雷
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属元素生命·雷。
 */
define skill {
  id 34063 as ElementalSedimentationElectro;
  skillType passive {
    on battleBegin {
      :characterStatus(ElementalLifeformElectro);
    };
  };
};

/**
 * @id 3406
 * @name 雷史莱姆
 * @hp 6
 * @energy 2
 * @description
 * 由雷元素凝聚成的魔物，总是附着雷元素。
 */
define character {
  id 3406 as ElectroSlime;
  since "v3.3.0";
  tags electro, monster;
  health 6;
  energy 2;
  skills Bounce, Slam, ElementalSedimentationElectro;
};
