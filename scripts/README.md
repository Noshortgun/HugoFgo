# 工程脚本索引

`scripts/` 是工程命令与资源管线，不是实机执行器。战斗逻辑位于 `executor/`；脚本路径被 `package.json`、CI 和验证入口引用，不要为整理目录而批量移动。

优先使用 `package.json` 中的 npm 入口；下表的直接命令用于需要脚本参数或排障时。

## 验证与预检

| 入口 | 用途 |
| --- | --- |
| `npm run preflight -- --offline` / `-- --live` | 施工前检查；实机模式仍不发送输入 |
| `npm run verify` / `npm run verify -- --list` | 全量验证 / 查看实际步骤 |
| `npm run replay` | 统一离线回放 |
| `node scripts/check-references.mjs [--include-local]` | 文档、模板和模板来源引用检查 |
| `npm run fixtures:check` | 感知 fixtures 结构与截图存在性 |
| `npm run verify:selftest` | 验证入口自身检查 |
| `node scripts/check-references.selftest.mjs` | 引用检查的断链与缺失检测自检 |
| `node scripts/report-ci-failure.mjs [日志]` | 生成 CI 失败摘要 |

## 资源与数据

| 入口 | 用途 |
| --- | --- |
| `npm run resources:check` / `update` / `verify` | 资源检查、更新与 SHA-256 校验 |
| `npm run resources:contract` / `selftest` | 资源类别合约与资源管线自检 |
| `npm run data:query -- find <名称或ID>` | 查询 Atlas 大 JSON，不读取整份文件 |
| `node scripts/resource-migrate.mjs` | 旧资源目录迁移工具 |

`resource-utils.mjs`、`resource-paths.mjs`、`asset-jobs.mjs` 是资源管线内部模块；`download-*-assets.mjs` 是旧分族下载入口，日常更新优先使用统一 `resources:*`。

## 设备与实机证据

| 入口 | 用途 |
| --- | --- |
| `node scripts/device.mjs list` / `info` / `screenshot` | adb 设备发现、信息与截图 |
| `node scripts/device.mjs tap <x> <y>` / `key <keycode>` | 真实测试输入，仅在明确画面使用 |
| `python scripts/live_action_audit.py <运行目录> [作业.json]` | 展开实机 action 事件，与作业计划对照 |
| `python scripts/perception_monitor.py [--adb]` | 交互式感知监控与采帧 |
| `python scripts/perception_frame_report.py [目录或图片...]` | 批量单帧感知报告 |

`adb.mjs`、`automation-processes.mjs`、`python-env.mjs` 是共享内部模块。动作审计输出原始控件名；作业技能标题与控件名不同，需要按槽位和动作语义核对，不能把“缺”文本当成独立自动验收结论。

## 桌面、作业与 Issues

| 入口 | 用途 |
| --- | --- |
| `npm run desktop:prepare` / `desktop:selftest` | 准备随包资源 / 打包合约自检 |
| `npm run package:windows` | 构建 Windows NSIS 安装包 |
| `npm run test:job-roundtrip` / `test:run-state` | 作业往返与运行状态自检 |
| `npm run issues:check` / `issues:selftest` | GitHub 票据漂移检查 / 自检 |
| `node scripts/chaldea-to-hugofgo.mjs <team.json> [out.json]` | Chaldea 测试转换器 |

完整规则见 [开发指南](../docs/development.md)、[测试与验收](../docs/testing.md) 和 [资源、素材与引用](../docs/resources.md)。
