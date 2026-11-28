from __future__ import annotations

import unittest
from dataclasses import replace
from unittest import mock

from executor.core import RunStatus
from executor.core.smoke import ConfirmedStartPerception
from executor.core.tests.test_runner import (button, command_card, observe, run, scripted,
                                             single_wave_plan, support_bottom)
from executor.domain import (ActionPlan, AttackPlan, ExecutionPlan, FormationSlotPlan, TurnPlan,
                             WavePlan)
from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                 FieldStatus, Observation, SupportCandidate, Variant)
from executor.session import MemoryEventSink


def identified_card(index: int, slot: int, svt_id: int, card_type: str = 'arts') -> Control:
    boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
             (811, 415, 976, 655), (1066, 415, 1231, 655))
    return Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                   card=CommandCardIdentity(servant_slot=slot, card_type=card_type, svt_id=svt_id))


def any_support_plan(ce_id: int | None = None) -> ExecutionPlan:
    base = single_wave_plan()
    support = FormationSlotPlan(slot=0, name='', support=True, support_required=False, ce_id=ce_id)
    return replace(base, formation=(support, *base.formation[1:]))


class AnySupportRuleTest(unittest.TestCase):
    def test_any_support_selects_first_stable_row_without_identity(self) -> None:
        plan = any_support_plan()
        unknown_row = Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True)
        other_row = Control('助战卡片第2行', ControlKind.ROW, (40, 400, 1000, 560), True,
                            support=SupportCandidate(name='其他', svt_id=1, ce_id=9400480))
        support = observe(BasePage.SUPPORT, unknown_row, other_row)
        device, perception = scripted(
            [support, support, support, support, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.5, 1.0, 1.5, 2.0, 34.0])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual(['助战卡片第1行'], controls)
        self.assertFalse(any(control.startswith('职阶筛选') for control in controls))

    def test_any_support_ce_filter_accepts_unknown_servant(self) -> None:
        plan = any_support_plan(ce_id=9403520)
        no_identity = Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True)
        wrong_ce = Control('助战卡片第2行', ControlKind.ROW, (40, 400, 1000, 560), True,
                           support=SupportCandidate(name='娄希', svt_id=705000, ce_id=9400480))
        right_ce = Control('助战卡片第3行', ControlKind.ROW, (40, 520, 1000, 660), True,
                           support=SupportCandidate(name='', svt_id=None, ce_id=9403520))
        support = observe(BasePage.SUPPORT, no_identity, wrong_ce, right_ce)
        device, perception = scripted(
            [support, support, support, support, observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.5, 1.0, 1.5, 2.0, 34.0])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('unknown_timeout', result.reason)
        self.assertEqual(['助战卡片第3行'],
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_any_support_without_ce_never_opens_refresh(self) -> None:
        plan = any_support_plan()
        bottom = support_bottom(observe(BasePage.SUPPORT, button('列表更新', (888, 98, 972, 162))))
        device, perception = scripted([bottom] * 8, [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 33.0])
        events = MemoryEventSink()

        run(plan, device, perception, events)

        self.assertNotIn('列表更新',
                         [event.data['control'] for event in events.events if event.type == 'action'])

    def test_multiple_support_slots_are_refused_at_start(self) -> None:
        base = single_wave_plan()
        plan = replace(base, formation=(
            replace(base.formation[0], support=True, support_required=True),
            replace(base.formation[1], support=True, support_required=False),
            *base.formation[2:],
        ))
        device, perception = scripted([observe(BasePage.SUPPORT)], [0.0])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual('multiple_support_slots', result.reason)
        self.assertFalse(any(event.type == 'action' for event in events.events))

    def test_support_rows_that_never_stabilize_stop_safely(self) -> None:
        plan = any_support_plan(ce_id=9403520)
        wrong = SupportCandidate(name='其他', svt_id=1, ce_id=9400480)
        frames = [observe(BasePage.SUPPORT,
                          Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 720), True,
                                  support=wrong, visual_hash='0' * 64 if index % 2 else '1' * 64))
                  for index in range(11)]
        times = [index * 0.5 for index in range(10)] + [35.0]
        device, perception = scripted(frames, times)
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('support_candidates_unstable', result.reason)
        self.assertFalse(any(event.type == 'action' for event in events.events))

    def test_team_check_skips_any_support_identity_but_keeps_ce(self) -> None:
        plan = any_support_plan(ce_id=9403520)
        team_wrong = observe(BasePage.TEAM, button('开始任务')).with_fields({
            'team_support_ce': Field(FieldStatus.CONFIRMED, 9400480),
        })
        device, perception = scripted([team_wrong] * 2 + [team_wrong] * 2,
                                      [0.0, 0.5, 1.0, 31.0])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('team_mismatch', result.reason)
        self.assertFalse(any(event.type == 'action' for event in events.events))

    def test_team_check_accepts_any_support_without_ce(self) -> None:
        plan = any_support_plan()
        team = observe(BasePage.TEAM, button('开始任务')).with_fields({
            'team_servants': Field(FieldStatus.CONFIRMED, (999, 2, 3)),
        })
        battle = observe(BasePage.BATTLE, button('攻击'))
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted([team, team, battle, battle, wrong_cards],
                                      [0.0, 0.5, 1.0, 1.5, 2.0])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual('card_mismatch', result.reason)
        self.assertIn('开始任务', [event.data['control'] for event in events.events
                                  if event.type == 'action'])


