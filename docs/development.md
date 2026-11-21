# 开发指南

## 1. 环境准备

安装 Node.js 20+、Python 3.11+ 和 Rust 工具链。设备开发还需要 adb 与安卓模拟器。

```bash
npm install
python -m venv cv/.venv
cv/.venv/Scripts/python.exe -m pip install -r cv/requirements.txt   # Windows
cv/.venv/bin/python -m pip install -r cv/requirements.txt           # macOS / Linux
npm run resources:update
```

`HUGOFGO_PYTHON` 可指定 Python 解释器，`HUGOFGO_ADB` 可指定 adb。回放依赖版本固定在 `cv/requirements.txt`。资源更新会下载各再临阶段的从者头像和 Atlas `commands` 指令卡立绘原图，分别供助战与普通指令卡身份识别使用；新增从者的原图随图库更新生效，不需要手工登记模板；实际识别泛化仍需真实截图留出集验证，感知运行时不访问网络。素材字段、查询命令和资源合约见 [Atlas 资源](resources.md)。

### Windows 构建环境（src-tauri）

桌面壳是 Rust + Tauri 2。没有工具链时 `src-tauri` 的改动无法在本地验证，只能人工审阅；改壳之前先装好：

```bash
winget install --id Rustlang.Rustup -e
rustup default stable
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

MSVC 链接器是必需的：Rust 的构建脚本要先链接成 exe 才能运行，`cargo check` 同样绕不开。

**Git Bash 的 `link.exe` 会冒名顶替 MSVC 链接器。** `where.exe link.exe` 的第一条如果是 `C:\Program Files\Git\usr\bin\link.exe`，cargo 会拿它链接，报 `link: extra operand`。绕法是显式指定链接器（版本号以本机为准）：

```bash
export CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER="C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Tools/MSVC/<版本>/bin/Hostx64/x64/link.exe"
cargo check --tests --manifest-path src-tauri/Cargo.toml   # 需先 npm run desktop:prepare
cargo test --lib --manifest-path src-tauri/Cargo.toml
```

`npm run package:windows` 先跑全量回放再 `tauri build`，产出 NSIS 安装包；CI 的 `Windows 安装包` 任务在 windows-latest 上做同样的构建并上传产物。

## 2. 每次施工前预检

```bash
npm run preflight -- --offline       # 纯代码或文档工作
npm run preflight -- --live          # 实机工作，仍由预检保持只读
npm run preflight -- --allow-branch  # 明确确认在特殊分支上工作
```

预检检查分支、工作区、回放依赖、Rust 工具链、票据源、adb 设备和外部自动化进程。离线模式跳过设备与进程检查；实机模式把设备冲突和连接问题作为阻断项。预检本身不发送设备输入。

新施工从 `origin/main` 创建 `ticket/NN-slug` 分支。`main` 和 `prototype/*` 默认不作为施工分支。

## 3. 常用命令

```bash
npm run dev
npm run tauri dev
npm run build
npm run verify
npm run replay
```

完整测试、CI 和实机验收命令见 [测试与验收](testing.md)。

## 4. 目录职责

| 目录 | 职责 | 入库规则 |
| --- | --- | --- |
| `src/`、`src-tauri/` | Vue 前端与 Tauri 壳 | 源码入库 |
| `executor/` | 新 Python 执行器的领域、设备与感知模块 | 源码、测试和所需模板入库 |
| `schemas/` | 跨语言作业契约 | 入库 |
| `scripts/` | 工程命令、资源管线和旧原型入口 | 脚本入库，运行产物不入库 |
| `cv/` | 旧原型识别与统一回放入口 | 源码、依赖锁和回放用例入库 |
| `jobs/`、`examples/` | 本地作业与示例 | 按用途入库，不含私人账号数据 |
| `captures/` | 回放和实机故障的验证素材 | 只保留关键帧、回归帧和索引 |
| `data/<区服>/` | 按区服隔离的资源元数据、清单和图库 | 生成的图库与原始元数据不入库 |
| `logs/` | 回放报告、运行日志和失败截图 | 本地保留，不入库 |
| `.scratch/` | 临时实验、调试和报告 | 默认不入库；`backend-rebuild/` 中已跟踪的治理文档例外 |

`scripts/farm_ember.py` 与 `cv/` 中的旧原型只作行为参考。新功能进入 `executor/`，不要继续扩张旧原型。

## 5. 文档与领域规则

文档归属和更新时机见 [文档地图](README.md)。当前能力变化时更新 `docs/status.md`，需求或架构变化时更新对应基线，不能只追加 HANDOFF。

领域用词遵循根目录 `GLOSSARY.md`。重要且难以逆转的取舍记录到 `docs/adr/`。执行器或识别行为变化必须同步更新 `cv/replay.py` 的离线回放。

## 6. Issue 与提交检查

Issues 发布到 `Noshortgun/HugoFgo`。本地可编辑源位于 `.scratch/backend-rebuild/issues/NN-<slug>.md`，详细规则见 [Issue tracker](agents/issue-tracker.md)。

```bash
npm run issues:check
npm run issues:selftest
```

提交前至少运行与改动对应的构建和测试。执行器、图像识别或回放依赖变化必须保证 `npm run replay` 全绿；每个实机故障都要增加回归用例。
