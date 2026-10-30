// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card (PLAN.md §4.4, PvE-B1); official GCG enemy card id 3002
// "Hilichurl Shooter". Values mirror configs/npc/npc_enemies_all.json.
// Physical (element-less) character, so no element directory applies.

import { $, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 30021
 * @name 射击
 * @cost 2*Void
 * @description
 * 造成1点物理伤害。
 */
define skill {
  id 30021 as Shooting;
  skillType normal;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 1);
};

/**
 * @id 30022
 * @name 几支一起
 * @cost 3*Same, 2*Energy
 * @description
 * 造成5点物理伤害。
 */
define skill {
  id 30022 as Multishot;
  skillType burst;
  cost DiceType.Aligned, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Physical, 5);
};

/**
 * @id 30023
 * @name 躲起来
 * @description
 * 【被动】此角色使用技能后，自动切换到下一个角色。
 */
define skill {
  id 30023 as Hide;
  skillType passive {
    on useSkill {
      when :( :e.skill.caller.definition.id === HilichurlShooter );
      :switchActive($.my.next);
    };
  };
};

/**
 * @id 3002
 * @name 射手丘丘人
 * @hp 6
 * @energy 2
 * @description
 * 手持弓箭的丘丘人，攻击之后会躲到后方。
 */
define character {
  id 3002 as HilichurlShooter;
  since "v3.3.0";
  tags monster, hilichurl;
  health 6;
  energy 2;
  skills Shooting, Multishot, Hide;
};
