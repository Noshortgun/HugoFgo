# 19: LDPlayer 快速原始帧截图通道（优先）

## Parent

#3（设备网关）

## Priority

优先施工，先于 #16 的正式实机联调，并作为 #12 速度验收的发布前置。离线感知规则可与本票并行。

## What to build

为设备网关增加可选的 **LDPlayer `RenderWindow` 原始帧通道**，替换当前「adb 子进程 + PNG 编解码 + 传输」的高频路径。感知仍只消费 `Frame`，不关心帧来自哪里。

本票只实现截图，不实现战斗逻辑，也不把原型直接并入正式执行器。

## Context and prototype evidence

当前 `adb exec-out screencap -p` 平均约 **454 ms/帧**，是每次动作都要支付的固定成本。

原型 `prototype/perception-fast-path/window_capture.py` 已在本机验证：

- 枚举 LDPlayer 外层窗口，再定位类名为 `RenderWindow` 的子窗口；
- `PrintWindow(..., PW_CLIENTONLY | PW_RENDERFULLCONTENT)` 读取纯游戏画面；
- 输出恰好 **1280×720**，不含模拟器工具栏；
- 平均 **13.82 ms/帧**；截图 + 多锚点感知平均 **21.68 ms**、P95 **23.49 ms**（80 帧）。

## Implementation requirements

- 新增可选 `ldplayer-window` 设备适配器，输出原始像素 `Frame`；不要为了沿用 adb 的 PNG 字节接口再次编码 PNG。
- 正确解析 64 位 Windows HWND/HDC/HBITMAP，释放 GDI 资源；截图校验尺寸、空帧、全黑帧和解码/复制失败。
- 通道可配置，默认仍为 adb；找不到 LDPlayer 或 `RenderWindow`、分辨率不支持、窗口不可用时明确报错，不静默退回。
- 保持既有 adb 与 fake 适配器行为不变；坐标仍以 1280×720 为基准。
- 记录每帧捕获耗时与失败原因，供 #16/#17 的端到端日志使用。

## Acceptance criteria

- [ ] 同一模拟器、同一画面下，快速通道与 adb 截图内容一致，且快速通道不含窗口边框或工具栏。
- [ ] 输出恰好 1280×720 原始帧；连续至少 100 帧无资源泄漏、黑帧或尺寸漂移。
- [ ] 本机实测平均与 P95 均显著低于 adb，目标单帧 `<100 ms`；原型 13.82 ms 只作参考，正式实现需重新测量。
- [ ] 窗口不存在、最小化、被遮挡或图形渲染导致捕获失败时有明确行为；实机记录哪些场景可用，不凭空承诺后台/最小化可用。
- [ ] 通道失败不影响既有 adb 路径与回放；`npm run replay` 与构建保持通过。
- [ ] 文档写明适用范围：Windows + LDPlayer；其他模拟器不因此自动获得支持。

## Verification

使用真实 LDPlayer 验证，不能用合成图片代替。对照 adb 保存同屏证据，并运行快速通道连续截图、感知组合与既有回放。最小实机动作验证由 #16/#17 接续，本票不自行扩展为自动刷本。
