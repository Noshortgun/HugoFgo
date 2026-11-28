# 资源、素材与引用

本页区分四类容易混淆的内容：Atlas 图库、感知模板、回放截图和本地实机证据。**文件存在不等于识别验收通过；未被实机代码读取也不等于可以删除。**

## 1. 资源分类与用途

| 类别 | 路径 | 谁在使用 | 入库与清理原则 |
| --- | --- | --- | --- |
| Atlas 图库与元数据 | `data/<区服>/assets/`、`atlas-*.json` | 编队 UI 与实机身份识别 | 图库和原始大 JSON 默认不入库；由资源管线再生成 |
| 资源清单 | `data/<区服>/resource-manifest.json` | 完整性校验、类别合约 | 入库；记录来源、版本、数量、路径与 SHA-256 |
| 当前感知模板 | `executor/perception/templates/cn/` | 实机识别器直接加载 | 运行硬依赖，全部入库 |
| 固定 scrcpy 服务端 | `resources/scrcpy/` | scrcpy 通道启动前校验并推送到设备 | 官方 3.3.3 服务端、来源清单、SHA-256 与 Apache-2.0 许可证全部入库；随 Windows 安装包分发 |
| 内置身份样本 | `executor/perception/templates/cn/identities/` | 离线回归与少量未绑定分析 | 不是从者登记表，不代替生产图库 |
| 感知断言 | `executor/perception/fixtures/` | 单测、统一回放、fixtures 校验 | 测试硬依赖，全部入库 |
| 旧分类模板与标注 | `cv/templates/`、`cv/labels.json` | 旧四态分类器和统一回放 | 只服务离线验证，全部入库 |
| 回归与关键帧 | `captures/<日期>/key/`、`.gitignore` 显式允许的根部截图 | 回放、模板构建、故障证据 | 入库并登记索引，不按“运行不读取”删除 |
| 文档导读图片 | `docs/assets/` | 使用指南与工作原理中的场景化说明 | 只保留必要的模糊或缩放派生图；来源必须是已入库关键帧，不替代原始证据或验收 |
| 原始过程帧 | `captures/<日期>/` 其他截图 | 排障、关键帧来源、部分模板再生成 | 多数被忽略；必须查引用后才能归档 |
| 实机与留出集证据 | `logs/farm/`、`logs/holdout/` | 人工验收、故障定位、后续回归候选 | 本地唯一证据，不入库，不作为干净 CI 依赖 |
| 临时实验与治理源 | `.scratch/` | 草稿、派生缓存、票据与行为基线 | 默认不入库；已跟踪的 `backend-rebuild/` 是例外 |

高风险例外：`cv/build_templates.py` 的部分裁切源是被 Git 忽略的原始截图。删除它们不会立刻破坏现有回放，却会导致模板无法重新生成。`executor/perception/build_templates.py` 和模板清单也记录当前感知模板的来源截图；其中面、回合、NP 与暴击率数字模板生成到 `templates/cn/digits/`，来源裁剪框逐项记录在清单的 `digits` 段；NP 的暗帧变体以 `dim` 后缀登记。新增数字字形必须同时登记真实截图来源与识别断言，不能只把图片文件放入模板目录。

因此清理顺序固定为：**查代码与清单引用 → 查票据/报告/行为基线 → 区分入库和本地证据 → 归档候选 → 跑验证**。禁止按目录年龄、文件是否入库或“测试没直接用”批量删除。

## 2. Atlas 查询入口

Atlas 原始元数据与图库由 `npm run resources:update` 生成，默认不入库。原始 JSON 是单行大文件，查询单个从者时使用命令，不要读取整份文件：

```bash
npm run data:query -- find <名称或 ID>
npm run data:query -- servant <ID>
npm run data:query -- assets <ID>
npm run data:query -- counts
```

每条查询都可追加 `--json` 或 `--region JP` / `--region TW`。`assets` 同时显示 Atlas URL、预期本地路径和是否已下载。

## 3. 素材字段与生成路径

