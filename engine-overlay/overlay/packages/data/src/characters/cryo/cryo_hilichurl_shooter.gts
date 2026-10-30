// Copyright (C) 2026 genshin-GITCG contributors
// SPDX-License-Identifier: AGPL-3.0-only
//
// PvE NPC enemy card ported into the engine data pool for genshin-GITCG
// (PLAN.md §4.4, PvE-B1). This file lives in `engine-overlay/overlay/` and is
// applied on top of the pinned upstream engine tree; it is NOT part of upstream.
//
// Values mirror `configs/npc/npc_enemies_all.json` (genshin-db in-game data);
// official GCG enemy card id 3102 "Cryo Hilichurl Shooter".

import { $, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * @id 31021
 * @name 冰箭
 * @cost 1*Cryo, 2*Void
 * @description
 * 造成1点冰元素伤害。
 */
define skill {
  id 31021 as IceArrow;
  skillType normal;
  cost DiceType.Cryo, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Cryo, 1);
};

/**
 * @id 31022
 * @name 几支一起
 * @cost 3*Cryo, 2*Energy
 * @description
 * 造成4点冰元素伤害。
 */
define skill {
  id 31022 as Multishot;
  skillType burst;
  cost DiceType.Cryo, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Cryo, 4);
};

/**
 * @id 31023
 * @name 躲起来
 * @description
 * 【被动】此角色出招后，自动切换到下一个角色。
 */
define skill {
  id 31023 as Hide;
  skillType passive {
    on useSkill {
      when :( :e.skill.caller.definition.id === CryoHilichurlShooter );
      :switchActive($.my.next);
    };
  };
};

/**
 * @id 3102
 * @name 冰箭丘丘人
 * @hp 6
 * @energy 2
 * @description
 * 手持冰箭的丘丘人射手，攻击之后会躲到后方。
 */
define character {
  id 3102 as CryoHilichurlShooter;
  since "v3.3.0";
  tags cryo, monster, hilichurl;
  health 6;
  energy 2;
  skills IceArrow, Multishot, Hide;
};
