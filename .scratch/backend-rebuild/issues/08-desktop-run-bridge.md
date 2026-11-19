# 08: 桌面端一键运行与真实运行面板

**Status:** ready-for-agent
**Blocked by:** 05（执行器）、07（运行设置与恢复策略）

## Context

`src-tauri/src/lib.rs` 的 `start_run` 目前只登记 `RunStatus`，`stop_run` 只清状态；`src/components/RunOverlay.vue` 的面、回合、动作、耗时全是写死演示数据。前端通过 `src/device.ts` 调 Tauri 命令。

本票据把壳与执行器真正连起来，不重写壳的既有设备预览功能。

## Goal

桌面端选设备与作业后能启动 Python 执行器子进程；运行面板实时显示真实状态与恢复道具消耗；停止按钮立即终止执行器；浏览器预览明确禁用运行。

## Constraints

- Rust 不实现任何战斗、识别或作业编译逻辑。
- 同一时间只允许一个执行器子进程。
- 停止后设备上不得再有点击；子进程必须被终止或按协议安全退出。
- 子进程 stdout 使用 UTF-8 JSON Lines，Windows 下不能因编码卡死。
- 浏览器预览下运行按钮禁用并说明原因。
- `npm run replay` 与 `npm run build` 全绿。

## Implementation notes

壳侧新建 `src-tauri/src/run.rs`：

- `RunManager` 保存当前子进程、运行目录、最新状态。
- 启动参数：作业路径、serial、区服、运行目录、场数与恢复策略（来自 07 的设置）。
- 启动后读取子进程 stdout，逐行解析事件并通过 Tauri 事件转发给前端。
- 停止：先发送停止信号，等待有限时间；未退出则终止子进程，记录原因。
- 子进程异常退出：捕获退出码与 stderr，发 `failed` 事件。
- 运行目录统一为应用数据目录下的运行记录位置；开发期可映射到项目 `logs/farm/`。

新增或修改 Tauri 命令：

- `start_run(serial, job_path, settings)`
- `stop_run()`
- `get_run_state()`

前端：

- 新增 `src/run.ts`：封装启动、停止、状态查询与事件订阅。
- `RunOverlay.vue` 改为消费真实事件：面、回合、动作、耗时、恢复消耗、日志位置。
- `App.vue` 管理运行状态；运行中禁止重复启动。
- 运行设置 UI 使用 07 的数据模型。

执行器需要提供假运行模式用于桌面联调（使用 fake 设备，不接触真实模拟器）。

## Steps

1. 实现壳的进程管理与事件转发。
2. 实现前端运行 API 与事件订阅。
3. 替换 RunOverlay 的演示数据。
4. 接入运行设置与单实例限制。
5. 用执行器假运行模式做桌面联调。
6. 运行 Verification。

## Verification

```bash
cargo check --manifest-path src-tauri/Cargo.toml
npm run build
npm run replay
python -m executor.replay

# 桌面联调（假运行，不接触真实设备）
npm run tauri dev
# 期望：能启动假运行，面板状态推进，停止后立即结束
```

## Acceptance criteria

- [ ] 开始运行会启动执行器子进程并读取选中的作业。
- [ ] 面板所有字段来自真实事件，无演示文案。
- [ ] 停止按钮立即结束运行，之后无设备点击。
- [ ] 重复启动被阻止并提示。
- [ ] 子进程异常退出时 UI 显示原因与日志位置。
- [ ] 浏览器预览运行禁用。
- [ ] 假运行模式下可完整演示启动、事件推进与停止。
- [ ] Rust 检查、前端构建、回放全部通过。

## References

- 现有空壳：`src-tauri/src/lib.rs`、`src/components/RunOverlay.vue`。
- 前端设备调用参考：`src/device.ts`。
- 进程缝协议：`docs/architecture.md` 第 3 节。
- 需求：F6、F8.4。