class AnyServantRuleTest(unittest.TestCase):
    def test_any_servant_skill_target_waits_for_frontline_confirmation(self) -> None:
        skill = ActionPlan(kind='skill', title='指定从者技能', detail='', icon='',
                           member_slot=0, skill=2, player_target_slot=1)
        plan = single_wave_plan((skill,))
        plan = replace(plan, formation=(
            plan.formation[0],
            FormationSlotPlan(slot=1, name=''),
            FormationSlotPlan(slot=2, name=''),
        ))
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能3', ControlKind.SKILL, (217, 548, 278, 610), True),
                         Control('从者2技能1', ControlKind.SKILL, (400, 548, 460, 610), True))
        unconfirmed = battle.with_fields({'battle_servants': Field(FieldStatus.UNCERTAIN)})
        occupied = battle.with_fields({
            'battle_servants': Field(FieldStatus.CONFIRMED, (705000, -1, 3))})
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        targets = Observation(
            base_page=BasePage.BATTLE, variants=frozenset({Variant.SKILL_TARGET}),
            controls=tuple(Control(f'目标从者{index}', ControlKind.TARGET,
                                   (index * 300, 269, index * 300 + 200, 499), True)
                           for index in range(1, 4)),
        )
        wrong_cards = observe(BasePage.CARD, *(command_card(index, 4) for index in range(1, 6)))
        device, perception = scripted(
            [unconfirmed, unconfirmed, occupied, occupied, animation, occupied, occupied,
             targets, targets, animation, occupied, occupied, wrong_cards],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0, 1.1, 1.2, 1.7, 2.0])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        controls = [event.data['control'] for event in events.events if event.type == 'action']
        self.assertEqual(['从者1技能3', '目标从者2', '战斗跳过', '攻击'], controls)
        self.assertEqual('card_mismatch', result.reason)

    def test_any_servant_slot_without_a_visible_servant_stops_without_tapping(self) -> None:
        skill = ActionPlan(kind='skill', title='指定从者技能', detail='', icon='',
                           member_slot=0, skill=2, player_target_slot=1)
        plan = single_wave_plan((skill,))
        plan = replace(plan, formation=(
            plan.formation[0], FormationSlotPlan(slot=1, name=''), *plan.formation[2:]))
        battle = observe(BasePage.BATTLE, button('攻击'),
                         Control('从者1技能3', ControlKind.SKILL, (217, 548, 278, 610), True))
        target = Observation(
            base_page=BasePage.BATTLE, variants=frozenset({Variant.SKILL_TARGET}),
            controls=tuple(Control(f'目标从者{index}', ControlKind.TARGET,
                                   (index * 300, 269, index * 300 + 200, 499), True)
                           for index in range(1, 4)),
            fields={'battle_servants': Field(FieldStatus.UNCERTAIN)},
        )
        device, perception = scripted(
            [battle, battle, target, target] + [target] * 6,
            [0.0, 0.1, 0.2, 1.0, 5.0, 10.0, 20.0, 31.0, 32.0, 33.0])
        events = MemoryEventSink()

        result = run(plan, device, perception, events)

        self.assertEqual(RunStatus.STOPPED, result.status)
        self.assertEqual('target_unavailable', result.reason)
        self.assertFalse(any(event.type == 'action' for event in events.events))


