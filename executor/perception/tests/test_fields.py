"""图像数字字段：同步读取、按帧绑定、缓存与字段状态。"""
from __future__ import annotations

import unittest

from executor.perception import Field, FieldStatus, StaleFrameError
from executor.perception.tests.fixtures import isolated_perception, load_frame


class FieldTest(unittest.TestCase):
    def setUp(self) -> None:
        self.perception = isolated_perception(self.addCleanup)
        self.battle = load_frame('captures/2026-11-23/key/manual-w1-t1-after-s3-07.png')
        self.second_wave = load_frame('captures/2026-11-23/key/manual-w2-t1-start-10.png')
        self.card = load_frame('captures/2026-11-23/key/manual-w2-t1-cards-14.png')

    def test_fields_are_synchronous_without_wait(self) -> None:
        self.perception.read(self.battle)
        first = self.perception.read_fields(self.battle, ['wave'], timeout=0)

        self.assertIs(FieldStatus.CONFIRMED, first.field('wave').status)
        self.assertEqual(1, first.field('wave').value)
        self.assertIs(FieldStatus.NOT_READ, first.field('turn').status)

        ready = self.perception.read_fields(self.battle, ['wave', 'turn', 'np'], timeout=0)

        self.assertEqual(1, ready.field('wave').value)
        self.assertEqual(1, ready.field('turn').value)
        self.assertEqual((40, 100, 40), ready.field('np').value)

    def test_turn_boundary_is_derived_from_actionable_battle_and_image_turn(self) -> None:
        self.perception.read(self.battle)
        ready = self.perception.read_fields(self.battle, ['wave', 'turn'], timeout=0)

        self.assertIs(FieldStatus.CONFIRMED, ready.field('turn_boundary').status)
        self.assertIs(True, ready.field('turn_boundary').value)

    def test_digits_only_run_once_per_frame_and_record_cache_hits(self) -> None:
        self.perception.read(self.battle)
        self.perception.read_fields(self.battle, ['wave', 'turn'])
        cached = self.perception.read_fields(self.battle, ['wave'], timeout=0)
        self.perception.read_fields(self.battle, ['turn'], timeout=0)

        stats = self.perception.stats()
        self.assertEqual(1, stats['field_reads'])
        self.assertEqual(2, stats['field_cache_hits'])
        self.assertIsNotNone(stats['field_last_ms'])
        self.assertGreaterEqual(cached.evidence['field_ms'], 0.0)
        self.assertEqual(1.0, cached.evidence['field_cache_hit'])

    def test_frame_not_just_read_is_rejected(self) -> None:
        self.perception.read(self.card)
        with self.assertRaises(StaleFrameError):
            self.perception.read_fields(self.battle, ['wave'])

    def test_unknown_field_name_is_rejected(self) -> None:
        self.perception.read(self.battle)
        with self.assertRaises(ValueError):
            self.perception.read_fields(self.battle, ['bond'])

    def test_field_not_applicable_on_page_stays_unread(self) -> None:
        result = load_frame('captures/2026-11-20/key/result.png')
        self.perception.read(result)
        observed = self.perception.read_fields(result, ['wave'])

        self.assertIs(FieldStatus.NOT_APPLICABLE, observed.field('wave').status)

    def test_cached_image_result_is_used_by_next_read(self) -> None:
        self.perception.read(self.battle)
        self.perception.read_fields(self.battle, ['wave'], timeout=0)

        fresh = self.perception.read(self.battle)
        self.assertIs(FieldStatus.CONFIRMED, fresh.field('wave').status)
        self.assertTrue(isinstance(fresh.field('wave'), Field))

    def test_unrequested_fields_stay_not_read(self) -> None:
        self.perception.read(self.battle)
        ready = self.perception.read_fields(self.battle, ['wave'], timeout=0)

        self.assertIs(FieldStatus.CONFIRMED, ready.field('wave').status)
        self.assertIs(FieldStatus.NOT_READ, ready.field('turn').status)
        self.assertIs(FieldStatus.NOT_READ, ready.field('np').status)

    def test_results_are_bound_to_one_frame(self) -> None:
        self.perception.read(self.battle)
        first = self.perception.read_fields(self.battle, ['wave', 'turn'], timeout=0)
        self.perception.read(self.second_wave)
        second = self.perception.read_fields(self.second_wave, ['wave', 'turn'], timeout=0)
        self.perception.read(self.battle)
        cached = self.perception.read_fields(self.battle, ['wave', 'turn'], timeout=0)

        self.assertEqual((1, 1), (first.field('wave').value, first.field('turn').value))
        self.assertEqual((2, 2), (second.field('wave').value, second.field('turn').value))
        self.assertEqual((1, 1), (cached.field('wave').value, cached.field('turn').value))

    def test_unused_fields_are_uncertain_without_ocr_fallback(self) -> None:
        self.perception.read(self.battle)
        battle = self.perception.read_fields(self.battle, ['enemies'], timeout=0)
        self.assertIs(FieldStatus.UNCERTAIN, battle.field('enemies').status)

        recovery = load_frame('captures/2026-11-20/key/ap-recovery.png')
        self.perception.read(recovery)
        ap = self.perception.read_fields(recovery, ['ap'], timeout=0)
        self.assertIs(FieldStatus.UNCERTAIN, ap.field('ap').status)


if __name__ == '__main__':
    unittest.main()
