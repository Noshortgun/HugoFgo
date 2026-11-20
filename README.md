# HugoFgo

FGO 自动刷本桌面工具：可视化编队与作业编排，一键让模拟器按作业刷本。支持国服，日服/台服在后续阶段。

- 前端：Vue 3 + TypeScript + Vite（保留）
- 桌面壳：Tauri 2 / Rust（复用）
- 执行器：Python + OpenCV + RapidOCR（重写中，见 `.scratch/backend-rebuild/`）

## 环境要求

- Node.js 20+
- Python 3.11+
- adb（安卓 platform-tools，或设置 `HUGOFGO_ADB` 指向 adb.exe）
- 安卓模拟器，1280×720（当前实测 LDPlayer）

## 快速开始

```bash
npm install
npm run dev        # 浏览器预览
npm run build      # 类型检查 + 构建
npm run tauri dev  # 桌面版
```

Python 环境（回放与执行器需要）：

```bash
python -m venv cv/.venv
cv/.venv/Scripts/python.exe -m pip install opencv-python numpy rapidocr-onnxruntime
```

也可用 `HUGOFGO_PYTHON` 指定解释器。

## 预检

任何实施或实机会话开始前运行一次，判断当前环境是否适合继续：

```bash
npm run preflight                    # 实机会话前：分支 + 回放依赖 + adb 设备 + 外部自动化进程
npm run preflight -- --offline       # 只做代码：跳过设备与进程检查
npm run preflight -- --live          # 实机会话：外部进程与设备问题改为阻断；预检本身不发送输入
npm run preflight -- --allow-branch  # 确认在非施工分支（如原型分支）上继续
```

- 退出码：`0` 无阻断项（警告不阻断）、`1` 存在阻断项、`2` 参数错误。
- 阻断项附可直接执行的修复命令；`--json` 输出机器可读结果。
- 非施工分支（`main`、`prototype/*` 等）默认阻断，确认后加 `--allow-branch`。
- 预检本身完全只读，不发送任何实机输入（实机操作只走 Python 执行器）；`--live` 表示进入实机会话，外部自动化冲突、进程扫描失败、多设备未指定或设备未就绪都会阻断。

## 测试

```bash
npm run replay     # 全部离线回放用例，当前 36 个；失败退出码非 0
```

纪律：执行器任何改动必须回放全绿；每个实机故障补一条回放用例；未经实机验收不标记稳定。

## 资源数据

```bash
npm run resources:check    # 检查远端是否有新版本
npm run resources:update   # 增量下载元数据与图库，并校验
npm run resources:verify   # 校验本地文件 SHA-256
```

图库与原始元数据不入库，首次克隆后需执行一次 `resources:update`。

## 文档地图

| 文件 | 内容 |
| --- | --- |
| `docs/requirements.md` | 需求基线与已确认决策 |
| `docs/architecture.md` | 目标架构、模块接口与缝 |
| `GLOSSARY.md` | 领域术语 |
| `docs/adr/` | 架构决策记录 |
| `docs/agents/` | issue tracker、标签、领域文档规则 |
| `.scratch/backend-rebuild/` | 现状分析、spec、重构票据 |
| `HANDOFF.md` | 逐阶段进展流水 |

## 免责声明

本项目是非官方工具，与 TYPE-MOON、Aniplex、Lasengle 无关，仅供个人学习与自动化研究使用。

- `LICENSE` 只覆盖本仓库的代码，不覆盖任何游戏素材。
- 游戏内图像与文本的版权归原权利方所有；仓库中的截图仅用于自动化流程的兼容性测试，不构成授权。
- 图库与从者数据在运行时从 Atlas Academy 获取，不随本仓库分发。
- 请勿将游戏素材用于商业用途；因使用本工具导致的账号风险由使用者自行承担。

## 素材入库规则

- `captures/*/key/` 关键帧与 `index.json` 入库。
- 被回放直接引用的故障截图入库（在 `.gitignore` 中有显式允许列表，新增用例时同步添加）。
- 过程调试图、`logs/` 运行产物不入库。
