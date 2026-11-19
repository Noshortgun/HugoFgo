# 06: 补刀策略与选卡在执行器中生效

**Status:** ready-for-agent
**Blocked by:** 05（画面状态机执行器）

## Context

作业中的补刀策略定义在前端 `src/types.ts` 的 `FallbackStrategy`：`allowNp`、`servantOrder`、`servantOrderSlots`、`cards`、`colorPriority`、`strategyOrder`，并可按面覆盖（`strategyByWave`）。

旧执行器的 `choose_cards()` 固定按"力击 → 迅击 → 技击"选卡，完全不读策略。本票据让策略真正决定选卡行为。

## Goal

同一手牌在不同策略下产生不同选卡结果；一面未按作业清场时，执行器按该面生效的补刀策略继续，直到该面结束或触发安全上限。

## Constraints

- 不改作业格式；策略字段语义与 `src/types.ts` 一致。
- 同名同职阶从者按槽位区分，优先使用 `servantOrderSlots`。
- 只在指令卡区域选卡；攻击动画中的文字不算手牌。
- 手牌为空、不足三张或识别不确定时安全停止，不盲点。
- 新回放与旧回放都必须全绿。

## Implementation notes

新建 `executor/core/strategy.py`：

```python
select_cards(observation: Observation, strategy: FallbackStrategy, plan: ExecutionPlan, wave: int) -> CardSelection
```

`CardSelection` 包含三张卡的基准坐标、选择理由与使用的策略来源（全局或按面）。

规则实现：

- 策略来源：`plan.strategyByWave[wave]` 非空则用该面，否则用全局 `plan.strategy`。
- 从者优先级：`servantOrder` 与 `servantOrderSlots` 联合确定；缺槽位信息时按名称匹配并记录警告。
- 卡色优先级：`colorPriority`；`cards` 为固定偏好时优先满足，`balanced` 时按 `strategyOrder` 权衡。
- `strategyOrder` 中的 `servant / cards / crit` 决定从者、卡色、暴击权重的比较顺序。
- `allowNp` 为假时，补刀阶段不选宝具卡。
- 选不出合法三张卡时返回诊断，由执行器安全停止。

在 `runner.py` 中接入：作业动作未清场、需要继续选卡时调用本模块；不要在策略模块内直接点击设备。

回放 fixture 至少覆盖：从者优先级、卡色优先级、按面覆盖、同名从者槽位区分、`allowNp=false`、手牌不足。

## Steps

1. 定义策略与选卡结果的数据结构，字段与前端类型一一对应。
2. 实现纯函数 `select_cards()`，不接触设备。
3. 在状态机执行器中接入未清场分支。
4. 补六类回放 fixture。
5. 运行 Verification。

## Verification

```bash
python -m executor.replay --filter strategy
# 期望：策略相关用例全部通过

python -m executor.replay
npm run replay
npm run build
```

## Acceptance criteria

- [ ] 同一手牌在不同策略下产生不同选卡结果，回放可断言。
- [ ] 按面覆盖生效，未配置的面使用全局策略。
- [ ] 同名同职阶从者按槽位区分。
- [ ] `allowNp=false` 时补刀不选宝具卡。
- [ ] 只在指令卡区域选卡，攻击动画文字不触发。
- [ ] 手牌为空或不足三张时安全停止并给出诊断。
- [ ] 新旧回放全绿，构建通过。

## References

- 策略字段：`src/types.ts` 的 `FallbackStrategy`、`AttackCardPlan`。
- 旧行为参考：`scripts/farm_ember.py` 的 `choose_cards()`。
- 行为基线：B16、B17、B23。
- 架构：`docs/architecture.md` 2.4。
