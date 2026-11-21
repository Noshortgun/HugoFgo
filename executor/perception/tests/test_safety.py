"""覆盖层、残留元素与“第三张卡之后”的动作安全。"""
from __future__ import annotations

import unittest

from executor.perception import Perception
from executor.perception.tests.fixtures import load_frame


class SafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.perception = Perception(ocr_reader=None)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.perception.close()

    def read(self, relative: str):
        return self.perception.read(load_frame(relative))

    def test_third_card_submitted_stops_card_selection(self) -> None:
        for relative in (
            'captures/2026-11-19/13-cards-selected.png',
            'captures/2026-11-19/19-cards2-selected.png',
            'captures/2026-11-19/25-cards3-selected.png',
        ):
            with self.subTest(relative=relative):
                observation = self.read(relative)
                self.assertEqual('battle', observation.base_page.value)
                self.assertEqual({'animation'}, {item.value for item in observation.variants})
                self.assertEqual((), observation.controls)
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

        self.assertEqual({'skill_target'}, {item.value for item in skill.variants})
        self.assertNotIn('command_spell_target', {item.value for item in skill.overlays})
        self.assertIn('command_spell_target', {item.value for item in spell.overlays})
        self.assertEqual(frozenset(), spell.variants)
        for name in ('目标从者1', '目标从者2', '目标从者3'):
            self.assertTrue(skill.control(name).actionable, name)

    def test_result_page_keeps_battle_residue_out_of_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/result.png')

        self.assertEqual('result', observation.base_page.value)
        self.assertGreaterEqual(observation.evidence['battle'], 0.58)
        self.assertEqual((), observation.controls)
        self.assertFalse(observation.actionable)

    def test_master_skills_does_not_block_battle_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/60-current-master-expanded.png')

        self.assertEqual('battle', observation.base_page.value)
        for name in ('攻击', '战斗菜单', '令咒', '从者1技能1', '敌方1', '御主技能3'):
            self.assertTrue(observation.control(name).actionable, name)

    def test_modal_overlay_hides_base_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/70-command-spell-open.png')

        for name in ('攻击', '战斗菜单', '令咒', '从者1技能1'):
            self.assertIsNone(observation.control(name), name)
        for name in ('宝具解放', '灵基修复', '关闭'):
            self.assertTrue(observation.control(name).actionable, name)

    def test_card_page_only_offers_confirmed_cards(self) -> None:
        observation = self.read('captures/2026-11-20/key/card-select.png')

        cards = [control for control in observation.controls if control.kind.value == 'card']
        self.assertEqual(5, len(cards))
        self.assertTrue(all(control.actionable for control in cards))
        self.assertTrue(all(control.center[1] > 400 for control in cards))

    def test_attack_animation_exposes_no_controls(self) -> None:
        observation = self.read('captures/2026-11-19-ember/36-attack-animation.png')

        self.assertEqual('battle', observation.base_page.value)
        self.assertEqual({'animation'}, {item.value for item in observation.variants})
        self.assertEqual((), observation.controls)
        self.assertFalse(observation.actionable)

    def test_order_change_needs_both_selections(self) -> None:
        waiting = self.read('captures/2026-11-20/key/61-order-change-select.png')
        ready = self.read('captures/2026-11-19-mystic/key/07-swap-in-selected.png')

        self.assertFalse(waiting.control('进行更替').actionable)
        self.assertTrue(ready.control('进行更替').actionable)


if __name__ == '__main__':
    unittest.main()
