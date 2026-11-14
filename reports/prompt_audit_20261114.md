# Prompt × 七圣召唤 wiki 审计与修复（2026-11-14）

## 方法

- 由 subagent 只读审计：本地 wiki 快照 `/tmp/gitcg_wiki.html`
  （<https://wiki.biligame.com/ys/七圣召唤>，3.3），对照
  `skillloop/rich_render.py`、`skillloop/passive_skills.py`、`skillloop/render.py`、
  `skillloop/loadout.py` 与 `data/skills/*/library.json`。
- 同时用哑 client 构造 llm_empty / static / dynamic 的 ellin prompt，
  比较模板骨架与 user 骨架。
- 未联网、未调用 LLM API、未修改文件；本报告由主 agent 按结论修复。

## 发现与处理

| 级别 | 问题 | 处理 |
|---|---|---|
| HIGH-1 | `rule_equipment` 把所有装备说成快速行动，漏掉天赋装备的“战斗行动”例外 | 已补“卡面写战斗行动的行动牌除外” |
| HIGH-2 | `rule_burst` 声称“受到伤害积累充能” | 已改为“普攻/战技后+1；受伤不充能” |
| MEDIUM-1 | 冰草共存附着（aura=113）被渲染成“无附着”，反应提示错误 | 已映射为“附着:冰草”，提示按冰/草相关反应说明 |
| MEDIUM-2 | “战斗行动后换对方行动”漏掉一方已宣布结束的例外 | 规则块已补“未宣布方可继续行动” |
| MEDIUM-3 | 角色所有附属都被标成“装备”，冻结/护盾被误分类 | 按卡牌标签区分“装备/状态” |
| MEDIUM-4 | 用描述字符串判断“战斗行动”，假阳性（交给我吧！） | 改用 `GCG_TAG_SLOWLY`；候选显示“快速行动/战斗行动” |
| MEDIUM-5 | `rule_mulligan` “换掉的牌不会回来”与引擎相反 | 改为“洗回牌堆，之后仍可能抽到” |
| MEDIUM-6 | 超载无条件强制切换，漏“仅出战角色/击倒分支” | 规则块与反应提示已补条件 |
| LOW-1 | 元素骰上限 16 缺失 | 已补 |
| LOW-2 | 召唤物满 4 无法生成缺失 | 已补；支援满 4 替换也写明 |
| LOW-3 | 初始 5 张、结束阶段先结算再抓牌、牌堆空不抓 | 已补 |
| LOW-4 | 食物“每回合一次”不精确 | 规则技能改为“每角色每回合一次（饱腹）+复苏限一” |
| LOW-5 | “强制切换到下一个”与 wiki 的“选择”不一致 | `rule_switch` 改为“免费选择” |
| LOW-6 | “被扩散元素伤害”歧义 | 改为“目标原附着元素（冰/水/火/雷）伤害” |
| LOW-7 | 护盾/穿透顺序、非伤害附着、爆发清空充能 | 规则块已明确“普通伤害先被护盾抵消；穿透绕过”；补非伤害附着；爆发支付全部充能 |
| LOW-8 | knowledge 头声称局面只给名称/数值，实际带效果 | 改为“名称、数值与行动所需效果” |
| LOW-9 | “轮/回合”术语混用 | 保留（低风险，不影响数值） |

## 三组模板一致性（subagent 实测）

- `llm_empty` / `static` / `dynamic` 都走 `skillloop.run.build_policy` →
  `SkillAgentPolicy` → 同一个 `RichStateRenderer.system_message`；web 桥也直接调用
  `build_policy`。
- 同一 ellin 合成局面下：knowledge 块逐字节相同（1982 字符），TaskSpec 行、
  OUTPUT_CONTRACT、user 状态/候选骨架相同。
- 骨架 diff：`llm_empty vs static: IDENTICAL`，`static vs dynamic: IDENTICAL`。
- 差异只来自：技能库内容、loadout 固定技能（dynamic 固定了一条 rule 技能，
  rules note 少一行）、关卡牌组。**不是模板分叉。**
- 已知语义不对称：`llm_empty` 没有可引用技能，输出契约里的 `used` 永远为空；
  字段保留但与三组模板一致。

## 残留

- LOW-9 术语未统一。
- `parked.py` 是 `build_policy` 的镜像实现，模板一致但未来改动有漂移风险；
  `web/ai_rpc_server.py` 的 docstring 仍写“plain StateRenderer”，已过时。
- 审计未跑完整对局；修复后已用单元测试覆盖附着 113、装备/状态分类、
  打牌动作标签、被动技能。
