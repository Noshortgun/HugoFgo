from pathlib import Path
import unittest

from executor.device.base import Frame
from executor.perception import Perception


ROOT = Path(__file__).resolve().parents[3]


def frame_from(path: Path) -> Frame:
    data = path.read_bytes()
    return Frame(image=data, width=1280, height=720, captured_at=0.0)


class PerceptionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.perception = Perception()

    def test_support_screen_reports_support_and_anchor(self):
        observation = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-ember/key/02-support-select.png'
        ))

        self.assertEqual('support', observation.state)
        self.assertFalse(observation.uncertain)
        self.assertTrue(any('助战选择' in anchor.text for anchor in observation.anchors))

    def test_reads_all_supported_screens(self):
        cases = [
            ('captures/2026-11-19/key/01-main.png', 'menu'),
            ('captures/2026-11-19-ember/key/02-support-select.png', 'support'),
            ('captures/2026-11-19-ember/key/03-team-confirm.png', 'team'),
            ('captures/2026-11-19-ember/key/04-battle-wave1.png', 'battle'),
            ('captures/2026-11-19-ember/key/06-card-select.png', 'card'),
            ('captures/2026-11-19-ember/key/12-battle-result-bond.png', 'result'),
            ('captures/2026-11-19-ember/05-quest-start.png', 'loading'),
            ('captures/2026-11-19-ember/key/01-daily-quest-list.png', 'unknown'),
        ]
        for relative, expected in cases:
            with self.subTest(relative=relative):
                observation = self.perception.read(frame_from(ROOT / relative))
                self.assertEqual(expected, observation.state)
                self.assertEqual(expected == 'unknown', observation.uncertain)

    def test_battle_reports_wave_and_card_screen_reports_cards(self):
        battle = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-ember/key/04-battle-wave1.png'
        ))
        self.assertEqual(1, battle.wave)

        card = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-ember/key/06-card-select.png'
        ))
        self.assertGreaterEqual(len(card.cards), 4)
        self.assertTrue(all(card.y > 450 for card in card.cards))

    def test_unknown_screen_is_uncertain(self):
        observation = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-ember/key/01-daily-quest-list.png'
        ))

        self.assertEqual('unknown', observation.state)
        self.assertTrue(observation.uncertain)

    def test_battle_turn_is_parsed_when_visible(self):
        observation = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-ember/36-attack-animation.png'
        ))

        self.assertEqual('battle', observation.state)
        self.assertEqual(3, observation.wave)
        self.assertEqual(3, observation.turn)

    def test_grand_formation_is_unknown_and_uncertain(self):
        observation = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-ember/34-grand-support-confirm.png'
        ))

        self.assertEqual('unknown', observation.state)
        self.assertTrue(observation.uncertain)
        self.assertTrue(any('冠位编队' in anchor.text for anchor in observation.anchors))

    def test_retreat_confirmation_is_unknown_and_uncertain(self):
        observation = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-mystic/key/12-retreat-confirm.png'
        ))

        self.assertEqual('unknown', observation.state)
        self.assertTrue(observation.uncertain)
        self.assertTrue(any('撤退' in anchor.text for anchor in observation.anchors))

    def test_ap_recovery_is_unknown_and_uncertain(self):
        observation = self.perception.read(frame_from(
            ROOT / 'captures/2026-11-19-ember/37-ap-recovery.png'
        ))

        self.assertEqual('unknown', observation.state)
        self.assertTrue(observation.uncertain)
        self.assertTrue(any('行动力回复' in anchor.text for anchor in observation.anchors))


if __name__ == '__main__':
    unittest.main()
