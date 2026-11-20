# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues in `Noshortgun/HugoFgo`. Use the `gh` CLI for all operations.

本地 `.scratch/backend-rebuild/` 保留 spec、行为基线与票据草稿，作为发布源；已发布的票据以 GitHub issue 为准。

## Conventions

- **Create an issue**: `gh issue create --title "..." --body-file <file>`。多行正文用文件，不用行内字符串。
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`。
- **List issues**: `gh issue list --state open --json number,title,body,labels`。
- **Blocking**: 使用 GitHub 原生 issue 依赖。创建时 `gh issue create --blocked-by <n>,<n>`，或事后 `gh issue edit <child> --add-blocked-by <blocker>`。不可用时退化为正文顶部的 `Blocked by: #<n>` 行。
- **Count open blockers**: `gh api repos/Noshortgun/HugoFgo/issues/<n> --jq .issue_dependencies_summary.blocked_by`。
- **Comment**: `gh issue comment <number> --body "..."`。
- **Labels**: `gh issue edit <number> --add-label "..."`。五个角色标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。
- **Close**: 先 comment 结果，再 `gh issue close <number>`。

票据编号与本地文件编号一致（`NN`），便于对照。

## Pull requests as a triage surface

**PRs as a request surface: no.**

## When a skill says "publish to the issue tracker"

Create a GitHub issue. 本地票据文件继续保留，作为内容源。

## When a skill says "fetch the relevant ticket"

Read the GitHub issue by number；本地文件可用于查看未发布的修改。

## Wayfinding operations

Used by `/wayfinder`。Map 是一个 issue，child 是 sub-issue；blocking 用原生依赖；frontier 为无 open blocker 且无 assignee 的 child。
