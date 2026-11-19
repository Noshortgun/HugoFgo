# NN: 标题

**Status:** ready-for-agent
**Blocked by:** NN（标题），或 None

## Context

为什么做、现状是什么。给出具体文件与行为，而不是抽象描述。引用相关 spec / ADR / 行为基线条目。

## Goal

用户或调用方能观察到的结果。一句话。

## Constraints

不能破坏的东西：作业格式、前端契约、安全规则、回放全绿等。

## Implementation notes

给施工者的具体指引：
- 新建/修改哪些模块与文件
- 对外接口的形状（参数、返回值、错误）
- 数据结构或协议的关键字段
- 可以复用的现有代码
- 明确不做的部分

## Steps

建议实施顺序，3–7 步。每步都可独立验证。

## Verification

必须能直接复制执行的命令，以及期望输出。例如：

```bash
npm run replay
# 期望：36/36 passed，退出码 0
```

## Acceptance criteria

- [ ] 可观察、可核对的条件
- [ ] 与 Verification 一一对应，不写无法检查的条目

## References

- Spec：相关章节
- ADR：相关决策
- 行为基线：Bxx
- 现有代码：路径与入口
