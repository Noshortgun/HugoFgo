from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from unittest import mock
from pathlib import Path

from executor.core import RunOptions, RunStatus, run as core_run
from executor.core.ap_policy import RecoveryItem, RecoveryPolicy
from executor.core.strategy import select_cards
from executor.device.base import DeviceError
from executor.device.config import DeviceConfig
from executor.device.fake import FakeDevice
from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FallbackStrategy,
                             FormationSlotPlan, TurnPlan, WavePlan, compile_job)
from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                 FieldStatus, Observation, Overlay, SupportCandidate, Variant)
from executor.perception.tests.fixtures import isolated_perception, load_frame
from executor.session import MemoryEventSink, RunControl
from executor.testing import ScriptedPerception, scenario_frames


def run(plan, device, perception, events, *, options=None, control=None):
    if options is None:
        options = RunOptions(write_evidence=False)
    elif options.run_dir is None:
        options = replace(options, write_evidence=False)
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        return core_run(plan, device, perception, events, options=options, control=control)


def button(name: str, box=(1069, 564, 1196, 637)) -> Control:
    return Control(name, ControlKind.BUTTON, box, True)


def command_card(index: int, slot: int) -> Control:
    boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
             (811, 415, 976, 655), (1066, 415, 1231, 655))
    return Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                   card=CommandCardIdentity(servant_slot=slot, card_type='arts'))


def observe(page: BasePage, *controls: Control) -> Observation:
    fields = {}
    if page is BasePage.BATTLE:
        fields = {
            'wave': Field(FieldStatus.CONFIRMED, 1),
            'turn': Field(FieldStatus.CONFIRMED, 1),
            'turn_boundary': Field(FieldStatus.CONFIRMED, True),
        }
        if (any(control.name == '攻击' and control.actionable for control in controls)
                and not any(control.name == '战斗菜单' for control in controls)):
            controls = (*controls, button('战斗菜单', (1141, 162, 1245, 250)))
    elif page is BasePage.TEAM:
        fields = {
            'team_servants': Field(FieldStatus.CONFIRMED, (705000, 2, 3)),
            'team_support_ce': Field(FieldStatus.CONFIRMED, 9403520),
            'team_mystic': Field(FieldStatus.CONFIRMED, 'default'),
        }
    return Observation(base_page=page, controls=tuple(controls), fields=fields, frame_hash=page.value)


def scripted(observations: list[Observation], times: list[float], *,
             preserve_hashes: bool = False, config: DeviceConfig | None = None) -> tuple[FakeDevice, object]:
    frames = scenario_frames(times, len(observations))

    return FakeDevice(frames, config=config), ScriptedPerception(observations, preserve_hashes=preserve_hashes)


def single_wave_plan(actions=(), np_positions=None) -> ExecutionPlan:
    cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
    turn = TurnPlan(actions=tuple(actions), np_positions=np_positions or {},
                    attack=AttackPlan(mode='verify', cards=cards))
    formation = (FormationSlotPlan(slot=0, name='从者0', svt_id=705000),
                 FormationSlotPlan(slot=1, name='从者1', svt_id=2),
                 FormationSlotPlan(slot=2, name='从者2', svt_id=3))
    return ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),), formation=formation)


def append_planned_turn_frames(observations: list[Observation], battle: Observation,
                               card: Observation, animation: Observation) -> None:
    observations.extend([battle, battle, animation, battle, battle])
    observations.append(card)
    observations.append(animation)


