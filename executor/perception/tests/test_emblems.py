"""卡种纹章判据：用合成图覆盖 Buster（红色）路径，真实截图只覆盖了 Quick/Arts。"""
from __future__ import annotations

import unittest

import cv2
import numpy as np

from executor.perception import evidence


def canvas() -> np.ndarray:
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def draw_blob(image: np.ndarray, center: tuple[int, int], size: tuple[int, int],
              bgr: tuple[int, int, int]) -> None:
    cv2.ellipse(image, center, (size[0] // 2, size[1] // 2), 0, 0, 360, bgr, -1)


def measure(image: np.ndarray):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scores, blobs, _faces = evidence.measure_np_slots(image, gray, {})
    return scores, blobs


class EmblemTest(unittest.TestCase):
    def test_red_buster_emblem_is_detected(self) -> None:
        image = canvas()
        draw_blob(image, (412, 300), (90, 60), (0, 0, 220))

        scores, blobs = measure(image)

        self.assertTrue(blobs['A'])
        self.assertFalse(blobs['B'])
        self.assertFalse(blobs['C'])
        self.assertEqual(0.0, scores['B'])

    def test_small_or_low_red_marker_is_not_a_card_emblem(self) -> None:
        image = canvas()
        draw_blob(image, (412, 380), (50, 45), (0, 0, 220))
        draw_blob(image, (640, 75), (90, 60), (0, 0, 220))

        _, blobs = measure(image)

        self.assertFalse(blobs['A'])
        self.assertFalse(blobs['B'])

    def test_oversized_effect_ring_is_not_a_card_emblem(self) -> None:
        image = canvas()
        draw_blob(image, (412, 290), (240, 160), (0, 200, 0))

        _, blobs = measure(image)

        self.assertFalse(blobs['A'])

    def test_green_and_blue_emblems_are_detected_in_their_slots(self) -> None:
        image = canvas()
        draw_blob(image, (412, 300), (120, 60), (0, 200, 0))
        draw_blob(image, (640, 300), (120, 60), (220, 0, 0))

        _, blobs = measure(image)

        self.assertTrue(blobs['A'])
        self.assertTrue(blobs['B'])
        self.assertFalse(blobs['C'])


class SkillCooldownTest(unittest.TestCase):
    def test_only_cooling_icons_have_the_remaining_label(self) -> None:
        from executor.perception.regions import cn
        from executor.perception.tests.fixtures import load_frame

        emblem = evidence.load_emblems()['skill_cooldown']
        cases = {
            'captures/2026-11-23/key/skill-cooldowns.png': {'1:3', '2:3', '3:2', '3:3'},
            'captures/2026-11-23/key/skill1-cooldown.png': {'1:3', '2:1', '2:3', '3:2', '3:3'},
            'captures/2026-11-20/key/battle-wave1.png': set(),
        }
        for file, expected in cases.items():
            with self.subTest(file=file):
                gray = cv2.cvtColor(evidence.decode_frame(load_frame(file)), cv2.COLOR_BGR2GRAY)
                scores = evidence.measure_skill_cooldowns(gray, emblem)
                cooling = {name for name, score in scores.items()
                           if score >= cn.SKILL_COOLDOWN_THRESHOLD}
                self.assertEqual(expected, cooling)


if __name__ == '__main__':
    unittest.main()