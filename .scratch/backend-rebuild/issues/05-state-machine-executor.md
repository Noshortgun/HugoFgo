# 05: 画面状态机执行器

**Status:** ready-for-agent
**Blocked by:** 03（设备网关）、04（作业编译）

## Context

旧执行器 `scripts/farm_ember.py` 的 `main()` 是线性流程：假设从助战页开始，依次点队伍、战斗、结算。它能刷种火，但只认固定作业与固定路径，异常分支靠各处补丁。

新架构要求执行器由画面状态驱动，且能从任意已知画面接续。本票据交付第一条完整闭环，补刀策略与 AP 恢复策略分别在 06、07 中增强。

## Goal

给定编译后的作业计划与设备，执行器能完成：

```
助战选择 → 队伍确认 → 战斗（按面/回合执行） → 结算 → 连续出击或完成
```

遇到未知画面、从者详情、意外冠位页时安全停止并留存证据；从战斗中途、结算页、助战页启动都能接续。

## Constraints

- 只能通过 03 的 `Device` 接口操作设备，不直接调 adb。
- 只能通过 04 的 `ExecutionPlan` 获取作业动作，不自己解析作业 JSON。
- 每个动作后必须重新截图识别；禁止在未知画面上使用固定坐标回退。
- 本票据不实现补刀策略细节（06）与 AP 道具白名单（07）；遇到 AP 恢复页先安全停止。
- 旧 `cv/replay.py` 保持 36/36；新回放用例放在新执行器下，不删除旧体系。
- 首期只实装国服识别配置。

## Implementation notes

新建：

- `executor/perception/observation.py`：`Observation` 数据结构。
- `executor/perception/recognizer.py`：`Perception.read(frame) -> Observation`。
- `executor/perception/regions/cn.py`：国服锚点文案、区域限制与模板配置。
- `executor/core/state.py`：`ScreenState` 枚举。
- `executor/core/runner.py`：对外 `run()`。
- `executor/core/layout.py`：1280×720 基准坐标常量。
- `executor/session/events.py`：事件出口与 JSON Lines 输出。
- `executor/replay/`：新回放 harness，复用 `captures/` 素材。

核心接口：

```python
run(plan: ExecutionPlan, device: Device, perception: Perception, events: EventSink) -> RunResult
```

`Observation` 至少包含：

- `state`：`menu / support / team / battle / card / result / loading / unknown`
- `anchors`：识别到的文字及其坐标
- `cards`：指令卡候选（含区域过滤后的结果）
- `wave` / `turn`：能识别则填，不能则为空
- `uncertain`：识别置信不足时必须为真

状态机规则：

- `unknown`：等待并重试，超过上限后安全停止；不点击。
- `loading`：等待，不点击。
- `support`：按作业要求搜索助战；沿用行为基线 B12/B13 的列表刷新与条目点击规则。
- `team`：确认队伍后点开始任务。
- `battle`：按计划执行当前面/回合动作；动作后重新识别。
- `card`：选择宝具与指令卡；宝具卡序读 `npPositions`。
- `result`：推进结算；连续出击只在弹窗出现后点击，末场不点。
- 从者详情、意外冠位页：立即停止。
- AP 恢复页：本票据一律停止（07 再按白名单处理）。

`EventSink` 至少接收：`state_changed`、`action`、`wave_started`、`turn_started`、`warning`、`finished`、`failed`。每个事件带时间戳；`action` 带基准坐标与原因。

回放 harness 用 `FakeDevice` 喂截图、用固定或假感知产生 `Observation`，断言动作序列、停止原因与事件顺序。行为基线 B01–B23 中属于本票据范围的条目必须有用例。

## Steps

1. 定义状态、观察、事件与运行结果数据结构。
2. 实现国服感知适配器，先复用 `cv/state_classifier.py` 的模板，再逐步迁移。
3. 实现 `runner.py` 的状态循环与安全停止。
4. 接入 03 的设备网关与 04 的执行计划。
5. 新建回放 harness，迁移本票据涉及的行为基线用例。
6. 运行 Verification。

## Verification

```bash
# 新执行器回放：完整闭环、未知停止、任意已知画面接续
python -m executor.replay
# 期望：全部通过，退出码 0

# 旧回放保持
npm run replay
# 期望：36/36 passed

# 类型与构建不受影响
npm run build
```

## Acceptance criteria

- [ ] 完整闭环在新回放中跑通，含加载与过渡画面。
- [ ] 每个动作后重新识别；不存在连续盲点。
- [ ] unknown、从者详情、意外冠位页、AP 恢复页均安全停止并保存证据。
- [ ] 从战斗中途、结算页、助战页启动均可接续。
- [ ] 连续出击只在弹窗出现后点击，末场不点。
- [ ] 行为基线中本票据相关条目均有新回放用例并通过。
- [ ] 事件流包含面、回合、当前动作，可被壳直接转发。
- [ ] 旧回放 36/36，构建通过。

## References

- 行为基线：`.scratch/backend-rebuild/behavior-baseline.md` B01–B23。
- 旧流程参考：`scripts/farm_ember.py` 的 `main()`、`select_support()`、`finish_results()`。
- 架构：`docs/architecture.md` 2.3–2.5。
- 安全规则：`docs/requirements.md` F8。
