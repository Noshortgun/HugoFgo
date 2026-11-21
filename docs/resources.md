# Atlas 资源与查询

Atlas 原始元数据与图库由 `npm run resources:update` 生成，默认不入库。原始 JSON 是单行大文件，`data/CN/atlas-nice-cn.json` 约 57 MB；查询单个从者时使用下方命令，不要把整份文件读进上下文。区服目录默认为 `CN`，可用 `--region JP` 或 `--region TW` 指定其他区服。

## 查询入口

```bash
npm run data:query -- find <名称或 ID>
npm run data:query -- servant <ID>
npm run data:query -- assets <ID>
npm run data:query -- counts
```

每条查询都可追加 `--json` 或 `--region JP`。`assets` 同时显示 Atlas URL、预期本地路径和是否已下载。

## 素材字段

| 字段 | 用途 | 本地路径 |
| --- | --- | --- |
| `extraAssets.faces.ascension` | 状态头像、助战头像 | `data/<区服>/assets/atlas/faces/<从者ID>-<再临>.png` |
| `extraAssets.commands.ascension` | 普通指令卡人物原图（透明 RGBA） | `data/<区服>/assets/atlas/commands/<从者ID>/<再临>.png` |
| `extraAssets.commands.costume` | 灵衣指令卡原图 | `data/<区服>/assets/atlas/commands/<从者ID>/costume-<灵衣ID>.png` |
| `extraAssets.charaGraph.ascension` | 卡面立绘 | `data/<区服>/assets/atlas/cards/<从者ID>-<再临>.png` |
| `extraAssets.equipFace.equip` | 概念礼装图标 | `data/<区服>/assets/atlas/ce/<礼装ID>.png` |
| `extraAssets.item` / `masterFigure` | 御主礼装道具与人物图 | `data/<区服>/assets/atlas/mystic/` |
| `skills[].icon` | 技能图标 | `data/<区服>/assets/atlas/skills/<文件名>` |

新增识别能力时先查是否已有原生素材族；普通指令卡使用 `commands`，不要从游戏截图手工裁切长期模板。离线回放的少量内置原图放在 `executor/perception/templates/cn/identities/`，它们只作回归素材，不是从者登记表。

## 生成与校验

```bash
npm run resources:update      # 下载 CN 缺失素材、生成索引与清单
npm run resources:update -- --region JP   # 其他区服写入独立目录
npm run resources:verify      # 校验本地文件 SHA-256
npm run resources:contract    # 校验清单包含运行时必需类别
npm run resources:selftest    # 校验任务映射、类别合约和清单哈希逻辑
```

首次运行资源命令时，旧的 `data/` 国服内容会复制到 `data/CN/`，原文件保留供校验对照。`resources:contract` 和 `resources:selftest` 不需要本地图库或网络，已纳入 `npm run verify`。修改 `scripts/asset-jobs.mjs` 的资源类别后，必须运行 `resources:update` 并提交新的 `data/CN/resource-manifest.json`；否则 CI 会发现清单缺少 `commands.ascension` 等非空类别。图库、原始元数据与清单都按 `data/<区服>/` 隔离，切换区服不会重下已有区服。

## 边界

- 资源覆盖不等于识别验收。素材文件数只能证明素材已下载，真实识别需要在 `docs/testing.md` 的截图与留出集规则下单独验证。
- 测试的图库隔离和生产资源根规则见 [测试与验收](testing.md#2-行为变更规则)；本机图库的有无不能改变单测或回放结论。
- 感知运行时只读本地文件，不访问 Atlas 网络。
- Atlas 原始 JSON 由资源更新维护，不作为手工编辑源。
