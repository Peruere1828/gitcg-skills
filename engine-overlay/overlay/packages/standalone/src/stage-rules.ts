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
//
// --- 评价协议：关卡特殊规则（`GET /npc-decks` 每条的 `eval.rules`）----------
// 桥把 `configs/npc/special_rules.json` 归一化成引擎契约载荷后随 NPC 阵容下发：
//   {"player":0,
//    "supports":[{"card_id":322007,"removable":true}],
//    "effects":[{"kind":"initial_energy","value":2,"target":"friendly_all"},
//               {"kind":"element_application","element":"HYDRO","target":"both_active"}]}
// 这里只做**载荷 → core 类型**的机械映射，不掺任何战术/过滤/重排逻辑：
//   * 规则视角是「参赛者」（player=0），落到浏览器引擎时统一换成 AI 座位
//     （`OTHER_WHO`；人类执 NPC 座位）；
//   * core 的 `StageRuleElement` 是大写枚举（CRYO/HYDRO/...），载荷可能给
//     小写（数据源就是小写），映射时统一 toUpperCase；
//   * 未知 kind / 非法 target / 非法 element 直接丢弃该条，不让脏数据打断开局。

import type {
  StageRuleEffect,
  StageRuleEffectSpec,
  StageRuleSupportSpec,
} from "@gi-tcg/core";

/** 桥下发的单条关卡效果（字段按 kind 取舍） */
export interface StageRuleEffectPayload {
  kind?: string;
  value?: number;
  element?: string;
  target?: string;
}

/** 桥下发的单张开局支援 */
export interface StageRuleSupportPayload {
  card_id?: number;
  removable?: boolean;
}

/** `eval.rules`；关卡没有特殊规则时为 null */
export interface StageRulesPayload {
  /** 数据视角的参赛者座位；前端不用它，一律按 AI 座位落规则 */
  player?: number;
  supports?: StageRuleSupportPayload[];
  effects?: StageRuleEffectPayload[];
}

const STAGE_RULE_ELEMENTS = [
  "CRYO",
  "HYDRO",
  "PYRO",
  "ELECTRO",
  "GEO",
  "DENDRO",
] as const;
type StageRuleElementName = (typeof STAGE_RULE_ELEMENTS)[number];

/** 把一条载荷效果收窄成 core 的 `StageRuleEffect`，不认识就返回 null */
function parseStageRuleEffect(
  raw: StageRuleEffectPayload,
): StageRuleEffect | null {
  if (raw.kind === "initial_energy") {
    if (typeof raw.value !== "number" || !Number.isFinite(raw.value)) {
      return null;
    }
    // core 只接受 friendly_all；载荷没给 target 时按契约默认。
    if (raw.target !== undefined && raw.target !== "friendly_all") {
      return null;
    }
    return {
      kind: "initial_energy",
      value: raw.value,
      target: "friendly_all",
    };
  }
  if (raw.kind === "element_application") {
    const element = (raw.element ?? "").toUpperCase() as StageRuleElementName;
    if (!STAGE_RULE_ELEMENTS.includes(element)) {
      return null;
    }
    if (
      raw.target !== "enemy_active" &&
      raw.target !== "both_active" &&
      raw.target !== "friendly_active"
    ) {
      return null;
    }
    return {
      kind: "element_application",
      element,
      target: raw.target,
    };
  }
  return null;
}

export interface StageRuleSpecs {
  supports: StageRuleSupportSpec[];
  effects: StageRuleEffectSpec[];
}

/**
 * `eval.rules` → `Game.createInitialState` 的 `stageRuleSupports` /
 * `stageRuleEffects`。`who` 传 AI 座位（`OTHER_WHO`）；规则只在全新对局传入。
 * 没有可落地的条目时返回 null（调用方据此不传参）。
 */
export function toStageRuleSpecs(
  rules: StageRulesPayload | null | undefined,
  who: 0 | 1,
  stage: string | undefined,
): StageRuleSpecs | null {
  if (!rules) {
    return null;
  }
  const stageName = stage ?? "";
  const supports: StageRuleSupportSpec[] = [];
  for (const raw of rules.supports ?? []) {
    const definitionId = Number(raw.card_id);
    if (!Number.isInteger(definitionId) || definitionId <= 0) {
      continue;
    }
    supports.push({
      who,
      definitionId,
      removable: raw.removable ?? true,
      effects: [],
      stage: stageName,
    });
  }
  const effects: StageRuleEffectSpec[] = [];
  for (const raw of rules.effects ?? []) {
    const effect = parseStageRuleEffect(raw);
    if (effect) {
      effects.push({ who, effect, stage: stageName });
    }
  }
  if (supports.length === 0 && effects.length === 0) {
    return null;
  }
  return { supports, effects };
}