| 字段 | 用途 | 本地路径 |
| --- | --- | --- |
| `extraAssets.faces.ascension` | 状态头像、助战头像 | `data/<区服>/assets/atlas/faces/<从者ID>-<再临>.png` |
| `extraAssets.faces.costume` | 灵衣状态头像、助战与队伍身份核对 | `data/<区服>/assets/atlas/faces/<从者ID>-costume-<灵衣ID>.png` |
| `extraAssets.commands.ascension` | 普通指令卡人物原图（透明 RGBA） | `data/<区服>/assets/atlas/commands/<从者ID>/<再临>.png` |
| `extraAssets.commands.costume` | 灵衣指令卡原图 | `data/<区服>/assets/atlas/commands/<从者ID>/costume-<灵衣ID>.png` |
| `extraAssets.charaGraph.ascension` | 卡面立绘 | `data/<区服>/assets/atlas/cards/<从者ID>-<再临>.png` |
| `extraAssets.equipFace.equip` | 概念礼装图标 | `data/<区服>/assets/atlas/ce/<礼装ID>.png` |
| `skills[].script.SelectAddInfo[].btn[].imageUrl` | 追加选择图片按钮 | `data/<区服>/assets/atlas/secondary/<图片名>.png` |
| `extraAssets.item` / `masterFigure` | 御主礼装道具与人物图 | `data/<区服>/assets/atlas/mystic/` |
| `skills[].icon` | 技能图标 | `data/<区服>/assets/atlas/skills/<文件名>` |
| `skills[].functions[].buffs[].type` = `donotSelectCommandcard` + `functions[].funcType` = `cardReset` | 锁卡技能表（技能序号与持续手数） | `data/<区服>/skill-effects-by-svt.json` |

新增识别能力时先查是否已有原生素材族；普通指令卡使用 `commands`，不要从游戏截图手工裁切长期模板。

## 4. 实际资源类别

`resource-manifest.json` 的 `assetCategories` 记录实际下载类别：

| 类别 | 内容与条件 |
| --- | --- |
| `faces.base` / `faces.ascension` | 基础头像与各再临头像 |
| `faces.costume` | 各从者的灵衣头像，身份归一到原从者；`800100` 另保留特殊形态别名。不作为全局非空强制类别 |
| `cards.base` / `cards.ascension` | 基础卡面与再临卡面；后者目前只为 `800100`、`4000100` 下载 |
| `cards.costume` | 特殊从者的灵衣卡面 |
| `commands.ascension` / `commands.costume` | 各再临与灵衣的普通指令卡原图 |
| `ce` | 概念礼装图标 |
| `classes` | 单独构造的职阶图标任务，不来自 `extraAssets` |
| `mystic.item` / `mystic.figure` | 御主礼装道具与人物图，分男女御主形态 |
| `skills.servant` / `skills.mystic` | 从者与御主礼装技能图标 |

这张表说明当前管线会生成什么，不要求每个类别对所有从者都非空。运行必需的非空类别由 `resources:contract` 校验。

## 5. 生成与校验

```bash
npm run resources:update      # 下载 CN 缺失素材、生成索引与清单
npm run resources:update -- --region JP
npm run resources:verify      # 校验本地文件 SHA-256
npm run resources:contract    # 校验清单包含运行时必需类别
npm run resources:selftest    # 校验任务映射、类别合约和清单哈希逻辑
```

Windows 安装版由桌面壳启动随包 Node.js 执行同一资源管线，不需要用户安装 Node.js。开发期读写项目 `data/<区服>/`；安装版读写应用数据目录。安装包不包含图库或 Atlas 原始元数据，空库首次更新完成后编队界面会重新加载。

修改 `scripts/asset-jobs.mjs` 的资源类别后，必须运行 `resources:update` 并提交新的 `data/CN/resource-manifest.json`。`resources:contract` 和 `resources:selftest` 不需要本地图库或网络，已纳入 `npm run verify`。

## 6. 引用检查与证据边界

```bash
node scripts/check-references.mjs                  # 文档链接与仓库必需模板源
node scripts/check-references.mjs --include-local  # 额外检查本机唯一模板源
npm run fixtures:check                            # 感知 fixtures 与截图
```

默认检查只强制干净克隆中应该存在的文件，不要求 CI 拥有本机 `logs/` 或被忽略的原始截图。`--include-local` 用于实机素材归档前检查本地来源是否完整。

- 资源覆盖不等于识别验收；素材数量只能证明下载覆盖。
- 感知运行时只读本地文件，不访问 Atlas 网络。
- Atlas 原始 JSON 由资源更新维护，不作为手工编辑源。
- 实机证据、回放证据和当前能力的关系见 [测试与验收](testing.md) 与 [当前状态](status.md)。
