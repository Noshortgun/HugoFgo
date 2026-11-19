# 03: 新建设备网关

**Status:** done（2026-11-20；设备网关、adb/fake 适配器、14 项自检与评审修复完成）
**Blocked by:** 01、02（均已完成）

## Context

当前有三套设备访问代码：

- `src-tauri/src/device.rs`：Rust 的 adb 封装，供设备面板使用，保留。
- `scripts/farm_ember.py`：Python 原型内直接 `subprocess` 调 adb，硬编码路径与 serial。
- `scripts/device.mjs`：Node 辅助脚本。

按 ADR-0001，新执行器是 Python 子进程并自带设备层；本票据只建设执行器内的设备网关，不动 Rust 壳。

## Goal

执行器内部只有一套设备接口；实机与回放分别使用 adb 与 fake 两个适配器，上层代码不感知差异。设备配置包含区服。

## Constraints

- 坐标统一使用 1280×720 基准；由网关换算到设备实际分辨率。
- 不支持的分辨率必须报错，不允许直接盲点。
- adb 路径、serial、区服均为配置项，不硬编码。
- 截图无效、adb 失败、设备离线都要抛出带上下文的错误。
- 不修改 `scripts/farm_ember.py`；它仍是行为参考。
- `npm run replay` 保持 36/36。

## Implementation notes

新建：

- `executor/device/base.py`：`Device` 协议与 `DeviceError`。
- `executor/device/adb.py`：实机适配器。
- `executor/device/fake.py`：回放适配器。
- `executor/device/config.py`：`DeviceConfig` 与区服枚举。
- `executor/device/__main__.py`：自检与手动验收入口。

接口形状：

```python
class Device(Protocol):
    def metrics(self) -> DeviceMetrics: ...      # width/height/density/region
    def frame(self) -> Frame: ...                # image + width/height + captured_at
    def tap(self, x: int, y: int) -> None: ...   # 参数是 1280×720 基准坐标
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None: ...
```

`DeviceConfig` 字段：`serial`、`region`（`CN` / `JP` / `TW`）、`adb_path`（可选）、`width` / `height`（可选，缺省从设备读取）。

`AdbDevice` 实现要点：

- adb 定位顺序：`config.adb_path` → `HUGOFGO_ADB` → PATH → 常见模拟器路径。
- 截图用 `exec-out screencap -p`，校验 PNG 头与尺寸。
- 坐标换算：基准坐标 × 实际分辨率 / 1280×720，取整并限制在屏幕内。
- 所有 adb 调用带 `-s serial`；非零退出码抛 `DeviceError`，包含命令与 stderr。

`FakeDevice` 实现要点：

- 构造时接受帧列表或帧目录；`frame()` 按序返回。
- `tap` / `swipe` 只记录到 `actions` 列表，不调用外部进程。
- 提供 `actions` 供回放断言。

区服只作为配置存在；本票据不实现任何区服识别规则。

## Steps

1. 定义 `Device` 协议、数据结构、`DeviceError` 与 `DeviceConfig`。
2. 实现 `AdbDevice`，先用 `--self-test` 覆盖坐标换算与错误分支，不依赖真机。
3. 实现 `FakeDevice`，记录动作用于后续回放。
4. 实现 `python -m executor.device --self-test` 与 `--list`（后者需要真机，仅手动）。
5. 运行 Verification。

## Verification

```bash
# 自检：坐标换算、PNG 校验、fake 动作记录、配置错误
python -m executor.device --self-test

# 既有回放不受影响
npm run replay
# 期望：36/36 passed

# 可选实机检查（需要模拟器在线）
python -m executor.device --list
```

## Acceptance criteria

- [ ] `Device` 协议只有 `metrics/frame/tap/swipe` 四个操作，上层无需了解 adb。
- [ ] `AdbDevice` 与 `FakeDevice` 实现同一协议。
- [ ] 基准坐标到实际分辨率的换算有自检覆盖，越界被限制。
- [ ] adb 路径、serial、区服均可配置；缺失时错误信息可操作。
- [ ] 无效 PNG、adb 失败、设备离线均抛出带上下文的 `DeviceError`。
- [ ] `FakeDevice` 不调用外部进程，且能记录全部动作。
- [ ] `python -m executor.device --self-test` 退出码 0。
- [ ] `npm run replay` 36/36。

## References

- ADR-0001：执行器为 Python 子进程，自带设备层。
- ADR-0003：区服属于设备。
- 现有实现参考：`src-tauri/src/device.rs`（adb 发现与截图）、`scripts/farm_ember.py`（Python adb 调用）。
- 架构：`docs/architecture.md` 2.2。
