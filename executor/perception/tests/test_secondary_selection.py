from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from executor.device.base import Frame, FrameFormat
from executor.perception import BasePage, Perception, Variant
from executor.perception.evidence import Template
from executor.perception.tests.fixtures import load_frame

ROOT = Path(__file__).resolve().parents[3]


def marker(gray: int) -> np.ndarray:
    patch = np.full((20, 20), gray // 2, dtype=np.uint8)
    patch[:, 10:] = gray
    return patch


def scene(option_boxes=(), brightness=220, title=True) -> tuple[np.ndarray, dict[str, Template]]:
    image = np.full((720, 1280, 3), 25, dtype=np.uint8)
    templates = {}
    for index, name in enumerate(('battle', 'battle_chrome', 'attack')):
        patch = marker(120 + index * 40)
        x = 10 + index * 30
        image[10:30, x:x + 20] = cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)
        templates[name] = Template(name, patch, (x, 10, x + 20, 30), 0.99)
    if option_boxes and title:
        title_y = min(box[1] for box in option_boxes) - 50
        center_x = round(sum((box[0] + box[2]) / 2 for box in option_boxes) / len(option_boxes))
        cv2.line(image, (center_x - 80, title_y), (center_x + 80, title_y), (230, 230, 230), 4)
    for box in option_boxes:
        x1, y1, x2, y2 = box
        cv2.rectangle(image, (x1, y1), (x2, y2), (brightness, brightness, brightness), -1)
        cv2.rectangle(image, (x1, y1), (x2, y2), (30, 180, 240), 3)
        cv2.line(image, (x1 + 15, (y1 + y2) // 2), (x2 - 15, (y1 + y2) // 2), (20, 20, 20), 3)
    return image, templates


def read(image: np.ndarray, templates: dict[str, Template]):
    with TemporaryDirectory() as directory:
        perception = Perception(templates=templates, emblems={}, identity_root=Path(directory))
        return perception.read(Frame(image.tobytes(), 1280, 720, 0.0, FrameFormat.BGR24))


class SecondarySelectionPerceptionTest(unittest.TestCase):
    def test_two_button_menu_uses_detected_regions(self) -> None:
        boxes = [(380, 250, 560, 390), (720, 250, 900, 390)]
        image, templates = scene(boxes)

        observation = read(image, templates)

        self.assertEqual(BasePage.BATTLE, observation.base_page)
        self.assertIn(Variant.SECONDARY_SELECTION, observation.variants)
        controls = [control for control in observation.controls if control.name.startswith('追加按钮')]
        self.assertEqual(['追加按钮1', '追加按钮2'], [control.name for control in controls])
        self.assertEqual([(470, 320), (810, 320)], [control.center for control in controls])
        self.assertTrue(all(control.actionable for control in controls))

    def test_three_button_menu(self) -> None:
        boxes = [(270, 400, 450, 500), (550, 400, 730, 500), (830, 400, 1010, 500)]
        image, templates = scene(boxes)

        observation = read(image, templates)

        self.assertIn(Variant.SECONDARY_SELECTION, observation.variants)
        self.assertEqual([(360, 450), (640, 450), (920, 450)],
                         [control.center for control in observation.controls if control.name.startswith('追加按钮')])

    def test_disabled_option_is_not_actionable(self) -> None:
        boxes = [(380, 250, 560, 390), (720, 250, 900, 390)]
        image, templates = scene(boxes, brightness=55)
        x1, y1, x2, y2 = boxes[0]
        cv2.rectangle(image, (x1 + 3, y1 + 3), (x2 - 3, y2 - 3), (220, 220, 220), -1)

        observation = read(image, templates)

        controls = [control for control in observation.controls if control.name.startswith('追加按钮')]
        self.assertTrue(controls[0].actionable)
        self.assertFalse(controls[1].actionable)
        self.assertEqual('secondary_option_disabled', controls[1].reason)

    def test_aligned_buttons_without_menu_title_are_rejected(self) -> None:
        image, templates = scene([(380, 250, 560, 390), (720, 250, 900, 390)], title=False)

        observation = read(image, templates)

        self.assertNotIn(Variant.SECONDARY_SELECTION, observation.variants)

    def test_plain_battle_has_no_secondary_selection(self) -> None:
        image, templates = scene()

        observation = read(image, templates)

        self.assertNotIn(Variant.SECONDARY_SELECTION, observation.variants)
        self.assertFalse(any(control.name.startswith('追加按钮') for control in observation.controls))

    def test_real_cn_kukulkan_menu_has_cancel_and_disabled_star_option(self) -> None:
        with TemporaryDirectory() as directory:
            observation = Perception(identity_root=Path(directory)).read(
                load_frame('captures/2026-11-28/key/kukulkan-s1-menu.png'))

        self.assertIn(Variant.SECONDARY_SELECTION, observation.variants)
        controls = [control for control in observation.controls if control.name.startswith('追加按钮')]
        self.assertEqual(['追加按钮1', '追加按钮2', '追加按钮3'], [control.name for control in controls])
        self.assertEqual([True, True, False], [control.actionable for control in controls])
        self.assertEqual('secondary_option_disabled', controls[2].reason)

    def test_real_cn_battle_hud_is_not_a_secondary_menu(self) -> None:
        path = ROOT / 'captures/2026-11-19-ember/key/04-battle-wave1.png'
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)
        frame = Frame(image.tobytes(), image.shape[1], image.shape[0], 0.0, FrameFormat.BGR24)

        with TemporaryDirectory() as directory:
            observation = Perception(identity_root=Path(directory)).read(frame)

        self.assertNotIn(Variant.SECONDARY_SELECTION, observation.variants)
        self.assertFalse(any(control.name.startswith('追加按钮') for control in observation.controls))


if __name__ == '__main__':
    unittest.main()
