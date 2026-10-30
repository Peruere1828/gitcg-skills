"""C3 配套：把 NPC 敌人 `.gts` 的元数据转成 assets-manager 的 ``CustomData``。

**为什么需要它**：NPC 敌人是我们移植的自定义角色，官方静态数据镜像里**没有**它们的
datum。而 `assets-manager.getData(id)` 不检查 `r.ok`，会把镜像的 404 JSON 当成数据返回，
于是 ``rawDescription`` / ``playCost`` / ``skills`` 全是 undefined —— 前端详情面板已经
因此崩过两次（``undefined.matchAll``、``e.playCost is not iterable``），技能也没有名字
与描述。

上游本来就有这条通路：``AssetsManager`` 的 ``customData`` 选项。它的
``setupCustomData`` 会把自定义角色/技能/实体的**完整 datum** 灌进 ``dataCacheSync``，
而 ``getData`` 先查缓存 —— 于是名字、技能描述、骰子费用、卡面占位图全都有了，也不再打
镜像。缺的只是「谁来产出 CustomData」：上游 ``custom-data-loader`` 那套要在**运行时重新
编译 .gts**（node-vm / esbuild-wasm 后端），不适合浏览器 standalone。

所以我们直接从 overlay 里的 ``.gts`` 源文件解析元数据（``@id/@name/@description/@cost``
与 ``skillType/cost/tags/health/energy/skills``），产出同形状的 ``CustomData``：
``GET /npc-decks`` 的 ``assets`` 字段 → 前端 ``new AssetsManager({customData})`` 传给
``createClient``。**单一事实来源仍然是 `.gts`**（改了 .gts 无需改本模块）。

骰子费用取自 DSL 的 ``cost DiceType.X, N;``（不是 ``@cost`` 注释文本），``Energy`` 被
跳过——充能是角色的 ``maxEnergy``，官方卡面也只在技能行显示骰子。
"""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from common.paths import REPO_ROOT

DEFAULT_GTS_DIR = REPO_ROOT / "engine-overlay" / "overlay" / "packages" / "data" / "src" / "characters"

# .gts DSL 里的骰子名 -> @gi-tcg/typings 的 DiceType 数值（enums.ts:
# DiceRequirementType VOID=0..LEGEND=10；元素骰 1..7、ALIGNED=8）。
DICE_TYPES: dict[str, int] = {
    "Void": 0,
    "Cryo": 1,
    "Hydro": 2,
    "Pyro": 3,
    "Electro": 4,
    "Anemo": 5,
    "Geo": 6,
    "Dendro": 7,
    "Aligned": 8,
    "Energy": 9,   # 解析出来但不进 playCost（见模块 docstring）
    "Legend": 10,
}

# define <kind> -> CustomEntity.type（core 的 EntityType）
ENTITY_TYPES: dict[str, str] = {
    "status": "status",
    "combatStatus": "combatStatus",
    "summon": "summon",
    "support": "support",
    "equipment": "equipment",
}

# 允许出现在 CustomSkill.type 里的技能类型（core 的 CommonSkillType + "passive"）
_SKILL_TYPES = {"normal", "elemental", "burst", "technique", "passive"}


def _iter_blocks(text: str) -> Iterator[tuple[str, str, str]]:
    """按「文档注释 + define <kind> { … }」切块；块体用花括号配平（status 有嵌套）。"""
    for match in re.finditer(r"/\*\*([\s\S]*?)\*/\s*define\s+(\w+)\s*\{", text):
        comment, kind = match.group(1), match.group(2)
        depth = 1
        index = match.end()
        while index < len(text) and depth:
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        yield comment, kind, text[match.end() : index - 1]


def _comment_field(comment: str, key: str) -> str:
    """取注释里的 ``@key`` 值（可跨行，直到下一个 ``@`` 字段）。"""
    out: list[str] = []
    capturing = False
    for line in comment.splitlines():
        stripped = line.strip().lstrip("*").strip()
        if stripped.startswith(f"@{key}"):
            rest = stripped[len(key) + 1 :].strip()
            if rest:
                out.append(rest)
            capturing = True
            continue
        if capturing:
            if stripped.startswith("@"):
                break
            if stripped:
                out.append(stripped)
    return "\n".join(out).strip()


