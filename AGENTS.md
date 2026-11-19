# HugoFgo

FGO 自动刷本桌面工具：Vue 3 + Vite 前端，Tauri 2 (Rust) 桌面壳，Python + OpenCV + RapidOCR 图像识别与实机执行原型。

## Agent skills

### Issue tracker

Issues、spec 与票据以本地 markdown 文件形式存放在 `.scratch/<feature-slug>/`，不使用外部平台。See `docs/agents/issue-tracker.md`.

### Triage labels

使用五个默认角色标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`，记录在每张票据的 `Status:` 行。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根目录 `GLOSSARY.md` + `docs/adr/`。See `docs/agents/domain.md`.

## 工作约定

- 执行器与图像识别的每次行为变更，都必须同步更新 `cv/replay.py` 的离线回放用例；实机验收前不得宣称"稳定"。
- 提交前跑 `npm run replay`，全绿才提交；每个实机故障补一条回放用例。
- 运行记录写入 `logs/farm/<时间戳>/`，实机截图素材登记到 `captures/<日期>/index.json`。
- 截图入库规则：`captures/*/key/` 与被回放引用的故障截图入库，过程调试图不入库；新增回放引用的截图时同步更新 `.gitignore` 允许列表。
- 历史进展见 `HANDOFF.md`，新阶段继续在其末尾追加，不改写已有条目。
