"""28 屏回归集与跨会话截图的逐屏断言。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from executor.perception.tests.fixtures import isolated_perception, load_frame

FIXTURES = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'screens-cn.json')
                      .read_text(encoding='utf-8'))


class ScreenRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.perception = isolated_perception(cls.addClassCleanup)

    def check(self, case: dict) -> None:
        observation = self.perception.read(load_frame(case['file']))
        self.assertEqual(case['base'], observation.base_page.value, case['file'])
        self.assertEqual(sorted(case['variants']), sorted(item.value for item in observation.variants),
                         case['file'])
        self.assertEqual(sorted(case['overlays']), sorted(item.value for item in observation.overlays),
                         case['file'])
        self.assertEqual(case['actionable'], observation.actionable, case['file'])
        for name in case.get('absentControls', ()):
            control = observation.control(name)
            self.assertTrue(control is None or not control.actionable,
                            f"{case['file']} 不应可点：{name}")
        for name in case.get('actionableControls', ()):
            control = observation.control(name)
            self.assertIsNotNone(control, f"{case['file']} 缺少控件：{name}")
            self.assertTrue(control.actionable, f"{case['file']} 应可点：{name}")

    def test_regression_set_28_screens(self) -> None:
        cases = FIXTURES['regression']
        self.assertEqual(28, len(cases))
        for case in cases:
            with self.subTest(file=case['file']):
                self.check(case)

    def test_cross_session_captures(self) -> None:
        for case in FIXTURES['crossSession']:
            with self.subTest(file=case['file']):
                self.check(case)


if __name__ == '__main__':
    unittest.main()
