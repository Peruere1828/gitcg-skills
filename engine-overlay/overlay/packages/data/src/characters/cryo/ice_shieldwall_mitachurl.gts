// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card ported into the engine data pool for genshin-GITCG
// (PLAN.md §4.4, PvE-B1). This file lives in `engine-overlay/overlay/` and is
// applied on top of the pinned upstream engine tree; it is NOT part of upstream.
//
// Values mirror `configs/npc/npc_enemies_all.json` (genshin-db in-game data);
// official GCG enemy card id 3101 "Ice Shieldwall Mitachurl".
//
// The auxiliary empty passive "Shattered Shield" (official id 31015) has no
// published text in genshin-db; the shield-removal behaviour is modelled by the
// status usage instead, so that passive is intentionally omitted here.

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 131011
 * @name 寒冰大盾
 * @description
 * 所附属角色所受伤害-2。
 * 此状态会提供2次冰元素附着（可被元素反应消耗）：耗尽后移除此状态。
 */
define status {
  id 131011 as HoarfrostGreatshield;
  tags barrier;
  on selfEnter {
    :apply(DamageType.Cryo, :self.master);
  };
  on decreaseDamaged {
    :e.decreaseDamage(2);
  };
  on modifyReaction {
    listenTo all;
    when :( :e.target.id === :self.master.id );
    usage 2;
    :apply(DamageType.Cryo, :self.master);
  };
};

/**
 * @id 31011
 * @name Plata Plama
 * @cost 1*Cryo, 2*Void
 * @description
 * 造成2点物理伤害。
 */
define skill {
  id 31011 as PlataPlama;
  skillType normal;
  cost DiceType.Cryo, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 31012
 * @name Da Plama
 * @cost 3*Cryo
 * @description
 * 造成3点物理伤害。
 */
define skill {
  id 31012 as DaPlama;
  skillType elemental;
  cost DiceType.Cryo, 3;
  :damage(DamageType.Physical, 3);
};

/**
 * @id 31013
 * @name Aba Movo
 * @cost 3*Cryo, 2*Energy
 * @description
 * 造成5点物理伤害。
 */
define skill {
  id 31013 as AbaMovo;
  skillType burst;
  cost DiceType.Cryo, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Physical, 5);
};

/**
 * @id 31014
 * @name 寒冰大盾
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属寒冰大盾。
 */
define skill {
  id 31014 as HoarfrostGreatshieldPassive;
  skillType passive {
    on battleBegin {
      :characterStatus(HoarfrostGreatshield);
    };
  };
};

/**
 * @id 3101
 * @name 冰盾丘丘暴徒
 * @hp 10
 * @energy 2
 * @description
 * 手持厚重冰盾的丘丘暴徒，盾牌能抵挡伤害。
 */
define character {
  id 3101 as IceShieldwallMitachurl;
  since "v3.3.0";
  tags cryo, monster, hilichurl;
  health 10;
  energy 2;
  skills PlataPlama,
    DaPlama,
    AbaMovo,
    HoarfrostGreatshieldPassive;
};
