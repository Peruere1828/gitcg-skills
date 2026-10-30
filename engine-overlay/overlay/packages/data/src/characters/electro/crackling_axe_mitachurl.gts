// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3403
// "Crackling Axe Mitachurl". Values mirror configs/npc/npc_enemies_all.json.

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 134031
 * @name 流煌磨刃
 * @description
 * 角色造成的物理伤害变为雷元素伤害。
 * 可用次数：3
 */
define status {
  id 134031 as RippleElectroEdge;
  on modifySkillDamageType {
    when :( :e.type === DamageType.Physical );
    usage 3;
    :e.changeDamageType(DamageType.Electro);
  };
};

/**
 * @id 34031
 * @name Du Plama
 * @cost 1*Electro, 2*Void
 * @description
 * 造成2点物理伤害。
 */
define skill {
  id 34031 as DuPlamaElectro;
  skillType normal;
  cost DiceType.Electro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 34032
 * @name Upa
 * @cost 3*Electro
 * @description
 * 造成3点物理伤害。
 */
define skill {
  id 34032 as UpaElectro;
  skillType elemental;
  cost DiceType.Electro, 3;
  :damage(DamageType.Physical, 3);
};

/**
 * @id 34033
 * @name Oto Tiga
 * @cost 3*Electro, 2*Energy
 * @description
 * 造成5点物理伤害。
 */
define skill {
  id 34033 as OtoTigaElectro;
  skillType burst;
  cost DiceType.Electro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Physical, 5);
};

/**
 * @id 34034
 * @name 流煌磨刃
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属流煌磨刃。
 */
define skill {
  id 34034 as RippleElectroEdgePassive;
  skillType passive {
    on battleBegin {
      :characterStatus(RippleElectroEdge);
    };
  };
};

/**
 * @id 3403
 * @name 雷斧丘丘暴徒
 * @hp 10
 * @energy 2
 * @description
 * 挥舞着雷光巨斧的丘丘暴徒。
 */
define character {
  id 3403 as CracklingAxeMitachurl;
  since "v3.3.0";
  tags electro, monster, hilichurl;
  health 10;
  energy 2;
  skills DuPlamaElectro, UpaElectro, OtoTigaElectro, RippleElectroEdgePassive;
};
