// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3703
// "Dendro Samachurl". Values mirror configs/npc/npc_enemies_all.json.

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 137031
 * @name 藤蔓
 * @description
 * 所在阵营切换角色后：对切换到的角色造成1点草元素伤害。
 * 可用次数：2
 */
define combatStatus {
  id 137031 as Vines;
  on switchActive {
    usage 2;
    :damage(DamageType.Dendro, 1, :e.switchInfo.to);
  };
};

/**
 * @id 37031
 * @name 杖击
 * @cost 2*Void
 * @description
 * 造成1点物理伤害。
 */
define skill {
  id 37031 as StaffStrike;
  skillType normal;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 1);
};

/**
 * @id 37032
 * @name 毒藤蔓生
 * @cost 3*Dendro
 * @description
 * 造成1点草元素伤害，在对方场上生成藤蔓。
 */
define skill {
  id 37032 as VenomousVines;
  skillType elemental;
  cost DiceType.Dendro, 3;
  :damage(DamageType.Dendro, 1);
  :combatStatus(Vines, "opp");
};

/**
 * @id 37033
 * @name 草之域
 * @cost 3*Dendro, 2*Energy
 * @description
 * 造成5点草元素伤害。
 */
define skill {
  id 37033 as DendroField;
  skillType burst;
  cost DiceType.Dendro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Dendro, 5);
};

/**
 * @id 3703
 * @name 草丘丘萨满
 * @hp 6
 * @energy 2
 * @description
 * 驱使草元素的丘丘萨满，能在对方场上生成藤蔓。
 */
define character {
  id 3703 as DendroSamachurl;
  since "v3.3.0";
  tags dendro, monster, hilichurl;
  health 6;
  energy 2;
  skills StaffStrike, VenomousVines, DendroField;
};
