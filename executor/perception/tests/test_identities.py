from __future__ import annotations

import unittest

import cv2
import numpy as np

from executor.device.base import Frame
from executor.perception import BasePage, Perception
from executor.domain import FormationSlotPlan
from executor.perception.readiness import readiness_gaps
from executor.perception.tests.fixtures import load_frame


class IdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.perception = Perception()

    def tearDown(self) -> None:
        self.perception.close()

    def test_support_candidate_includes_servant_and_craft_essence(self) -> None:
        observation = self.perception.read(
            load_frame('captures/2026-11-20/key/support-select-correct-ce.png'))

        self.assertIs(BasePage.SUPPORT, observation.base_page)
        candidate = observation.control('助战卡片第2行').support
        self.assertIsNotNone(candidate)
        self.assertEqual((705000, 9403520), (candidate.svt_id, candidate.ce_id))
        self.assertEqual([704700, 705000, 704700],
                         [observation.control(f'助战卡片第{index}行').support.svt_id
                          for index in range(1, 4)])
        self.assertNotIn('support_identity', readiness_gaps(observation))

    def test_support_with_another_craft_essence_is_not_a_tea_match(self) -> None:
        observation = self.perception.read(
            load_frame('captures/2026-11-20/key/support-select-wrong-ce.png'))

        candidate = observation.control('助战卡片第2行').support
        self.assertEqual((705000, 9400480), (candidate.svt_id, candidate.ce_id))
        self.assertFalse(any(control.support is not None
                             and control.support.svt_id == 705000
                             and control.support.ce_id == 9403520
                             for control in observation.controls))
    def test_bound_support_skips_unneeded_catalog_identities(self) -> None:
        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True,
                              ce='迦勒底午茶时光', ce_id=9403520),
        ))
        observation = self.perception.read(
            load_frame('captures/2026-11-20/key/support-select-wrong-ce.png'))

        candidate = observation.control('助战卡片第2行').support
        other = observation.control('助战卡片第1行').support
        self.assertEqual(705000, candidate.svt_id)
        self.assertTrue(other is None or other.svt_id is None)

    def test_rebinding_support_reloads_target_and_clears_frame_cache(self) -> None:
        frame = load_frame('captures/2026-11-20/key/support-select-wrong-ce.png')
        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True, ce_id=9403520),
        ))
        first = self.perception.read(frame)
        other = first.control('助战卡片第1行').support
        self.assertTrue(other is None or other.svt_id is None)

        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='因陀罗', svt_id=305900, support=True, ce_id=9300520),
        ))
        second = self.perception.read(frame)

        candidate = second.control('助战卡片第1行').support
        self.assertEqual((305900, 9300520), (candidate.svt_id, candidate.ce_id))

    def test_command_cards_include_servant_type_and_support_identity(self) -> None:
        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True),
            FormationSlotPlan(slot=1, name='娄希', svt_id=705000),
            FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100),
        ))
        observation = self.perception.read(load_frame('captures/2026-11-20/key/card-select.png'))

        self.assertIs(BasePage.CARD, observation.base_page)
        cards = [observation.control(f'卡{index}').card for index in range(1, 6)]
        self.assertEqual([2800100, 705000, 705000, 705000, 705000],
                         [card.svt_id for card in cards])
        self.assertEqual(['quick', 'quick', 'quick', 'quick', 'arts'],
                         [card.card_type for card in cards])
        self.assertEqual([False, True, True, False, False], [card.support for card in cards])
        self.assertEqual([2, 0, 0, 1, 1], [card.servant_slot for card in cards])
        self.assertNotIn('command_card_identity', readiness_gaps(observation))
    def test_command_card_slot_is_not_guessed_without_formation(self) -> None:
        observation = self.perception.read(load_frame('captures/2026-11-20/key/card-select.png'))

        self.assertEqual(-1, observation.control('卡1').card.servant_slot)
        self.assertIn('command_card_identity', readiness_gaps(observation))

    def test_obscured_command_art_stays_unknown(self) -> None:
        frame = load_frame('captures/2026-11-20/key/card-select.png')
        image = cv2.imdecode(np.frombuffer(frame.image, dtype=np.uint8), cv2.IMREAD_COLOR)
        image[350:560, 26:246] = 0
        encoded = cv2.imencode('.png', image)[1].tobytes()
        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True),
            FormationSlotPlan(slot=1, name='娄希', svt_id=705000),
            FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100),
        ))

        observation = self.perception.read(Frame(image=encoded, width=1280, height=720, captured_at=0.0))

        self.assertIsNone(observation.control('卡1').card)
        self.assertEqual(705000, observation.control('卡4').card.svt_id)

    def test_command_catalog_rebinding_is_scoped_to_the_current_formation(self) -> None:
        frame = load_frame('captures/2026-11-20/key/card-select.png')
        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='奥伯龙', svt_id=2800100),
        ))
        first = self.perception.read(frame)
        self.assertEqual(2800100, first.control('卡1').card.svt_id)
        self.assertIsNone(first.control('卡4').card)

        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True),
        ))
        second = self.perception.read(frame)

        self.assertIsNone(second.control('卡1').card)
        self.assertEqual(705000, second.control('卡4').card.svt_id)

    def test_atlas_command_assets_work_across_sessions(self) -> None:
        self.perception.bind_formation((
            FormationSlotPlan(slot=0, name='梅林', svt_id=500800, support=True),
            FormationSlotPlan(slot=1, name='娄希', svt_id=705000),
            FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100),
        ))
        observation = self.perception.read(load_frame('captures/2026-11-19-ember/key/06-card-select.png'))

        self.assertEqual((2800100, 'buster', False),
                         (observation.control('卡1').card.svt_id,
                          observation.control('卡1').card.card_type,
                          observation.control('卡1').card.support))
        self.assertEqual((500800, 'arts', True), (observation.control('卡2').card.svt_id,
                                                 observation.control('卡2').card.card_type,
                                                 observation.control('卡2').card.support))
        self.assertEqual((2800100, 'buster'), (observation.control('卡3').card.svt_id,
                                               observation.control('卡3').card.card_type))
        self.assertEqual((500800, 'arts', True), (observation.control('卡4').card.svt_id,
                                                 observation.control('卡4').card.card_type,
                                                 observation.control('卡4').card.support))
        self.assertEqual((705000, 'quick'), (observation.control('卡5').card.svt_id,
                                             observation.control('卡5').card.card_type))
        self.assertEqual([2, 0, 2, 0, 1],
                         [observation.control(f'卡{index}').card.servant_slot
                          for index in range(1, 6)])


if __name__ == '__main__':
    unittest.main()
