# HugoFgo

HugoFgo 是一个 FGO 自动刷本桌面工具：用可视化界面编队、编排作业，并由执行器驱动安卓模拟器完成战斗。项目使用 Vue 3、TypeScript、Tauri 2 和 Python。

当前仍在重构。编队与作业编辑可用，但桌面 UI 尚未接入新的自动执行流程，不能作为已完成的一键刷本工具使用。具体能力与限制见 [当前状态](docs/status.md)。

## 快速开始

环境要求：

- Node.js 20+
- Rust 工具链（运行 Tauri 桌面版）
- Python 3.11+（回放与执行器）
- adb 和安卓模拟器（设备功能）

```bash
npm install
npm run resources:update   # 首次克隆后下载图库与元数据
npm run dev                # 浏览器预览
npm run tauri dev          # 桌面版
```

浏览器预览不支持设备命令。作业保存和资源任务目前依赖 Vite 开发服务；打包后的完整使用流程尚未接通。

Python 回放环境：

```bash
python -m venv cv/.venv
cv/.venv/Scripts/python.exe -m pip install -r cv/requirements.txt   # Windows
cv/.venv/bin/python -m pip install -r cv/requirements.txt           # macOS / Linux
```

也可用 `HUGOFGO_PYTHON` 指定解释器。

## 文档

从 [文档地图](docs/README.md) 进入：

- [使用指南](docs/user-guide.md)：资源、编队、作业和设备预览。
- [当前状态](docs/status.md)：已实现能力、限制和后续工作。
- [作业格式](docs/job-format.md)：作业 JSON v1 字段与示例。
- [开发指南](docs/development.md)：环境、预检、构建、分支和 issue。
- [测试与验收](docs/testing.md)：单元测试、离线回放、素材和实机验收。
- [需求基线](docs/requirements.md) 与 [目标架构](docs/architecture.md)：产品和系统设计。
- [领域术语](GLOSSARY.md) 与 [架构决策](docs/adr/)：统一用词和关键取舍。

## 常用验证

```bash
npm run preflight -- --offline
npm run build
npm run replay
```

完整验证命令见 [测试与验收](docs/testing.md)。执行器或图像识别行为变更必须同步更新离线回放，实机验收完成前不得标记为稳定。

## 免责声明

本项目是非官方工具，与 TYPE-MOON、Aniplex、Lasengle 无关，仅供个人学习与自动化研究使用。

- `LICENSE` 只覆盖本仓库的代码，不覆盖任何游戏素材。
- 游戏内图像与文本的版权归原权利方所有；仓库中的截图仅用于自动化流程的兼容性测试，不构成授权。
- 图库与从者数据在运行时从 Atlas Academy 获取，不随本仓库分发。
- 请勿将游戏素材用于商业用途；因使用本工具导致的账号风险由使用者自行承担。

截图与运行产物的入库规则见 [测试与验收](docs/testing.md#3-验证素材与运行产物)。
