"""覆盖层、残留元素与“第三张卡之后”的动作安全。"""
from __future__ import annotations

import unittest
from pathlib import Path

from executor.device.base import Frame
from executor.perception import Perception

ROOT = Path(__file__).resolve().parents[3]


def load(relative: str) -> Frame:
    return Frame(image=(ROOT / relative).read_bytes(), width=1280, height=720, captured_at=0.0)


class SafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.perception = Perception(ocr_reader=None)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.perception.close()

    def read(self, relative: str):
        return self.perception.read(load(relative))

    def test_third_card_submitted_stops_card_selection(self) -> None:
        for relative in (
            'captures/2026-11-19/13-cards-selected.png',
            'captures/2026-11-19/19-cards2-selected.png',
            'captures/2026-11-19/25-cards3-selected.png',
        ):
            with self.subTest(relative=relative):
                observation = self.read(relative)
                self.assertEqual('battle', observation.base_page.value)
                for name in ('卡1', '卡2', '卡3', '卡4', '卡5', '攻击'):
                    control = observation.control(name)
                    self.assertTrue(control is None or not control.actionable,
                                    f'{relative} 仍输出可点的 {name}')

    def test_retreat_confirm_is_not_battle_menu(self) -> None:
        observation = self.read('captures/2026-11-19-mystic/key/12-retreat-confirm.png')

        self.assertEqual({'retreat_confirm'}, {item.value for item in observation.overlays})
        self.assertIsNone(observation.control('撤退'))
        self.assertTrue(observation.control('决定').actionable)
        self.assertTrue(observation.control('取消').actionable)

    def test_skill_target_is_not_command_spell_target(self) -> None:
        skill = self.read('captures/2026-11-19-ember/key/09-skill-target.png')
        spell = self.read('captures/2026-11-20/key/72-command-spell-target-select.png')

        self.assertNotIn('command_spell_target', {item.value for item in skill.overlays})
        self.assertIn('command_spell_target', {item.value for item in spell.overlays})
        self.assertIsNone(skill.control('目标从者1'))

    def test_result_page_keeps_battle_residue_out_of_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/result.png')

        self.assertEqual('result', observation.base_page.value)
        self.assertGreaterEqual(observation.evidence['battle'], 0.58)
        self.assertEqual((), observation.controls)
        self.assertFalse(observation.actionable)

    def test_overlay_blocks_base_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/60-current-master-expanded.png')

        self.assertEqual('battle', observation.base_page.value)
        for name in ('攻击', '战斗菜单', '御主技能', '令咒'):
            self.assertFalse(observation.control(name).actionable, name)
        self.assertTrue(observation.control('御主技能3').actionable)

    def test_card_page_only_offers_confirmed_cards(self) -> None:
        observation = self.read('captures/2026-11-20/key/card-select.png')

        cards = [control for control in observation.controls if control.kind.value == 'card']
        self.assertEqual(5, len(cards))
        self.assertTrue(all(control.actionable for control in cards))
        self.assertTrue(all(control.center[1] > 400 for control in cards))

    def test_battle_without_attack_anchor_is_not_clickable(self) -> None:
        observation = self.read('captures/2026-11-19-ember/36-attack-animation.png')

        self.assertEqual('battle', observation.base_page.value)
        attack = observation.control('攻击')
        self.assertIsNotNone(attack)
        self.assertFalse(attack.actionable)


if __name__ == '__main__':
    unittest.main()
