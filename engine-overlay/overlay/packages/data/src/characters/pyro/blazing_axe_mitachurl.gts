// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card ported into the engine data pool for genshin-GITCG
// (PLAN.md §4.4, PvE-B1). This file lives in `engine-overlay/overlay/` and is
// applied on top of the pinned upstream engine tree; it is NOT part of upstream.
//
// Values mirror `configs/npc/npc_enemies_all.json` (genshin-db in-game data);
// official GCG enemy card id 3302 "Blazing Axe Mitachurl".

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 133021
 * @name 流焰磨刃
 * @description
 * 角色造成的物理伤害变为火元素伤害。
 * 可用次数：3
 */
define status {
  id 133021 as FlowfireEdge;
  on modifySkillDamageType {
    when :( :e.type === DamageType.Physical );
    usage 3;
    :e.changeDamageType(DamageType.Pyro);
  };
};

/**
 * @id 33021
 * @name Du Plama
 * @cost 1*Pyro, 2*Void
 * @description
 * 造成2点物理伤害。
 */
define skill {
  id 33021 as DuPlama;
  skillType normal;
  cost DiceType.Pyro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 33022
 * @name Upa
 * @cost 3*Pyro
 * @description
 * 造成3点物理伤害。
 */
define skill {
  id 33022 as Upa;
  skillType elemental;
  cost DiceType.Pyro, 3;
  :damage(DamageType.Physical, 3);
};

/**
 * @id 33023
 * @name Oto Tiga
 * @cost 3*Pyro, 2*Energy
 * @description
 * 造成5点物理伤害。
 */
define skill {
  id 33023 as OtoTiga;
  skillType burst;
  cost DiceType.Pyro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Physical, 5);
};

/**
 * @id 33024
 * @name 流焰磨刃
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属流焰磨刃。
 */
define skill {
  id 33024 as FlowfireEdgePassive;
  skillType passive {
    on battleBegin {
      :characterStatus(FlowfireEdge);
    };
  };
};

/**
 * @id 3302
 * @name 火斧丘丘暴徒
 * @hp 10
 * @energy 2
 * @description
 * 挥舞着燃烧巨斧的丘丘暴徒，攻击附着火元素。
 */
define character {
  id 3302 as BlazingAxeMitachurl;
  since "v3.3.0";
  tags pyro, monster, hilichurl;
  health 10;
  energy 2;
  skills DuPlama, Upa, OtoTiga, FlowfireEdgePassive;
};
