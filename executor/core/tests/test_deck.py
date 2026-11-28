from __future__ import annotations

import unittest

from executor.core.deck import DeckTracker, HandCard
from executor.domain import FormationSlotPlan

CIEL = 2300900
MERLIN = 500800
ANY = 999


def formation() -> tuple[FormationSlotPlan, ...]:
    return (
        FormationSlotPlan(slot=0, name='希耶尔', svt_id=CIEL),
        FormationSlotPlan(slot=1, name='梅林', svt_id=MERLIN),
        FormationSlotPlan(slot=2, name=''),
        FormationSlotPlan(slot=3, name=''),
        FormationSlotPlan(slot=4, name=''),
        FormationSlotPlan(slot=5, name=''),
    )


def hand(slots: tuple[int | None, ...],
         svt_ids: tuple[int | None, ...] | None = None) -> tuple[HandCard, ...]:
    """槽位 0/1 是编队指定从者；空槽位在画面上只能确认从者身份，不能确认槽位。"""
    svt_ids = svt_ids or tuple({0: CIEL, 1: MERLIN}.get(slot, ANY) for slot in slots)
    return tuple(HandCard(svt_id=svt_id, servant_slot=slot if slot in (0, 1) else None)
                 for slot, svt_id in zip(slots, svt_ids))


def balanced_hands() -> list[tuple[HandCard, ...]]:
    return [
        hand((0, 0, 1, 1, None), (CIEL, CIEL, MERLIN, MERLIN, ANY)),
        hand((0, 0, 1, 1, None), (CIEL, CIEL, MERLIN, MERLIN, ANY)),
        hand((0, 1, None, None, None), (CIEL, MERLIN, ANY, ANY, ANY)),
    ]


class DeckTrackerTest(unittest.TestCase):
    def test_complete_cycle_with_balanced_counts_is_silent(self) -> None:
        tracker = DeckTracker(formation())
        warnings = []
        for turn, cards in enumerate(balanced_hands(), start=1):
            warnings.extend(tracker.record_hand(1, turn, (0, 1, 2), cards))

        self.assertEqual([], warnings)

    def test_complete_cycle_with_wrong_count_warns_per_servant(self) -> None:
        tracker = DeckTracker(formation())
        hands = [
            hand((0, 0, 0, 0, 0)),
            hand((0, 1, 1, 1, 1)),
            hand((1, 1, None, None, None)),
        ]
        warnings = []
        for turn, cards in enumerate(hands, start=1):
            warnings.extend(tracker.record_hand(1, turn, (0, 1, 2), cards))

        self.assertEqual({'deck_servant_count_mismatch'}, {warning.reason for warning in warnings})
        self.assertEqual({0, 1}, {warning.data['slot'] for warning in warnings})

    def test_unknown_cards_suppress_count_alarms(self) -> None:
        tracker = DeckTracker(formation())
        hands = [
            hand((None, None, 1, 1, None), (None, None, MERLIN, MERLIN, ANY)),
            *balanced_hands()[1:],
        ]
        warnings = []
        for turn, cards in enumerate(hands, start=1):
            warnings.extend(tracker.record_hand(1, turn, (0, 1, 2), cards))

        self.assertEqual(['deck_unknown_cards'], [warning.reason for warning in warnings])
        self.assertEqual(2, warnings[0].data['unknown'])

    def test_incomplete_evidence_discards_the_segment(self) -> None:
        tracker = DeckTracker(formation())
        tracker.record_hand(1, 1, (0, 1, 2), hand((0, 0, 0, 0, 0)))
        tracker.record_hand(1, 2, (0, 1, 2), hand((0, 0, 0, 0, 0)))
        warnings = []
        for turn, cards in enumerate(balanced_hands(), start=1):
            warnings.extend(tracker.record_hand(2, turn, (0, 1, 2), cards))

        self.assertEqual([], warnings)

    def test_frontline_change_restarts_the_segment(self) -> None:
        tracker = DeckTracker(formation())
        tracker.record_hand(1, 1, (0, 1, 2), hand((0, 0, 0, 0, 0)))
        tracker.record_hand(1, 2, (0, 1, 2), hand((0, 0, 0, 0, 0)))
        warnings = []
        for turn, cards in enumerate(balanced_hands(), start=3):
            warnings.extend(tracker.record_hand(1, turn, (2, 1, 0), cards))

        self.assertEqual([], warnings)

    def test_lock_skill_lookup_uses_atlas_effect_metadata(self) -> None:
        tracker = DeckTracker(formation(), {CIEL: {2: 1}})

        self.assertEqual(1, tracker.lock_turns(0, 2))
        self.assertIsNone(tracker.lock_turns(0, 0))
        self.assertIsNone(tracker.lock_turns(1, 2))

    def test_lock_hand_all_locked_servant_is_silent(self) -> None:
        tracker = DeckTracker(formation(), {CIEL: {2: 1}})
        tracker.begin_lock(0)

        warnings = tracker.record_hand(1, 1, (0, 1, 2),
                                       hand((0, 0, 0, 0, 0), (CIEL,) * 5))

        self.assertEqual((), warnings)

    def test_lock_hand_with_other_servant_warns_mismatch(self) -> None:
        tracker = DeckTracker(formation(), {CIEL: {2: 1}})
        tracker.begin_lock(0)

        warnings = tracker.record_hand(1, 1, (0, 1, 2), hand((0, 0, 0, 0, 1)))

        self.assertEqual(['lock_state_mismatch'], [warning.reason for warning in warnings])

    def test_lock_hand_with_unknown_cards_warns_instability(self) -> None:
        tracker = DeckTracker(formation(), {CIEL: {2: 1}})
        tracker.begin_lock(0)

        warnings = tracker.record_hand(1, 1, (0, 1, 2),
                                       hand((0, 0, 0, 0, None), (CIEL, CIEL, CIEL, CIEL, None)))

        self.assertEqual(['lock_cards_unknown'], [warning.reason for warning in warnings])

    def test_reset_discards_pending_lock(self) -> None:
        tracker = DeckTracker(formation(), {CIEL: {2: 1}})
        tracker.begin_lock(0)
        tracker.reset()

        warnings = tracker.record_hand(1, 1, (0, 1, 2), hand((0, 0, 0, 0, 1)))

        self.assertEqual((), warnings)
        self.assertFalse(tracker.lock_checked)

    def test_lock_does_not_assume_identity_for_blank_slot(self) -> None:
        tracker = DeckTracker(formation(), {CIEL: {2: 1}})
        tracker.begin_lock(2)

        warnings = tracker.record_hand(1, 1, (0, 1, 2), hand((0, 0, 0, 0, 0)))

        self.assertEqual((), warnings)


if __name__ == '__main__':
    unittest.main()
