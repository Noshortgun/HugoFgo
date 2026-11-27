# HugoFgo

HugoFgo 是一个 FGO 自动刷本桌面工具。你先把自己的操作编成一份“作业”：使用哪些从者、每面每回合点哪些技能、怎样使用宝具、没有清场时怎样选卡。启动后，程序读取游戏画面并操作安卓模拟器，按作业完成战斗；无法确认画面或操作条件时会停止并保留证据。

当前仅在指定国服种火作业上完成五项实机验收。识别泛化、安装版和其他副本仍未完整验收，不能把它当作任意作业都稳定的一键刷本工具。具体证据和限制见[当前状态](docs/status.md)。

## 一次刷本怎样进行

```text
你在编队页安排操作
        ↓
HugoFgo 检查作业与设备
        ↓
寻找助战、核对编队、开始任务
        ↓
读取画面 → 点击技能或选卡 → 再读画面确认
        ↓
完成结算，或在不安全时停止
        ↓
保存结果、动作摘要和必要的截图
```

例如，作业要求“第 1 面第 1 回合先使用某个充能技能，再把指定从者的宝具放在第一张”。程序不会只按时间连续点击：它会等技能流程结束，重新读取战斗画面，再进入选卡。如果卡面身份一直无法确认，它不会随便选三张继续。

第一次使用请直接阅读[使用指南](docs/user-guide.md)。想知道软件为什么这样工作，再读[工作原理](docs/how-it-works.md)。

## 选择使用方式

- **安装版**：适合验收测试。安装包能构建，但安装、首启和完整实机流程尚未验收。
- **源码开发版**：用于当前实机使用和开发，需要 Node.js 20+、Rust、Python、adb 和安卓模拟器。
- **浏览器预览**：只用于查看和编辑界面，不支持设备、真实运行、桌面作业库或资源任务。

源码开发版快速启动：

```bash
npm install
python -m venv cv/.venv
cv/.venv/Scripts/python.exe -m pip install -r cv/requirements.txt   # Windows
cv/.venv/bin/python -m pip install -r cv/requirements.txt           # macOS / Linux
npm run resources:update
npm run tauri dev
```

可用 `HUGOFGO_PYTHON` 指定解释器、`HUGOFGO_ADB` 指定 adb。只需要浏览器界面时使用 `npm run dev`。安装版、设备和游戏画面的准备步骤见[使用指南](docs/user-guide.md)。

## 按需要阅读文档

| 你的目标 | 推荐阅读 |
| --- | --- |
| 第一次跑通刷本 | [使用指南](docs/user-guide.md) |
| 理解编队、选卡和安全停止背后的机制 | [工作原理](docs/how-it-works.md) |
| 查看当前可用能力和未验收范围 | [当前状态](docs/status.md) |
| 编写或检查作业 JSON | [作业格式](docs/job-format.md) |
| 修改项目或参与开发 | [开发指南](docs/development.md)、[架构与接口](docs/architecture.md)、[测试与验收](docs/testing.md) |

完整入口和维护规则见[文档地图](docs/README.md)。

## 常用验证

```bash
npm run preflight -- --offline
npm run verify
```

执行器或图像识别行为变更必须同步更新离线回放；完整规则见[测试与验收](docs/testing.md)。

## 技术组成

Vue 3 + TypeScript 前端，Tauri 2 (Rust) 桌面壳，Python + OpenCV 图像识别与实机执行器。模块边界和本地接口见[架构与接口](docs/architecture.md)。

## 免责声明

本项目是非官方工具，与 TYPE-MOON、Aniplex、Lasengle 无关，仅供个人学习与自动化研究使用。

- `LICENSE` 只覆盖本仓库的代码，不覆盖任何游戏素材。
- 游戏内图像与文本的版权归原权利方所有；仓库中的截图用于自动化流程的兼容性测试与原理说明，不构成授权。
- 图库与从者数据在运行时从 Atlas Academy 获取，不随本仓库分发。
- 请勿将游戏素材用于商业用途；因使用本工具导致的账号风险由使用者自行承担。
