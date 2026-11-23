# 测试与验收

不同验证回答不同问题，不能互相替代：

| 证据层级 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| 单测 / 脚本化场景 | 输入输出、状态机和错误分支符合断言 | 真实画面识别或实机稳定 |
| 真实截图回放 | 已入库画面上的识别与动作符合预期 | 未采集画面、设备时序和完整实机流程 |
| 限定实机验收 | 所列设备、区服、作业和场景实际跑通 | 其他作业、副本、设备或区服稳定 |
| 留出集验收 | 冻结规则后，新采画面上的识别泛化达到清单要求 | 执行器全流程或安装版稳定 |
| 安装版验收 | 安装、首启、资源、作业、运行和历史在分发形态下可用 | 其他副本或未覆盖场景稳定 |

当前每层的实际结论见 [当前状态](status.md)。不要把“素材数量”“回放通过”或“安装包能构建”写成更高层级的验收结论。

## 1. 本地验证

先运行预检：

```bash
npm run preflight -- --offline
```

完整验证：

```bash
npm run verify
```

该入口执行前端构建、执行器单测、设备自检、文档与素材引用检查、感知 fixtures、资源合约、桌面打包自检、统一回放和 issue-sync 自检；任一失败即返回非零。`npm run verify -- --list` 打印当次实际步骤，是检查覆盖范围的准确入口。

单独运行某项时仍可使用：

```bash
npm run build
python -m unittest discover -s executor -t . -v
python -m executor.device --self-test
python scripts/battle_performance.py <运行目录> [...] [--output 报告.json] [--enforce-thresholds]
npm run scrcpy:verify
node scripts/check-references.mjs
npm run fixtures:check
npm run replay
npm run issues:selftest
```

Windows 下如未激活虚拟环境，把上述 `python` 替换为 `cv/.venv/Scripts/python.exe`；macOS / Linux 使用 `cv/.venv/bin/python`。

`npm run replay` 是统一回放入口，会先检查 Python 与 OpenCV、NumPy、RapidOCR 是否可导入。报告写入 `logs/replay/latest.json`，失败时包含用例、预期、实际结果和相关截图路径。`python -m executor.replay` 默认运行新执行器的真实截图场景，可用 `--only <名称>` 选择；`python -m executor.replay --filter <文本>` 转发到 `cv/replay.py` 统一用例集。

`npm run issues:check` 还需要 GitHub CLI 登录和网络，用于检查本地票据源与 GitHub 是否漂移。

`battle_performance.py` 同时兼容旧逐卡日志和新批量日志：选卡间隔统计同回合相邻三卡提交（含宝具卡），攻击到首卡统计攻击输入到本次首张选卡输入；不统计选卡后的攻击动画或结算。中位数门槛为 450 ms、1000 ms，只有存在样本且两者都达标时 `timing_thresholds_pass` 才为真。初始批次若发生恢复或未确认，不计入无恢复成功数，报告保留逐批次结果。`--enforce-thresholds` 只把这两个时间门槛转为退出码，不代表完整实机验收或默认通道资格。

`python scripts/live_action_audit.py <运行目录> <作业.json>` 使用与执行器相同的作业编译和卡请求展开结果，逐场逐面逐回合核对控件、固定普通卡身份/顺序与宝具槽位/卡位；旧日志若没有选卡身份字段，会因证据不足而不能通过固定卡序审计。

### 有边界的冒烟与感知就绪检查

```bash
python -m executor.core <作业.json> --serial <设备> --expected-stop <原因> --max-frames 200
python -m executor.core <作业.json> --serial <设备> --confirm-start 1:1 --expected-stop card_mismatch
python -m executor.perception <截图> --ready --json
```

冒烟在帧数上限或预期停止原因处结束，不执行清理循环。`--expected-stop` 只核对停止原因；完成时退出码为 0，停止或失败按进程契约返回非零。`--confirm-start` 只用于人工已经确认起始面与回合的测试适配，生产感知不得把不确定字段当成确认值。

就绪检查在缺少助战身份、指令卡身份、回合边界或结算控件时返回退出码 2，并列出缺口。独立感知 CLI 不读取作业，因此普通指令卡即使有从者身份，也会因缺少编队槽位消歧而保持未就绪；真实执行器会传入编译后的编队。

## 2. 行为变更规则

- 执行器或图像识别的每次行为变更，都要同步更新 `cv/replay.py` 的离线回放。
- 每个实机故障都要补一条回归用例。
- 修改 `cv/requirements.txt` 的版本或安装方式后，必须重跑全部回放。
- 测试断言外部可观察行为，包括画面分类、动作、坐标、停止和错误诊断。
- 声称“图库自动覆盖”的识别测试必须从独立资源根加载不在仓库内置素材中的原图；内置素材只用于离线回归，不能单独证明生产图库加载路径正确。
- 感知单测和统一回放默认使用空生成图库，测试工具通过 `isolated_perception` / `TemporaryDirectory` 显式注入；本机 `data/<区服>/assets/` 的有无不能改变测试结论。
- 不用文档中的固定用例数量作为验收条件，以命令退出码和当次报告为准。

旧原型的行为基线见 `.scratch/backend-rebuild/behavior-baseline.md`。它记录重写时必须保留的行为及证据强度，其中被新 ADR 取代的规则以当前需求和 ADR 为准。

## 3. 验证素材与运行产物

`captures/` 是测试输入和模板来源，不是普通截图存放目录：

- `captures/<日期>/key/` 保存筛选后的关键帧。
- 被回放直接引用的故障截图可以保存在日期目录根部，但必须在 `.gitignore` 中添加显式允许规则。
- 每个日期目录用 `index.json` 登记来源、设备和画面含义。
- 新增回放截图时，同时更新索引、回放引用和 `.gitignore`。
- 过程调试图、OCR 临时输出和评审报告不进入 `captures/`。

`logs/` 保存本地运行证据、留出集和回放报告，不入库。`.scratch/` 保存临时实验，默认不入库；现有 `.scratch/backend-rebuild/` 中已跟踪的票据源、规格与行为基线是例外。

清理前必须检查：

1. `executor/perception/fixtures/`、两套回放和单测的直接引用。
2. `cv/build_templates.py`、`executor/perception/build_templates.py` 与模板清单中的裁切来源。
3. `captures/<日期>/index.json`、票据、行为基线和本地报告的来源引用。
4. 文件是唯一本地证据，还是可由干净克隆和资源管线恢复。

```bash
node scripts/check-references.mjs                  # 仓库文档与必需模板源
node scripts/check-references.mjs --include-local  # 额外检查本机唯一模板源
npm run fixtures:check                            # 感知 fixtures 与截图
```

默认引用检查不要求 CI 拥有本机 `logs/` 或被忽略的原始截图；`--include-local` 用于归档或清理前确认本地模板来源完整。完整分类和用途见 [资源、素材与引用](resources.md)。

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
