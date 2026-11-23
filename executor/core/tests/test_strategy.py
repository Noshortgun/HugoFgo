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


if __name__ == '__main__':
    unittest.main()
