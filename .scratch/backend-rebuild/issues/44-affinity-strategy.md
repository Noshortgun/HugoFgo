# 44: 指令卡克制三态识别与策略排序

## Parent

无（独立增强票，源自 #12 实机验收讨论）

## Why

当前 `FallbackStrategy` 的策略顺序只有 `servant / cards / crit` 三项，克制关系在 `executor/core/strategy.py` 里固定作为最后一层兜底比较，用户无法调整；同时真实感知不提供克制信息，实机上只能按中立处理并记警告。

维护者在 2026-11-22 明确：**卡面上直接能读出克制、抵抗、无标识（中立）三种状态**，不需要按职阶推算。

## Goal

1. 感知能从指令卡上识别克制三态：`advantage`（克制）、`resist`（抵抗）、`neutral`（无标识）。
2. 克制成为策略的一等比较项：可以像从者顺序、选卡倾向、暴击率一样在策略顺序里调整位置。
3. 旧作业行为不变：没有配置克制顺序的作业，保持现有的末位兜底比较。

## Scope

- `executor/perception`：新增卡面克制标识识别，输出 `affinity ∈ {advantage, resist, neutral}`；识别失败时才退回 neutral 并记警告，不得整手牌拒选。
- `src/types.ts`：`StrategyCriterion` 增加 `affinity`（命名与现有风格一致）。
- `executor/domain`：作业编译接受新判据，旧作业缺省不改变行为。
- `executor/core/strategy.py`：按 `strategyOrder` 中的位置参与比较；不在顺序中时维持末位兜底。
- `src/components/FallbackDialog.vue`：增加「克制关系」卡片，可左右移动调整优先级，并显示当前从者/敌方关系提示（如可获得）。
- 回放与单测覆盖三态识别、顺序调整、旧作业兼容。

## Out of scope

- 暴击率识别：0 暴击星时游戏不显示百分比，维持缺失按 0 的降级处理。
- 按职阶表推算克制（本票只读卡面标识）。

## Blocked by

#12（实机验收）：需要稳定的实机采帧流程来获取克制/抵抗标识样本。

## Verification

- 三态各有真实截图用例；无标识帧作为中立对照。
- 策略顺序含 affinity 时，同手牌在不同顺序下选出不同组合的断言。
- `npm run replay`、`python -m executor.replay`、执行器单测、`npm run build` 全绿。

## References

- #12 实机验收记录：`logs/farm/20261122-033146-acceptance/README.md`
- 卡面标识证据：`logs/farm/run-1795293140147-4220/failure.png`
- 策略模型：`src/types.ts` 的 `FallbackStrategy`、`StrategyCriterion`
- 选卡实现：`executor/core/strategy.py`
