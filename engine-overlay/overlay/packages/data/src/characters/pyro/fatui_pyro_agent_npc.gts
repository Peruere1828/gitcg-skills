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

import { DamageType, DiceType } from "@gi-tcg/core/data";

/**
 * NPC 变体：愚人众·火之债务处理人。官方可玩角色卡 2301 保持原版（HP 11），
 * PvE 难度血量 13 只属于这张独立角色卡（id 9301）与其独立技能/状态号段。
 */

/**
 * @id 193011
 * @name 潜行
 * @description
 * 所附属角色受到的伤害-1，造成的伤害+1。
 * 可用次数：2
 */
define status {
  id 193011 as NpcStealth;
  tags barrier;
  usage 2;
  on decreaseDamaged {
    :e.decreaseDamage(1);
    :consumeUsage();
  };
  on increaseSkillDamage {
    :e.increaseDamage(1);
    :consumeUsage();
  };
};

/**
 * @id 93011
 * @name 突刺
 * @cost 1*Pyro, 2*Void
 * @description
 * 造成2点物理伤害。
 */
define skill {
  id 93011 as NpcThrust;
  skillType normal;
  cost DiceType.Pyro, 1;
  cost DiceType.Void, 2;
  :damage(DamageType.Physical, 2);
};

/**
 * @id 93012
 * @name 伺机而动
 * @cost 3*Pyro
 * @description
 * 造成1点火元素伤害，本角色附属潜行。
 */
define skill {
  id 93012 as NpcProwl;
  skillType elemental;
  cost DiceType.Pyro, 3;
  :damage(DamageType.Pyro, 1);
  :characterStatus(NpcStealth);
};

/**
 * @id 93013
 * @name 焚毁之锋
 * @cost 3*Pyro, 2*Energy
 * @description
 * 造成5点火元素伤害。
 */
define skill {
  id 93013 as NpcBladeAblaze;
  skillType burst;
  cost DiceType.Pyro, 3;
  cost DiceType.Energy, 2;
  :damage(DamageType.Pyro, 5);
};

/**
 * @id 93014
 * @name 潜行大师
 * @cost
 * @description
 * 【被动】战斗开始时，初始附属潜行。
 */
define skill {
  id 93014 as NpcStealthMaster;
  skillType passive {
    on battleBegin {
      :characterStatus(NpcStealth);
    };
  };
};

/**
 * @id 9301
 * @name 愚人众·火之债务处理人
 * @hp 13
 * @energy 2
 * @description
 * 「死债不可免，活债更难逃…」
 */
define character {
  id 9301 as FatuiPyroAgentNpc;
  since "v3.3.0";
  tags pyro, fatui;
  health 13;
  energy 2;
  skills NpcThrust, NpcProwl, NpcBladeAblaze, NpcStealthMaster;
};