class RunnerTest(unittest.TestCase):
    def test_support_and_team_lead_into_the_same_battle_flow(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker', ce='午茶', ce_id=9403520)
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker',
                                     ce='午茶', ce_id=9403520)
        support = observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                  support=candidate))
        team = observe(BasePage.TEAM, button('开始任务', (1086, 635, 1248, 707)))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [support, support, support, team, team, battle, battle, wrong_cards],
            [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['职阶筛选:berserker', '助战卡片第1行', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual((565, 128), (device.actions[0]['x'], device.actions[0]['y']))

    def test_team_checks_specified_backups_while_skipping_empty_slots(self) -> None:
        base = single_wave_plan()
        plan = replace(base, formation=(
            base.formation[0],
            FormationSlotPlan(slot=1, name=''),
            FormationSlotPlan(slot=2, name=''),
            FormationSlotPlan(slot=3, name='指定候补', svt_id=4),
            FormationSlotPlan(slot=4, name=''),
            FormationSlotPlan(slot=5, name=''),
        ))
        team = observe(BasePage.TEAM, button('开始任务', (1086, 635, 1248, 707))).with_fields({
            'team_servants': Field(FieldStatus.CONFIRMED, (705000, None, None)),
            'team_backline_servants': Field(FieldStatus.CONFIRMED, (999, None, None)),
        })
        device, perception = scripted([team, team, team], [0.0, 0.5, 30.5])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('team_mismatch', result.reason)
        self.assertEqual([], [action for action in device.actions if action['action'] == 'tap'])

    def test_support_uses_extra_tab_next_to_berserker(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='奥伯龙', support=True, support_required=True,
                               svt_id=2800100, role='Pretender')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        candidate = SupportCandidate(name='奥伯龙', svt_id=2800100, role='Pretender')
        support = observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                  support=candidate))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([support, support, wrong_cards], [0.0, 0.5, 31.0])

        result = run(plan, device, perception, MemoryEventSink())

        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertEqual('tap', device.actions[0]['action'])
        self.assertEqual((632, 128), (device.actions[0]['x'], device.actions[0]['y']))

    def test_support_search_refreshes_at_bottom_and_rechecks_candidates(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        confirm = Observation(base_page=BasePage.SUPPORT,
                              overlays=frozenset({Overlay.SUPPORT_CONFIRM}),
                              controls=(button('是', (708, 534, 969, 592)),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        match = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=candidate))
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        observations = [wrong] * 7 + [confirm, confirm, match, match, team, team, battle, battle, wrong_cards]
        times = [index * 0.5 for index in range(len(observations))]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['职阶筛选:berserker', '助战列表向下', '列表更新', '是',
                          '助战卡片第1行', '开始任务', '攻击'], controls)
        self.assertEqual(['swipe'], [action['action'] for action in device.actions
                                    if action['action'] == 'swipe'])

    def test_support_scroll_and_refresh_frames_are_saved(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        confirm = Observation(base_page=BasePage.SUPPORT,
                              overlays=frozenset({Overlay.SUPPORT_CONFIRM}),
                              controls=(button('是', (708, 534, 969, 592)),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        match = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=candidate))
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        observations = [wrong] * 7 + [confirm, confirm, match, match, team, team, battle, battle, wrong_cards]
        times = [index * 0.5 for index in range(len(observations))]
        device, perception = scripted(observations, times)
        with tempfile.TemporaryDirectory() as directory:
            result = run(plan, device, perception, MemoryEventSink(),
                         options=RunOptions(run_dir=directory))

            self.assertEqual('card_mismatch', result.reason)
            for kind in ('swipe', 'refresh'):
                self.assertTrue((Path(directory) / f'support-{kind}-001.png').is_file())
                self.assertTrue((Path(directory) / f'support-{kind}-001.json').is_file())

    def test_same_support_rows_above_bottom_do_not_refresh(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他人', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 574, 1216, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        changed = [replace(wrong, controls=(replace(wrong.controls[0], visual_hash=str(index)),
                                           *wrong.controls[1:])) for index in range(5)]
        device, perception = scripted(
            [wrong, *changed, changed[-1]],
            [0.0, 0.5, 1.0, 1.5, 2.0, 31.0, 31.5],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        actions = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertNotIn('列表更新', actions)
        self.assertIn('助战列表向下', actions)

    def test_same_support_rows_at_bottom_do_refresh(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他人', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 650), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        device, perception = scripted([wrong, wrong, wrong, wrong, wrong], [0.0, 0.5, 1.0, 1.5, 2.0])
        events = MemoryEventSink()

        run(plan, device, perception, events)

        self.assertIn('列表更新', [event.data['control'] for event in events.events if event.type == 'action'])

    def test_second_support_refresh_waits_for_candidates_before_not_found(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker', ce_id=9403520)
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        confirm = Observation(base_page=BasePage.SUPPORT,
                              overlays=frozenset({Overlay.SUPPORT_CONFIRM}),
                              controls=(button('是', (708, 534, 969, 592)),))
        blank = observe(BasePage.SUPPORT)
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker', ce_id=9403520)
        match = observe(BasePage.SUPPORT,
                        Control('助战卡片第2行', ControlKind.ROW, (40, 360, 1000, 560), True,
                                support=candidate))
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        observations = [wrong] * 7 + [confirm] * 2 + [wrong] * 6 + [confirm] * 2
        observations += [blank, blank, match, match, team, team, battle, battle, wrong_cards]
        times = [index * 0.5 for index in range(len(observations))]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        self.assertIn('助战卡片第2行', [event.data['control'] for event in events.events
                                      if event.type == 'action'])

    def test_support_refresh_cooldown_closes_and_resumes_after_wait(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        cooldown = Observation(base_page=BasePage.SUPPORT,
                               overlays=frozenset({Overlay.SUPPORT_REFRESH_COOLDOWN}),
                               controls=(button('关闭', (508, 531, 772, 599)),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        match = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=candidate))
        unknown = observe(BasePage.UNKNOWN)
        observations = [wrong] * 7 + [cooldown, cooldown, wrong, match, match, unknown, unknown]
        times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 9.0, 9.5, 10.0, 40.0]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['职阶筛选:berserker', '助战列表向下', '列表更新', '关闭',
                          '助战卡片第1行'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        close = next(action for action in device.actions if action['action'] == 'tap'
                     and (action['x'], action['y']) == (640, 565))
        self.assertEqual((640, 565), (close['x'], close['y']))
        self.assertEqual(9.0, next(event.timestamp for event in events.events
                                   if event.type == 'action' and event.data['control'] == '助战卡片第1行'))

    def test_support_refresh_tolerates_confirmation_still_visible(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        confirm = Observation(base_page=BasePage.SUPPORT,
                              overlays=frozenset({Overlay.SUPPORT_CONFIRM}),
                              controls=(button('是', (708, 534, 969, 592)),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        match = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=candidate))
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        observations = [wrong] * 7 + [confirm] * 4 + [match] * 3 + [team, team, battle, battle, wrong_cards]
        times = [index * 0.5 for index in range(len(observations))]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['职阶筛选:berserker', '助战列表向下', '列表更新', '是',
                          '助战卡片第1行', '开始任务', '攻击'], controls)

    def test_support_refresh_without_confirmation_stops_without_repeating_tap(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        observations = [wrong] * 8
        times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 33.5]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertEqual(['职阶筛选:berserker', '助战列表向下', '列表更新'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual('page', events.events[-1].data['stage'])

    def test_friend_request_is_closed_before_support_selection(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        friend = Observation(base_page=BasePage.SUPPORT,
                             overlays=frozenset({Overlay.FRIEND_REQUEST}),
                             controls=(button('结束', (167, 582, 493, 649)),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        support = observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                  support=candidate))
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [friend, friend, support, support, team, team, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['结束', '职阶筛选:berserker', '助战卡片第1行', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_unchanged_frame_cannot_confirm_an_action(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        first = replace(battle, frame_hash='first')
        stale = replace(battle, frame_hash='first')
        confirmed = replace(battle, frame_hash='second')
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [first, stale, confirmed, wrong_cards],
            [0.0, 0.5, 1.1, 1.2], preserve_hashes=True,
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['攻击'], [event.data['control'] for event in actions])
        self.assertEqual(0.0, actions[-1].timestamp)

    def test_untargeted_skill_waits_for_skip_and_battle_recovery(self) -> None:
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        battle = observe(BasePage.BATTLE, button('攻击'), button('战斗菜单'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, animation, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['从者1技能1', '战斗跳过', '攻击'], [event.data['control'] for event in actions])
        self.assertEqual([0.0, 0.1, 0.3], [event.timestamp for event in actions])
        self.assertTrue(all(event.data['channel_ms'] >= 0 for event in actions))
        self.assertTrue(all(event.data['wait_ms'] >= 0 for event in actions))
        self.assertTrue(all(event.data['input_finished_at'] > 0 for event in actions))
        self.assertEqual(1, sum(event.data.get('reason') == 'skill_animation_skip' for event in actions))

    def test_untargeted_skill_without_cooldown_measurement_keeps_legacy_confirmation(self) -> None:
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        available = Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True)
        battle = observe(BasePage.BATTLE, button('攻击'), available)
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, animation, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者1技能1', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.type == 'warning' and event.data.get('reason') == 'action_recovery'
                             for event in events.events))

    def test_consecutive_skills_regress_the_live_361ms_early_input(self) -> None:
        first = ActionPlan(kind='skill', title='二技能', detail='', icon='', member_slot=0, skill=1)
        second = ActionPlan(kind='skill', title='三技能', detail='', icon='', member_slot=0, skill=2)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能2', ControlKind.SKILL, (130, 548, 190, 610), True),
                         Control('从者1技能3', ControlKind.SKILL, (217, 548, 278, 610), True))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, animation, battle, battle, animation, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        )
        events = MemoryEventSink()
        input_times = [
            1795498380.6858945,
            1795498380.7996883,
            1795498380.9,
            1795498381.6858945,
            1795498381.8,
            1795498381.9,
            1795498382.6858945,
        ]

        with mock.patch('executor.core.runner.time.time', side_effect=input_times):
            result = run(single_wave_plan((first, second)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['从者1技能2', '战斗跳过', '从者1技能3', '战斗跳过', '攻击'],
                         [event.data['control'] for event in actions])
        interval = actions[2].data['input_finished_at'] - actions[0].data['input_finished_at']
        self.assertGreater(interval, 0.3613193)
        self.assertGreater(actions[2].data['input_finished_at'], 1795498381.0472138)
        self.assertEqual(2, sum(event.data.get('reason') == 'skill_animation_skip' for event in actions))

    def test_merlin_second_skill_waits_for_first_skill_cooldown(self) -> None:
        first = ActionPlan(kind='skill', title='梅林一技能', detail='', icon='', member_slot=1, skill=0)
        second = ActionPlan(kind='skill', title='梅林二技能', detail='', icon='', member_slot=1, skill=1)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者2技能1', ControlKind.SKILL, (365, 548, 426, 610), True,
                                 'slot_visible:2', on_cooldown=False),
                         Control('从者2技能2', ControlKind.SKILL, (452, 548, 513, 610), True,
                                 'slot_visible:2', on_cooldown=False))
        device, perception = scripted([battle, battle, battle, battle, battle], [0.0, 0.1, 2.2, 63.0, 93.0])
        events = MemoryEventSink()

        result = run(single_wave_plan((first, second)), device, perception, events)

        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertEqual(['从者2技能1', '战斗跳过', '从者2技能1'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_skill_retry_requires_stable_available_evidence(self) -> None:
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        available = Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True,
                            'slot_visible:1', on_cooldown=False)
        battle = observe(BasePage.BATTLE, button('攻击'), available)
        device, perception = scripted(
            [battle, battle, battle, battle, battle, battle],
            [0.0, 0.1, 2.2, 2.4, 2.8, 33.0],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('action_confirmation_timeout', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['从者1技能1', '战斗跳过', '从者1技能1'],
                         [event.data['control'] for event in actions])
        self.assertEqual(2.8, actions[-1].timestamp)

    def test_servant_skill_is_confirmed_before_attack(self) -> None:
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.0, 1.5, 1.6],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者1技能1', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_self_target_skill_accepts_direct_animation_without_target_page(self) -> None:
        skill = ActionPlan(kind='skill', title='自身技能', detail='', icon='',
                           member_slot=1, skill=2, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者2技能3', ControlKind.SKILL, (539, 548, 600, 610), True))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.0, 1.5, 1.6],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者2技能3', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_self_target_skill_uses_frames_instead_of_fixed_interval(self) -> None:
        skill = ActionPlan(kind='skill', title='自身技能', detail='', icon='',
                           member_slot=1, skill=2, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者2技能3', ControlKind.SKILL, (539, 548, 600, 610), True))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.0, 1.5, 1.6])

        with mock.patch('executor.core.runner.time.sleep', return_value=None) as sleep:
            result = core_run(single_wave_plan((skill,)), device, perception, MemoryEventSink(),
                              options=RunOptions(write_evidence=False))

        self.assertEqual('card_mismatch', result.reason)
        self.assertNotIn(mock.call(1.5), sleep.call_args_list)

    def test_self_target_skill_still_selects_target_when_game_asks(self) -> None:
        skill = ActionPlan(kind='skill', title='自身技能', detail='', icon='',
                           member_slot=1, skill=2, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者2技能3', ControlKind.SKILL, (539, 548, 600, 610), True))
        target = Observation(
            base_page=BasePage.BATTLE,
            variants=frozenset({Variant.SKILL_TARGET}),
            controls=tuple(Control(f'目标从者{index}', ControlKind.TARGET,
                                   (index * 300, 269, index * 300 + 200, 499), True)
                           for index in range(1, 4)),
        )
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, target, animation, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者2技能3', '目标从者2', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual([0.0, 0.2, 0.3, 0.5],
                         [event.timestamp for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_self_target_skill_stops_instead_of_selecting_target_after_skip(self) -> None:
        skill = ActionPlan(kind='skill', title='自身技能', detail='', icon='',
                           member_slot=1, skill=2, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者2技能3', ControlKind.SKILL, (539, 548, 600, 610), True))
        target = Observation(
            base_page=BasePage.BATTLE,
            variants=frozenset({Variant.SKILL_TARGET}),
            controls=tuple(Control(f'目标从者{index}', ControlKind.TARGET,
                                   (index * 300, 269, index * 300 + 200, 499), True)
                           for index in range(1, 4)),
        )
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, animation, target, animation, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('skill_target_after_animation_skip', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['从者2技能3', '战斗跳过'],
                         [event.data['control'] for event in actions])
        self.assertEqual(1, sum(event.data.get('reason') == 'skill_animation_skip' for event in actions))

    def test_targeted_skill_confirms_target_panel_open_and_close(self) -> None:
        skill = ActionPlan(kind='skill', title='单体技能', detail='', icon='',
                           member_slot=0, skill=0, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'), button('战斗菜单'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
        target = Observation(
            base_page=BasePage.BATTLE,
            variants=frozenset({Variant.SKILL_TARGET}),
            controls=tuple(Control(f'目标从者{index}', ControlKind.TARGET,
                                   (index * 300, 269, index * 300 + 200, 499), True)
                           for index in range(1, 4)),
        )
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, target, battle, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者1技能1', '目标从者2', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual([0.0, 0.1, 0.2, 0.4],
                         [event.timestamp for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_targeted_skill_does_not_retry_after_target_panel_appears(self) -> None:
        skill = ActionPlan(kind='skill', title='单体技能', detail='', icon='',
                           member_slot=0, skill=0, player_target_slot=1)
        available = Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True,
                            'slot_visible:1', on_cooldown=False)
        battle = observe(BasePage.BATTLE, button('攻击'), button('战斗菜单'), available)
        used = observe(BasePage.BATTLE, button('攻击'), button('战斗菜单'),
                       Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), False,
                               'skill_cooldown', on_cooldown=True))
        target = Observation(
            base_page=BasePage.BATTLE,
            variants=frozenset({Variant.SKILL_TARGET}),
            controls=tuple(Control(f'目标从者{index}', ControlKind.TARGET,
                                   (index * 300, 269, index * 300 + 200, 499), True)
                           for index in range(1, 4)),
        )
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, battle, battle, battle, battle, target, used, used, used, wrong_cards],
            [0.0, 0.1, 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 2.8, 2.9, 3.0],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者1技能1', '目标从者2', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        recoveries = [event for event in events.events
                      if event.type == 'warning' and event.data.get('reason') == 'action_recovery']
        self.assertEqual(0, len(recoveries))

    def test_targeted_skill_stops_after_one_failed_recovery(self) -> None:
        skill = ActionPlan(kind='skill', title='单体技能', detail='', icon='',
                           member_slot=0, skill=0, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'), button('战斗菜单'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True,
                                 'slot_visible:1', on_cooldown=False))
        device, perception = scripted(
            [battle, battle, battle, battle, battle, battle],
            [0.0, 0.1, 2.1, 2.2, 2.6, 32.2],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('target_selection_timeout', result.reason)
        self.assertEqual(['从者1技能1', '从者1技能1'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, len([event for event in events.events
                                 if event.type == 'warning' and event.data.get('reason') == 'action_recovery']))
        self.assertEqual('skill_target', events.events[-1].data['stage'])
        self.assertGreaterEqual(events.events[-1].data['wait_ms'], 30000.0)

    def test_order_change_selects_both_slots_before_confirming(self) -> None:
        order_change = ActionPlan(kind='orderChange', title='换人', detail='', icon='',
                                  swap_out_slot=0, swap_in_slot=3)
        new_skill = ActionPlan(kind='skill', title='候补技能', detail='', icon='', member_slot=3, skill=0)
        base = single_wave_plan((order_change, new_skill))
        plan = replace(base, formation=(*base.formation, FormationSlotPlan(slot=3, name='候补')))
        battle = observe(BasePage.BATTLE, button('攻击'), button('御主技能', (1141, 162, 1245, 250)),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
        menu = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'御主技能{index}', ControlKind.BUTTON,
                                   (850 + index * 90, 275, 920 + index * 90, 352), True)
                           for index in range(1, 4)),
        )
        order_controls = [
            *(Control(f'先发第{index}位', ControlKind.TARGET,
                      (index * 200, 250, index * 200 + 180, 565), True) for index in range(1, 4)),
            *(Control(f'候补第{index}位', ControlKind.TARGET,
                      (600 + index * 200, 250, 780 + index * 200, 565), True) for index in range(1, 4)),
            Control('进行更替', ControlKind.BUTTON, (478, 591, 803, 661), True),
        ]
        order = Observation(base_page=BasePage.BATTLE, overlays=frozenset({Overlay.ORDER_CHANGE}),
                            controls=tuple(order_controls))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        changed = battle.with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2))})
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, menu, menu, order, order, order, order, order, order,
             animation, changed, changed, animation, changed, changed, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.9,
             3.0, 3.2, 3.7, 3.8, 4.0, 4.5, 4.6],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['御主技能', '御主技能3', '先发第1位', '候补第1位', '进行更替',
                          '战斗跳过', '从者1技能1', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(2, result.progress.action_index)
        confirmed = next(event for event in events.events if event.type == 'order_change_confirmed')
        self.assertEqual((3, 1, 2), confirmed.data['active_slots'])
        self.assertEqual((0,), confirmed.data['reserve_slots'])
        self.assertEqual((0, 3), (confirmed.data['out_slot'], confirmed.data['in_slot']))

    def test_order_change_mismatched_frontline_never_updates_mapping(self) -> None:
        order_change = ActionPlan(kind='orderChange', title='换人', detail='', icon='',
                                  swap_out_slot=0, swap_in_slot=3)
        base = single_wave_plan((order_change,))
        plan = replace(base, formation=(*base.formation, FormationSlotPlan(slot=3, name='候补')))
        battle = observe(BasePage.BATTLE, button('攻击'), button('御主技能', (1141, 162, 1245, 250))
                         ).with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (0, 1, 2))})
        menu = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'御主技能{index}', ControlKind.BUTTON,
                                   (850 + index * 90, 275, 920 + index * 90, 352), True)
                           for index in range(1, 4)),
        )
        order = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.ORDER_CHANGE}),
            controls=(
                *(Control(f'先发第{index}位', ControlKind.TARGET,
                          (index * 200, 250, index * 200 + 180, 565), True) for index in range(1, 4)),
                *(Control(f'候补第{index}位', ControlKind.TARGET,
                          (600 + index * 200, 250, 780 + index * 200, 565), True) for index in range(1, 4)),
                Control('进行更替', ControlKind.BUTTON, (478, 591, 803, 661), True),
            ),
        )
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, menu, order, order, order, animation, battle, battle],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 30.6],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertFalse(any(event.type == 'order_change_confirmed' for event in events.events))
        self.assertEqual(0, result.progress.action_index)

    def test_confirmed_auto_fill_uses_the_departed_frontline_position(self) -> None:
        retreat = ActionPlan(kind='skill', title='自我退场', detail='', icon='', member_slot=0, skill=0)
        successor = ActionPlan(kind='skill', title='候补技能', detail='', icon='', member_slot=3, skill=0)
        base = single_wave_plan((retreat, successor))
        plan = replace(base, formation=(*base.formation, FormationSlotPlan(slot=3, name='候补', svt_id=4)))
        battle = observe(
            BasePage.BATTLE, button('攻击'),
            Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True),
        ).with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (0, 1, 2))})
        filled = battle.with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2))})
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, animation, filled, filled, filled, animation, filled, filled, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        self.assertEqual(['从者1技能1', '战斗跳过', '从者1技能1', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        fills = [event for event in events.events if event.type == 'auto_fill_confirmed']
        self.assertEqual(1, len(fills))
        self.assertEqual((0, 3, 1), (fills[0].data['out_slot'], fills[0].data['in_slot'],
                                    fills[0].data['frontline_position']))

    def test_transient_skill_frame_cannot_confirm_auto_fill(self) -> None:
        retreat = ActionPlan(kind='skill', title='自我退场', detail='', icon='', member_slot=0, skill=0)
        following = ActionPlan(kind='skill', title='后续技能', detail='', icon='', member_slot=1, skill=0)
        base = single_wave_plan((retreat, following))
        plan = replace(base, formation=(*base.formation, FormationSlotPlan(slot=3, name='候补')))
        battle = observe(
            BasePage.BATTLE, button('攻击'),
            Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True),
            Control('从者2技能1', ControlKind.SKILL, (365, 548, 426, 610), True),
        ).with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (0, 1, 2))})
        transient = battle.with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2))})
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, animation, transient, battle, battle, animation, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        self.assertFalse(any(event.type == 'auto_fill_confirmed' for event in events.events))
        self.assertIn('从者2技能1', [event.data['control'] for event in events.events
                                   if event.type == 'action'])

    def test_live_faded_skill_frame_does_not_break_the_next_planned_skill(self) -> None:
        reader = isolated_perception(self.addCleanup)
        target = ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2)
        skill = ActionPlan(kind='skill', title='破灭的难题', detail='', icon='', member_slot=1, skill=2)
        following = ActionPlan(kind='skill', title='天体轨道调整', detail='', icon='', member_slot=1, skill=0)
        base = single_wave_plan((target, skill, following))
        plan = replace(base, formation=(
            replace(base.formation[0], support=True),
            *base.formation[1:],
            *(FormationSlotPlan(slot=slot, name='') for slot in range(3, 6)),
        ))
        reader.bind_formation(plan.formation)
        progress_fields = {
            'wave': Field(FieldStatus.CONFIRMED, 1),
            'turn': Field(FieldStatus.CONFIRMED, 1),
            'turn_boundary': Field(FieldStatus.CONFIRMED, True),
        }
        ready = reader.read(load_frame('captures/2026-11-20/key/battle-wave1.png')).with_fields(progress_fields)
        process = reader.read(
            load_frame('captures/2026-11-25/key/battle-faded-no-auto-fill.png')).with_fields(progress_fields)
        self.assertFalse(process.field('battle_servants').usable)
        used = replace(ready, controls=tuple(
            replace(control, actionable=False, reason='skill_cooldown', on_cooldown=True)
            if control.name == '从者2技能3' else control for control in ready.controls))
        device, perception = scripted(
            [ready, ready, process, used, used], [0.0, 0.1, 0.2, 0.3, 0.4])
        control = RunControl()

        class StopAfterFollowingSkill(MemoryEventSink):
            def emit(self, event):
                super().emit(event)
                if event.type == 'action' and event.data.get('control') == '从者2技能1':
                    control.stop()

        events = StopAfterFollowingSkill()
        result = run(plan, device, perception, events, control=control)

        self.assertEqual('manual_stop', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        self.assertEqual(['敌方2', '从者2技能3', '战斗跳过', '从者2技能1'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.type == 'auto_fill_confirmed' for event in events.events))

    def test_guts_or_uncertain_departure_keeps_the_original_frontline(self) -> None:
        for field in (Field(FieldStatus.CONFIRMED, (0, 1, 2)), Field(FieldStatus.UNCERTAIN)):
            with self.subTest(status=field.status):
                retreat = ActionPlan(kind='skill', title='可能自我退场', detail='', icon='',
                                     member_slot=0, skill=0)
                successor = ActionPlan(kind='skill', title='候补技能', detail='', icon='',
                                       member_slot=3, skill=0)
                base = single_wave_plan((retreat, successor))
                plan = replace(base, formation=(*base.formation, FormationSlotPlan(slot=3, name='候补')))
                battle = observe(BasePage.BATTLE, button('攻击'),
                                 Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True),
                                 ).with_fields({'battle_servants': field})
                animation = Observation(base_page=BasePage.BATTLE,
                                        variants=frozenset({Variant.BATTLE_ANIMATION}))
                device, perception = scripted(
                    [battle, animation, battle, battle], [0.0, 0.1, 0.2, 0.3])
                events = MemoryEventSink()

                result = run(plan, device, perception, events)

                self.assertEqual('unsupported_action', result.reason)
                self.assertFalse(any(event.type == 'auto_fill_confirmed' for event in events.events))

    def test_auto_fill_successor_inherits_strategy_priority(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='strategy', cards=()))
        strategy = FallbackStrategy(
            servant_order=('从者0', '从者1', '从者2', '候补'),
            servant_order_slots=(0, 1, 2, 3),
        )
        plan = replace(
            base,
            waves=(replace(base.waves[0], turns=(turn,)),),
            formation=(*base.formation, FormationSlotPlan(slot=3, name='候补', svt_id=4)),
            strategy=strategy,
        )
        battle = observe(BasePage.BATTLE, button('攻击')).with_fields({
            'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2)),
        })
        card = observe(BasePage.CARD, *(
            replace(command_card(index, slot), card=replace(command_card(index, slot).card,
                                                           crit_rate=10, affinity='neutral'))
            for index, slot in enumerate((3, 1, 2, 3, 2), start=1)
        ))
        device, perception = scripted(
            [battle, battle, card, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 0.2, 0.3, 30.3],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        card_actions = [event.data for event in events.events
                        if event.type == 'action' and event.data.get('reason') == 'command_card_batch']
        self.assertEqual([3, 3, 1], [item['servant_slot'] for item in card_actions])

    def test_order_change_uses_current_reserve_order_after_auto_fill(self) -> None:
        order_change = ActionPlan(kind='orderChange', title='换人', detail='', icon='',
                                  swap_out_slot=1, swap_in_slot=4)
        base = single_wave_plan((order_change,))
        plan = replace(base, formation=(
            *base.formation,
            FormationSlotPlan(slot=3, name='候补3', svt_id=4),
            FormationSlotPlan(slot=4, name='候补4', svt_id=5),
        ))
        battle = observe(BasePage.BATTLE, button('攻击'), button('御主技能', (1141, 162, 1245, 250))
                         ).with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2))})
        menu = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'御主技能{index}', ControlKind.BUTTON,
                                   (850 + index * 90, 275, 920 + index * 90, 352), True)
                           for index in range(1, 4)),
        )
        order = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.ORDER_CHANGE}),
            controls=(
                *(Control(f'先发第{index}位', ControlKind.TARGET,
                          (index * 200, 250, index * 200 + 180, 565), True) for index in range(1, 4)),
                *(Control(f'候补第{index}位', ControlKind.TARGET,
                          (600 + index * 200, 250, 780 + index * 200, 565), True) for index in range(1, 4)),
                Control('进行更替', ControlKind.BUTTON, (478, 591, 803, 661), True),
            ),
        )
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        changed = battle.with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (3, 4, 2))})
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, menu, order, order, order, animation, changed, changed, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        self.assertEqual(['御主技能', '御主技能3', '先发第2位', '候补第1位', '进行更替',
                          '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        confirmed = next(event for event in events.events if event.type == 'order_change_confirmed')
        self.assertEqual(((3, 4, 2), (1,)), (confirmed.data['active_slots'], confirmed.data['reserve_slots']))

    def test_consecutive_auto_fills_consume_each_reserve_once(self) -> None:
        base = single_wave_plan()
        plan = replace(base, formation=(
            *base.formation,
            FormationSlotPlan(slot=3, name='候补3', svt_id=4),
            FormationSlotPlan(slot=4, name='候补4', svt_id=5),
        ))
        first = observe(BasePage.BATTLE, button('攻击')).with_fields({
            'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2)),
        })
        second = first.with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (4, 1, 2))})
        device, perception = scripted(
            [first, first, second, second, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 0.2, 0.3, 0.4, 30.4],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        fills = [event.data for event in events.events if event.type == 'auto_fill_confirmed']
        self.assertEqual([(0, 3), (3, 4)], [(item['out_slot'], item['in_slot']) for item in fills])
        self.assertEqual((4, 1, 2), fills[-1]['active_slots'])
        self.assertEqual((), fills[-1]['reserve_slots'])

    def test_empty_slot_auto_fill_is_confirmed_without_joining_specified_order(self) -> None:
        base = single_wave_plan()
        plan = replace(base, formation=(
            FormationSlotPlan(slot=0, name='从者0', svt_id=705000),
            FormationSlotPlan(slot=1, name='从者1', svt_id=2),
            FormationSlotPlan(slot=2, name=''),
            FormationSlotPlan(slot=3, name=''),
        ))
        battle = observe(BasePage.BATTLE, button('攻击')).with_fields({
            'battle_servants': Field(FieldStatus.CONFIRMED, (-1, 1, -1)),
        })
        device, perception = scripted(
            [battle, battle, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 0.2, 30.2],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        fill = next(event for event in events.events if event.type == 'auto_fill_confirmed')
        self.assertEqual((0, 3, 1), (fill.data['out_slot'], fill.data['in_slot'],
                                    fill.data['frontline_position']))
        self.assertEqual((3, 1, 2), fill.data['active_slots'])

    def test_wave_strategy_recomputes_from_its_own_order_and_keeps_auto_fill(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='strategy', cards=()))
        global_strategy = FallbackStrategy(
            servant_order=('从者0', '从者1', '从者2', '候补'),
            servant_order_slots=(0, 1, 2, 3),
        )
        wave_strategy = replace(
            global_strategy,
            servant_order=('从者1', '从者0', '从者2', '候补'),
            servant_order_slots=(1, 0, 2, 3),
        )
        plan = replace(
            base,
            waves=(WavePlan(number=1, target=1, turns=(turn,)),
                   WavePlan(number=2, target=1, turns=(turn,))),
            formation=(*base.formation, FormationSlotPlan(slot=3, name='候补', svt_id=4)),
            strategy=global_strategy,
            strategy_by_wave={2: wave_strategy},
        )
        battle = observe(BasePage.BATTLE, button('攻击')).with_fields({
            'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2)),
        })
        next_wave = battle.with_fields({
            'wave': Field(FieldStatus.CONFIRMED, 2),
            'turn': Field(FieldStatus.CONFIRMED, 2),
        })
        card = observe(BasePage.CARD, *(
            replace(command_card(index, slot), card=replace(command_card(index, slot).card,
                                                           crit_rate=10, affinity='neutral'))
            for index, slot in enumerate((3, 3, 1, 2, 2), start=1)
        ))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, battle, card, animation, next_wave, next_wave, card,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.5, 0.6, 0.7, 0.8, 1.0, 1.1, 1.2, 31.2],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        batches = {}
        for event in events.events:
            if event.type == 'action' and event.data.get('reason') == 'command_card_batch':
                batches.setdefault(event.data['batch_id'], []).append(event.data['servant_slot'])
        self.assertEqual([[3, 3, 1], [1, 3, 3]], list(batches.values()))
        self.assertEqual(1, len([event for event in events.events if event.type == 'auto_fill_confirmed']))

    def test_command_spell_requires_menu_and_confirmation(self) -> None:
        spell = ActionPlan(kind='spell', title='宝具解放', detail='', icon='', skill=0, member_slot=2)
        battle = observe(BasePage.BATTLE, button('攻击'), button('令咒', (1000, 20, 1122, 133)))
        menu = Observation(base_page=BasePage.BATTLE,
                           overlays=frozenset({Overlay.COMMAND_SPELL_MENU}),
                           controls=(button('宝具解放', (246, 296, 1033, 422)),))
        confirm = Observation(base_page=BasePage.BATTLE,
                              overlays=frozenset({Overlay.COMMAND_SPELL_CONFIRM}),
                              controls=(button('决定', (692, 399, 958, 461)),))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, menu, menu, confirm, confirm, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.0, 2.5, 2.6],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((spell,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['令咒', '宝具解放', '决定', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_enemy_target_action_uses_the_planned_enemy(self) -> None:
        target = ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         *(Control(f'敌方{index}', ControlKind.ENEMY,
                                   (index * 250, 20, index * 250 + 200, 100), True)
                           for index in range(1, 4)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([battle, battle, wrong_cards], [0.0, 0.1, 0.2])
        events = MemoryEventSink()

        result = run(single_wave_plan((target,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['敌方2', '攻击'], [event.data['control'] for event in actions])
        self.assertEqual([0.0, 0.1], [event.timestamp for event in actions])
        self.assertEqual(1, result.progress.action_index)

    def test_wave_target_and_matching_planned_target_tap_once(self) -> None:
        target = ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2)
        base = single_wave_plan((target,))
        plan = replace(base, waves=(replace(base.waves[0], target=2),))
        battle = observe(BasePage.BATTLE, button('攻击'),
                         *(Control(f'敌方{index}', ControlKind.ENEMY,
                                   (index * 250, 20, index * 250 + 200, 100), True)
                           for index in range(1, 4)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([battle, battle, battle, battle, wrong_cards],
                                      [0.0, 0.5, 0.6, 1.1, 1.2])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['敌方2', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.data.get('control') == '关闭' for event in events.events
                             if event.type == 'action'))

    def test_distinct_planned_enemy_targets_still_switch(self) -> None:
        targets = tuple(ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=target)
                        for target in (2, 3))
        base = single_wave_plan(targets)
        plan = replace(base, waves=(replace(base.waves[0], target=2),))
        battle = observe(BasePage.BATTLE, button('攻击'),
                         *(Control(f'敌方{index}', ControlKind.ENEMY,
                                   (index * 250, 20, index * 250 + 200, 100), True)
                           for index in range(1, 4)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, battle, battle, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['敌方2', '敌方3', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_wave_target_without_planned_target_action_does_not_tap(self) -> None:
        base = single_wave_plan()
        plan = replace(base, waves=(replace(base.waves[0], target=2),))
        battle = observe(BasePage.BATTLE, button('攻击'),
                         *(Control(f'敌方{index}', ControlKind.ENEMY,
                                   (index * 250, 20, index * 250 + 200, 100), True)
                           for index in range(1, 4)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([battle, battle, wrong_cards], [0.0, 0.5, 0.6])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_enemy_status_detail_closes_before_battle_continues(self) -> None:
        target = ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         *(Control(f'敌方{index}', ControlKind.ENEMY,
                                   (index * 250, 20, index * 250 + 200, 100), True)
                           for index in range(1, 4)))
        detail = Observation(base_page=BasePage.BATTLE,
                             overlays=frozenset({Overlay.BATTLE_STATUS_CHANGE_DETAIL}),
                             controls=(button('关闭', (990, 640, 1010, 660)),))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, detail, detail, battle, battle, wrong_cards],
            [0.0, 0.1, 0.6, 0.7, 1.2, 1.3],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((target,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['敌方2', '关闭', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual((1000, 650), (device.actions[1]['x'], device.actions[1]['y']))
        self.assertEqual(1, result.progress.action_index)

    def test_master_skill_two_uses_the_planned_target(self) -> None:
        master = ActionPlan(kind='master', title='御主二技能', detail='', icon='',
                            skill=1, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'), button('御主技能', (1141, 162, 1245, 250)))
        menu = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'御主技能{index}', ControlKind.BUTTON,
                                   (850 + index * 90, 275, 920 + index * 90, 352), True)
                           for index in range(1, 4)),
        )
        target = Observation(
            base_page=BasePage.BATTLE,
            variants=frozenset({Variant.SKILL_TARGET}),
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'目标从者{index}', ControlKind.TARGET,
                                   (index * 300, 269, index * 300 + 200, 499), True)
                           for index in range(1, 4)),
        )
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, menu, menu, target, target, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.0, 2.5, 2.6],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((master,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, dict(event.data)) for event in events.events])
        self.assertEqual(['御主技能', '御主技能2', '目标从者2', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_master_target_skill_recovers_once_when_panel_does_not_change(self) -> None:
        master = ActionPlan(kind='master', title='御主二技能', detail='', icon='',
                            skill=1, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'), button('御主技能', (1141, 162, 1245, 250)))
        menu = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'御主技能{index}', ControlKind.BUTTON,
                                   (850 + index * 90, 275, 920 + index * 90, 352), True)
                           for index in range(1, 4)),
        )
        device, perception = scripted(
            [battle, menu, menu, menu, menu, menu],
            [0.0, 0.1, 0.2, 2.2, 2.3, 32.3],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((master,)), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('target_selection_timeout', result.reason)
        self.assertEqual(['御主技能', '御主技能2', '御主技能2'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, len([event for event in events.events
                                 if event.type == 'warning' and event.data.get('reason') == 'action_recovery']))

    def test_master_skill_without_target_does_not_tap_a_servant(self) -> None:
        master = ActionPlan(kind='master', title='御主二技能', detail='', icon='', skill=1)
        battle = observe(BasePage.BATTLE, button('攻击'), button('御主技能', (1141, 162, 1245, 250)))
        menu = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'御主技能{index}', ControlKind.BUTTON,
                                   (850 + index * 90, 275, 920 + index * 90, 352), True)
                           for index in range(1, 4)),
        )
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, menu, menu, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.3, 1.8, 1.9],
        )
        events = MemoryEventSink()

        with mock.patch('executor.core.runner.time.sleep', return_value=None) as sleep:
            result = core_run(single_wave_plan((master,)), device, perception, events,
                              options=RunOptions(write_evidence=False))

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['御主技能', '御主技能2', '战斗跳过', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.data.get('control', '').startswith('目标从者')
                             for event in events.events if event.type == 'action'))
        self.assertNotIn(mock.call(1.5), sleep.call_args_list)

    def test_master_skill_target_timeout_stops_without_attacking(self) -> None:
        master = ActionPlan(kind='master', title='御主二技能', detail='', icon='',
                            skill=1, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'), button('御主技能', (1141, 162, 1245, 250)))
        menu = Observation(
            base_page=BasePage.BATTLE,
            overlays=frozenset({Overlay.MASTER_SKILLS}),
            controls=tuple(Control(f'御主技能{index}', ControlKind.BUTTON,
                                   (850 + index * 90, 275, 920 + index * 90, 352), True)
                           for index in range(1, 4)),
        )
        target = Observation(
            base_page=BasePage.BATTLE,
            variants=frozenset({Variant.SKILL_TARGET}),
            overlays=frozenset({Overlay.MASTER_SKILLS}),
        )
        device, perception = scripted(
            [battle, battle, menu, menu, target, target, target],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.3, 31.2],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((master,)), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('target_selection_timeout', result.reason)
        self.assertEqual(1, events.events[-1].data['slot'])
        self.assertIn('目标', events.events[-1].data['message'])
        self.assertEqual(['御主技能', '御主技能2'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_default_run_creates_an_evidence_directory(self) -> None:
        unknown = observe(BasePage.UNKNOWN)
        with tempfile.TemporaryDirectory() as directory:
            device, perception = scripted([unknown, unknown], [0.0, 30.0])
            with mock.patch('executor.core.runner.Path', return_value=Path(directory)):
                result = core_run(single_wave_plan(), device, perception, MemoryEventSink())

            self.assertEqual(RunStatus.STOPPED, result.status)
            self.assertTrue(result.evidence_dir.startswith(directory))
            self.assertTrue((Path(result.evidence_dir) / 'result.json').is_file())

    def test_unknown_stop_writes_frames_observation_events_and_result(self) -> None:
        unknown = observe(BasePage.UNKNOWN)
        with tempfile.TemporaryDirectory() as directory:
            device, perception = scripted([unknown, unknown], [0.0, 30.0])
            events = MemoryEventSink()

            result = run(single_wave_plan(), device, perception, events,
                         options=RunOptions(run_dir=directory))

            root = Path(directory)
            payload = json.loads((root / 'result.json').read_text(encoding='utf-8'))
            observation = json.loads((root / 'observation.json').read_text(encoding='utf-8'))
            rows = [json.loads(line) for line in (root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(directory, result.evidence_dir)
            self.assertEqual('stopped', payload['status'])
            self.assertEqual('unknown_timeout', payload['reason'])
            self.assertTrue((root / 'plan.json').is_file())
            summary = (root / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('运行状态：已停止', summary)
            self.assertIn('未知画面等待超时', summary)
            self.assertEqual('fake', payload['device']['serial'])
            self.assertEqual('default', payload['job']['mystic_id'])
            self.assertEqual('unknown', observation['base_page'])
            self.assertEqual('unknown_timeout', rows[-1]['data']['reason'])
            self.assertTrue((root / 'failure.png').is_file())

    def test_perception_stats_are_saved_in_result(self) -> None:
        unknown = observe(BasePage.UNKNOWN)
        with tempfile.TemporaryDirectory() as directory:
            device, perception = scripted([unknown, unknown], [0.0, 30.0])
            perception.stats = mock.Mock(return_value={
                'field_reads': 2, 'field_cache_hits': 3, 'field_last_ms': 4.5,
                'field_mean_ms': 4.0, 'field_p95_ms': 4.5, 'field_max_ms': 4.5,
            })

            result = run(single_wave_plan(), device, perception, MemoryEventSink(),
                         options=RunOptions(run_dir=directory))

            payload = json.loads((Path(directory) / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(RunStatus.STOPPED, result.status)
            self.assertEqual(2, payload['perception']['field_reads'])
            self.assertEqual(3, payload['perception']['field_cache_hits'])
            self.assertEqual(4.5, payload['perception']['field_last_ms'])

    def test_completed_run_saves_the_final_frame(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        result_page = observe(BasePage.RESULT, button('结算推进', (0, 0, 1280, 720)))
        drops = observe(BasePage.DROPS, button('下一步', (948, 606, 1272, 676)))
        repeat = Observation(base_page=BasePage.BATTLE,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击', (800, 540, 940, 610)),))
        observations = [battle, battle, card, card, card, card, card, card, animation,
                        result_page, result_page, drops, drops, repeat]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0, 3.1, 3.6, 3.7]
        device, perception = scripted(observations, times)
        with tempfile.TemporaryDirectory() as directory:
            result = run(single_wave_plan(), device, perception, MemoryEventSink(),
                         options=RunOptions(run_dir=directory))

            payload = json.loads((Path(directory) / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(RunStatus.COMPLETED, result.status)
            self.assertTrue((Path(directory) / 'final.png').is_file())
            self.assertTrue((Path(directory) / 'card-batch-1-attempt-0.png').is_file())
            self.assertTrue((Path(directory) / 'card-batch-1-attempt-0.json').is_file())
            self.assertEqual(str(Path(directory) / 'final.png'), payload['final_frame'])

    def test_tap_device_error_becomes_a_failed_run_result(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        device, perception = scripted([battle, battle], [0.0, 0.5])
        events = MemoryEventSink()

        with mock.patch.object(device, 'tap', side_effect=DeviceError('tap failed')):
            result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual('device_error', result.reason)
        self.assertEqual('device_error', events.events[-1].data['reason'])

    def test_attack_recovers_once_from_fresh_battle_frame(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD)
        device, perception = scripted(
            [battle, battle, battle, card, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN),
             observe(BasePage.UNKNOWN)],
            [0.0, 0.5, 2.5, 2.6, 2.7, 31.0, 33.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '攻击'], [event.data['control'] for event in events.events
                                           if event.type == 'action'])
        recovery = next(event for event in events.events
                        if event.type == 'warning' and event.data.get('reason') == 'action_recovery')
        self.assertEqual(('攻击', 1, 2.5), (recovery.data['control'], recovery.data['attempt'], recovery.timestamp))

    def test_scrcpy_successful_input_without_screen_response_stops(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        device, perception = scripted(
            [battle, battle, battle, battle], [0.0, 0.5, 2.5, 32.5],
            config=DeviceConfig(serial='replay-scrcpy', region='CN', channel='scrcpy'))
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertEqual(['攻击', '攻击'], [event.data['control'] for event in events.events
                                           if event.type == 'action'])
        self.assertFalse(any(event.type == 'channel_changed' for event in events.events))

    def test_stop_takes_effect_at_the_action_boundary(self) -> None:
        control = RunControl()

        class StopAfterAction(MemoryEventSink):
            def emit(self, event):
                super().emit(event)
                if event.type == 'action':
                    control.stop()

        battle = observe(BasePage.BATTLE, button('攻击'))
        events = StopAfterAction()
        device, perception = scripted([battle, battle, battle, battle], [0.0, 0.5, 0.6, 1.1])

        result = run(single_wave_plan(), device, perception, events, control=control)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('manual_stop', result.reason)
        self.assertEqual(1, len(device.actions))
        self.assertTrue(any(event.type == 'stopped' for event in events.events))

    def test_manual_stop_interrupts_card_batch_before_next_tap(self) -> None:
        control = RunControl()

        class StopAfterFirstCard(MemoryEventSink):
            def emit(self, event):
                super().emit(event)
                if event.type == 'action' and event.data.get('control') == '卡1':
                    control.stop()

        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        device, perception = scripted([battle, card], [0.0, 0.1])
        events = StopAfterFirstCard()

        with mock.patch('executor.core.runner.time.sleep'):
            result = run(single_wave_plan(), device, perception, events, control=control)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('manual_stop', result.reason)
        self.assertEqual(['攻击', '卡1'], [event.data['control'] for event in events.events
                                          if event.type == 'action'])
        self.assertEqual(2, len(device.actions))

    def test_small_control_box_jitter_does_not_restart_stability(self) -> None:
        first = observe(BasePage.BATTLE, button('攻击'))
        moved = observe(BasePage.BATTLE, button('攻击', (1073, 568, 1200, 641)))
        device, perception = scripted(
            [first, moved, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.5, 0.6, 30.6],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击'], [event.data['control'] for event in events.events
                                    if event.type == 'action'])

    def test_attack_waits_for_stable_observation_and_unknown_stops_without_blind_taps(self) -> None:
        observations = [
            observe(BasePage.BATTLE, button('攻击')),
            observe(BasePage.BATTLE, button('攻击')),
            observe(BasePage.BATTLE, button('攻击')),
            observe(BasePage.UNKNOWN),
            observe(BasePage.UNKNOWN),
        ]
        device, perception = scripted(observations, [0.0, 0.25, 0.5, 0.6, 31.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(page_timeout=30.0))

        self.assertEqual([{'action': 'tap', 'x': 1132, 'y': 600,
                           'device_x': 1132, 'device_y': 600}], device.actions)
        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击'], [event.data['control'] for event in events.events
                                    if event.type == 'action'])

    def test_verify_mode_waits_for_required_np_evidence_before_batch(self) -> None:
        np_action = ActionPlan(kind='attack', title='宝具', detail='', icon='', member_slot=0)
        plan = single_wave_plan((np_action,), {0: 2})
        battle = observe(BasePage.BATTLE, button('攻击'))
        normal = [command_card(index, index - 1) for index in range(1, 6)]
        waiting = observe(BasePage.CARD, *normal)
        np_card = Control('宝具卡A', ControlKind.CARD, (330, 100, 510, 330), True,
                          card=CommandCardIdentity(servant_slot=0, card_type='buster', noble_phantasm=True))
        settled = observe(BasePage.CARD, np_card, *normal)
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, waiting, settled, animation, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 0.2, 0.3, 0.4, 30.4],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '宝具卡A', '卡2', '战斗跳过'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, len([event for event in events.events
                                 if event.type == 'warning'
                                 and event.data.get('reason') == 'np_reservation_waiting']))

    def test_np_action_and_position_form_one_card_in_the_requested_slot(self) -> None:
        np_action = ActionPlan(kind='attack', title='宝具', detail='', icon='', member_slot=0)
        battle = observe(BasePage.BATTLE, button('攻击'))
        np_card = Control('宝具卡A', ControlKind.CARD, (330, 100, 510, 330), True,
                          card=CommandCardIdentity(servant_slot=0, card_type='buster', noble_phantasm=True))
        card = observe(BasePage.CARD, np_card,
                       *(command_card(index, index - 1) for index in range(1, 6)))
        observations = [battle, battle, card, card, card, card, card, card,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        device, perception = scripted(observations, [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4])
        events = MemoryEventSink()

        result = run(single_wave_plan((np_action,), {0: 2}), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual(['攻击', '卡1', '宝具卡A', '卡2'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_strategy_fills_cards_around_scheduled_np(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=None, np_positions={0: 2})
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),),
                       strategy=FallbackStrategy(strategy_order=('cards', 'crit')))
        battle = observe(BasePage.BATTLE, button('攻击'))
        np_card = Control('宝具卡A', ControlKind.CARD, (330, 100, 510, 330), True,
                          card=CommandCardIdentity(servant_slot=0, card_type='buster', noble_phantasm=True,
                                                   crit_rate=0, affinity='neutral'))
        normal = [replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
                  for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))]
        card = observe(BasePage.CARD, np_card, *normal)
        observations = [battle, battle, card, card, card, card, card, card,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        device, perception = scripted(observations, [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '宝具卡A', '卡2'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_unidentified_np_regions_use_fixed_screen_positions_for_reservation(self) -> None:
        base = single_wave_plan()
        formation = (
            FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True),
            FormationSlotPlan(slot=1, name='娄希', svt_id=705000),
            FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100),
        )
        turn = replace(base.waves[0].turns[0], attack=None, np_positions={1: 1})
        plan = replace(base, formation=formation,
                       waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        normal = [
            replace(command_card(index, slot), card=CommandCardIdentity(slot, card_type, svt_id=svt_id))
            for index, (slot, card_type, svt_id) in enumerate((
                (1, 'buster', 705000), (2, 'buster', 2800100), (1, 'buster', 705000),
                (2, 'quick', 2800100), (1, 'quick', 705000),
            ), start=1)
        ]
        np_cards = (
            Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A'),
            Control('宝具卡2', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:B'),
        )
        card = observe(BasePage.CARD, *normal, *np_cards)
        observations = [battle, battle, card, card, card, card, card, card,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        device, perception = scripted(observations, [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        self.assertEqual(['攻击', '宝具卡2', '卡1', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.type == 'failed' and event.data.get('reason') == 'fallback_cards_unavailable'
                             for event in events.events[:-2]))

    def test_np_reservation_waits_for_transient_effect_to_settle(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=None, np_positions={1: 1})
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        normal = [command_card(index, (index - 1) % 3) for index in range(1, 6)]
        effect = observe(BasePage.CARD, *normal)
        settled = observe(
            BasePage.CARD, *normal,
            Control('宝具卡1', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:B'))
        observations = [battle, battle, effect, settled, settled, settled, settled, settled, settled,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.5, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.4, 2.5, 32.5]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        actions = [event.data for event in events.events if event.type == 'action']
        self.assertEqual('宝具卡1', actions[1]['control'])
        self.assertEqual(635, actions[1]['x'])
        waiting = [event for event in events.events
                   if event.type == 'warning' and event.data.get('reason') == 'np_reservation_waiting']
        self.assertEqual(1, len(waiting))

    def test_transient_second_np_slot_never_gets_clicked(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=None, np_positions={1: 2})
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        normal = [command_card(index, (index - 1) % 3) for index in range(1, 6)]
        first_np = Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A')
        false_second_np = Control('宝具卡2', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:B')
        effect = observe(BasePage.CARD, *normal, first_np, false_second_np)
        settled = observe(BasePage.CARD, *normal, first_np)
        observations = [battle, battle, effect, settled, settled, settled, settled, settled,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.5, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.4, 32.5]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        actions = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertFalse(any(control.startswith('宝具卡') for control in actions))

    def test_card_batch_waits_for_complete_five_card_hand(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=None, np_positions={1: 1})
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        normal = [command_card(index, (index - 1) % 3) for index in range(1, 6)]
        np_region = Control('宝具卡1', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:B')
        settled = observe(BasePage.CARD, *normal, np_region)
        transient = observe(BasePage.CARD, *(replace(item, card=None) for item in normal), np_region)
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle, transient, settled, animation,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 30.9]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        actions = [event.data for event in events.events if event.type == 'action']
        self.assertEqual(['攻击', '宝具卡1', '卡1', '卡4', '战斗跳过'],
                         [item['control'] for item in actions])
        waiting = [event for event in events.events
                   if event.type == 'warning' and event.data.get('reason') == 'card_batch_waiting']
        self.assertEqual(1, len(waiting))

    def test_unconfirmable_five_card_hand_stops_without_batch_taps(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=None, np_positions={1: 1})
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        normal = [command_card(index, (index - 1) % 3) for index in range(1, 6)]
        np_region = Control('宝具卡1', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:B')
        transient = observe(BasePage.CARD, *(replace(item, card=None) for item in normal), np_region)
        observations = [battle, battle, transient, transient]
        times = [0.0, 0.5, 0.6, 30.6]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('fallback_cards_unavailable', result.reason)
        self.assertEqual('五张普通指令卡尚未完整确认', events.events[-1].data['message'])
        self.assertGreaterEqual(events.events[-1].timestamp, 30.6)
        self.assertEqual(['攻击'], [event.data['control'] for event in events.events
                                    if event.type == 'action'])
        waiting = [event for event in events.events
                   if event.type == 'warning' and event.data.get('reason') == 'card_batch_waiting']
        self.assertEqual(1, len(waiting))

    def test_ambiguous_np_regions_stop_with_fixed_position_diagnostic(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=None, np_positions={1: 1})
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        normal = [command_card(index, (index - 1) % 3) for index in range(1, 6)]
        duplicate_regions = (
            Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A'),
            Control('宝具卡2', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:B'),
        )
        card = observe(BasePage.CARD, *normal, *duplicate_regions)
        device, perception = scripted([battle, battle, card, card, card], [0.0, 0.5, 0.6, 1.1, 31.1])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('fallback_cards_unavailable', result.reason)
        self.assertEqual(['攻击'], [event.data['control'] for event in events.events
                                    if event.type == 'action'])
        diagnostic = events.events[-1].data.get('message', '')
        self.assertIn('宝具卡区域重复', diagnostic)
        self.assertIn('固定位置', diagnostic)

    def test_isolated_np_regions_map_by_geometry_not_visible_card_order(self) -> None:
        for servant_slot, box, expected_x in (
            (1, (545, 85, 725, 315), 635),
            (2, (765, 100, 945, 330), 855),
        ):
            with self.subTest(servant_slot=servant_slot):
                base = single_wave_plan()
                turn = replace(base.waves[0].turns[0], attack=None, np_positions={servant_slot: 1})
                plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
                battle = observe(BasePage.BATTLE, button('攻击'))
                normal = [command_card(index, (index - 1) % 3) for index in range(1, 6)]
                np_region = Control('宝具卡1', ControlKind.CARD, box, True, 'np_slot:A')
                card = observe(BasePage.CARD, *normal, np_region)
                observations = [battle, battle, card, card, card, card, card, card,
                                observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
                device, perception = scripted(
                    observations, [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4])
                events = MemoryEventSink()

                result = run(plan, device, perception, events)

                actions = [event.data for event in events.events if event.type == 'action']
                self.assertEqual('unknown_timeout', result.reason,
                                 [(event.type, event.data) for event in events.events])
                self.assertEqual('宝具卡1', actions[1]['control'])
                self.assertEqual(expected_x, actions[1]['x'])

    def test_command_card_batch_uses_one_hand_and_350ms_between_taps(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, card, animation, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 0.7, 0.8, 30.8],
        )
        events = MemoryEventSink()

        with mock.patch('executor.core.runner.time.sleep') as sleep:
            result = core_run(single_wave_plan(), device, perception, events,
                              options=RunOptions(write_evidence=False))

        self.assertEqual('unknown_timeout', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '战斗跳过'],
                         [event.data['control'] for event in actions])
        self.assertEqual(2, sum(call.args == (0.35,) for call in sleep.call_args_list))
        card_actions = [event.data for event in actions if event.data['control'].startswith('卡')]
        self.assertEqual([1, 2, 3], [item['card_position'] for item in card_actions])
        self.assertEqual([(0, 'arts', None), (1, 'arts', None), (2, 'arts', None)],
                         [(item['servant_slot'], item['card_type'], item['deck_index'])
                          for item in card_actions])
        self.assertTrue(all('affinity' in item for item in card_actions))
        self.assertEqual(1, len({item['batch_id'] for item in card_actions}))
        self.assertEqual([0], list({item['batch_attempt'] for item in card_actions}))
        self.assertGreaterEqual(card_actions[0]['attack_to_first_card_ms'], 0)
        self.assertTrue(all(item['interval_ms'] >= 0 for item in card_actions[1:]))
        submitted = next(event for event in events.events if event.type == 'card_batch_submitted')
        self.assertEqual(350.0, submitted.data['configured_interval_ms'])

    def test_adb_and_scrcpy_share_the_same_action_and_card_batch_case(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, card, animation, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.1, 0.7, 0.8, 30.8]
        contracts = []

        for channel in ('adb', 'scrcpy'):
            with self.subTest(channel=channel):
                device, perception = scripted(
                    observations, times,
                    config=DeviceConfig(serial=f'replay-{channel}', region='CN', channel=channel))
                events = MemoryEventSink()
                result = run(single_wave_plan(), device, perception, events)
                actions = [event.data for event in events.events if event.type == 'action']
                contract = [(item['control'], item['x'], item['y'], item['reason'],
                             item.get('card_position'), item.get('batch_attempt')) for item in actions]
                self.assertEqual('unknown_timeout', result.reason)
                self.assertEqual(channel, device.config.channel.value)
                contracts.append(contract)

        self.assertEqual(contracts[0], contracts[1])
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '战斗跳过'],
                         [item[0] for item in contracts[0]])

    def test_card_batch_recovers_only_remaining_cards_after_first_card_took_effect(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        initial = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        remaining = observe(BasePage.CARD, *(
            replace(command_card(index, index - 1),
                    box=(command_card(index, index - 1).box[0] + 10,
                         command_card(index, index - 1).box[1],
                         command_card(index, index - 1).box[2] + 10,
                         command_card(index, index - 1).box[3]))
            for index in range(2, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, initial, initial, remaining, animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 1.0, 2.9, 3.0, 3.1, 33.2],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        actions = [event.data for event in events.events if event.type == 'action']
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '卡2', '卡3', '战斗跳过'],
                         [item['control'] for item in actions])
        recovered = [item for item in actions if item.get('batch_attempt') == 1]
        self.assertEqual([2, 3], [item['card_position'] for item in recovered])
        self.assertEqual(['卡2', '卡3'], [item['control'] for item in recovered])
        self.assertEqual([initial.controls[1].center[0] + 10, initial.controls[2].center[0] + 10],
                         [item['x'] for item in recovered])

    def test_card_batch_recovers_only_last_card_after_two_cards_took_effect(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        initial = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        remaining = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(3, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, initial, initial, remaining, animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 1.0, 2.9, 3.0, 3.1, 33.2],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        recovered = [event.data for event in events.events if event.type == 'action'
                     and event.data.get('batch_attempt') == 1]
        self.assertEqual(['卡3'], [item['control'] for item in recovered])
        self.assertEqual([3], [item['card_position'] for item in recovered])

    def test_card_batch_partial_recovery_failure_does_not_repeat_the_batch(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        initial = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        remaining = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(2, 6)))
        device, perception = scripted(
            [battle, initial, initial, remaining, remaining, remaining],
            [0.0, 0.1, 2.2, 2.9, 3.3, 5.7],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, events.events[-1].data['recovery_attempts'])

    def test_card_batch_recovers_once_from_a_fresh_recomputed_hand(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        initial = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        changed = observe(BasePage.CARD, *(command_card(index, slot)
                                           for index, slot in enumerate((2, 0, 1, 0, 1), start=1)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, initial, initial, changed, animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 1.0, 2.9, 3.0, 3.1, 33.2],
        )
        events = MemoryEventSink()

        with mock.patch('executor.core.runner.time.sleep'):
            result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '卡2', '卡3', '卡1', '战斗跳过'],
                         [event.data['control'] for event in actions])
        recovered = [event.data for event in actions if event.data.get('batch_attempt') == 1]
        self.assertEqual(['卡2', '卡3', '卡1'], [item['control'] for item in recovered])
        self.assertEqual(1, len({item['batch_id'] for item in recovered}))
        recoveries = [event for event in events.events
                      if event.type == 'warning' and event.data.get('reason') == 'action_recovery']
        self.assertEqual(1, len(recoveries))
        self.assertEqual('command_card_batch', recoveries[0].data['control'])

    def test_card_batch_recovery_requires_a_new_frame(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        changed = observe(BasePage.CARD, *(command_card(index, slot)
                                           for index, slot in enumerate((2, 0, 1, 0, 1), start=1)))
        changed = replace(changed, frame_hash='fresh-card')
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, card, card, changed, animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 2.9, 3.0, 3.5, 3.6, 33.7], preserve_hashes=True,
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '卡2', '卡3', '卡1', '战斗跳过'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        recovery = next(event for event in events.events
                        if event.type == 'warning' and event.data.get('reason') == 'action_recovery')
        self.assertEqual(3.0, recovery.timestamp)

    def test_card_batch_stops_after_one_unconfirmed_recovery(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        device, perception = scripted(
            [battle, card, card, card, card],
            [0.0, 0.1, 2.2, 2.9, 5.7],
        )
        events = MemoryEventSink()

        with mock.patch('executor.core.runner.time.sleep'):
            result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, events.events[-1].data['recovery_attempts'])
        self.assertEqual(1, len([event for event in events.events
                                 if event.type == 'warning'
                                 and event.data.get('reason') == 'action_recovery']))

    def test_attack_animation_confirmation_waits_past_thirty_seconds(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        next_battle = battle.with_fields({'turn': Field(FieldStatus.CONFIRMED, 2)})
        unknown = observe(BasePage.UNKNOWN)
        device, perception = scripted(
            [battle, card, card, animation, animation, animation, next_battle, unknown, unknown],
            [0.0, 0.1, 0.2, 35.0, 35.1, 91.0, 91.5, 122.0, 122.1],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertNotEqual('action_confirmation_timeout', result.reason)
        self.assertTrue(any(event.type == 'turn_started' and event.data.get('turn') == 2
                            for event in events.events))

    def test_card_recovery_wait_still_confirms_attack_end_before_next_turn(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        obscured = observe(BasePage.CARD, *(replace(item, card=None) for item in card.controls))
        next_battle = battle.with_fields({'turn': Field(FieldStatus.CONFIRMED, 2)})
        wrong = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, card, obscured, next_battle, wrong, wrong, wrong],
            [0.0, 0.1, 2.6, 2.7, 2.8, 32.8, 62.8],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('fallback_cards_unavailable', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(2, result.progress.turn)
        confirmed_index = next(index for index, event in enumerate(events.events)
                               if event.type == 'action_confirmed' and event.data.get('stage') == 'attack_end')
        second_attack_index = next(index for index, event in enumerate(events.events)
                                   if index > confirmed_index and event.type == 'action'
                                   and event.data.get('control') == '攻击')
        self.assertGreater(second_attack_index, confirmed_index)

    def test_third_command_card_skips_attack_animation_at_most_once_per_second(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, card, card, card, card, card, card,
                        animation, animation, animation,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.0, 2.9, 3.0, 33.0]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '战斗跳过', '战斗跳过'],
                         [event.data['control'] for event in actions])
        self.assertEqual([1.9, 2.9], [event.timestamp for event in actions
                                      if event.data['reason'] == 'attack_animation_skip'])
        self.assertEqual([(1240, 420), (1240, 420)],
                         [(event.data['x'], event.data['y']) for event in actions
                          if event.data['reason'] == 'attack_animation_skip'])
        self.assertEqual(6, len(device.actions))

    def test_attack_skip_stops_when_battle_controls_are_ready(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        ready_animation = observe(BasePage.BATTLE, button('攻击'), button('战斗菜单'))
        ready_animation = replace(ready_animation, variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, card, card, card, card, card, card, ready_animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.0, 32.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_card_page_remaining_after_third_card_never_skips(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        observations = [battle, card, card, card, card, card, card, card, card, card]
        times = [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.9, 31.9]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('action_confirmation_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(7, len(device.actions))
        self.assertEqual(1, events.events[-1].data['recovery_attempts'])

    def test_unexpected_popup_after_third_card_stops_without_skip(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        popup = Observation(base_page=BasePage.BATTLE,
                            overlays=frozenset({Overlay.STORY_SKIP_CONFIRM}))
        observations = [battle, card, card, card, card, card, card, popup]
        times = [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('unexpected_overlay', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(4, len(device.actions))

    def test_untargeted_skill_waits_for_battle_menu_recovery(self) -> None:
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
        without_menu = replace(battle, controls=tuple(
            control for control in battle.controls if control.name != '战斗菜单'))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [without_menu, animation, without_menu, battle, battle, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['从者1技能1', '战斗跳过', '攻击'],
                         [event.data['control'] for event in actions])
        self.assertEqual(0.4, actions[-1].timestamp)

    def test_last_run_finishes_at_repeat_dialog_without_an_extra_tap(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        result_page = observe(BasePage.RESULT, button('结算推进', (0, 0, 1280, 720)))
        drops = observe(BasePage.DROPS, button('下一步', (948, 606, 1272, 676)))
        repeat = Observation(base_page=BasePage.BATTLE,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击', (800, 540, 940, 610)),))
        observations = [battle, battle, card, card, card, card, card, card, animation,
                        result_page, result_page, drops, drops, repeat]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0, 3.1, 3.6, 3.7]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual(1, result.progress.completed_runs)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '战斗跳过', '结算推进', '下一步'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertNotIn('连续出击', [event.data['control'] for event in events.events
                                      if event.type == 'action'])

    def test_next_wave_without_target_action_attacks_without_enemy_tap(self) -> None:
        base = single_wave_plan()
        first_wave = base.waves[0]
        second_wave = WavePlan(number=2, target=2, turns=first_wave.turns)
        plan = replace(base, waves=(first_wave, second_wave))
        battle = observe(BasePage.BATTLE, button('攻击'))
        enemies = tuple(Control(f'敌方{index}', ControlKind.ENEMY,
                                (index * 250, 20, index * 250 + 200, 100), True)
                        for index in range(1, 4))
        second_battle = observe(BasePage.BATTLE, button('攻击'), *enemies).with_fields({
            'wave': Field(FieldStatus.CONFIRMED, 2),
            'turn': Field(FieldStatus.CONFIRMED, 2),
            'turn_boundary': Field(FieldStatus.CONFIRMED, True),
        })
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle, card, card, card, card, card, card, animation,
                        second_battle, second_battle, wrong_cards]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0, 3.1]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(2, result.progress.wave)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '战斗跳过', '攻击'], controls)
        self.assertTrue(any(event.type == 'wave_started' and event.data['wave'] == 2
                            for event in events.events))

    def test_next_turn_advances_only_from_confirmed_fields(self) -> None:
        base = single_wave_plan()
        first_turn = base.waves[0].turns[0]
        plan = replace(base, waves=(replace(base.waves[0], turns=(first_turn, first_turn)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        next_battle = battle.with_fields({
            'wave': Field(FieldStatus.CONFIRMED, 1),
            'turn': Field(FieldStatus.CONFIRMED, 2),
        })
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle, card, card, card, card, card, card, animation,
                        next_battle, next_battle, wrong_cards]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0, 3.1]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(2, result.progress.turn)
        self.assertEqual(2, [event.data['control'] for event in events.events
                             if event.type == 'action'].count('攻击'))
        self.assertTrue(any(event.type == 'turn_started' and event.data['turn'] == 2
                            for event in events.events))

    def test_unplanned_turn_continues_with_fallback_strategy(self) -> None:
        base = single_wave_plan()
        plan = replace(base, strategy=FallbackStrategy(strategy_order=('cards', 'crit')))
        battle = observe(BasePage.BATTLE, button('攻击'))
        next_battle = battle.with_fields({
            'wave': Field(FieldStatus.CONFIRMED, 1),
            'turn': Field(FieldStatus.CONFIRMED, 2),
        })
        card = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle, card, card, card, card, card, card, animation,
                        next_battle, next_battle, card, card, card, card, card, card,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5,
                 3.0, 3.1, 3.6, 3.7, 4.2, 4.3, 4.8, 4.9, 34.9]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(2, result.progress.turn)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '战斗跳过', '攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_cumulative_counter_preserves_one_planned_turn_per_wave(self) -> None:
        base = single_wave_plan()
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        turn = TurnPlan(actions=(skill,), np_positions={0: 1}, attack=None)
        plan = replace(
            base,
            waves=tuple(WavePlan(number=wave, target=2, turns=(turn,)) for wave in range(1, 4)),
            strategy=FallbackStrategy(strategy_order=('cards', 'crit')),
        )
        controls = (
            button('攻击'),
            button('从者1技能1'),
            Control('敌方2', ControlKind.ENEMY, (300, 20, 500, 100), True),
        )
        battles = {
            (wave, cumulative): observe(BasePage.BATTLE, *controls).with_fields({
                'wave': Field(FieldStatus.CONFIRMED, wave),
                'turn': Field(FieldStatus.CONFIRMED, cumulative),
            })
            for wave, cumulative in ((1, 1), (2, 2), (2, 3), (3, 4))
        }
        card = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))),
            Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A'))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations: list[Observation] = []
        append_planned_turn_frames(observations, battles[1, 1], card, animation)
        append_planned_turn_frames(observations, battles[2, 2], card, animation)
        observations.extend([battles[2, 3], battles[2, 3], card, animation])
        append_planned_turn_frames(observations, battles[3, 4], card, animation)
        times = [index * 0.5 for index in range(len(observations))]
        observations.extend([observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)])
        times.extend([len(times) * 0.5, len(times) * 0.5 + 30.0])
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        actions = [event.data for event in events.events if event.type == 'action']
        self.assertEqual([(1, 1), (2, 1), (3, 1)],
                         [(item['wave'], item['turn']) for item in actions
                          if item['control'] == '从者1技能1'])
        self.assertEqual(3, sum(item['control'] == '宝具卡1' for item in actions))
        self.assertEqual(0, sum(item['control'] == '敌方2' for item in actions))
        self.assertEqual(4, sum(item['control'] == '攻击' for item in actions))
        self.assertEqual((3, 1), (result.progress.wave, result.progress.turn))

    def test_cumulative_counter_maps_two_planned_turns_in_each_wave(self) -> None:
        base = single_wave_plan()
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        turn = TurnPlan(actions=(skill,), np_positions={0: 1}, attack=None)
        plan = replace(
            base,
            waves=tuple(WavePlan(number=wave, target=2, turns=(turn, turn)) for wave in range(1, 3)),
            strategy=FallbackStrategy(strategy_order=('cards', 'crit')),
        )
        controls = (
            button('攻击'),
            button('从者1技能1'),
            Control('敌方2', ControlKind.ENEMY, (300, 20, 500, 100), True),
        )
        battles = {
            (wave, cumulative): observe(BasePage.BATTLE, *controls).with_fields({
                'wave': Field(FieldStatus.CONFIRMED, wave),
                'turn': Field(FieldStatus.CONFIRMED, cumulative),
            })
            for wave, cumulative in ((1, 1), (1, 2), (2, 3), (2, 4))
        }
        card = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))),
            Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A'))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations: list[Observation] = []
        append_planned_turn_frames(observations, battles[1, 1], card, animation)
        append_planned_turn_frames(observations, battles[1, 2], card, animation)
        append_planned_turn_frames(observations, battles[2, 3], card, animation)
        append_planned_turn_frames(observations, battles[2, 4], card, animation)
        times = [index * 0.5 for index in range(len(observations))]
        observations.extend([observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)])
        times.extend([len(times) * 0.5, len(times) * 0.5 + 30.0])
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        actions = [event.data for event in events.events if event.type == 'action']
        self.assertEqual([(1, 1), (1, 2), (2, 1), (2, 2)],
                         [(item['wave'], item['turn']) for item in actions
                          if item['control'] == '从者1技能1'])
        self.assertEqual(4, sum(item['control'] == '宝具卡1' for item in actions))
        self.assertEqual(0, sum(item['control'] == '敌方2' for item in actions))
        self.assertEqual((2, 2), (result.progress.wave, result.progress.turn))

    def test_forward_wave_cumulative_counter_starts_planned_turn_one(self) -> None:
        base = single_wave_plan()
        first_wave = base.waves[0]
        second_wave = WavePlan(number=2, target=1, turns=first_wave.turns)
        plan = replace(base, waves=(first_wave, second_wave),
                       strategy=FallbackStrategy(strategy_order=('cards', 'crit')))
        battle = observe(BasePage.BATTLE, button('攻击'))
        next_battle = battle.with_fields({
            'wave': Field(FieldStatus.CONFIRMED, 2),
            'turn': Field(FieldStatus.CONFIRMED, 2),
        })
        card = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle, card, card, card, card, card, card, animation,
                        next_battle, next_battle, card, card, card, card, card, card,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5,
                 3.0, 3.1, 3.6, 3.7, 4.2, 4.3, 4.8, 4.9, 34.9]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        self.assertEqual((2, 1), (result.progress.wave, result.progress.turn))
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '战斗跳过', '攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertTrue(any(event.type == 'wave_started' and event.data['wave'] == 2
                            and event.data['turn'] == 1 for event in events.events))
        self.assertTrue(any(event.type == 'turn_started' and event.data['wave'] == 2
                            and event.data['turn'] == 1 for event in events.events))

    def test_repeat_can_continue_through_support_selection(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击'),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        support = observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                  support=candidate))
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [repeat, repeat, support, support, team, team, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events, options=RunOptions(run_count=2))

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['连续出击', '职阶筛选:berserker', '助战卡片第1行', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_repeat_support_can_go_directly_to_battle(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击'),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        support = observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                  support=candidate))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [repeat, repeat, support, support, battle, battle, wrong_cards, battle],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 31.2],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events, options=RunOptions(run_count=2))

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, event.data) for event in events.events])
        self.assertEqual(['连续出击', '职阶筛选:berserker', '助战卡片第1行', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.type == 'failed' and event.data.get('reason') == 'action_confirmation_timeout'
                             for event in events.events))

    def test_two_runs_tap_repeat_once_and_count_each_dialog_once(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        team = observe(BasePage.TEAM, button('开始任务', (1086, 635, 1248, 707)))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        result_page = observe(BasePage.RESULT, button('结算推进', (0, 0, 1280, 720)))
        drops = observe(BasePage.DROPS, button('下一步', (948, 606, 1272, 676)))
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击', (800, 540, 940, 610)),))
        observations = [
            battle, battle, card, card, card, card, card, card, animation,
            result_page, result_page, drops, drops, repeat, repeat,
            team, team, battle, battle, card, card, card, card, card, card, animation,
            result_page, result_page, drops, drops, repeat,
        ]
        times = [
            0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4,
            2.5, 3.0, 3.1, 3.6, 3.7, 4.2,
            4.3, 4.8, 4.9, 5.4, 5.5, 6.0, 6.1, 6.6, 6.7, 7.2, 7.3,
            7.4, 7.9, 8.0, 8.5, 8.6,
        ]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(run_count=2))

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual(2, result.progress.completed_runs)
        self.assertEqual(1, controls.count('连续出击'))
        self.assertEqual(1, controls.count('开始任务'), controls)
        self.assertEqual(2, controls.count('攻击'), controls)

    def test_repeat_does_not_recheck_formation(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        team = observe(BasePage.TEAM, button('开始任务', (1086, 635, 1248, 707)))
        changed_team = team.with_fields({
            'team_servants': Field(FieldStatus.CONFIRMED, (999, 999, 999)),
        })
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        result_page = observe(BasePage.RESULT, button('结算推进', (0, 0, 1280, 720)))
        drops = observe(BasePage.DROPS, button('下一步', (948, 606, 1272, 676)))
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击', (800, 540, 940, 610)),))
        observations = [
            team, team, battle, battle, card, card, card, card, card, card, animation,
            result_page, result_page, drops, drops, repeat, repeat,
            changed_team, changed_team, battle, battle, card, card, card, card, card, card, animation,
            result_page, result_page, drops, drops, repeat,
        ]
        device, perception = scripted(observations, [index * 0.5 for index in range(len(observations))])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(run_count=2))

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual(2, result.progress.completed_runs)
        self.assertEqual(2, controls.count('开始任务'), controls)
        self.assertEqual(1, controls.count('连续出击'))

    def test_next_battle_restores_initial_frontline_and_strategy_order(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='strategy', cards=()))
        strategy = FallbackStrategy(
            servant_order=('从者0', '从者1', '从者2', '候补'),
            servant_order_slots=(0, 1, 2, 3),
        )
        plan = replace(
            base,
            waves=(replace(base.waves[0], turns=(turn,)),),
            formation=(*base.formation, FormationSlotPlan(slot=3, name='候补', svt_id=4)),
            strategy=strategy,
        )
        filled = observe(BasePage.BATTLE, button('攻击')).with_fields({
            'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2)),
        })
        initial = observe(BasePage.BATTLE, button('攻击')).with_fields({
            'battle_servants': Field(FieldStatus.CONFIRMED, (0, 1, 2)),
        })
        team = observe(BasePage.TEAM, button('开始任务', (1086, 635, 1248, 707)))

        def cards(slots) -> Observation:
            return observe(BasePage.CARD, *(
                replace(command_card(index, slot), card=replace(command_card(index, slot).card,
                                                               crit_rate=10, affinity='neutral'))
                for index, slot in enumerate(slots, start=1)
            ))

        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        result_page = observe(BasePage.RESULT, button('结算推进', (0, 0, 1280, 720)))
        drops = observe(BasePage.DROPS, button('下一步', (948, 606, 1272, 676)))
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击', (800, 540, 940, 610)),))
        observations = [
            filled, filled, cards((3, 3, 1, 2, 2)), animation,
            result_page, result_page, drops, drops, repeat, repeat, team, team,
            initial, initial, cards((0, 0, 1, 2, 2)), animation,
            result_page, result_page, drops, drops, repeat,
        ]
        times = [index * 0.5 for index in range(len(observations))]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events, options=RunOptions(run_count=2))

        self.assertEqual(RunStatus.COMPLETED, result.status,
                         [(event.type, dict(event.data)) for event in events.events])
        batches = {}
        for event in events.events:
            if event.type == 'action' and event.data.get('reason') == 'command_card_batch':
                batches.setdefault(event.data['batch_id'], []).append(event.data['servant_slot'])
        self.assertEqual([[3, 3, 1], [0, 0, 1]], list(batches.values()))
        self.assertEqual(1, len([event for event in events.events if event.type == 'auto_fill_confirmed']))

    def test_prefer_mode_uses_strategy_when_cards_do_not_match(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0],
                       attack=replace(base.waves[0].turns[0].attack, mode='prefer'))
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),),
                       strategy=FallbackStrategy(strategy_order=('cards', 'crit'), cards='buster'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        cards = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, card_type='buster', crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))))
        observations = [battle, battle, cards, cards, cards, cards, cards, cards,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        device, perception = scripted(observations, [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_battle_start_without_turn_boundary_evidence_stops(self) -> None:
        battle = Observation(base_page=BasePage.BATTLE, controls=(button('攻击'),))
        device, perception = scripted([battle], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('resume_uncertain', result.reason)
        self.assertEqual([], device.actions)

    def test_battle_overall_timeout_stops_without_more_input(self) -> None:
        battle = observe(BasePage.BATTLE)
        device, perception = scripted([battle, battle], [0.0, 10.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(battle_timeout=10.0))

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('battle_timeout', result.reason)
        self.assertEqual([], device.actions)

    def test_loading_waits_without_clicking_then_resumes(self) -> None:
        loading = observe(BasePage.LOADING)
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([loading, loading, battle, battle, wrong_cards],
                                      [0.0, 1.0, 1.1, 1.6, 1.7])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['攻击'], [event.data['control'] for event in events.events
                                    if event.type == 'action'])

    def test_unexpected_popup_stops_instead_of_experimental_clicking(self) -> None:
        popup = Observation(base_page=BasePage.BATTLE,
                            variants=frozenset(),
                            overlays=frozenset({Overlay.STORY_SKIP_CONFIRM}),
                            fields={
                                'wave': Field(FieldStatus.CONFIRMED, 1),
                                'turn': Field(FieldStatus.CONFIRMED, 1),
                                'turn_boundary': Field(FieldStatus.CONFIRMED, True),
                            },
                            controls=(button('是'), button('攻击')))
        device, perception = scripted([popup, popup], [0.0, 0.5])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('unexpected_overlay', result.reason)
        self.assertEqual([], device.actions)

    def test_repeat_dialog_on_support_page_is_unexpected_even_after_drops(self) -> None:
        drops = observe(BasePage.DROPS)
        popup = Observation(base_page=BasePage.SUPPORT,
                            overlays=frozenset({Overlay.REPEAT_DIALOG}),
                            controls=(button('连续出击'),))
        device, perception = scripted([drops, popup], [0.0, 0.1])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unexpected_overlay', result.reason)
        self.assertEqual([], device.actions)

    def test_persistent_repeat_dialog_cannot_be_tapped_twice(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        drops = observe(BasePage.DROPS)
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击'),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        support = observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                  support=candidate))
        device, perception = scripted([drops, repeat, repeat, repeat, support, support],
                                      [0.0, 0.1, 0.6, 1.1, 1.2, 1.7])
        events = MemoryEventSink()

        result = run(plan, device, perception, events, options=RunOptions(run_count=2))

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual(1, controls.count('连续出击'))
        self.assertEqual('device_error', result.reason)

    def test_repeat_dialog_before_any_result_is_unexpected(self) -> None:
        popup = observe(BasePage.BATTLE, button('连续出击'))
        popup = replace(popup, overlays=frozenset({Overlay.REPEAT_DIALOG}))
        device, perception = scripted([popup], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unexpected_overlay', result.reason)
        self.assertEqual([], device.actions)

    def test_unsolicited_master_menu_stops_as_unexpected_overlay(self) -> None:
        popup = observe(BasePage.BATTLE, button('攻击'))
        popup = replace(popup, overlays=frozenset({Overlay.MASTER_SKILLS}))
        device, perception = scripted([popup], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('unexpected_overlay', result.reason)
        self.assertEqual([], device.actions)

    def test_incidental_overlay_without_recovery_control_times_out(self) -> None:
        popup = Observation(base_page=BasePage.SUPPORT,
                            overlays=frozenset({Overlay.FRIEND_REQUEST}))
        device, perception = scripted([popup, popup], [0.0, 30.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual('overlay_recovery_unavailable', result.reason)
        self.assertEqual([], device.actions)

    def test_manual_stop_event_carries_evidence_location(self) -> None:
        control = RunControl()
        control.stop()
        battle = observe(BasePage.BATTLE, button('攻击'))
        with tempfile.TemporaryDirectory() as directory:
            device, perception = scripted([battle], [0.0])
            events = MemoryEventSink()
            result = run(single_wave_plan(), device, perception, events,
                         options=RunOptions(run_dir=directory), control=control)
            rows = [json.loads(line) for line in (Path(directory) / 'events.jsonl').read_text(
                encoding='utf-8').splitlines()]

        self.assertEqual('manual_stop', result.reason)
        self.assertEqual('stopped', rows[-1]['type'])
        self.assertEqual(directory, rows[-1]['data']['evidence_dir'])

    def test_default_policy_uses_visible_bronze_and_emits_cumulative_count(self) -> None:
        recovery = observe(BasePage.AP_RECOVERY,
                           Control('青铜果实', ControlKind.ITEM, (100, 100, 300, 200), True))
        team = observe(BasePage.TEAM)
        device, perception = scripted([recovery, recovery, team], [0.0, 0.5, 0.6])
        events = MemoryEventSink()

        run(single_wave_plan(), device, perception, events)

        self.assertEqual(['tap'], [action['action'] for action in device.actions])
        used = [event for event in events.events if event.type == 'recovery_used']
        self.assertEqual(1, len(used))
        self.assertEqual({'item': 'bronze', 'count': 1}, used[0].data)

    def test_whitelisted_recovery_confirmation_restores_ap_and_counts_once(self) -> None:
        recovery = observe(BasePage.AP_RECOVERY,
                           Control('青铜果实', ControlKind.ITEM, (315, 290, 981, 415), True))
        confirm = Observation(
            base_page=BasePage.AP_RECOVERY,
            overlays=frozenset({Overlay.AP_RECOVERY_CONFIRM}),
            controls=(
                Control('青铜果实', ControlKind.ITEM, (315, 100, 981, 240), True),
                button('决定', (708, 550, 972, 614)),
                button('取消', (307, 550, 572, 614)),
            ),
        )
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [recovery, recovery, confirm, confirm, team, team, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset({RecoveryItem.BRONZE}))))

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, event.data) for event in events.events])
        self.assertEqual(['青铜果实', '决定', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        used = [event for event in events.events if event.type == 'recovery_used']
        self.assertEqual(1, len(used))
        self.assertEqual({'item': 'bronze', 'count': 1}, used[0].data)

    def test_saint_quartz_recovery_confirmation_is_allowed_when_whitelisted(self) -> None:
        recovery = observe(BasePage.AP_RECOVERY,
                           Control('圣晶石', ControlKind.ITEM, (315, 120, 981, 265), True))
        confirm = Observation(
            base_page=BasePage.AP_RECOVERY,
            overlays=frozenset({Overlay.AP_RECOVERY_CONFIRM}),
            controls=(
                Control('圣晶石', ControlKind.ITEM, (315, 100, 981, 240), True),
                button('决定', (708, 550, 972, 614)),
                button('取消', (307, 550, 572, 614)),
            ),
        )
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [recovery, recovery, confirm, confirm, team, team, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(recovery=RecoveryPolicy(
                         allowed=frozenset({RecoveryItem.SAINT_QUARTZ}))))

        self.assertEqual('card_mismatch', result.reason,
                         [(event.type, event.data) for event in events.events])
        self.assertEqual(['圣晶石', '决定', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        used = [event for event in events.events if event.type == 'recovery_used']
        self.assertEqual(1, len(used))
        self.assertEqual({'item': 'saint_quartz', 'count': 1}, used[0].data)

    def test_empty_whitelist_recovery_confirmation_does_not_tap_decision(self) -> None:
        confirm = Observation(
            base_page=BasePage.AP_RECOVERY,
            overlays=frozenset({Overlay.AP_RECOVERY_CONFIRM}),
            controls=(Control('青铜果实', ControlKind.ITEM, (315, 100, 981, 240), True),
                      button('决定', (708, 550, 972, 614))),
        )
        device, perception = scripted([confirm], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset())))

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual([], device.actions)
        self.assertFalse(any(event.type == 'recovery_used' for event in events.events))

    def test_recovery_confirmation_item_mismatch_stops_without_decision(self) -> None:
        recovery = observe(BasePage.AP_RECOVERY,
                           Control('青铜果实', ControlKind.ITEM, (315, 290, 981, 415), True))
        confirm = Observation(
            base_page=BasePage.AP_RECOVERY,
            overlays=frozenset({Overlay.AP_RECOVERY_CONFIRM}),
            controls=(Control('圣晶石', ControlKind.ITEM, (315, 100, 981, 240), True),
                      button('决定', (708, 550, 972, 614))),
        )
        device, perception = scripted([recovery, recovery, confirm], [0.0, 0.5, 0.6])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset({RecoveryItem.BRONZE}))))

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('ap_recovery_confirm_mismatch', result.reason)
        self.assertEqual(['青铜果实'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.type == 'recovery_used' for event in events.events))

    def test_default_policy_scrolls_once_to_find_bronze(self) -> None:
        top = observe(BasePage.AP_RECOVERY,
                      Control('圣晶石', ControlKind.ITEM, (315, 120, 981, 265), True),
                      Control('黄金果实', ControlKind.ITEM, (315, 267, 981, 412), True),
                      Control('白银果实', ControlKind.ITEM, (315, 414, 981, 559), True))
        bottom = observe(BasePage.AP_RECOVERY,
                         Control('青铜果实', ControlKind.ITEM, (315, 290, 981, 415), True))
        team = observe(BasePage.TEAM)
        device, perception = scripted([top, top, bottom, bottom, team], [0.0, 0.5, 0.6, 1.1, 1.2])
        events = MemoryEventSink()

        run(single_wave_plan(), device, perception, events)

        self.assertEqual(['swipe', 'tap'], [action['action'] for action in device.actions])
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual('AP 恢复列表向下', actions[0].data['control'])
        self.assertEqual(1, len([event for event in events.events if event.type == 'recovery_used']))

    def test_ap_recovery_stops_without_using_items(self) -> None:
        recovery = observe(BasePage.AP_RECOVERY,
                           Control('青铜果实', ControlKind.ITEM, (100, 100, 300, 200), True))
        device, perception = scripted([recovery], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset())))

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('ap_recovery', result.reason)
        self.assertEqual([], device.actions)

    def test_non_whitelisted_items_are_not_used(self) -> None:
        recovery = observe(BasePage.AP_RECOVERY,
                           Control('白银果实', ControlKind.ITEM, (100, 100, 300, 200), True),
                           Control('青铜果实', ControlKind.ITEM, (100, 200, 300, 300), True))
        device, perception = scripted([recovery], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset({RecoveryItem.GOLD}))))

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual([], device.actions)
        self.assertFalse(any(event.type == 'recovery_used' for event in events.events))

    def test_drain_completes_at_recovery_without_using_an_item(self) -> None:
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击'),))
        recovery = observe(BasePage.AP_RECOVERY,
                           Control('青铜果实', ControlKind.ITEM, (100, 100, 300, 200), True))
        device, perception = scripted([repeat, repeat, recovery], [0.0, 0.5, 0.6])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events,
                     options=RunOptions(recovery=RecoveryPolicy(drain_after_turns=True)))

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual('ap_drained', result.reason)
        self.assertEqual(['连续出击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertFalse(any(event.type == 'recovery_used' for event in events.events))

    def test_drops_fallback_is_limited_to_confirmed_drops_page(self) -> None:
        drops = observe(BasePage.DROPS)
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击'),))
        device, perception = scripted([drops, drops, repeat], [0.0, 0.5, 0.6])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual((1130, 660), (device.actions[0]['x'], device.actions[0]['y']))
        last_action = next(event for event in reversed(events.events) if event.type == 'action')
        self.assertEqual('drops_fallback', last_action.data['reason'])

    def test_result_page_can_resume_and_finish(self) -> None:
        result_page = observe(BasePage.RESULT, button('结算推进', (0, 0, 1280, 720)))
        drops = observe(BasePage.DROPS, button('下一步', (948, 606, 1272, 676)))
        repeat = Observation(base_page=BasePage.DROPS,
                             overlays=frozenset({Overlay.REPEAT_DIALOG}),
                             controls=(button('连续出击'),))
        device, perception = scripted(
            [result_page, result_page, drops, drops, repeat],
            [0.0, 0.5, 0.6, 1.1, 1.2],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual(1, result.progress.completed_runs)

    def test_mid_battle_resume_without_turn_uses_fallback_until_next_wave(self) -> None:
        base = single_wave_plan()
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        turn = TurnPlan(actions=(skill,), np_positions={0: 1}, attack=None)
        plan = replace(
            base,
            waves=tuple(WavePlan(number=wave, target=1, turns=(turn,)) for wave in range(1, 4)),
            strategy=FallbackStrategy(strategy_order=('cards', 'crit')),
        )
        controls = (button('攻击'), button('从者1技能1'))
        resumed = observe(BasePage.BATTLE, *controls).with_fields({
            'wave': Field(FieldStatus.CONFIRMED, 2),
            'turn': Field(FieldStatus.UNCERTAIN, None),
        })
        next_wave = observe(BasePage.BATTLE, *controls).with_fields({
            'wave': Field(FieldStatus.CONFIRMED, 3),
            'turn': Field(FieldStatus.CONFIRMED, 4),
        })
        card = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))),
            Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A'))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [resumed, resumed, *([card] * 6), animation]
        append_planned_turn_frames(observations, next_wave, card, animation)
        times = [index * 0.5 for index in range(len(observations))]
        observations.extend([observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)])
        times.extend([len(times) * 0.5, len(times) * 0.5 + 30.0])
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason,
                         [(event.type, event.data) for event in events.events])
        actions = [event.data for event in events.events if event.type == 'action']
        self.assertEqual([(3, 1)], [(item['wave'], item['turn']) for item in actions
                                    if item['control'] == '从者1技能1'])
        self.assertEqual(1, sum(item['control'] == '宝具卡1' for item in actions))
        self.assertEqual(2, sum(item['control'] == '攻击' for item in actions))
        self.assertEqual((3, 1), (result.progress.wave, result.progress.turn))

    def test_first_turn_battle_resume_executes_planned_turn(self) -> None:
        base = single_wave_plan()
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        turn = TurnPlan(actions=(skill,), np_positions={0: 1}, attack=None)
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),),
                       strategy=FallbackStrategy(strategy_order=('cards', 'crit')))
        battle = observe(BasePage.BATTLE, button('攻击'), button('从者1技能1'))
        card = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))),
            Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A'))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations: list[Observation] = []
        append_planned_turn_frames(observations, battle, card, animation)
        times = [index * 0.5 for index in range(len(observations))]
        observations.extend([observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)])
        times.extend([len(times) * 0.5, len(times) * 0.5 + 30.0])
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        actions = [event.data for event in events.events if event.type == 'action']
        self.assertEqual([(1, 1)], [(item['wave'], item['turn']) for item in actions
                                    if item['control'] == '从者1技能1'])
        self.assertEqual(1, sum(item['control'] == '宝具卡1' for item in actions))

    def test_battle_resume_with_unreadable_wave_still_stops(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击')).with_fields({
            'wave': Field(FieldStatus.UNCERTAIN, None),
            'turn': Field(FieldStatus.UNCERTAIN, None),
        })
        device, perception = scripted([battle], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('resume_uncertain', result.reason)
        self.assertEqual([], device.actions)

    def test_card_start_without_saved_progress_stops_instead_of_guessing(self) -> None:
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        device, perception = scripted([card, card], [0.0, 0.5])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('resume_uncertain', result.reason)
        self.assertEqual([], device.actions)

    def test_prefer_mode_falls_back_without_tapping_when_usable_cards_are_insufficient(self) -> None:
        base = single_wave_plan()
        wanted = (AttackCardPlan(slot=0, type='arts'), AttackCardPlan(slot=1, type='arts'),
                  AttackCardPlan(slot=2, type='arts'))
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='prefer', cards=wanted))
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        partial = observe(BasePage.CARD, command_card(1, 0), command_card(2, 2), command_card(3, 3),
                          command_card(4, 3), command_card(5, 4))
        device, perception = scripted([battle, battle, partial, partial], [0.0, 0.5, 0.6, 30.6])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('fallback_cards_unavailable', result.reason)
        self.assertEqual(['攻击'], [event.data['control'] for event in events.events
                                    if event.type == 'action'])
        self.assertEqual('可用指令卡不足三张', events.events[-1].data['message'])

    def test_strategy_waits_for_flying_card_identities_to_settle(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='strategy', cards=()),
                       np_positions={0: 2})
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        np_region = Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A')
        flying = observe(BasePage.CARD, *(
            replace(command_card(index, 0), card=CommandCardIdentity(-1, 'arts'))
            for index in range(1, 6)), np_region)
        settled = observe(BasePage.CARD, *(
            command_card(index, index - 1) for index in range(1, 6)), np_region)
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, flying, settled, settled, animation, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 30.5],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '宝具卡1', '卡2', '战斗跳过'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        waiting = [event for event in events.events
                   if event.type == 'warning' and event.data.get('reason') == 'card_batch_waiting']
        self.assertEqual(1, len(waiting))

    def test_same_servant_hand_waits_for_two_buster_colors_to_stabilize(self) -> None:
        job_path = Path('jobs/谜之代行者C.I.E.L、梅林、光之高扬斯卡娅.json')
        compiled = compile_job(job_path.read_text(encoding='utf-8'))
        self.assertIsNotNone(compiled.plan)
        plan = compiled.plan
        turn = TurnPlan(actions=(ActionPlan(kind='attack', title='宝具', detail='', icon='', member_slot=0),),
                        np_positions={0: 2}, attack=AttackPlan(mode='strategy', cards=()))
        plan = replace(plan, waves=(replace(plan.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        np_card = Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A')
        flying = observe(BasePage.CARD, *(
            replace(command_card(index, 0), card=CommandCardIdentity(-1, 'unknown'))
            for index in range(1, 6)), np_card)
        wrong = observe(BasePage.CARD, *(
            replace(command_card(index, 0), card=CommandCardIdentity(0, card_type))
            for index, card_type in enumerate(('buster', 'arts', 'arts', 'arts', 'quick'), start=1)), np_card)
        settled = observe(BasePage.CARD, *(
            replace(command_card(index, 0), card=CommandCardIdentity(0, card_type))
            for index, card_type in enumerate(('buster', 'buster', 'arts', 'arts', 'quick'), start=1)), np_card)
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, battle, flying, wrong, settled, settled, animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 30.6],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '宝具卡1', '卡2', '战斗跳过'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        submitted = next(event for event in events.events if event.type == 'card_batch_submitted')
        self.assertEqual(['buster', 'buster', 'arts', 'arts', 'quick'],
                         [card['card_type'] for card in submitted.data['hand']])

    def test_empty_slot_never_selects_a_single_frame_unknown_card(self) -> None:
        base = single_wave_plan()
        formation = (*base.formation[:2], FormationSlotPlan(slot=2, name=''))
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='strategy', cards=()))
        plan = replace(base, formation=formation, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        uncertain = observe(BasePage.CARD, *(
            replace(command_card(index, 0 if index != 2 else 1),
                    card=CommandCardIdentity(-1 if index == 2 else 0, 'buster'))
            for index in range(1, 6)))
        settled = observe(BasePage.CARD, *(
            replace(command_card(index, 0 if index != 2 else 1),
                    card=CommandCardIdentity(0 if index == 2 else 1, 'buster'))
            for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        device, perception = scripted(
            [battle, battle, uncertain, settled, settled, animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 30.5],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        submitted = next(event for event in events.events if event.type == 'card_batch_submitted')
        self.assertEqual(1.0, submitted.timestamp)
        self.assertTrue(all(card['servant_slot'] != -1 for card in submitted.data['hand']))

    def test_strategy_degrades_missing_rank_data_and_excludes_unknown_cards(self) -> None:
        controls = (
            replace(command_card(1, 2), card=CommandCardIdentity(2, 'buster', svt_id=2800100)),
            replace(command_card(2, 0), card=CommandCardIdentity(0, 'arts', svt_id=705000, support=True)),
            replace(command_card(3, 1), card=CommandCardIdentity(1, 'buster', svt_id=705000)),
            replace(command_card(4, 0), card=None),
            replace(command_card(5, 2), card=CommandCardIdentity(2, 'arts', svt_id=2800100)),
        )
        observation = observe(BasePage.CARD, *controls)
        strategy = FallbackStrategy(strategy_order=('crit', 'cards'))

        selection = select_cards(observation, strategy, single_wave_plan(), 1)

        self.assertEqual(('卡1', '卡3', '卡2'), tuple(control.name for control in selection.controls))
        self.assertEqual('按补刀策略顺序选择三张指令卡', selection.reason)
        self.assertIsNone(selection.diagnostic)
        self.assertEqual(('缺少暴击率，按 0 处理', '缺少克制信息，按中立处理'), selection.warnings)

    def test_strategy_mode_selects_three_cards(self) -> None:
        base = single_wave_plan()
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='strategy', cards=()))
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),),
                       strategy=FallbackStrategy(strategy_order=('cards', 'crit')))
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(
            replace(control, card=replace(control.card, crit_rate=10, affinity='neutral'))
            for control in (command_card(index, (index - 1) % 3) for index in range(1, 6))))
        observations = [battle, battle, card, card, card, card, card, card,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        device, perception = scripted(observations, [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(4, len(device.actions))

    def test_equal_cards_use_screen_x_not_candidate_input_order(self) -> None:
        controls = tuple(command_card(index, 0) for index in (5, 4, 3, 2, 1))
        observation = observe(BasePage.CARD, *controls)

        selection = select_cards(observation, FallbackStrategy(), single_wave_plan(), 1)

        self.assertEqual(('卡1', '卡2', '卡3'), tuple(control.name for control in selection.controls))

    def test_unknown_support_rows_still_count_as_scroll_content(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        unknown_rows = observe(BasePage.SUPPORT,
                               Control('助战卡片第1行', ControlKind.ROW, (40, 150, 1216, 350), True),
                               Control('助战卡片第2行', ControlKind.ROW, (40, 350, 1216, 720), True),
                               button('列表更新', (888, 98, 972, 162)))
        observations = [unknown_rows] * 6 + [observe(BasePage.UNKNOWN)] * 2
        times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 33.0]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertIn('列表更新', [event.data['control'] for event in events.events if event.type == 'action'])

    def test_empty_scrolled_support_page_never_counts_as_bottom(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        empty = observe(BasePage.SUPPORT, button('列表更新', (888, 98, 972, 162)))
        observations = [wrong] * 4 + [empty] * 4 + [observe(BasePage.UNKNOWN)] * 2
        times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 34.0]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertNotIn('列表更新', [event.data['control'] for event in events.events if event.type == 'action'])

    def test_verify_mode_stops_when_required_card_is_not_dealt(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([battle, battle, wrong_cards, wrong_cards], [0.0, 0.5, 0.6, 1.1])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(1, len(device.actions))


if __name__ == '__main__':
    unittest.main()
