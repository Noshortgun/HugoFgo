# 65: 从者技能冷却识别与生效确认

**Status:** ready-for-agent
**Blocked by:** None

## Context

实机运行 `logs/farm/run-1795544391435-48136` 暴露技能漏点：上一招的空白加速刚结束，执行器在同一轮循环里立刻点下一招，点击被游戏输入锁吞掉。更严重的是确认只看「画面回到可操作」，不看技能本身是否生效，被吞的技能可能被当成成功并永久跳过。

既有测试 `test_untargeted_skill_does_not_wait_for_cooldown_detection` 明确把「不等待冷却检测」当成既定行为；`docs/status.md` 也写着「不检测冷却、NP 或实际生效」。

## Goal

用技能图标下方的「剩余 N」标签识别冷却，让「没放出去」可以被当场发现：冷却中的技能不点，点击后必须看到图标进入冷却才确认。

## Constraints

- 不新增作业字段，不改变作业 Schema 与编译规则。
- 冷却判据只覆盖从者技能；御主技能与令咒保持原确认方式。
- 没有冷却测量的帧（control 缺失或 `on_cooldown` 为 None）保持原确认规则，不据此否决。
- 该位置已换成其他从者（自我退场、被击倒补位）时不再要求原技能进入冷却。
- 补点只在实测图标仍可用时触发，且最多一次，避免把已生效的技能再点一次。
- 不检测 NP 数值与实际伤害效果；敌方回合识别不在本票范围。

## Implementation notes

- 判据选择：冷却图标是「原图 × 暗化」，可用与冷却图标的平均灰度在实测样本中只有 83 → 99.5 的间隙，无法泛化到全部技能图标；「剩余」二字是所有从者共用的固定 UI，模板匹配得分冷却 0.72–1.00、可用 0.24–0.43，取阈值 0.6。
- 模板：`executor/perception/build_templates.py` 的 `EMBLEMS` 新增 `skill_cooldown`，取自 `captures/2026-11-23/key/skill-cooldowns.png`；`measure_np_slots` 只遍历 `np_` 前缀纹章，互不污染。
- 感知：`evidence.measure_skill_cooldowns` 按图标几何逐槽匹配；`Control.on_cooldown` 为三态（True 冷却 / False 可用 / None 未测量），冷却图标 `actionable=False`、`reason='skill_cooldown'`。
- 执行器：`skill_effect_confirmed` 把冷却作为确认条件；`skill_retry_allowed` 让 2 秒恢复补点只在图标仍可用时发生；`skill_unavailable` 在冷却持续 30 秒未转好时安全停止。
- 标注修正：2026-11-19-ember 的 07/08 帧中 `从者1技能1` 实为冷却中（剩余 3），原断言要求可点，已改判。

## Steps

1. 增加冷却模板与逐槽测量，接入技能控件可操作性。
2. 执行器加入冷却确认门槛、有限补点与冷却超时停止。
3. 修正错误标注，补单测与真实截图回放用例。
4. 实机复验指定作业后再更新验收状态。

## Verification

```bash
python -m unittest discover -s executor -t .
npm run replay
npm run verify
# 期望：全部通过，退出码 0
```

## Acceptance criteria

- [x] 冷却中的技能图标不再可点，持续 30 秒未转好以 `skill_unavailable` 停止。
- [x] 点击后图标未进入冷却不予确认；实测仍可用时 2 秒后最多补点一次。
- [x] 自我退场／补位换掉该位置从者时豁免冷却要求。
- [x] 新增 `skill-cooldown-unconfirmed` 回放：图标未进冷却则不确认并超时停止。
- [ ] 指定作业实机复验通过。

## References

- 实机证据：`logs/farm/run-1795544391435-48136`、`logs/farm/run-1795540884604-48136`
- 相关票据：第 62 票（技能动画加速与战斗恢复确认）、第 57 票（退场补位）、第 63 票（运行证据摘要）
- 状态文档：`docs/status.md` 第 4、5 节
