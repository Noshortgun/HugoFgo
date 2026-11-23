"""真实截图回放；--filter 与 --output 组合转发到 cv/replay.py 旧用例集。

默认运行本文件的真实截图场景，--only 选择场景，--report 保存 JSON 汇总。
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from executor.core import RunOptions, RunStatus, run
from executor.core.ap_policy import RecoveryPolicy
from executor.core.model import RunResult
from executor.core.smoke import ConfirmedStartPerception
from executor.device import FakeDevice
from executor.device.base import Frame
from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan,
                             TurnPlan, WavePlan, compile_job)
from executor.perception import Observation, Perception
from executor.session import MemoryEventSink, RunEvent

ROOT = Path(__file__).resolve().parents[1]


class ReplayFailure(AssertionError):
    pass


@dataclass(frozen=True)
class ReplayCase:
    name: str
    frames: tuple[str, ...]
    actions: tuple[str, ...]
    status: RunStatus = RunStatus.COMPLETED
    reason: str = 'completed'
    states: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    excluded_event_types: tuple[str, ...] = ()
    times: tuple[float, ...] | None = None
    plan_factory: Callable[[], ExecutionPlan] = field(default=lambda: replay_plan(), repr=False)
    options: RunOptions = field(default_factory=lambda: RunOptions(write_evidence=False))
    completed_runs: int = 1
    evidence: bool = False
    confirmed_start: tuple[int, int] | None = None
    suppress_controls: tuple[str, ...] = ()
    fresh_hashes: bool = True
    ignored_action_reasons: tuple[str, ...] = ()


@dataclass
class ReplayReport:
    case: ReplayCase
    result: RunResult
    device: FakeDevice
    events: MemoryEventSink
    observations: list[Observation]
    trace: list[dict]

    def summary(self) -> dict:
        return {
            'name': self.case.name,
            'status': self.result.status.value,
            'reason': self.result.reason,
            'actions': [dict(action) for action in self.device.actions],
            'action_controls': [event.data['control'] for event in self.events.events
                                if event.type == 'action'],
            'states': [observation.base_page.value for observation in self.observations],
            'events': [{'type': event.type, 'data': dict(event.data)}
                       for event in self.events.events],
            'evidence_dir': self.result.evidence_dir,
        }


class ReplayPerception:
    def __init__(self, perception: Perception, trace: list[dict], *,
                 suppress_controls: tuple[str, ...] = (), fresh_hashes: bool = True) -> None:
        self.perception = perception
        self.trace = trace
        self.suppress_controls = frozenset(suppress_controls)
        self.fresh_hashes = fresh_hashes
        self.observations: list[Observation] = []
        self.capture = 0
        self.hashes: dict[str, str] = {}

    def bind_formation(self, formation) -> None:
        self.perception.bind_formation(formation)

    def bind_mystic(self, mystic_id: str) -> None:
        bind = getattr(self.perception, 'bind_mystic', None)
        if bind is not None:
            bind(mystic_id)

    def _record(self, observation: Observation) -> Observation:
        if self.suppress_controls:
            observation = replace(observation, controls=tuple(
                control for control in observation.controls if control.name not in self.suppress_controls))
        self.capture += 1
        capture_hash = (f'{observation.frame_hash}:capture-{self.capture}' if self.fresh_hashes
                        else observation.frame_hash)
        self.hashes[observation.frame_hash] = capture_hash
        observed = replace(observation, frame_hash=capture_hash)
        self.observations.append(observed)
        self.trace.append({'kind': 'observation', 'capture': self.capture,
                           'state': observed.base_page.value})
        return observed

    def read(self, frame: Frame) -> Observation:
        return self._record(self.perception.read(frame))

    def read_fields(self, frame: Frame, names, *, timeout=None) -> Observation:
        observed = self.perception.read_fields(frame, names, timeout=timeout)
        return replace(observed, frame_hash=self.hashes.get(observed.frame_hash, observed.frame_hash))


class ReplayEvents(MemoryEventSink):
    def __init__(self, trace: list[dict]) -> None:
        super().__init__()
        self.trace = trace

    def emit(self, event: RunEvent) -> None:
        super().emit(event)
        self.trace.append({'kind': 'event', 'type': event.type, 'data': dict(event.data)})


def replay_plan() -> ExecutionPlan:
    cards = (
        AttackCardPlan(slot=2, type='quick'),
        AttackCardPlan(slot=0, type='quick'),
        AttackCardPlan(slot=1, type='quick'),
    )
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=cards))
    formation = (
        FormationSlotPlan(slot=0, name='娄希', svt_id=705000, role='Berserker',
                          support=True, support_required=True, ce='迦勒底午茶时光', ce_id=9403520),
        FormationSlotPlan(slot=1, name='娄希', svt_id=705000, role='Berserker'),
        FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100, role='Pretender'),
    )
    return ExecutionPlan(mystic_id='default', enemy_count=1,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),), formation=formation)


def skill_plan() -> ExecutionPlan:
    plan = replay_plan()
    skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
    turn = replace(plan.waves[0].turns[0], actions=(skill,))
    return replace(plan, waves=(replace(plan.waves[0], turns=(turn,)),))


def target_plan() -> ExecutionPlan:
    plan = replay_plan()
    target = ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2)
    turn = replace(plan.waves[0].turns[0], actions=(target,))
    return replace(plan, waves=(replace(plan.waves[0], turns=(turn,)),))


def load_frames(case: ReplayCase) -> list[Frame]:
    times = case.times or tuple(index * 0.5 for index in range(len(case.frames)))
    if len(times) != len(case.frames):
        raise ValueError(f'{case.name}: 帧数与时间数不一致')
    frames = []
    for relative, captured_at in zip(case.frames, times):
        data = (ROOT / relative).read_bytes()
        frames.append(Frame(image=data, width=1280, height=720, captured_at=captured_at))
    return frames


def require_equal(case: ReplayCase, expected, actual, label: str) -> None:
    if expected != actual:
        raise ReplayFailure(f'{case.name}: {label}\n预期：{expected!r}\n实际：{actual!r}')


def require_ordered(case: ReplayCase, expected: tuple[str, ...], actual: list[str], label: str) -> None:
    position = 0
    for value in actual:
        if position < len(expected) and value == expected[position]:
            position += 1
    if position != len(expected):
        raise ReplayFailure(f'{case.name}: {label} 缺少有序子序列 {expected!r}；实际 {actual!r}')


def check_report(report: ReplayReport) -> None:
    case = report.case
    require_equal(case, case.status, report.result.status, '运行状态')
    require_equal(case, case.reason, report.result.reason, '停止原因')
    actual_actions = [event.data['control'] for event in report.events.events
                      if event.type == 'action' and event.data.get('reason') not in case.ignored_action_reasons]
    require_equal(case, list(case.actions), actual_actions, '动作序列')
    require_ordered(case, case.states, [item.base_page.value for item in report.observations], '画面顺序')
    actual_events = [event.type for event in report.events.events]
    require_ordered(case, case.event_types, actual_events, '事件顺序')
    for event_type in case.excluded_event_types:
        if event_type in actual_events:
            raise ReplayFailure(f'{case.name}: 不应出现事件 {event_type}')
    require_equal(case, case.completed_runs, report.result.progress.completed_runs, '完成场数')

    action_positions = [index for index, item in enumerate(report.trace)
                        if item['kind'] == 'event' and item['type'] == 'action']
    for position in action_positions:
        action = report.trace[position]
        data = action['data']
        if data.get('reason') == 'command_card_batch' and data.get('card_position') in {1, 2}:
            following = report.trace[position + 1] if position + 1 < len(report.trace) else None
            if (following is None or following.get('type') != 'action'
                    or following['data'].get('batch_id') != data.get('batch_id')
                    or following['data'].get('card_position') != data['card_position'] + 1):
                raise ReplayFailure(f'{case.name}: 连击内部出现取帧或动作中断：{action}')
            continue
        if not any(item['kind'] == 'observation' for item in report.trace[position + 1:]):
            raise ReplayFailure(f'{case.name}: 动作后没有重新识别：{action}')

    if case.evidence:
        if report.result.evidence_dir is None:
            raise ReplayFailure(f'{case.name}: 缺少证据目录')
        root = Path(report.result.evidence_dir)
        for name in ('result.json', 'observation.json', 'events.jsonl', 'failure.png'):
            if not (root / name).is_file():
                raise ReplayFailure(f'{case.name}: 缺少证据文件 {name}')


def run_case(case: ReplayCase, output: str | Path) -> ReplayReport:
    trace: list[dict] = []
    device = FakeDevice(load_frames(case))
    perception = Perception()
    reader = (ConfirmedStartPerception(perception, *case.confirmed_start)
              if case.confirmed_start is not None else perception)
    observed = ReplayPerception(reader, trace, suppress_controls=case.suppress_controls,
                                fresh_hashes=case.fresh_hashes)
    events = ReplayEvents(trace)
    run_dir = Path(output) / case.name
    options = replace(case.options, run_dir=str(run_dir), write_evidence=True)
    try:
        result = run(case.plan_factory(), device, observed, events, options=options)
    finally:
        perception.close()
    report = ReplayReport(case, result, device, events, observed.observations, trace)
    check_report(report)
    return report


FULL_LOOP = 'captures/2026-11-20/key/'
MANUAL_LOOP = 'captures/2026-11-23/key/'


def manual_frames(*names: str) -> tuple[str, ...]:
    return tuple(MANUAL_LOOP + name for name in names for _ in range(2))


def manual_job_plan() -> ExecutionPlan:
    compiled = compile_job((ROOT / 'jobs/娄希、娄希、奥伯龙.json').read_text(encoding='utf-8'))
    if compiled.plan is None:
        raise ValueError(f'人工关键帧作业无法编译：{compiled.diagnostics}')
    return compiled.plan


CASES = (
    ReplayCase(
        name='full-loop',
        frames=(
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            FULL_LOOP + 'team-confirm.png', FULL_LOOP + 'team-confirm.png',
            FULL_LOOP + '12-battle-start.png',
            FULL_LOOP + 'battle-wave1.png', FULL_LOOP + 'battle-wave1.png',
            FULL_LOOP + 'card-select.png',
            'captures/2026-11-19-ember/36-attack-animation.png',
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            FULL_LOOP + 'result-exp.png', FULL_LOOP + 'result-exp.png',
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png',
        ),
        actions=('助战卡片第2行', '开始任务', '攻击', '卡1', '卡2', '卡4', '战斗跳过',
                 '结算推进', '结算推进', '下一步'),
        states=('support', 'team', 'loading', 'battle', 'card', 'result', 'drops', 'battle'),
        event_types=('state_changed', 'action', 'action', 'action', 'finished'),
    ),
    ReplayCase(
        name='stale-result-frame',
        frames=(
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 31.5),
        actions=('结算推进',),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('result', 'unknown'),
        event_types=('action', 'warning', 'failed'),
        completed_runs=0,
        fresh_hashes=False,
    ),
    ReplayCase(
        name='skill-action-loop',
        frames=(
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            FULL_LOOP + 'team-confirm.png', FULL_LOOP + 'team-confirm.png',
            FULL_LOOP + '12-battle-start.png',
            FULL_LOOP + 'battle-wave1.png', FULL_LOOP + 'battle-wave1.png',
            'captures/2026-11-19-ember/key/05-skill.png',
            FULL_LOOP + 'battle-after-cards.png', FULL_LOOP + 'battle-after-cards.png',
            FULL_LOOP + 'card-select.png',
            'captures/2026-11-19-ember/36-attack-animation.png',
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            FULL_LOOP + 'result-exp.png', FULL_LOOP + 'result-exp.png',
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png',
        ),
        actions=('助战卡片第2行', '开始任务', '从者1技能1', '攻击', '卡1', '卡2', '卡4',
                 '战斗跳过', '结算推进', '结算推进', '下一步'),
        states=('support', 'team', 'loading', 'battle', 'card', 'result', 'drops', 'battle'),
        event_types=('action', 'action', 'action', 'action', 'finished'),
        plan_factory=skill_plan,
    ),
    ReplayCase(
        name='drops-fallback',
        frames=(
            'captures/2026-11-19-ember/key/14-drops.png',
            'captures/2026-11-19-ember/key/14-drops.png',
            FULL_LOOP + 'repeat-dialog.png',
        ),
        actions=('下一步',),
        states=('drops', 'battle'),
        event_types=('action', 'finished'),
        suppress_controls=('下一步',),
    ),
    ReplayCase(
        name='support-search-refresh',
        frames=(
            FULL_LOOP + 'support-select-wrong-ce.png', FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-select-wrong-ce.png', FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-select-wrong-ce.png', FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-list-update-confirm.png', FULL_LOOP + 'support-list-update-confirm.png',
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 40.0),
        actions=('助战列表向下', '列表更新', '是', '助战卡片第2行'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('support', 'unknown'),
        event_types=('action', 'action', 'action', 'action', 'warning', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='support-refresh-cooldown',
        frames=(
            FULL_LOOP + 'support-select-wrong-ce.png', FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-select-wrong-ce.png', FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-select-wrong-ce.png', FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-select-wrong-ce.png',
            'captures/2026-11-22/key/support-refresh-cooldown.png',
            'captures/2026-11-22/key/support-refresh-cooldown.png',
            FULL_LOOP + 'support-select-wrong-ce.png',
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 9.0, 9.5, 10.0, 40.0),
        actions=('助战列表向下', '列表更新', '关闭', '助战卡片第2行'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('support', 'unknown'),
        event_types=('action', 'action', 'action', 'action', 'warning', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='battle-status-detail',
        frames=(
            FULL_LOOP + 'battle-wave1.png',
            'captures/2026-11-22/key/battle-status-change-detail.png',
            'captures/2026-11-22/key/battle-status-change-detail.png',
            FULL_LOOP + 'battle-target-enemy2.png', FULL_LOOP + 'battle-target-enemy2.png',
            FULL_LOOP + 'card-select.png',
            'captures/2026-11-19-ember/36-attack-animation.png',
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            FULL_LOOP + 'result-exp.png', FULL_LOOP + 'result-exp.png',
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png',
        ),
        actions=('敌方2', '关闭', '攻击', '卡1', '卡2', '卡4', '战斗跳过',
                 '结算推进', '结算推进', '下一步'),
        states=('battle', 'card', 'result', 'drops'),
        event_types=('state_changed', 'action', 'action', 'action', 'finished'),
        plan_factory=target_plan,
        confirmed_start=(1, 1),
    ),
    ReplayCase(
        name='repeat-two-runs',
        frames=(
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            FULL_LOOP + 'result-exp.png', FULL_LOOP + 'result-exp.png',
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png', FULL_LOOP + 'repeat-dialog.png',
            FULL_LOOP + 'team-confirm.png', FULL_LOOP + 'team-confirm.png',
            FULL_LOOP + 'battle-wave1.png', FULL_LOOP + 'battle-wave1.png',
            FULL_LOOP + 'card-select.png',
            'captures/2026-11-19-ember/36-attack-animation.png',
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            FULL_LOOP + 'result-exp.png', FULL_LOOP + 'result-exp.png',
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png',
        ),
        actions=('结算推进', '结算推进', '下一步', '连续出击', '开始任务', '攻击',
                 '卡1', '卡2', '卡4', '战斗跳过', '结算推进', '结算推进', '下一步'),
        states=('result', 'drops', 'battle', 'team', 'battle', 'card', 'result', 'drops', 'battle'),
        event_types=('action', 'action', 'action', 'action', 'action', 'finished'),
        options=RunOptions(run_count=2, write_evidence=False),
        completed_runs=2,
    ),
    ReplayCase(
        name='ap-drain-complete',
        frames=(
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png', FULL_LOOP + 'repeat-dialog.png',
            FULL_LOOP + 'ap-recovery-bronze.png',
        ),
        actions=('下一步', '连续出击'),
        reason='ap_drained',
        states=('drops', 'battle', 'ap_recovery'),
        event_types=('action', 'drain_started', 'action', 'finished'),
        options=RunOptions(recovery=RecoveryPolicy(drain_after_turns=True), write_evidence=False),
    ),
    ReplayCase(
        name='ap-recovery-after-repeat',
        frames=(
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png', FULL_LOOP + 'repeat-dialog.png',
            FULL_LOOP + 'ap-recovery-bronze.png', FULL_LOOP + 'ap-recovery-bronze.png',
            FULL_LOOP + 'team-confirm.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 33.5),
        actions=('下一步', '连续出击', '青铜果实'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('drops', 'battle', 'ap_recovery', 'team', 'unknown'),
        event_types=('action', 'action', 'action', 'recovery_used', 'warning', 'failed'),
        options=RunOptions(run_count=2, write_evidence=False),
        completed_runs=1,
    ),
    ReplayCase(
        name='ap-recovery-back-to-repeat',
        frames=(
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png', FULL_LOOP + 'repeat-dialog.png',
            FULL_LOOP + 'ap-recovery-bronze.png', FULL_LOOP + 'ap-recovery-bronze.png',
            FULL_LOOP + 'repeat-dialog.png', FULL_LOOP + 'repeat-dialog.png',
            FULL_LOOP + 'team-confirm.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 34.5),
        actions=('下一步', '连续出击', '青铜果实', '连续出击'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('drops', 'battle', 'ap_recovery', 'battle', 'team', 'unknown'),
        event_types=('action', 'action', 'action', 'recovery_used', 'action', 'warning', 'failed'),
        options=RunOptions(run_count=2, write_evidence=False),
        completed_runs=1,
    ),
    ReplayCase(
        name='ap-recovery-unconfirmed',
        frames=(
            FULL_LOOP + 'ap-recovery-bronze.png', FULL_LOOP + 'ap-recovery-bronze.png',
            FULL_LOOP + 'ap-recovery-bronze.png',
        ),
        times=(0.0, 0.5, 31.0),
        actions=('青铜果实',),
        status=RunStatus.STOPPED,
        reason='action_confirmation_timeout',
        states=('ap_recovery',),
        event_types=('state_changed', 'action', 'failed'),
        excluded_event_types=('recovery_used',),
        completed_runs=0,
    ),
    ReplayCase(
        name='ap-default-bronze',
        frames=(
            FULL_LOOP + 'ap-recovery-bronze.png', FULL_LOOP + 'ap-recovery-bronze.png',
            FULL_LOOP + 'team-confirm.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 2.0, 32.0),
        actions=('青铜果实',),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('ap_recovery', 'team', 'unknown'),
        event_types=('action', 'recovery_used', 'warning', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='ap-scroll-bronze',
        frames=(
            FULL_LOOP + 'ap-recovery.png', FULL_LOOP + 'ap-recovery.png',
            FULL_LOOP + 'ap-recovery-bronze.png', FULL_LOOP + 'ap-recovery-bronze.png',
            FULL_LOOP + 'team-confirm.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 32.5),
        actions=('AP 恢复列表向下', '青铜果实'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('ap_recovery', 'team', 'unknown'),
        event_types=('action', 'action', 'recovery_used', 'warning', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='ap-empty-whitelist',
        frames=(FULL_LOOP + 'ap-recovery-bronze.png',),
        actions=(),
        status=RunStatus.STOPPED,
        reason='ap_recovery',
        states=('ap_recovery',),
        event_types=('state_changed', 'failed'),
        options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset()), write_evidence=False),
        completed_runs=0,
        evidence=True,
    ),
    ReplayCase(
        name='grand-page-recovery',
        frames=(
            'captures/2026-11-19-ember/34-grand-support-confirm.png',
            'captures/2026-11-19-ember/34-grand-support-confirm.png',
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 32.0),
        actions=('关闭', '助战卡片第2行'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('support', 'unknown'),
        event_types=('action', 'action', 'warning', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='friend-request-recovery',
        frames=(
            'captures/2026-11-21/key/friend-request.png',
            'captures/2026-11-21/key/friend-request.png',
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 32.0),
        actions=('结束', '助战卡片第2行'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('support', 'unknown'),
        event_types=('action', 'action', 'warning', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='class-info-recovery',
        frames=(
            FULL_LOOP + '09-class-filter.png', FULL_LOOP + '09-class-filter.png',
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 32.0),
        actions=('关闭', '助战卡片第2行'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('support', 'unknown'),
        event_types=('action', 'action', 'warning', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='resume-support-unknown',
        frames=(
            FULL_LOOP + 'support-select-correct-ce.png', FULL_LOOP + 'support-select-correct-ce.png',
            FULL_LOOP + 'team-confirm.png', FULL_LOOP + 'team-confirm.png',
            'captures/2026-11-21/key/repeat-over-support.png',
            'captures/2026-11-21/key/repeat-over-support.png',
        ),
        times=(0.0, 0.5, 1.0, 1.5, 2.0, 32.0),
        actions=('助战卡片第2行', '开始任务'),
        status=RunStatus.STOPPED,
        reason='unknown_timeout',
        states=('support', 'team', 'unknown'),
        event_types=('action', 'action', 'warning', 'failed'),
        completed_runs=0,
        evidence=True,
    ),
    ReplayCase(
        name='resume-battle',
        frames=(
            FULL_LOOP + 'battle-wave1.png', FULL_LOOP + 'battle-wave1.png',
            FULL_LOOP + 'card-select.png',
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            FULL_LOOP + 'result-exp.png', FULL_LOOP + 'result-exp.png',
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png',
        ),
        actions=('攻击', '卡1', '卡2', '卡4', '结算推进', '结算推进', '下一步'),
        states=('battle', 'card', 'result', 'drops', 'battle'),
        event_types=('state_changed', 'action', 'finished'),
        confirmed_start=(1, 1),
    ),
    ReplayCase(
        name='resume-result',
        frames=(
            FULL_LOOP + 'result.png', FULL_LOOP + 'result.png',
            FULL_LOOP + 'result-exp.png', FULL_LOOP + 'result-exp.png',
            FULL_LOOP + 'drops.png', FULL_LOOP + 'drops.png',
            FULL_LOOP + 'repeat-dialog.png',
        ),
        actions=('结算推进', '结算推进', '下一步'),
        states=('result', 'drops', 'battle'),
        event_types=('action', 'action', 'action', 'finished'),
    ),
    ReplayCase(
        name='manual-full-loop',
        frames=manual_frames(
            'manual-support-01.png', 'manual-support-berserker-02.png', 'manual-support-berserker-03.png',
            'manual-team-04.png', 'manual-w1-t1-start-05.png', 'manual-w1-t1-target2-06.png',
            'manual-w1-t1-after-s3-07.png', 'manual-w1-t1-after-s1-08.png', 'manual-w1-t1-cards-09.png',
            'manual-w2-t1-start-10.png', 'manual-w2-t1-after-support-s3-11.png',
            'manual-w2-t1-after-support-s1-12.png', 'manual-w2-t1-after-oberon-s1-13.png',
            'manual-w2-t1-cards-14.png', 'manual-w3-t1-start-15.png',
            'manual-w3-t1-oberon-s2-target-16.png', 'manual-w3-t1-after-oberon-s2-17.png',
            'manual-w3-t1-oberon-s3-target-18.png', 'manual-w3-t1-after-oberon-s3-19.png',
            'manual-w3-t1-master-menu-20.png', 'manual-w3-t1-after-master-s1-21.png',
            'manual-w3-t1-master-menu-s2-22.png', 'manual-w3-t1-master-s2-target-23.png',
            'manual-w3-t1-after-master-s2-24.png', 'manual-w3-t1-cards-25.png',
            'manual-result-26.png', 'manual-result-exp-27.png', 'manual-drops-28.png',
            'manual-repeat-dialog-29.png',
        ),
        plan_factory=manual_job_plan,
        actions=(
            '职阶筛选:berserker', '助战列表向下', '助战卡片第2行', '开始任务',
            '敌方2', '从者2技能3', '从者2技能1', '攻击', '宝具卡2', '卡4', '卡1',
            '从者1技能3', '从者1技能1', '从者3技能1', '攻击', '卡4', '宝具卡1', '卡1',
            '从者3技能2', '目标从者2', '从者3技能3', '目标从者2',
            '御主技能', '御主技能1', '御主技能', '御主技能2', '目标从者2',
            '攻击', '宝具卡2', '卡1', '卡3', '结算推进', '结算推进', '下一步',
        ),
        ignored_action_reasons=('skill_animation_skip', 'attack_animation_skip'),
        states=('support', 'team', 'battle', 'card', 'result', 'drops', 'battle'),
        event_types=('state_changed', 'action', 'finished'),
    ),
    ReplayCase(
        name='manual-team-mystic-mismatch',
        frames=(MANUAL_LOOP + 'manual-team-04.png', MANUAL_LOOP + 'manual-team-04.png'),
        times=(0.0, 30.0),
        plan_factory=lambda: replace(manual_job_plan(), mystic_id='default'),
        actions=(),
        status=RunStatus.STOPPED,
        reason='team_mismatch',
        states=('team',),
        event_types=('state_changed', 'failed'),
        completed_runs=0,
    ),
    ReplayCase(
        name='manual-team-servant-mismatch',
        frames=(MANUAL_LOOP + 'manual-team-04.png', MANUAL_LOOP + 'manual-team-04.png'),
        times=(0.0, 30.0),
        plan_factory=lambda: replace(manual_job_plan(), formation=(
            *manual_job_plan().formation[:2], replace(manual_job_plan().formation[2], svt_id=500800))),
        actions=(), status=RunStatus.STOPPED, reason='team_mismatch', states=('team',),
        event_types=('state_changed', 'failed'), completed_runs=0,
    ),
    ReplayCase(
        name='manual-team-support-ce-mismatch',
        frames=(MANUAL_LOOP + 'manual-team-04.png', MANUAL_LOOP + 'manual-team-04.png'),
        times=(0.0, 30.0),
        plan_factory=lambda: replace(manual_job_plan(), formation=(
            replace(manual_job_plan().formation[0], ce_id=9400480), *manual_job_plan().formation[1:])),
        actions=(), status=RunStatus.STOPPED, reason='team_mismatch', states=('team',),
        event_types=('state_changed', 'failed'), completed_runs=0,
    ),
)


def get_case(name: str) -> ReplayCase:
    try:
        return next(case for case in CASES if case.name == name)
    except StopIteration as error:
        raise KeyError(f'未知回放用例：{name}') from error


def legacy_main(argv: list[str]) -> int:
    namespace = runpy.run_path(str(ROOT / 'cv' / 'replay.py'))
    previous = sys.argv
    sys.argv = [str(ROOT / 'cv' / 'replay.py'), *argv]
    try:
        return int(namespace['main']())
    finally:
        sys.argv = previous


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if any(argument == '--filter' or argument.startswith('--filter=') for argument in arguments):
        return legacy_main(arguments)
    parser = argparse.ArgumentParser(prog='python -m executor.replay', description=__doc__)
    parser.add_argument('--only', action='append', help='只运行指定用例，可重复')
    parser.add_argument('--output', help='保留各用例运行证据的目录；与 --filter 同用时为旧报告路径')
    parser.add_argument('--report', help='JSON 汇总报告路径')
    parser.add_argument('--filter', help='运行名称包含指定文本的 cv/replay.py 旧用例')
    args = parser.parse_args(arguments)
    selected = tuple(get_case(name) for name in args.only) if args.only else CASES

    temporary = None
    if args.output:
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix='hugofgo-executor-replay-')
        output = Path(temporary.name)
    try:
        reports = []
        try:
            for case in selected:
                report = run_case(case, output)
                reports.append(report.summary())
                print(f"OK   {case.name:<36} {report.result.status.value}/{report.result.reason}")
        except (ReplayFailure, KeyError) as error:
            print(f'FAIL {error}')
            return 1
        payload = {'passed': len(reports), 'total': len(selected), 'cases': reports}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.report:
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(text + '\n', encoding='utf-8')
        print(f'{len(reports)}/{len(selected)} passed')
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == '__main__':
    raise SystemExit(main())
