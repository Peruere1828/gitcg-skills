// Copyright (C) 2024-2025 Guyutongxue
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as
// published by the Free Software Foundation, either version 3 of the
// License, or (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

import { $, DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * NPC 变体：愚人众·藏镜仕女。官方可玩角色卡 2202 保持原版（HP 10），
 * PvE 难度血量 12 只属于这张独立角色卡（id 9202）与其独立技能/状态号段。
 */

/**
 * @id 192021
 * @name 水光破镜
 * @description
 * 所附属角色切换到其他角色时：需要多花费1个元素骰。
 * 持续回合：2
 * （同一方场上最多存在一个此状态）
 */
define status {
  id 192021 as NpcRefraction;
  conflictWith crossCharacter;
  duration 2;
  on addDice {
    when :(
      :e.action.type === "switchActive" &&
        :self.master.id === :e.action.from?.id
    );
    :e.addCost(DiceType.Void, 1);
  };
};

/**
 * @id 92021
 * @name 水弹
 * @cost 1*Hydro, 2*Void
 * @description
 * 造成1点水元素伤害。
 */
define skill {
  id 92021 as NpcWaterBall;
  skillType normal;
  cost DiceType.Hydro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Hydro, 1);
};

/**
 * @id 92022
 * @name 潋波绽破
 * @cost 3*Hydro
 * @description
 * 造成3点水元素伤害，目标角色附属水光破镜。
 */
define skill {
  id 92022 as NpcInfluxBlast;
  skillType elemental;
  cost DiceType.Hydro, 3;
  :damage(DamageType.Hydro, 3);
  :characterStatus(NpcRefraction, $.opp.active);
};

/**
 * @id 92023
 * @name 粼镜折光
 * @cost 3*Hydro, 2*Energy
 * @description
 * 造成5点水元素伤害。
 */
define skill {
  id 92023 as NpcRippledReflection;
  skillType burst;
  cost DiceType.Hydro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Hydro, 5);
};

/**
 * @id 9202
 * @name 愚人众·藏镜仕女
 * @hp 12
 * @energy 2
 * @description
 * 一切隐秘，都将深藏于潋光的水镜之中吧…
 */
define character {
  id 9202 as MirrorMaidenNpc;
  since "v3.3.0";
  tags hydro, fatui;
  health 12;
  energy 2;
  skills NpcWaterBall, NpcInfluxBlast, NpcRippledReflection;
};
