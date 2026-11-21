# HugoFgo

HugoFgo 是一个 FGO 自动刷本桌面工具：用可视化界面编队、编排作业，并由执行器驱动安卓模拟器完成战斗。项目使用 Vue 3、TypeScript、Tauri 2 和 Python。

当前仍在重构。编队、作业编辑和桌面执行器桥接已接通，但完整实机验收尚未完成，不能视为稳定的一键刷本工具。具体能力与限制见 [当前状态](docs/status.md)。

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

浏览器预览只读：设备命令、作业保存与资源任务需要 `npm run tauri dev` 启动的桌面版。桌面壳负责作业读写，并按区服把图库与清单放在 `data/<区服>/`。

Python 回放环境：

```bash
python -m venv cv/.venv
cv/.venv/Scripts/python.exe -m pip install -r cv/requirements.txt   # Windows
cv/.venv/bin/python -m pip install -r cv/requirements.txt           # macOS / Linux
```

也可用 `HUGOFGO_PYTHON` 指定解释器。

## Windows 安装版与发布

安装包不包含图库。首次启动会使用占位卡；打开“设置与资源”，选择区服并点击“增量更新”，下载完成后编队会自动刷新。资源更新使用随包附带的 Node.js，无需另装 Node.js。

安装版的作业、图库、设置和运行证据统一保存在 `%APPDATA%/com.hugofgo.desktop/` 下的 `jobs/`、`data/`、`run-settings.json` 与 `logs/farm/`，不依赖源码目录或开发服务器。

**Python 执行器采用检测用户 Python 的分发策略，不随包附带 Python。** 运行作业前请安装 64 位 Python 3.12，再按实际安装位置安装固定依赖：

```powershell
py -3.12 -m pip install -r "<HugoFgo 安装目录>\cv\requirements.txt"
```

也可用 `HUGOFGO_PYTHON` 指向已安装这些依赖的解释器。设备运行另需可用的 adb 与安卓模拟器；假运行可用于离线联调。

发布者在具备 Rust 工具链的 Windows 机器上执行：

```bash
npm ci
python -m pip install -r cv/requirements.txt
npm run package:windows
```

该命令先准备固定版本 Node.js 运行时，再执行前端构建、全量回放和 `tauri build`。NSIS 安装包输出到 `src-tauri/target/release/bundle/nsis/`。CI 的“Windows 安装包”任务使用同一命令并上传安装包。发布时需核对：空库首启、国服下载、作业保存/打开，以及假运行或实机运行与历史记录；未做完整实机验收前不得标记稳定。

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
