from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from unittest import mock
from pathlib import Path

from executor.core import RunOptions, RunStatus, run as core_run
from executor.device.base import DeviceError
from executor.device.fake import FakeDevice
from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FallbackStrategy,
                             FormationSlotPlan, TurnPlan, WavePlan)
from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                 FieldStatus, Observation, Overlay, SupportCandidate, Variant)
from executor.session import MemoryEventSink, RunControl
from executor.testing import ScriptedPerception, scenario_frames


def run(plan, device, perception, events, *, options=None, control=None):
    if options is None:
        options = RunOptions(write_evidence=False)
    elif options.run_dir is None:
        options = replace(options, write_evidence=False)
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
    return Observation(base_page=page, controls=tuple(controls), fields=fields, frame_hash=page.value)


def scripted(observations: list[Observation], times: list[float], *,
             preserve_hashes: bool = False) -> tuple[FakeDevice, object]:
    frames = scenario_frames(times, len(observations))

    return FakeDevice(frames), ScriptedPerception(observations, preserve_hashes=preserve_hashes)


def single_wave_plan(actions=(), np_positions=None) -> ExecutionPlan:
    cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
    turn = TurnPlan(actions=tuple(actions), np_positions=np_positions or {},
                    attack=AttackPlan(mode='verify', cards=cards))
    formation = tuple(FormationSlotPlan(slot=slot, name=f'从者{slot}') for slot in range(3))
    return ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),), formation=formation)


