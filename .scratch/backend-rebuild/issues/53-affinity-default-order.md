# 53: 选卡默认顺序加入克制

Source: 2026-11-23 与用户确认的设计结论，承接 #44。

## What to build

#44 已把克制做成可排序判据，但新作业的默认顺序仍是 `servant → cards → crit`，克制只在末位兜底。现在把默认顺序改为 `servant → cards → affinity → crit`，让克制在默认配置下真正参与比较；旧作业的三项顺序保持原样。

同时给指令卡动作事件补上每张卡的克制档位，实机逐面逐回合核对时不用再靠截图猜为什么选中这三张卡。

## Acceptance criteria

- [ ] `FallbackStrategy`、`defaultFallbackStrategy`、Chaldea 导入与 `scripts/chaldea-to-hugofgo.mjs` 的默认顺序均为 `servant → cards → affinity → crit`。
- [ ] 旧作业 `strategyOrder` 只有三项时行为不变，克制仍作末位兜底；往返保存不得自动补写 `affinity`。
- [ ] 指令卡动作事件包含 `affinity`；识别缺失时为 `null`，不阻塞选卡。
- [ ] 单元测试与统一回放覆盖“默认顺序下克制先于暴击率生效”和“旧顺序不变”。
- [ ] 文档写明默认顺序、克制按当前选中目标判定、不按职阶推算。
- [ ] `npm run verify` 全绿。

## Out of scope

- 不改卡面克制识别本身。
- 不新增职阶表推算，也不为它开票。
- 不为界面增加从者/敌方关系提示。
