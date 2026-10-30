# engine-overlay/overlay — 我们的引擎改动（入库）

本目录是**引擎仓根目录的镜像**：`overlay/` 下的相对路径会被
`scripts/apply-overlay.sh` 原样复制进 `build/engine/`（引擎源码树根）。
纪律（PLAN §6.5）：`refs/genius-invokation` 只读；我们的改动只放这里。

## B0 现状

B0（构建链 go/no-go）**overlay 为空**——本阶段只证明工具链能重建出与 wheel
0.21.0 ABI 一致、可被 conda `gitcg` dlopen、且现有测试全绿的 `libgitcg.so`。

## B1 现状（PvE 敌人已移植）

`overlay/packages/data/src/characters/**` 下已放入 **17 个 NPC 敌人角色**（官方
GCG 敌人卡 id；数值对齐 `configs/npc/npc_enemies_all.json`）：

| 元素/目录 | 敌人（id） |
|---|---|
| cryo | 冰箭丘丘人 3102、冰盾丘丘暴徒 3101、冰史莱姆 3107、冰深渊法师 3103 |
| pyro | 火斧丘丘暴徒 3302、火深渊法师 3301 |
| electro | 雷箭丘丘人 3402、雷斧丘丘暴徒 3403、雷史莱姆 3406 |
| anemo | 风丘丘萨满 3501、愚人众·风拳前锋军 3502 |
| dendro | 草丘丘萨满 3703 |
| hydro | 水史莱姆 3204、水深渊法师 3201、愚人众·水铳重卫士 3203 |
| physical | 射手丘丘人 3002、打手丘丘人 3001 |

另两 2 类型（愚人众·火之债务处理人 2301、愚人众·藏镜仕女 2202）**上游已有**
（`fatui_pyro_agent.gts` / `mirror_maiden.gts`），直接复用其角色 id。合计覆盖
`npc_enemies_all.json` 全部 19 类。

**偏差**（详见各文件注释）：深渊法师护罩「碎裂后本回合禁用技能」、水铳重卫士
元素武装「治疗+1」、风拳前锋军「聚风屏障 2 点护盾（近似为 -1 伤害×2）+ 逆风奉还拳
增伤/转元素」未建模；火之债务处理人沿用上游 HP 11（NPC 数据为 9）。

导入表由 `scripts/register-data-imports.py`（`apply-overlay.sh` 调用）自动登记。

## B1 起怎么放敌人数据（路径对照）

引擎的数据源在 `packages/data/src/`：

| 我们要加的 | 放到 overlay 的路径 |
|---|---|
| NPC 敌人角色 `.gts`（技能/被动/HP/充能） | `packages/data/src/characters/<element>/<enemy>.gts` |
| 注册新角色/卡牌 | `packages/data/src/index.ts`（由 `register-data-imports.py` 自动重生成） |
| 若需新增数据版本 | `packages/data/src/` 内的版本常量 + `packages/pybinding/src/gitcg/create_param.py` 的 `DATA_VERSION` 同步 |

参考现有敌人实现（如 `packages/data/src/characters/anemo/consecrated_flying_serpent.gts`）
的 `.gts` 语法；数据权威来源见 `configs/npc/npc_enemies_timaeus.json` 与
`reports/explore/npc_pve_doc.md`。

## 快速循环

改 overlay 后**无需重编 V8**，只需重跑 TS 构建 + 重链：

```sh
bash engine-overlay/scripts/apply-overlay.sh
V8_OFFLINE=1 SKIP_PNPM=0 bash engine-overlay/scripts/build-in-docker.sh
```

`apply-overlay.sh` 复制完 overlay 后会自动调用 `scripts/register-data-imports.py`
重生成 `packages/data/src/index.ts` 的导入表（新增 `.gts` 才能被 tsdown 打进
`js_code.cpp`）；该脚本对空 overlay 是幂等的（逐字节还原上游 index.ts）。

（`SKIP_MATERIALIZE=1` 复用 `build/engine`；`SKIP_PNPM=1` 连 TS 构建也跳过，
仅重链 C++。）
