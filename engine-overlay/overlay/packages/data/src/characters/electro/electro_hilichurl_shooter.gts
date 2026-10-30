// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3402
// "Electro Hilichurl Shooter". Values mirror configs/npc/npc_enemies_all.json.

import { $, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 34021
 * @name 雷箭
 * @cost 1*Electro, 2*Void
 * @description
 * 造成1点雷元素伤害。
 */
define skill {
  id 34021 as ThunderArrow;
  skillType normal;
  cost DiceType.Electro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Electro, 1);
};

/**
 * @id 34022
 * @name 几支一起
 * @cost 3*Electro, 2*Energy
 * @description
 * 造成4点雷元素伤害。
 */
define skill {
  id 34022 as Multishot;
  skillType burst;
  cost DiceType.Electro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Electro, 4);
};

/**
 * @id 34023
 * @name 躲起来
 * @description
 * 【被动】此角色出招后，自动切换到下一个角色。
 */
define skill {
  id 34023 as Hide;
  skillType passive {
    on useSkill {
      when :( :e.skill.caller.definition.id === ElectroHilichurlShooter );
      :switchActive($.my.next);
    };
  };
};

/**
 * @id 3402
 * @name 雷箭丘丘人
 * @hp 6
 * @energy 2
 * @description
 * 手持雷箭的丘丘人射手，攻击之后会躲到后方。
 */
define character {
  id 3402 as ElectroHilichurlShooter;
  since "v3.3.0";
  tags electro, monster, hilichurl;
  health 6;
  energy 2;
  skills ThunderArrow, Multishot, Hide;
};
