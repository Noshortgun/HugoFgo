# 文档地图

先读 [当前状态](status.md)，再按任务选择文档。历史记录和票据快照不能代替当前文档。

| 文档 | 权威内容 | 需要更新时 |
| --- | --- | --- |
| [当前状态](status.md) | 当前可用能力、未接通功能和已知限制 | 功能接通、验收结果或限制变化 |
| [使用指南](user-guide.md) | 当前版本可执行的使用流程与故障处理 | 用户操作或错误提示变化 |
| [作业格式](job-format.md) | 作业 v1 的字段语义与示例 | Schema、导入或导出语义变化 |
| [开发指南](development.md) | 环境、预检、构建、分支和 issue 流程 | 工程命令或协作规则变化 |
| [工程脚本索引](../scripts/README.md) | 脚本分类、入口和输出用途 | 新增脚本或命令语义变化 |
| [资源、素材与引用](resources.md) | Atlas 素材、模板、截图、实机证据的分类和引用合约 | 资源类别、引用关系或资源命令变化 |
| [测试与验收](testing.md) | 测试入口、证据层级、回放、素材和实机验收规则 | 验证方式或素材规则变化 |
| [需求基线](requirements.md) | 产品目标、需求、发布门槛和范围 | 已确认产品决策变化 |
| [架构与接口](architecture.md) | 当前模块边界、Tauri/CLI/文件契约和目标目录 | 架构决策或模块契约变化 |
| [领域术语](../GLOSSARY.md) | 项目统一用词 | 领域概念或命名变化 |
| [架构决策](adr/) | 单项重要决策及其背景 | 新决策被确认或旧决策被取代 |
| [Agent 约定](agents/) | Agent 工具、领域文档和 issue tracker 规则 | 协作工具或规则变化 |
| [历史进展](../HANDOFF.md) | 按阶段追加的实施与验证记录 | 阶段收尾时追加，不改写旧条目 |

## 单一事实来源

- 当前能力以 `docs/status.md` 为准，README 只保留入口。
- 当前接口以代码和 [架构与接口的当前契约](architecture.md#3-当前接口契约) 为准；架构中的未来目标必须显式标注。
- 产品需求以 `docs/requirements.md` 为准，不另维护一份需求副本。
- 作业结构以 `schemas/job-v1.schema.json` 为机器可读契约，`docs/job-format.md` 负责解释字段。
- 已发布票据以 GitHub issue 为准，本地票据源用于修改和发布，规则见 [Issue tracker](agents/issue-tracker.md)。

`.scratch/backend-rebuild/current-state.md` 是 2026-11-19 的历史快照。`.scratch/backend-rebuild/spec.md` 是重构票据的原始规格快照；其中与当前需求或架构不同的内容，以文档地图中的权威文件为准。
