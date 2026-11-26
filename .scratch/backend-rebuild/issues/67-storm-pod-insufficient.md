# 67: 识别风暴罐不足弹窗并安全停止运行

## Context

实机截图 `captures/2026-11-26/key/storm-pod-insufficient.png`（emulator-5554，1280×720）：白纸化地球关卡结算后，画面中央弹出蓝色对话框：

> 挑战该任务所需风暴罐不足
> 返回关卡选择界面。

仅有一个「关闭」按钮（约 640,562）。执行器目前没有该弹窗的识别与处理（`executor` 中无任何风暴罐相关逻辑），战斗结束后会卡在等待结算的状态，直到超时或停在未知页面。

风暴罐按日恢复，等待恢复没有意义，正确处理是关闭弹窗并停止本次运行。

## Goal

执行器在任意页面识别到「风暴罐不足」弹窗时，点击「关闭」，随后以明确理由安全停止运行，事件与运行记录中可见中文原因。

## Constraints

- 只点「关闭」，不在弹窗或后续页面上做任何其他点击。
- 弹窗只会在结算后出现；连续出击前出现也属于这一场景。识别采用独立 overlay，不依赖当前页面等待逻辑。
- 停止理由固定为 `storm_pod_insufficient`，摘要用中文说明「风暴罐不足，已关闭弹窗并停止」。
- 本票据只实装国服文案；日服台服在 13。
- 按工作约定补离线回放用例，新旧回放全绿。

## Implementation notes

- 感知侧新增 overlay（如 `Overlay.STORM_POD_INSUFFICIENT`），用弹窗标题/文案区域模板匹配，参考现有 `REPEAT_DIALOG`、`AP_RECOVERY_CONFIRM` 的识别方式。
- `executor/core/runner.py`：该 overlay 优先级高于当前页面等待逻辑，出现时点击「关闭」并发出 action 事件，随后 `RunStatus.STOPPED`、reason `storm_pod_insufficient`。
- 点击关闭后立即停止，不读取或确认关闭后的页面，不再尝试出击；摘要不得声称已经返回关卡选择页。
- 回放用例引用已入库截图 `captures/2026-11-26/key/storm-pod-insufficient.png`：期望识别出 overlay、动作序列仅「关闭」、最终停止理由正确。

## Steps

1. 感知层加入风暴罐不足弹窗识别，回放接入入库截图。
2. runner 接入 overlay 处理：点关闭 → 停止 → 中文摘要。
3. 补回放用例并跑全量回放。

## Verification

```bash
python -m executor.replay --filter storm
python -m executor.replay
npm run replay
```

## Acceptance criteria

- [ ] 入库截图被识别为风暴罐不足弹窗，不误判其他页面。
- [ ] 动作序列只有「关闭」一次点击，无其他盲点。
- [ ] 运行以 `storm_pod_insufficient` 停止，事件与中文摘要可见。
- [ ] 结算后出现弹窗能处理；连续出击前出现归为同一场景，不虚构其他实机时机。
- [ ] 新旧回放全绿。

## References

- 截图与登记：`captures/2026-11-26/index.json`
- 类似机制：07（AP 恢复策略）、`Overlay.REPEAT_DIALOG` / `AP_RECOVERY_CONFIRM`
