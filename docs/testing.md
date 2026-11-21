# 测试与验收

离线验证和实机验收是两类证据。回放通过只能证明已覆盖的离线行为，不能代替真实设备验收。

## 1. 本地验证

先运行预检：

```bash
npm run preflight -- --offline
```

完整常用验证：

```bash
npm run verify
```

该入口依次执行前端构建、全部执行器单测、设备自检、感知素材校验、统一回放和 issue-sync 自检；任一失败即返回非零。`npm run verify -- --list` 只打印执行计划。单独运行某项时仍可使用 `npm run build`、`python -m unittest discover -s executor -t . -v`、`python -m executor.device --self-test`、`npm run fixtures:check`、`npm run replay`、`npm run issues:selftest`。

Windows 下如未激活虚拟环境，把上述 `python` 替换为 `cv/.venv/Scripts/python.exe`；macOS / Linux 使用 `cv/.venv/bin/python`。

`npm run replay` 是统一回放入口，会先检查 Python 与 OpenCV、NumPy、RapidOCR 是否可导入。报告写入 `logs/replay/latest.json`，失败时包含用例、预期、实际结果和相关截图路径。

`npm run issues:check` 还需要 GitHub CLI 登录和网络，用于检查本地票据源与 GitHub 是否漂移。

### 有边界的冒烟与感知就绪检查

```bash
python -m executor.core <作业.json> --serial <设备> --expected-stop <原因> --max-frames 200
python -m executor.core <作业.json> --serial <设备> --confirm-start 1:1 --expected-stop card_mismatch
python -m executor.perception <截图> --ready --json
```

冒烟在帧数上限或预期停止原因处结束，不执行清理循环。`--expected-stop` 只核对停止原因；完成时退出码为 0，停止或失败按进程契约返回非零。`--confirm-start` 只用于人工已经确认起始面与回合的测试适配，生产感知不得把不确定字段当成确认值。就绪检查在缺少助战身份、指令卡身份、回合边界或结算控件时返回退出码 2，并列出缺口。独立感知 CLI 不读取作业，因此普通指令卡即使有从者身份，也会因缺少编队槽位消歧而保持未就绪；真实执行器会传入编译后的编队。

## 2. 行为变更规则

- 执行器或图像识别的每次行为变更，都要同步更新 `cv/replay.py` 的离线回放。
- 每个实机故障都要补一条回归用例。
- 修改 `cv/requirements.txt` 的版本或安装方式后，必须重跑全部回放。
- 测试断言外部可观察行为，包括画面分类、动作、坐标、停止和错误诊断。
- 不用文档中的固定用例数量作为验收条件，以命令退出码和当次报告为准。

旧原型的行为基线见 `.scratch/backend-rebuild/behavior-baseline.md`。它记录重写时必须保留的行为及证据强度，其中被新 ADR 取代的规则以当前需求和 ADR 为准。

## 3. 验证素材与运行产物

`captures/` 是测试输入，不是普通截图存放目录：

- `captures/<日期>/key/` 保存筛选后的关键帧。
- 被回放直接引用的故障截图可以保存在日期目录根部，但必须在 `.gitignore` 中添加显式允许规则。
- 每个日期目录用 `index.json` 登记来源、设备和画面含义。
- 新增回放截图时，同时更新索引、回放引用和 `.gitignore`。
- 过程调试图、OCR 临时输出和评审报告不进入 `captures/`。

`logs/` 保存本地运行证据和回放报告，不入库。`.scratch/` 保存临时实验，默认不入库；现有 `.scratch/backend-rebuild/` 票据源是例外。清理本地文件前检查测试和行为基线中的路径引用。

## 4. CI

`.github/workflows/ci.yml` 在 PR 和 main 推送时通过 `npm run verify` 执行与本地一致的全部验证，覆盖 `executor/core` 与 `executor/session`。回放失败时上传 `logs/replay/latest.json`。未关闭施工票据的正文漂移会阻断 CI。

本地使用相同命令复现 CI，不另维护一套测试入口。

## 5. 实机验收

实机会话前运行：

```bash
npm run preflight -- --live
```

预检只读，不发送输入。实机操作必须由明确的执行器或测试命令完成，并记录运行目录、退出码和最终画面。

发布门槛以 [需求基线第 5 节](requirements.md#5-发布门槛) 为准，包括真实两场连刷、连续出击时序、AP 不足停止，以及打包后的作业保存、资源下载、运行和历史功能。任一门槛未完成时，不将对应能力标记为稳定。
