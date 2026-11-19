# 07: AP 恢复策略、连续出击与运行设置

**Status:** ready-for-agent
**Blocked by:** 05（画面状态机执行器）

## Context

旧执行器遇到 AP 恢复页一律停止（行为基线 B06）。用户已确认改为白名单制：4 种苹果与圣晶石共 5 种恢复方式，默认仅允许赤铜苹果，选择持久化；固定场数刷完后可选继续清空剩余 AP，清 AP 阶段不再使用任何恢复道具。决策见 ADR-0004。

前端目前没有任何体力/苹果相关逻辑，本票据同时新增运行设置的数据模型与最小 UI。

## Goal

用户能配置一次运行的场数、允许使用的恢复方式、是否清 AP；执行器严格按策略补体力或停止，并把每次消耗计入事件与历史。

## Constraints

- 默认只允许赤铜苹果；圣晶石必须显式勾选才可用。
- 清 AP 阶段不使用任何恢复道具。
- 白名单为空时，AP 恢复页只停不点。
- 不在未知恢复页面上盲点；无法识别恢复选项时安全停止。
- 恢复选项的区服文案先实装国服；日服台服在 13。
- 新旧回放全绿。

## Implementation notes

新建：

- `executor/core/ap_policy.py`：恢复策略与决策纯函数。
- `executor/session/settings.py`：运行设置的读取与持久化。
- 壳侧设置命令（与 08 协同）：读取/保存运行设置。
- 前端运行设置 UI：场数输入、5 项恢复方式复选框、清 AP 开关。

数据结构：

```python
RecoveryPolicy(
    allowed: set[RecoveryItem],   # gold/silver/copper/bronze/saint_quartz 的具体键名以国服恢复页实机采集为准
    drain_after_turns: bool,
)
RunSettings(
    battles: int,
    recovery: RecoveryPolicy,
)
```

执行器行为：

- 识别到 AP 恢复页时，列出可用恢复选项。
- 场数未完成且当前不是清 AP 阶段：只选择 `allowed` 中的选项；没有可用选项则安全停止。
- 进入清 AP 阶段后：恢复页直接停止并标记"AP 已清空"，不点任何道具。
- 每次成功消耗发出 `recovery_used` 事件，带道具键与累计数量。
- 达到用户场数且未开清 AP：末场结算后完成，不点连续出击。
- 达到用户场数且开清 AP：继续出击，直到 AP 归零。

设置持久化：桌面版存到应用数据目录的设置文件；浏览器预览可读写 localStorage 但不能启动运行。默认值为 `battles=1`、仅赤铜苹果、`drain_after_turns=false`。

## Steps

1. 采集并登记国服 AP 恢复页素材，确认 5 种选项的实际文案与位置。
2. 实现 `ap_policy.py` 纯函数与默认设置。
3. 在执行器恢复页分支接入策略与消耗事件。
4. 实现设置持久化与前端运行设置 UI。
5. 补回放：白名单内可用、非白名单不点、清 AP 不吃道具、AP 归零完成。
6. 运行 Verification。

## Verification

```bash
python -m executor.replay --filter recovery
python -m executor.replay
npm run replay
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```

## Acceptance criteria

- [ ] 默认仅允许赤铜苹果，其他选项不点。
- [ ] 用户选择持久化，重启后保持。
- [ ] 白名单为空时恢复页安全停止。
- [ ] 清 AP 阶段不使用任何恢复道具，AP 归零正常完成。
- [ ] 每次消耗有日志与事件，面板可见累计数量。
- [ ] 固定场数末场不多点连续出击。
- [ ] 新旧回放全绿，构建与 Rust 检查通过。

## References

- ADR-0004：AP 恢复白名单。
- 行为基线：B06、B07。
- 旧行为参考：`scripts/farm_ember.py` 的 `result_action()` 与 `finish_results()`。
- 需求：`docs/requirements.md` F5.7、F5.10、F8。