class DeckTrackingTest(unittest.TestCase):
    def three_turn_plan(self) -> ExecutionPlan:
        base = single_wave_plan()
        turn = TurnPlan(attack=AttackPlan(mode='strategy', cards=()))
        return replace(base, waves=(replace(base.waves[0], turns=(turn, turn, turn)),))

    def hand_frame(self, cards: tuple[tuple[int, int], ...]) -> Observation:
        return observe(BasePage.CARD, *(
            identified_card(index, slot, svt_id)
            for index, (slot, svt_id) in enumerate(cards, start=1)))

    def run_three_hands(self, hands, plan: ExecutionPlan):
        battle = observe(BasePage.BATTLE, button('攻击'))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        observations = [battle, battle]
        times = [0.0, 0.2]
        at = 0.4
        for index, cards in enumerate(hands, start=1):
            frame = self.hand_frame(cards)
            observations.extend([frame, frame])
            times.extend([at, at + 0.1])
            at += 0.3
            if index < len(hands):
                next_battle = observe(BasePage.BATTLE, button('攻击')).with_fields({
                    'wave': Field(FieldStatus.CONFIRMED, 1),
                    'turn': Field(FieldStatus.CONFIRMED, index + 1)})
                observations.extend([animation, next_battle, next_battle])
                times.extend([at, at + 0.1, at + 0.2])
                at += 0.4
        observations.extend([observe(BasePage.UNKNOWN)] * 2)
        times.extend([at, at + 32.0])
        device, perception = scripted(observations, times)
        events = MemoryEventSink()
        result = run(plan, device, perception, events)
        return result, events

    def test_complete_cycle_with_wrong_count_warns_without_stopping(self) -> None:
        hands = [
            ((0, 705000), (0, 705000), (0, 705000), (0, 705000), (0, 705000)),
            ((0, 705000), (1, 2), (1, 2), (1, 2), (1, 2)),
            ((1, 2), (1, 2), (2, 3), (2, 3), (2, 3)),
        ]
        result, events = self.run_three_hands(hands, self.three_turn_plan())

        warnings = [event for event in events.events
                    if event.type == 'warning' and str(event.data.get('reason', '')).startswith('deck_')]
        self.assertTrue(warnings, [(event.type, dict(event.data)) for event in events.events])
        self.assertTrue(all(event.data.get('message') for event in warnings))
        self.assertEqual('unknown_timeout', result.reason)

    def test_mid_battle_resume_does_not_warn_about_deck_cycle(self) -> None:
        base = single_wave_plan()
        turn = TurnPlan(attack=AttackPlan(mode='strategy', cards=()))
        plan = replace(base, waves=(WavePlan(number=1, target=1, turns=(turn,)),
                                    WavePlan(number=2, target=1, turns=(turn,))))
        battle = observe(BasePage.BATTLE, button('攻击'))
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        hand = self.hand_frame(((0, 705000), (1, 2), (2, 3), (0, 705000), (1, 2)))
        device, perception = scripted(
            [battle, battle, hand, hand, animation,
             observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)],
            [0.0, 0.2, 0.4, 0.5, 0.9, 1.2, 34.0])
        reader = ConfirmedStartPerception(perception, 2, 4)
        events = MemoryEventSink()

        result = run(plan, device, reader, events)

        reasons = [event.data.get('reason') for event in events.events if event.type == 'warning']
        self.assertFalse(any(str(reason).startswith('deck_') for reason in reasons), reasons)
        self.assertEqual('unknown_timeout', result.reason)

    def test_lock_skill_confirms_redrawn_hand_and_warns_on_other_servant(self) -> None:
        base = single_wave_plan()
        ciel = FormationSlotPlan(slot=0, name='希耶尔', svt_id=2300900)
        skill = ActionPlan(kind='skill', title='原理血戒', detail='', icon='', member_slot=0, skill=2)
        turn = TurnPlan(actions=(skill,), attack=AttackPlan(mode='strategy', cards=()))
        plan = replace(base, formation=(ciel, *base.formation[1:]),
                       waves=(replace(base.waves[0], turns=(turn, turn)),))
        available = Control('从者1技能3', ControlKind.SKILL, (217, 548, 278, 610), True,
                            on_cooldown=False)
        used = replace(available, on_cooldown=True, actionable=False)
        battle = observe(BasePage.BATTLE, button('攻击'), available)
        after = observe(BasePage.BATTLE, button('攻击'), used)
        animation = Observation(base_page=BasePage.BATTLE,
                                variants=frozenset({Variant.BATTLE_ANIMATION}))
        mixed_hand = observe(BasePage.CARD, *(
            [identified_card(index, 0, 2300900) for index in range(1, 5)]
            + [identified_card(5, 1, 500800)]))
        next_battle = after.with_fields({'turn': Field(FieldStatus.CONFIRMED, 2)})
        observations = [battle, battle, animation, after, after, mixed_hand, mixed_hand,
                        animation, next_battle, next_battle,
                        observe(BasePage.UNKNOWN), observe(BasePage.UNKNOWN)]
        times = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0, 1.5, 1.6, 34.0]
        device, perception = scripted(observations, times)
        events = MemoryEventSink()

        with mock.patch('executor.core.runner.lock_card_skills',
                        return_value={2300900: {2: 1}}):
            result = run(plan, device, perception, events)

        reasons = [event.data.get('reason') for event in events.events if event.type == 'warning']
        self.assertIn('lock_state_mismatch', reasons)
        self.assertTrue(any(event.type == 'lock_hand_checked' for event in events.events), reasons)
        self.assertEqual('unknown_timeout', result.reason)


if __name__ == '__main__':
    unittest.main()
