# 作业格式 v1

作业是包含编队、御主礼装、各面各回合动作与选卡策略的 JSON 文件。推荐用可视化界面创建和编辑，再导出备份或分享。

机器可读契约是 `schemas/job-v1.schema.json`，完整示例是 `examples/sample-job.json`。结构合法不代表当前执行器已支持运行其中的全部能力，实际接通情况见 [当前状态](status.md)。

## 1. 顶层字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `version` | 常量 `1` | 作业格式版本，必填 |
| `name` | 字符串 | 可选显示名称 |
| `mysticId` | 非空字符串 | 御主礼装标识，必填 |
| `enemyCount` | `3` 或 `6` | 敌方阵型规模，必填 |
| `formation` | 数组 | 编队槽位，必填 |
| `strategy` | 对象 | 全局补刀策略，必填 |
| `strategyByWave` | 对象 | 可选的按面补刀策略覆盖 |
| `waves` | 非空数组 | 面和回合定义，必填 |
| `warnings` | 字符串数组 | 可选警告信息 |
| `provenance` | 任意 JSON | 可选来源信息，不作为执行动作 |

Schema 不允许未列出的顶层字段。桌面编辑器显式保存时会保留原文件中的额外顶层字段，以免导入导出意外丢数据；但“编辑器保留”不等于“执行器接受”，含额外字段的文件仍需通过 Schema 与编译器校验才能运行。

## 2. 编队与槽位

`formation` 中每项至少需要：

```json
{ "slot": 0, "name": "阿尔托莉雅" }
```

槽位编号为 0–5，动作通过槽位引用从者，不使用界面会话中的临时 ID。常用可选字段：

| 字段 | 含义 |
| --- | --- |
| `svtId`、`npId` | 从者与宝具的数据标识 |
| `limitCount` | 再临阶段，1–4 |
| `role` | 职阶显示信息 |
| `support` | 是否为助战位 |
| `supportRequired` | 是否必须匹配指定助战 |
| `grandRequired` | 是否必须为冠位助战 |
| `ce`、`ceId` | 概念礼装名称与标识 |
| `grandCe`、`grandCeIds` | 冠位助战的三类礼装条件 |

编译器会拒绝重复槽位，以及引用空槽位的动作、宝具卡序、指令卡和补刀策略。

## 3. 面、回合与动作

每个 `waves` 元素包含：

- `turnCount`：回合数，必须与 `turns` 数组长度一致。
- `target`：保留的作业与界面元数据，范围 1–6；执行器不会据此自动切敌，需要切敌时必须在回合 `actions` 中显式添加 `target` 动作。
- `turns`：回合数组，每项包含 `actions`，可另含 `npPositions` 和 `attack`。

`actions` 按数组顺序执行。每个动作需要 `kind`、`title`、`detail` 和 `icon`，其中后三项用于显示。`kind` 可取：

| `kind` | 含义 | 常用附加字段 |
| --- | --- | --- |
| `skill` | 从者技能 | `memberSlot`、`skill`、`playerTargetSlot`、`secondarySelection` |
| `master` | 御主技能 | `skill`、`playerTargetSlot` |
| `spell` | 令咒 | 目标或二次选择字段 |
| `attack` | 从者宝具攻击 | `memberSlot` |
| `target` | 切换敌方目标 | `enemyTarget` 或 `fromTarget`、`toTarget` |
| `orderChange` | 换人 | `swapOutSlot`、`swapInSlot` |

`skill` 从 0 开始计数，0 表示一技能。换人上下场槽位不能相同。`secondarySelection` 保存技能或宝具的二次选择，例如暴击星消耗和宝具卡色；当前执行器会拒绝包含该字段的作业，不能仅凭 Schema 通过就认定可以运行。

## 4. 宝具卡序与指令卡

回合中的 `npPositions` 以从者槽位为键，值可以是：

- `1`、`2`、`3`：宝具卡在该回合卡序中的位置。
- `"auto"`：自动安排宝具卡位置。

```json
"npPositions": { "0": 1, "3": "auto" }
```

回合中的 `attack` 描述普通指令卡选择：

| `mode` | 行为语义 |
| --- | --- |
| `strategy` | 不指定固定卡序，使用补刀策略 |
| `prefer` | 尽量按给定卡序，发牌不符时回落到补刀策略 |
| `verify` | 必须匹配给定卡序，不匹配时停止并提示 |

`cards` 中每项包含从者 `slot`、卡色 `type`（`buster / arts / quick`），可选 `deckIndex` 用于记录牌库中的具体卡片。

## 5. 补刀策略

全局 `strategy` 和 `strategyByWave` 中的覆盖项使用相同结构：

| 字段 | 含义 |
| --- | --- |
| `allowNp` | 补刀时是否允许使用宝具 |
| `servantOrder` | 普通指令卡按从者名称排列的先后顺序，不包含宝具卡 |
| `servantOrderSlots` | 普通指令卡按槽位排列的先后顺序，可区分同名从者，不包含宝具卡 |
| `cards` | `buster / arts / quick / balanced` 选卡倾向 |
| `colorPriority` | 卡色优先顺序 |
| `strategyOrder` | `servant / cards / crit` 各策略的比较顺序 |

普通指令卡按卡序位置逐张选择：每个位置先按 `strategyOrder` 比较当前剩余普通卡，选定并移除一张后，再选择下一位置。已保留的宝具卡不参与普通卡排序；完全并列时选择画面中最靠左的卡。宝具固定在第二或第三卡序位置时，只占用该位置，之前的普通卡选完后，后续位置继续按同一规则选择剩余普通卡。不得改为一次性比较整组普通卡组合，也不得在选定普通卡后按屏幕位置重新排序。

`strategyByWave` 的面编号键为 `"1"`、`"2"`、`"3"`；值为 `null` 表示该面没有独立覆盖。

## 6. 本地校验

```bash
python -m executor.domain examples/sample-job.json
python -m executor.domain examples/sample-job.json --json
```

非法作业会返回非零退出码，并用 JSON 路径、面或回合定位错误。校验覆盖 Schema 结构、回合数、重复槽位、空槽位引用和换人槽位冲突等规则。