class RunnerTest(unittest.TestCase):
    def test_support_and_team_lead_into_the_same_battle_flow(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker', ce='午茶', ce_id=9403520)
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker',
                                     ce='午茶', ce_id=9403520)
        support = observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True,
                                  support=candidate))
        team = observe(BasePage.TEAM, button('开始任务', (1086, 635, 1248, 707)))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [support, support, team, team, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['助战卡片第1行', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_support_search_refreshes_at_bottom_and_rechecks_candidates(self) -> None:
        plan = single_wave_plan()
        support_slot = replace(plan.formation[0], name='娄希', support=True, support_required=True,
                               svt_id=705000, role='Berserker')
        plan = replace(plan, formation=(support_slot, *plan.formation[1:]))
        wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
        wrong = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True,
                                support=wrong_candidate),
                        button('列表更新', (888, 98, 972, 162)))
        confirm = Observation(base_page=BasePage.SUPPORT,
                              overlays=frozenset({Overlay.SUPPORT_CONFIRM}),
                              controls=(button('是', (708, 534, 969, 592)),))
        candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
        match = observe(BasePage.SUPPORT,
                        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True,
                                support=candidate))
        team = observe(BasePage.TEAM, button('开始任务'))
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        observations = [wrong, wrong, wrong, wrong, wrong, wrong, confirm, confirm,
                        match, match, team, team, battle, battle, wrong_cards]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3,
                 2.4, 2.9, 3.0, 3.5, 3.6, 4.1, 4.2]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['助战列表回顶', '助战列表向下', '列表更新', '是', '助战卡片第1行', '开始任务', '攻击'],
                         controls)
        self.assertEqual(['swipe', 'swipe'], [action['action'] for action in device.actions[:2]])

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
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True,
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
        self.assertEqual(['结束', '助战卡片第1行', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_unchanged_frame_cannot_confirm_an_action(self) -> None:
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
        first = replace(battle, frame_hash='first')
        stale = replace(battle, frame_hash='first')
        confirmed = replace(battle, frame_hash='second')
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [first, stale, stale, confirmed, animation, confirmed, confirmed, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.3, 1.8, 1.9], preserve_hashes=True,
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(['从者1技能1', '攻击'], [event.data['control'] for event in actions])
        self.assertEqual(1.8, actions[-1].timestamp)

    def test_skill_confirmation_requires_the_expected_animation(self) -> None:
        skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, battle, battle, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.3, 1.8, 1.9],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        actions = [event for event in events.events if event.type == 'action']
        self.assertEqual(1.8, actions[-1].timestamp)

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
        self.assertEqual(['从者1技能1', '攻击'],
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
        self.assertEqual(['从者2技能3', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

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
            [battle, battle, target, target, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.6, 2.1, 2.2],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者2技能3', '目标从者2', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_targeted_skill_selects_target_before_progressing(self) -> None:
        skill = ActionPlan(kind='skill', title='单体技能', detail='', icon='',
                           member_slot=0, skill=0, player_target_slot=1)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True))
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
            [battle, battle, target, target, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.6, 2.1, 2.2],
        )
        events = MemoryEventSink()

        result = run(single_wave_plan((skill,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['从者1技能1', '目标从者2', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

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
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [battle, battle, menu, menu, order, order, order, order, order, order,
             animation, battle, battle, animation, battle, battle, wrong_cards],
            [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.9,
             3.0, 3.2, 3.7, 3.8, 4.0, 4.5, 4.6],
        )
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['御主技能', '御主技能3', '先发第1位', '候补第1位', '进行更替', '从者1技能1', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(2, result.progress.action_index)

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
        self.assertEqual(['令咒', '宝具解放', '决定', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

    def test_enemy_target_action_uses_the_planned_enemy(self) -> None:
        target = ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2)
        battle = observe(BasePage.BATTLE, button('攻击'),
                         *(Control(f'敌方{index}', ControlKind.ENEMY,
                                   (index * 250, 20, index * 250 + 200, 100), True)
                           for index in range(1, 4)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([battle, battle, battle, battle, wrong_cards],
                                      [0.0, 0.5, 0.6, 1.1, 1.2])
        events = MemoryEventSink()

        result = run(single_wave_plan((target,)), device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(['敌方2', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
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
        self.assertEqual(['御主技能', '御主技能2', '目标从者2', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(1, result.progress.action_index)

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
            self.assertEqual('fake', payload['device']['serial'])
            self.assertEqual('default', payload['job']['mystic_id'])
            self.assertEqual('unknown', observation['base_page'])
            self.assertEqual('unknown_timeout', rows[-1]['data']['reason'])
            self.assertTrue((root / 'failure.png').is_file())

    def test_tap_device_error_becomes_a_failed_run_result(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        device, perception = scripted([battle, battle], [0.0, 0.5])
        events = MemoryEventSink()

        with mock.patch.object(device, 'tap', side_effect=DeviceError('tap failed')):
            result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual('device_error', result.reason)
        self.assertEqual('device_error', events.events[-1].data['reason'])

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

    def test_third_command_card_stops_input_and_waits_for_attack(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle, card, card, card, card, card, card, card, animation,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0, 33.0]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertEqual(4, len(device.actions))

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
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '结算推进', '下一步'],
                         [event.data['control'] for event in events.events if event.type == 'action'])
        self.assertNotIn('连续出击', [event.data['control'] for event in events.events
                                      if event.type == 'action'])

    def test_next_wave_applies_initial_target_before_attacking(self) -> None:
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
            'turn': Field(FieldStatus.CONFIRMED, 1),
            'turn_boundary': Field(FieldStatus.CONFIRMED, True),
        })
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle, card, card, card, card, card, card, animation,
                        second_battle, second_battle, second_battle, second_battle, wrong_cards]
        times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0, 3.1, 3.6, 3.7]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual('card_mismatch', result.reason)
        self.assertEqual(2, result.progress.wave)
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '敌方2', '攻击'], controls)
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
        self.assertEqual(['攻击', '卡1', '卡2', '卡3', '攻击', '卡1', '卡2', '卡3'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

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
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True,
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
        self.assertEqual(['连续出击', '助战卡片第1行', '开始任务', '攻击'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

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
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True,
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

    def test_ap_recovery_stops_without_using_items(self) -> None:
        recovery = observe(BasePage.AP_RECOVERY, button('青铜苹果', (100, 100, 300, 200)))
        device, perception = scripted([recovery], [0.0])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('ap_recovery', result.reason)
        self.assertEqual([], device.actions)

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
        self.assertEqual('drops_fallback', events.events[-2].data['reason'])

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

    def test_card_start_without_saved_progress_stops_instead_of_guessing(self) -> None:
        card = observe(BasePage.CARD, *(command_card(index, index - 1) for index in range(1, 6)))
        device, perception = scripted([card, card], [0.0, 0.5])
        events = MemoryEventSink()

        result = run(single_wave_plan(), device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('resume_uncertain', result.reason)
        self.assertEqual([], device.actions)

    def test_prefer_mode_falls_back_without_tapping_when_hand_is_uncertain(self) -> None:
        base = single_wave_plan()
        wanted = (AttackCardPlan(slot=0, type='arts'), AttackCardPlan(slot=1, type='arts'),
                  AttackCardPlan(slot=2, type='arts'))
        turn = replace(base.waves[0].turns[0], attack=AttackPlan(mode='prefer', cards=wanted))
        plan = replace(base, waves=(replace(base.waves[0], turns=(turn,)),))
        battle = observe(BasePage.BATTLE, button('攻击'))
        partial = observe(BasePage.CARD, command_card(1, 0), command_card(2, 2), command_card(3, 2),
                          command_card(4, 3), command_card(5, 4))
        device, perception = scripted([battle, battle, partial, partial], [0.0, 0.5, 0.6, 1.1])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('fallback_cards_unavailable', result.reason)
        self.assertEqual(['攻击'], [event.data['control'] for event in events.events
                                    if event.type == 'action'])
        self.assertEqual('指令卡身份、暴击率或克制信息不确定', events.events[-1].data['message'])

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
