# configs/pools — AI 侧卡池定义（入库）

本目录存放**卡池版本清单**（PLAN §9：`configs/` = run 配置 + engine.lock + 卡池版本）。
每个文件是**纯 id 清单**（角色 id + 行动牌 id），供训练/评测选取；不含卡面数据、不含抓取物。

## 文件

| 文件 | 口径 | 角色 | 行动牌 | 状态 |
|---|---|---|---|---|
| `pool_C_v34_mondliyue.json` | ≤ v3.4 且 蒙德/璃月 | 19 | 107 | **选定（现行）** |
| `pool_A_v34_all.json` | ≤ v3.4 全部区域 | 29 | 121 | 备选（对照） |
| `mond_liyue_pool_v33_35.json` | 蒙德/璃月，v3.3–v3.5 | 20 | 108 | 备选 |
| `mond_liyue_launch_v33.json` | 蒙德/璃月，v3.3 首发 | 17 | 105 | 备选 |

选定口径 **C** = 区域（蒙德/璃月）∩ 版本（`sinceVersion ≤ v3.4`）；已排除 嘉明 / 蓝砚（沉玉谷）。
取舍理由与逐卡区域标注见 [`reports/explore/mond_liyue_pool.md`](../../reports/explore/mond_liyue_pool.md)
（机器可读副本 `reports/explore/mond_liyue_pool.json`）。

## 口径怎么定（应该这样做）

- 区域用**标签** `tags` 里的 `GCG_TAG_NATION_*` 判定，**不是**用版本代理。
- 版本用 `sinceVersion` 字段，仅作**过滤 / 交叉校验**。
- ⚠️ `sinceVersion` **不能**当区域代理：早期就有稻妻/须弥卡，晚期也有蒙德/璃月卡 →
  区域必须按 tag + 名称/风味**人工校对**（见 `reports/explore/mond_liyue_pool.md` 的 `region_tag`）。

## 来源与再生成

- 派生自**资产目录缓存** `data/assets/expert_system_assets_chs_latest.json`（抓取物，**gitignore**；
  来源与再生成见 [`data/assets/README.md`](../../data/assets/README.md)）。
- 卡池 JSON 自带 id，**不依赖**该缓存也能被训练/评测直接使用。
- 卡池升级 = 显式迁移（PLAN D4）：**新增**文件、旧 run 用旧清单可重放，**不覆盖**历史文件。
