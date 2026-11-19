# 04: 作业编译为完整执行计划

**Status:** ready-for-agent
**Blocked by:** 01（工程基线，已完成）

## Context

作业 JSON v1 由前端 `src/job.ts` 生成，类型定义在 `src/types.ts`（`HugoFgoJob`）。旧执行器 `scripts/farm_ember.py` 的 `planned_attack()` 只读取每面 `turns[0]`，因此多回合作业、换人、固定卡序都执行不了。

本票据新建执行器的"作业模型"模块，把作业文本编译成后续状态机直接消费的计划。它**不操作设备**，可以完全离线验证。

相关决策：作业格式不变（ADR-0002）；Schema 为前后端契约单源（`docs/architecture.md` 2.1）。

## Goal

任意合法作业都能编译出"面 → 回合 → 动作"的完整计划；非法或含不支持能力的作业在运行前给出带面/回合定位的错误。

## Constraints

- 不修改 `jobs/` 与 `examples/` 中现有作业文件。
- 不修改前端代码；Schema 从前端类型推导，但前端本票据不改。
- 不引入新的第三方依赖；校验逻辑用 Python 标准库实现，JSON Schema 文件作为契约文档与测试依据。
- `npm run replay` 必须保持 36/36。
- 本票据不实现任何战斗逻辑、OCR 或 adb 调用。

## Implementation notes

新建目录与文件：

- `schemas/job-v1.schema.json`：覆盖 `HugoFgoJob` 的字段、必填项、枚举与槽位范围。
- `executor/domain/model.py`：计划的数据结构（dataclass）。
- `executor/domain/compiler.py`：对外接口。
- `executor/domain/__main__.py`：命令行入口，供人工与测试检查。
- `executor/domain/fixtures/`：合法、非法、含不支持能力的三类最小作业。

对外接口只有一个：

```python
compile_job(text: str) -> CompileResult
# CompileResult = { plan: ExecutionPlan | None, diagnostics: list[Diagnostic] }
# Diagnostic = { wave: int | None, turn: int | None, path: str, message: str }
```

计划形状：

- `plan.waves[]`：按作业顺序排列。
- `wave.turns[]`：保留全部回合，不再只取首回合。
- `turn.actions[]`：保留 `kind`、`skill`、`memberSlot`、`playerTargetSlot`、`enemyTarget`、`swapOutSlot`、`swapInSlot`、`secondarySelection`。
- `turn.npPositions`：解析为整数卡序；`auto` 保留为 `auto`。
- `turn.attack`：保留 `mode` 与 `cards`，不在编译期展开成坐标。

支持的动作 `kind`：`skill`、`master`、`spell`、`attack`、`target`、`orderChange`。出现其他 `kind` 时给诊断，不静默丢弃。

校验规则至少包括：

- 顶层 `version` 必须为 `1`。
- `formation` 槽位在 0–5；`memberSlot`、`playerTargetSlot`、`swapOutSlot`、`swapInSlot` 不得越界。
- `waves` 非空；`turnCount` 与 `turns` 数量一致或给出明确诊断。
- 引用空槽位（该槽位无 `name`）时报错。
- `enemyCount` 只能是 3 或 6。
- 不支持的能力（例如未知 `kind`、无法识别的 `secondarySelection.type`）报错并定位到面/回合。

CLI：

```bash
python -m executor.domain <作业路径> [--json]
# 合法：打印面/回合/动作摘要；--json 输出完整计划
# 非法：打印所有诊断，退出码 2
```

## Steps

1. 从 `src/types.ts` 与两个现有作业样例提炼字段清单，写 `schemas/job-v1.schema.json`。
2. 实现 `model.py` 与 `compiler.py` 的解析和校验。
3. 实现 CLI，先用 `jobs/娄希、娄希、奥伯龙.json` 与 `examples/sample-job.json` 跑通。
4. 补三类 fixture：合法多回合、槽位越界、未知动作。
5. 运行 Verification 中的全部命令。

## Verification

```bash
# 现有真实作业应编译出 3 面，且每面回合数与文件一致
python -m executor.domain "jobs/娄希、娄希、奥伯龙.json"

# 示例作业应成功
python -m executor.domain examples/sample-job.json

# 非法 fixture 必须退出码 2，并输出含 wave/turn 的诊断
python -m executor.domain executor/domain/fixtures/invalid-slot.json; echo $?

# 既有回放不受影响
npm run replay
# 期望：36/36 passed
```

## Acceptance criteria

- [ ] 两个现有作业都能编译成功，面/回合数量与 JSON 一致。
- [ ] 多回合 fixture 编译后保留全部回合，不再只取首回合。
- [ ] 换人、御主技能目标、宝具卡序在输出计划中字段完整。
- [ ] 槽位越界、空槽位引用、未知 `kind` 均返回诊断且退出码 2。
- [ ] 诊断包含 `path`，并在能定位时包含 `wave` 与 `turn`。
- [ ] `schemas/job-v1.schema.json` 与 `src/types.ts` 的字段语义一致。
- [ ] `npm run replay` 36/36 通过。
- [ ] 未新增第三方依赖，未修改前端与现有作业文件。

## References

- Spec：`.scratch/backend-rebuild/spec.md` → Implementation Decisions / 作业模型
- ADR：`docs/adr/0002-job-storage-owned-by-desktop-shell.md`
- 现有类型：`src/types.ts`（`HugoFgoJob`、`JobWave`、`JobTurn`、`JobAction`）
- 旧行为参考：`scripts/farm_ember.py` 的 `planned_attack()`（只读首回合，正是要消除的限制）
- 行为基线：B18（三面技能与宝具卡序）
