from __future__ import annotations

import unittest
from dataclasses import replace

from executor.core.strategy import select_cards
from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation


class StrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = FallbackStrategy(
            servant_order=('从者0', '从者1', '从者2'),
            servant_order_slots=(0, 1, 2),
            cards='buster',
            strategy_order=('servant', 'cards', 'crit'),
        )
        self.plan = ExecutionPlan(
            mystic_id='default',
            enemy_count=3,
            waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
            formation=tuple(FormationSlotPlan(slot=slot, name=f'从者{slot}') for slot in range(3)),
            strategy=self.strategy,
        )
        self.observation = Observation(base_page=BasePage.CARD, controls=tuple(
            Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                    card=CommandCardIdentity(slot, 'buster', crit_rate=10, affinity=affinity))
            for index, (slot, affinity) in enumerate((
                (0, 'advantage'),
                (0, 'resist'),
                (1, 'advantage'),
                (1, 'resist'),
                (2, 'advantage'),
            ), 1)
        ))

    def test_specified_servants_form_a_hard_tier_above_empty_slots(self) -> None:
        plan = replace(self.plan, formation=(
            FormationSlotPlan(slot=0, name='指定0'),
            FormationSlotPlan(slot=1, name=''),
            FormationSlotPlan(slot=2, name='指定2'),
        ))
        strategy = replace(self.strategy, servant_order=(), servant_order_slots=(),
                           strategy_order=('cards', 'crit', 'affinity'))
        observation = Observation(base_page=BasePage.CARD, controls=(
            Control('空槽优质卡1', ControlKind.CARD, (0, 415, 160, 655), True,
                    card=CommandCardIdentity(1, 'buster', crit_rate=100, affinity='advantage')),
            Control('指定卡1', ControlKind.CARD, (200, 415, 360, 655), True,
                    card=CommandCardIdentity(0, 'arts', crit_rate=0, affinity='resist')),
            Control('指定卡2', ControlKind.CARD, (400, 415, 560, 655), True,
                    card=CommandCardIdentity(2, 'quick', crit_rate=0, affinity='resist')),
            Control('指定卡3', ControlKind.CARD, (600, 415, 760, 655), True,
                    card=CommandCardIdentity(0, 'quick', crit_rate=0, affinity='resist')),
            Control('空槽优质卡2', ControlKind.CARD, (800, 415, 960, 655), True,
                    card=CommandCardIdentity(1, 'buster', crit_rate=90, affinity='advantage')),
        ))

        selection = select_cards(observation, strategy, plan, 1)

        self.assertEqual(['指定卡1', '指定卡2', '指定卡3'],
                         [control.name for control in selection.controls])

    def test_unknown_identity_normal_cards_can_fill_the_empty_tier(self) -> None:
        plan = replace(self.plan, formation=(
            FormationSlotPlan(slot=0, name='指定0'),
            *(FormationSlotPlan(slot=slot, name='') for slot in range(1, 6)),
        ))
        observation = Observation(base_page=BasePage.CARD, controls=(
            Control('指定卡1', ControlKind.CARD, (0, 415, 160, 655), True,
                    card=CommandCardIdentity(0, 'arts', crit_rate=0, affinity='resist')),
            Control('指定卡2', ControlKind.CARD, (200, 415, 360, 655), True,
                    card=CommandCardIdentity(0, 'quick', crit_rate=0, affinity='resist')),
            Control('未知卡1', ControlKind.CARD, (400, 415, 560, 655), True,
                    card=CommandCardIdentity(-1, 'buster', crit_rate=100, affinity='advantage')),
            Control('未知卡2', ControlKind.CARD, (600, 415, 760, 655), True,
                    card=CommandCardIdentity(-1, 'arts', crit_rate=50, affinity='neutral')),
            Control('未知卡3', ControlKind.CARD, (800, 415, 960, 655), True,
                    card=CommandCardIdentity(-1, 'quick', crit_rate=10, affinity='neutral')),
        ))

        selection = select_cards(observation, self.strategy, plan, 1)

        self.assertEqual(['指定卡1', '指定卡2', '未知卡1'],
                         [control.name for control in selection.controls])

    def test_unknown_identity_normal_cards_stay_excluded_without_empty_slots(self) -> None:
        strategy = replace(self.strategy, strategy_order=('crit', 'cards'))
        observation = Observation(base_page=BasePage.CARD, controls=(
            Control('未知卡', ControlKind.CARD, (0, 415, 160, 655), True,
                    card=CommandCardIdentity(-1, 'buster', crit_rate=100, affinity='advantage')),
            Control('指定卡0', ControlKind.CARD, (200, 415, 360, 655), True,
                    card=CommandCardIdentity(0, 'arts', crit_rate=10, affinity='neutral')),
            Control('指定卡1', ControlKind.CARD, (400, 415, 560, 655), True,
                    card=CommandCardIdentity(1, 'quick', crit_rate=20, affinity='neutral')),
        ))

        selection = select_cards(observation, strategy, self.plan, 1)

        self.assertEqual((), selection.controls)
        self.assertEqual('可用指令卡不足三张', selection.diagnostic)

    def test_automatic_np_precedes_only_its_own_servants_normal_cards(self) -> None:
        plan = replace(self.plan, formation=(
            FormationSlotPlan(slot=0, name='从者0'),
            FormationSlotPlan(slot=1, name='从者1'),
            FormationSlotPlan(slot=2, name=''),
        ))
        strategy = replace(self.strategy, allow_np=True)
        observation = Observation(base_page=BasePage.CARD, controls=(
            Control('宝具0', ControlKind.CARD, (330, 100, 510, 330), True,
                    card=CommandCardIdentity(0, 'unknown', noble_phantasm=True, affinity='neutral')),
            Control('宝具1', ControlKind.CARD, (545, 85, 725, 315), True,
                    card=CommandCardIdentity(1, 'unknown', noble_phantasm=True, affinity='neutral')),
            Control('空槽宝具', ControlKind.CARD, (765, 100, 945, 330), True,
                    card=CommandCardIdentity(2, 'unknown', noble_phantasm=True, affinity='neutral')),
            Control('普通0', ControlKind.CARD, (0, 415, 160, 655), True,
                    card=CommandCardIdentity(0, 'buster', crit_rate=10, affinity='neutral')),
            Control('普通1', ControlKind.CARD, (200, 415, 360, 655), True,
                    card=CommandCardIdentity(1, 'buster', crit_rate=10, affinity='neutral')),
            Control('空槽普通', ControlKind.CARD, (400, 415, 560, 655), True,
                    card=CommandCardIdentity(2, 'buster', crit_rate=100, affinity='advantage')),
        ))

        selection = select_cards(observation, strategy, plan, 1)

        self.assertEqual(['宝具0', '普通0', '宝具1'],
                         [control.name for control in selection.controls])

    def test_automatic_np_precedes_own_normal_cards_without_servant_criterion(self) -> None:
        strategy = replace(self.strategy, allow_np=True)
        observation = Observation(base_page=BasePage.CARD, controls=(
            Control('宝具0', ControlKind.CARD, (330, 100, 510, 330), True,
                    card=CommandCardIdentity(0, 'unknown', noble_phantasm=True, affinity='neutral')),
            Control('普通0', ControlKind.CARD, (0, 415, 160, 655), True,
                    card=CommandCardIdentity(0, 'buster', crit_rate=100, affinity='advantage')),
            Control('普通1', ControlKind.CARD, (200, 415, 360, 655), True,
                    card=CommandCardIdentity(1, 'buster', crit_rate=50, affinity='neutral')),
            Control('普通2', ControlKind.CARD, (400, 415, 560, 655), True,
                    card=CommandCardIdentity(1, 'arts', crit_rate=40, affinity='resist')),
        ))

        for order in (('cards', 'crit'), ('crit', 'cards'), ('affinity', 'cards', 'crit')):
            with self.subTest(strategy_order=order):
                selection = select_cards(observation, replace(strategy, strategy_order=order), self.plan, 1)
                self.assertEqual(['宝具0', '普通0', '普通1'],
                                 [control.name for control in selection.controls])

    def test_affinity_uses_its_configured_strategy_position(self) -> None:
        affinity_first = replace(self.strategy, strategy_order=('affinity', 'servant', 'cards', 'crit'))

        selection = select_cards(self.observation, affinity_first, self.plan, 1)

        self.assertEqual(['卡1', '卡3', '卡5'], [control.name for control in selection.controls])

        servant_first = select_cards(self.observation, self.strategy, self.plan, 1)
        self.assertEqual(['卡1', '卡2', '卡3'], [control.name for control in servant_first.controls])

    def test_legacy_order_keeps_affinity_as_final_tiebreaker(self) -> None:
        legacy = select_cards(self.observation, self.strategy, self.plan, 1)
        explicit = select_cards(
            self.observation,
            replace(self.strategy, strategy_order=('servant', 'cards', 'crit', 'affinity')),
            self.plan,
            1,
        )

        self.assertEqual([control.name for control in legacy.controls],
                         [control.name for control in explicit.controls])
        self.assertEqual(['卡1', '卡2', '卡3'], [control.name for control in legacy.controls])

    def test_missing_affinity_degrades_to_neutral_without_rejecting_hand(self) -> None:
        first = replace(self.observation.controls[0],
                        card=replace(self.observation.controls[0].card, affinity=None))
        observation = replace(self.observation, controls=(first, *self.observation.controls[1:]))

        selection = select_cards(observation, self.strategy, self.plan, 1)

        self.assertEqual(['卡1', '卡2', '卡3'], [control.name for control in selection.controls])
        self.assertEqual(('缺少克制信息，按中立处理',), selection.warnings)
        self.assertIsNone(selection.diagnostic)

    def test_default_order_places_affinity_before_crit(self) -> None:
        default = FallbackStrategy(servant_order=('从者0',), servant_order_slots=(0,))
        observation = Observation(base_page=BasePage.CARD, controls=(
            Control('卡1', ControlKind.CARD, (0, 415, 160, 655), True,
                    card=CommandCardIdentity(0, 'buster', crit_rate=50, affinity='neutral')),
            Control('卡2', ControlKind.CARD, (200, 415, 360, 655), True,
                    card=CommandCardIdentity(0, 'buster', crit_rate=10, affinity='advantage')),
            Control('卡3', ControlKind.CARD, (400, 415, 560, 655), True,
                    card=CommandCardIdentity(0, 'buster', crit_rate=10, affinity='neutral')),
        ))
        plan = replace(self.plan, strategy=default)

        selection = select_cards(observation, default, plan, 1)

        self.assertEqual(('servant', 'cards', 'affinity', 'crit'), default.strategy_order)
        self.assertEqual(['卡2', '卡1', '卡3'], [control.name for control in selection.controls])

        legacy = replace(default, strategy_order=('servant', 'cards', 'crit'))
        legacy_selection = select_cards(observation, legacy, replace(plan, strategy=legacy), 1)
        self.assertEqual(['卡1', '卡2', '卡3'], [control.name for control in legacy_selection.controls])


if __name__ == '__main__':
    unittest.main()
