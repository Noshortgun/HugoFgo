# HugoFgo

HugoFgo 是一个 FGO 自动刷本桌面工具：用可视化界面编队、编排作业，并由本地 Python 执行器驱动安卓模拟器完成战斗。项目使用 Vue 3、TypeScript、Tauri 2 和 Python。

当前仅在指定国服种火作业上完成五项实机验收；识别泛化、安装版和其他副本仍未完整验收。不要把本项目视为任意作业都稳定的一键刷本工具。准确的证据范围和限制见 [当前状态](docs/status.md)。

## 选择使用方式

- **安装版**：适合验收测试。安装包能构建，但安装、首启和完整实机流程尚未验收；准备与运行步骤见 [使用指南](docs/user-guide.md)。
- **源码开发版**：用于当前实机使用和开发。需要 Node.js 20+、Rust、Python、adb 和安卓模拟器。
- **浏览器预览**：用于查看和编辑界面，不支持设备、真实运行、桌面作业库或资源任务。

源码开发版快速启动：

```bash
npm install
python -m venv cv/.venv
cv/.venv/Scripts/python.exe -m pip install -r cv/requirements.txt   # Windows
cv/.venv/bin/python -m pip install -r cv/requirements.txt           # macOS / Linux
npm run resources:update
npm run tauri dev
```

也可用 `HUGOFGO_PYTHON` 指定解释器、`HUGOFGO_ADB` 指定 adb。只需要浏览器界面时使用 `npm run dev`。

## 文档

从 [文档地图](docs/README.md) 进入：

- [使用指南](docs/user-guide.md)：安装与开发启动、资源、编队、运行和故障处理。
- [当前状态](docs/status.md)：实机证据、已实现能力和明确限制。
- [作业格式](docs/job-format.md)：作业 JSON v1 字段、执行语义与示例。
- [开发指南](docs/development.md)：环境、预检、构建、分支和 issue。
- [架构与接口](docs/architecture.md)：进程模型、Tauri 命令、Python CLI 和文件契约。
- [测试与验收](docs/testing.md)：验证入口、资源引用、回放和实机验收。
- [需求基线](docs/requirements.md)、[领域术语](GLOSSARY.md) 与 [架构决策](docs/adr/)：产品目标、统一用词和关键取舍。

## 常用验证

```bash
npm run preflight -- --offline
npm run verify
```

执行器或图像识别行为变更必须同步更新离线回放；完整规则见 [测试与验收](docs/testing.md)。

## 免责声明

本项目是非官方工具，与 TYPE-MOON、Aniplex、Lasengle 无关，仅供个人学习与自动化研究使用。

- `LICENSE` 只覆盖本仓库的代码，不覆盖任何游戏素材。
- 游戏内图像与文本的版权归原权利方所有；仓库中的截图仅用于自动化流程的兼容性测试，不构成授权。
- 图库与从者数据在运行时从 Atlas Academy 获取，不随本仓库分发。
- 请勿将游戏素材用于商业用途；因使用本工具导致的账号风险由使用者自行承担。