def _placeholder_face(name: str) -> str:
    """卡面占位图（灰底 + 中文名），让棋盘/详情面板不再显示破图。"""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="210" height="360">'
        '<rect width="210" height="360" fill="#ddd"/>'
        '<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" '
        'font-size="28" fill="#333">'
        f"{name}</text></svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _dice_costs(body: str) -> list[dict[str, Any]]:
    """从 DSL 的 ``cost DiceType.X, N;`` 收集骰子费用（跳过 Energy/Legend 之外的都不跳）。"""
    costs: list[dict[str, Any]] = []
    for dice, count in re.findall(r"cost\s+DiceType\.(\w+)\s*,\s*(\d+)\s*;", body):
        if dice == "Energy":
            continue  # 充能走角色 maxEnergy，官方卡面技能行也只显示骰子
        cost_type = DICE_TYPES.get(dice)
        if cost_type is None:
            continue
        costs.append({"type": cost_type, "count": int(count)})
    return costs


@lru_cache(maxsize=1)
def npc_custom_data(gts_dir: str | None = None) -> dict[str, Any]:
    """解析 overlay 里的 NPC `.gts` → ``CustomData``（`AssetsManager` 的 `customData` 选项）。

    返回值被缓存（一次解析约 17 个文件）；调用方只读，不要改。

    技能按 **id** 收集；``as Name`` 之类的符号只在**单个文件内**解析（不同文件会重名，
    例如多个 ``Hit``，全局按符号去重会把后一个文件的技能覆盖掉）。
    """
    root = Path(gts_dir) if gts_dir else DEFAULT_GTS_DIR
    skills_by_id: dict[int, dict[str, Any]] = {}   # id -> CustomSkill（全局唯一）
    characters: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []

    for path in sorted(root.glob("*/*.gts")):
        text = path.read_text(encoding="utf-8")
        file_skills: dict[str, dict[str, Any]] = {}  # 仅本文件内的 符号 -> 技能
        for comment, kind, body in _iter_blocks(text):
            def_id = _comment_field(comment, "id")
            name = _comment_field(comment, "name")
            description = _comment_field(comment, "description")
            symbol_match = re.search(r"id\s+\d+\s+as\s+(\w+)\s*;", body)
            symbol = symbol_match.group(1) if symbol_match else ""
            tags_match = re.search(r"tags\s+([^;]*);", body)
            tags = (
                [tag.strip() for tag in tags_match.group(1).split(",") if tag.strip()]
                if tags_match
                else []
            )

            if kind == "skill":
                if not def_id.isdigit():
                    continue
                type_match = re.search(r"skillType\s+(\w+)\s*[;{]", body)
                skill_type = type_match.group(1) if type_match else "passive"
                if skill_type not in _SKILL_TYPES:
                    skill_type = "passive"
                skill = {
                    "id": int(def_id),
                    "name": name,
                    "rawDescription": description,
                    "skillIconUrl": "",
                    "type": skill_type,
                    "playCost": _dice_costs(body),
                }
                skills_by_id[int(def_id)] = skill
                if symbol:
                    file_skills[symbol] = skill
                continue

            if kind == "character":
                if not def_id.isdigit():
                    continue
                health = re.search(r"health\s+(\d+)\s*;", body)
                energy = re.search(r"energy\s+(\d+)\s*;", body)
                skill_symbols = re.search(r"skills\s+([^;]*);", body)
                own_skills: list[dict[str, Any]] = []
                if skill_symbols:
                    for symbol in skill_symbols.group(1).split(","):
                        skill = file_skills.get(symbol.strip())
                        if skill is not None:
                            own_skills.append(skill)
                characters.append(
                    {
                        "id": int(def_id),
                        "name": name,
                        "rawDescription": description,
                        "cardFaceUrl": _placeholder_face(name),
                        "obtainable": False,
                        "hp": int(health.group(1)) if health else 8,
                        "maxEnergy": int(energy.group(1)) if energy else 2,
                        "tags": tags,
                        "skills": own_skills,
                    }
                )
                continue

            if kind in ENTITY_TYPES:
                if not def_id.isdigit():
                    continue
                entities.append(
                    {
                        "id": int(def_id),
                        "name": name,
                        "rawDescription": description,
                        "type": ENTITY_TYPES[kind],
                        "cardFaceOrBuffIconUrl": _placeholder_face(name),
                        "obtainable": False,
                        "tags": tags,
                        "playCost": _dice_costs(body),
                        "skills": [],
                    }
                )

    return {
        "characters": characters,
        "entities": entities,
        # 顶层再放一份技能：`setupCustomData` 会先把它们灌进缓存（独立技能面板用）
        "skills": list(skills_by_id.values()),
        "attachments": [],
    }


def npc_custom_data_summary() -> dict[str, Any]:
    """给测试/日志用的小摘要（避免把整份数据塞进断言）。"""
    data = npc_custom_data()
    return {
        "characters": len(data["characters"]),
        "entities": len(data["entities"]),
        "skills": len(data["skills"]),
        "character_ids": [ch["id"] for ch in data["characters"]],
    }
