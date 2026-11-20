"""画面感知的离线验证：用 captures/2026-11-20 的 25 张实机截图逐屏断言。"""
from __future__ import annotations

import unittest
from pathlib import Path

import cv2
import numpy as np

from executor.device.base import Frame
from executor.perception import Perception, ScreenState

ROOT = Path(__file__).resolve().parents[3]
KEY_DIR = ROOT / 'captures/2026-11-20/key'

EXPECTED_STATES = {
    'battle-wave1': 'battle',
    'battle-target-enemy2': 'battle',
    'battle-slot1-skill3-cooldown': 'battle',
    'battle-after-cards': 'battle',
    '64-order-change-done': 'battle',
    'card-select': 'card',
    'card-np-selected': 'card',
    'support-select-correct-ce': 'support',
    'support-select-wrong-ce': 'support',
    'support-list-update-confirm': 'support_confirm',
    'team-confirm': 'team',
    'result': 'result',
    'result-exp': 'result',
    'drops': 'drops',
    'repeat-dialog': 'repeat_dialog',
    'ap-recovery': 'ap_recovery',
    'ap-recovery-bronze': 'ap_recovery',
    '61-order-change-select': 'order_change',
    '70-command-spell-open': 'command_spell_menu',
    '72-command-spell-target-select': 'command_spell_target',
    '73-command-spell-confirm': 'command_spell_confirm',
    '09-class-filter': 'class_info',
    '55-battle-menu': 'battle_menu',
    '60-current-master-expanded': 'master_skills',
    '12-battle-start': 'loading',
}

_observations: dict[str, object] = {}
_perception: Perception | None = None


def fixture(name: str) -> Frame:
    data = (KEY_DIR / f'{name}.jpg').read_bytes()
    return Frame(image=data, width=1280, height=720, captured_at=0.0)


def observe(name: str):
    global _perception
    if name not in _observations:
        if _perception is None:
            _perception = Perception()
        _observations[name] = _perception.read(fixture(name))
    return _observations[name]


def synthetic(image: np.ndarray) -> Frame:
    ok, buffer = cv2.imencode('.png', image)
    assert ok
    return Frame(image=buffer.tobytes(), width=image.shape[1],
                 height=image.shape[0], captured_at=0.0)


class PerceptionStateTest(unittest.TestCase):
    def test_every_captured_screen_is_classified(self):
        self.assertTrue(KEY_DIR.is_dir(), f'缺少验证截图：{KEY_DIR}')
        for name, expected in EXPECTED_STATES.items():
            with self.subTest(screen=name):
                observation = observe(name)
                self.assertEqual(expected, observation.state.value)
                self.assertFalse(observation.uncertain)


class PerceptionFieldTest(unittest.TestCase):
    def test_battle_reads_wave_turn_and_target(self):
        first = observe('battle-wave1')
        self.assertEqual(1, first.fields['wave'])
        self.assertEqual(3, first.fields['wave_total'])
        self.assertEqual(1, first.fields['turn'])
        self.assertEqual(3, first.fields['enemies_left'])

        second = observe('battle-after-cards')
        self.assertEqual(2, second.fields['wave'])
        self.assertEqual(2, second.fields['turn'])

    def test_target_marker_moves_left_when_switching(self):
        initial = observe('battle-wave1').fields['target_x']
        switched = observe('battle-target-enemy2').fields['target_x']
        self.assertGreater(initial - switched, 100)

    def test_repeat_dialog_reads_ap(self):
        observation = observe('repeat-dialog')
        self.assertEqual(23, observation.fields['ap_current'])
        self.assertEqual(144, observation.fields['ap_max'])
        self.assertEqual(5, observation.fields['ap_cost'])

    def test_ap_recovery_lists_available_items(self):
        bronze = observe('ap-recovery-bronze')
        self.assertIn('青铜果实', bronze.fields['items'])
        self.assertNotIn('圣晶石', bronze.fields['items'])

        unscrolled = observe('ap-recovery')
        self.assertIn('圣晶石', unscrolled.fields['items'])
        self.assertNotIn('青铜果实', unscrolled.fields['items'])

    def test_card_slots_carry_crit_and_affinity(self):
        observation = observe('card-np-selected')
        self.assertEqual(5, len(observation.cards))
        for card in observation.cards:
            self.assertEqual('20%', card.crit_star)
        # 四张有克制，第四张的标签被 OCR 读成“克利”，归一后仍是克制
        self.assertEqual('克制', observation.cards[0].affinity)
        self.assertEqual('克制', observation.cards[3].affinity)


class PerceptionButtonTest(unittest.TestCase):
    def test_support_exposes_list_update_button(self):
        observation = observe('support-select-correct-ce')
        self.assertIsNotNone(observation.button('列表更新'))

    def test_command_spell_confirm_exposes_cancel_and_decide(self):
        observation = observe('73-command-spell-confirm')
        self.assertIsNotNone(observation.button('取消'))
        self.assertIsNotNone(observation.button('决定（会真的消耗1划）'))

    def test_battle_exposes_nine_skill_buttons(self):
        observation = observe('battle-wave1')
        skills = [button for button in observation.buttons
                  if '号位' in button.label and '技能' in button.label]
        self.assertEqual(9, len(skills))


class PerceptionFallbackTest(unittest.TestCase):
    def test_blank_frame_is_unknown(self):
        observation = Perception().read(synthetic(np.zeros((720, 1280, 3), dtype=np.uint8)))
        self.assertIs(ScreenState.UNKNOWN, observation.state)
        self.assertTrue(observation.uncertain)

    def test_unknown_has_no_buttons(self):
        observation = Perception().read(synthetic(np.zeros((720, 1280, 3), dtype=np.uint8)))
        self.assertEqual((), observation.buttons)


if __name__ == '__main__':
    unittest.main()
