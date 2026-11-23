from __future__ import annotations

import unittest
from pathlib import Path

from executor.device import Frame
from executor.domain import compile_job
from executor.perception import Perception
from executor.perception.tests.fixtures import reject_ocr_imports


ROOT = Path(__file__).resolve().parents[3]


class ManualFieldTest(unittest.TestCase):
    def read_fields(self, name: str, *fields: str):
        perception = Perception()
        path = ROOT / name if name.startswith('captures/') else ROOT / 'captures/2026-11-23/key' / name
        try:
            frame = Frame(image=path.read_bytes(), width=1280, height=720, captured_at=1.0)
            with reject_ocr_imports() as imports:
                perception.read(frame)
                observation = perception.read_fields(frame, fields, timeout=0)
            self.assertEqual([], imports)
            self.assertGreaterEqual(observation.evidence['field_ms'], 0.0)
            self.assertEqual(0.0, observation.evidence['field_cache_hit'])
            return observation
        finally:
            perception.close()

    def test_after_skill_reads_wave_turn_and_np(self) -> None:
        observation = self.read_fields('manual-w1-t1-after-s3-07.png', 'wave', 'turn', 'np')

        self.assertEqual(1, observation.field('wave').value)
        self.assertEqual(1, observation.field('turn').value)
        self.assertEqual((40, 100, 40), observation.field('np').value)

    def test_team_page_reads_servants_support_ce_and_mystic(self) -> None:
        compiled = compile_job((ROOT / 'jobs/娄希、娄希、奥伯龙.json').read_text(encoding='utf-8'))
        perception = Perception()
        try:
            perception.bind_formation(compiled.plan.formation)
            perception.bind_mystic(compiled.plan.mystic_id)
            frame = Frame(image=(ROOT / 'captures/2026-11-23/key/manual-team-04.png').read_bytes(),
                          width=1280, height=720, captured_at=1.0)
            observation = perception.read(frame)
        finally:
            perception.close()

        self.assertEqual((705000, 705000, 2800100), observation.field('team_servants').value)
        self.assertEqual(9403520, observation.field('team_support_ce').value)
        self.assertEqual('atlas-260', observation.field('team_mystic').value)

    def test_crit_templates_cover_four_six_and_seven(self) -> None:
        for name, expected in (
            ('captures/2026-11-22/key/card-crit-50-60-100-40-30.png', (50, 60, 100, 40, 30)),
            ('captures/2026-11-22/key/card-crit-40-100-70-30-40.png', (40, 100, 70, 30, 40)),
            ('captures/2026-11-22/key/card-w3-t6.png', (10, 60, 100, 100, 40)),
        ):
            with self.subTest(expected=expected):
                observation = self.read_fields(name, 'crit')
                self.assertEqual(expected, observation.field('crit').value)

    def test_np_templates_cover_two_three_and_five_through_nine(self) -> None:
        for name, expected in (
            ('captures/2026-11-20/key/battle-wave1.png', (20, 20, 20)),
            ('captures/2026-11-22/key/card-crit-40-100-70-30-40.png', (107, 114, 43)),
            ('captures/2026-11-23/key/manual-w2-t1-cards-14.png', (140, 59, 80)),
            ('captures/2026-11-23/key/card-np-12-94-60.png', (12, 94, 60)),
            ('captures/2026-11-22/key/card-np-114-74-43.png', (114, 74, 43)),
            ('captures/2026-11-22/key/card-np-154-89-43.png', (154, 89, 43)),
            ('captures/2026-11-22/key/card-w3-t6.png', (154, 110, 43)),
        ):
            with self.subTest(expected=expected):
                observation = self.read_fields(name, 'np')
                self.assertEqual(expected, observation.field('np').value)

    def test_turn_templates_cover_four_through_seven(self) -> None:
        for name, wave, turn in (
            ('captures/2026-11-22/key/battle-w2-t4.png', 2, 4),
            ('captures/2026-11-22/key/battle-w3-t5.png', 3, 5),
            ('captures/2026-11-22/key/battle-w3-t6.png', 3, 6),
            ('captures/2026-11-22/key/battle-w3-t7.png', 3, 7),
        ):
            with self.subTest(wave=wave, turn=turn):
                observation = self.read_fields(name, 'wave', 'turn')
                self.assertEqual(wave, observation.field('wave').value)
                self.assertEqual(turn, observation.field('turn').value)

    def test_all_cards_can_read_three_digit_crit_rates(self) -> None:
        observation = self.read_fields('manual-w3-t1-cards-25.png', 'crit')
        self.assertEqual((100, 100, 100, 100, 100), observation.field('crit').value)

    def test_missing_crit_numbers_stay_uncertain(self) -> None:
        observation = self.read_fields('manual-w1-t1-cards-09.png', 'crit')
        self.assertIsNone(observation.field('crit').value)
        self.assertEqual('uncertain', observation.field('crit').status.value)

    def test_support_page_reads_selected_class(self) -> None:
        perception = Perception()
        try:
            frame = Frame(image=(ROOT / 'captures/2026-11-23/key/manual-support-berserker-02.png').read_bytes(),
                          width=1280, height=720, captured_at=1.0)
            observation = perception.read(frame)
        finally:
            perception.close()

        self.assertEqual('berserker', observation.field('support_class').value)

    def test_card_crit_rates_are_bound_to_screen_order(self) -> None:
        observation = self.read_fields('manual-w2-t1-cards-14.png', 'crit')

        self.assertEqual((10, 100, 80, 30, 90), observation.field('crit').value)
        self.assertEqual([10, 100, 80, 30, 90], [control.card.crit_rate for control in observation.controls
                                               if control.name in {'卡1', '卡2', '卡3', '卡4', '卡5'}])

    def test_crit_rates_also_cover_twenties_thirties_and_fifties(self) -> None:
        observation = self.read_fields(
            'captures/2026-11-19-ember/key/10-np-card-select.png', 'crit')

        self.assertEqual((20, 30, 50, 50, 30), observation.field('crit').value)


if __name__ == '__main__':
    unittest.main()
