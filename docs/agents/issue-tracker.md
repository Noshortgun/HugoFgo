# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues in `Noshortgun/HugoFgo`. Use the `gh` CLI for all operations.

本地 `.scratch/backend-rebuild/` 保留 spec、行为基线与票据草稿，作为发布源；已发布的票据以 GitHub issue 为准。

## 单一发布源与漂移检查

每张票只有**一个**可手工编辑的本地源：`.scratch/backend-rebuild/issues/NN-<slug>.md`，首行必须是 `# NN: 标题`。
GitHub issue 是权威内容，本地源是待发布内容；两者不一致时先看差异再决定发布，检查命令不会自动同步，也不会删除任何文件。

- `npm run issues:check`：逐票显示 `新增 / 修改 / 无变化 / 缺本地源`；有阻塞漂移或结构错误时退出码 1，并列出票据编号。
- `node scripts/issue-sync.mjs --diff`：附上逐票 unified diff；`--json` 给 agent 与 preflight 使用；`--from-file <json>` 可离线复现。
- `node scripts/issue-sync.mjs --target NN,NN`：发布前校验目标票据。父票据（被其他票的 `## Parent` 声明，或正文自己声明子票据）与已关闭票据一律拒绝，退出码 2；确需改父票据用 `--allow-parent`。
- 远端正文、标签与状态缓存在 `.scratch/issue-cache/NN.json`（派生数据，不入库）。
- 票据编号取 issue 标题前缀 `NN:`，可能与 GitHub issue 号不同（例：票据 `19` 是 issue `#20`）。对照时用票据编号，不要用 issue 号。
- 重复编号（两个 `NN-<slug>.md`）与遗留双份（`NN.md`、`NN.current.md`）只报告、不自动删除；发布源永远是 `NN-<slug>.md`。
- 本命令只读：不创建、不编辑、不关闭 issue。发布动作仍由人工执行。

## Conventions

- **Create an issue**: `gh issue create --title "..." --body-file <file>`。多行正文用文件，不用行内字符串。
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`。
- **List issues**: `gh issue list --state open --json number,title,body,labels`。
- **Blocking**: 使用 GitHub 原生 issue 依赖。创建时 `gh issue create --blocked-by <n>,<n>`，或事后 `gh issue edit <child> --add-blocked-by <blocker>`。不可用时退化为正文顶部的 `Blocked by: #<n>` 行。
- **Count open blockers**: `gh api repos/Noshortgun/HugoFgo/issues/<n> --jq .issue_dependencies_summary.blocked_by`。
- **Comment**: `gh issue comment <number> --body "..."`。
- **Labels**: `gh issue edit <number> --add-label "..."`。五个角色标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。
- **Close**: 先 comment 结果，再 `gh issue close <number>`。

票据编号与本地文件编号一致（`NN`），便于对照；文件名统一为 `NN-<slug>.md`，不要再新增 `NN.md` 或 `NN.current.md`。

## Pull requests as a triage surface

**PRs as a request surface: no.**

## When a skill says "publish to the issue tracker"

Create a GitHub issue. 本地票据文件继续保留，作为内容源。

## When a skill says "fetch the relevant ticket"

Read the GitHub issue by number；本地文件可用于查看未发布的修改。

## Wayfinding operations

Used by `/wayfinder`。Map 是一个 issue，child 是 sub-issue；blocking 用原生依赖；frontier 为无 open blocker 且无 assignee 的 child。
