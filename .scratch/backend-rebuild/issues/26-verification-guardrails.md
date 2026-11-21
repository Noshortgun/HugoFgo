# 26: 验证入口与实机护栏

## What to build

落实 #17 复盘发现的工程盲区，不改动作语义、作业格式或既有执行器生产流程。

- 统一本地与 CI 验证入口，CI 必须运行全部 `executor` 测试，而不是只跑 domain/perception。
- 恢复并接入感知素材校验；缺失的必需验证入口必须明确失败。
- 提供有最大帧数、明确预期停止原因和运行证据的实机冒烟入口；不支持无边界清理循环。
- 共享脚本化场景工具，默认提供新 frame hash，并校验场景长度与必需控件。
- 提供真实感知就绪检查，报告执行器需要但当前真实感知未提供的助战身份、指令卡身份和回合边界证据。
- 在权威文档中集中异常弹窗恢复规则、规格发布纪律和 #17 合并进展。

## Acceptance criteria

- [x] `npm run verify` 运行前端构建、全部执行器单测、设备自检、素材校验、统一回放和 issue-sync 自检，任一失败即返回非零。
- [x] CI 使用同一入口，新增 core/session 测试在 CI 中执行。
- [x] `fixtures:check` 有真实脚本与 npm 入口，缺失必需入口时验证计划明确失败。
- [x] 冒烟命令支持指定设备、最大帧数、预期停止原因和显式人工回合边界；超过帧数在动作边界停止，不进行额外点击。
- [x] 共享场景工具拒绝观察数/时间数不一致，并为每帧生成不同 hash。
- [x] 就绪检查用真实截图列出缺失的执行器输入，不把缺口伪报为通过。
- [x] 异常弹窗恢复表、规格发布纪律和 HANDOFF 的 #17 记录与当前行为一致。

## Verification

```bash
npm run verify
npm run issues:selftest
git diff --check
```

## Implementation evidence

- `npm run verify` 通过：前端构建、执行器单测 124 项、设备自检 21 项、素材校验 82 帧、统一回放 127/127、issue-sync 自检。
- `npm run verify -- --list` 与 `npm run verify:selftest` 验证统一发现整个 executor 测试树，CI 使用同一入口。
- 冒烟测试覆盖帧预算、预期停止原因和离线 CLI；真实感知就绪测试覆盖助战身份、指令卡身份、回合边界和结算控件缺口。
- `docs/requirements.md` 集中异常弹窗恢复表，`docs/agents/issue-tracker.md` 明确施工前发布规格，`HANDOFF.md` 已补 #17 / PR #36 合并记录。
