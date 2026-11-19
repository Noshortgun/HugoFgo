# 10: 真实运行记录

**Status:** ready-for-agent
**Blocked by:** 08（桌面运行桥接）

## Context

`src/App.vue` 的历史页写死了两条演示记录。执行器已有每次运行的日志与截图目录；08 提供运行事件与进程结果。本票据把这些信息固化为可查询的历史记录。

## Goal

每次运行生成一条真实记录；历史页展示作业、设备、区服、起止时间、耗时、结果与恢复道具消耗，并能定位到日志和失败截图。

## Constraints

- 记录由真实运行产生，不允许前端伪造。
- 完成、手动停止、失败三种结果必须区分。
- 记录持久化，重启应用后仍在。
- 历史页演示数据移除。
- 不改变既有运行日志与截图的保存内容。

## Implementation notes

执行器/会话侧：

- 每次运行结束（完成、停止、失败）都生成一条记录。
- 字段：`id`、`jobName`、`jobFile`、`serial`、`region`、`startedAt`、`finishedAt`、`durationMs`、`result`、`battlesCompleted`、`recoveryUsed`、`runDir`、`error`。
- 记录以 JSON Lines 追加到统一历史文件；运行目录仍按次保存详细日志与截图。
- 开发期历史文件位于项目运行目录；打包版位于应用数据目录（与 11 协调）。

壳侧新增命令：

- `list_run_history(limit?)`
- `get_run_record(id)`
- `open_run_artifacts(id)`（打开日志目录或返回路径）

前端：

- 历史页改为调用壳命令；展示结果、耗时、消耗与证据入口。
- 空历史显示真实空状态，不显示演示数据。

## Steps

1. 定义记录结构并在执行器结束时写入。
2. 实现壳的历史查询与证据定位命令。
3. 替换前端历史页。
4. 用手动停止、失败、完成三种运行各生成一条记录验证。
5. 运行 Verification。

## Verification

```bash
cargo check --manifest-path src-tauri/Cargo.toml
npm run build
npm run replay
python -m executor.replay

# 桌面手测：完成/停止/失败各产生一条记录，重启后仍在
npm run tauri dev
```

## Acceptance criteria

- [ ] 完成、停止、失败分别生成结果正确的记录。
- [ ] 记录包含作业、设备、区服、耗时与恢复消耗。
- [ ] 历史页无演示数据，空状态正常。
- [ ] 能从记录定位到日志与失败截图。
- [ ] 重启应用后记录仍在。
- [ ] 构建、Rust 检查与回放全绿。

## References

- 现有演示数据：`src/App.vue` 的 history 分支。
- 运行证据目录：`logs/farm/<时间戳>/`。
- 架构：`docs/architecture.md` 2.5。
- 需求：F7。
