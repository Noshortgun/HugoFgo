from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from executor.perception import BasePage, Control, ControlKind, Observation
from executor.perception.__main__ import main
from executor.perception.readiness import readiness_gaps


class ReadinessTest(unittest.TestCase):
    def test_executor_inputs_are_reported_as_missing(self) -> None:
        support = Observation(base_page=BasePage.SUPPORT, controls=(
            Control('助战卡片第1行', ControlKind.ROW, (0, 0, 100, 100), True),))
        card = Observation(base_page=BasePage.CARD, controls=(
            Control('卡1', ControlKind.CARD, (0, 0, 100, 100), True),))
        battle = Observation(base_page=BasePage.BATTLE)
        result = Observation(base_page=BasePage.RESULT)

        self.assertEqual(['support_identity'], readiness_gaps(support))
        self.assertEqual(['command_card_identity'], readiness_gaps(card))
        self.assertEqual(['wave_turn', 'turn_boundary'], readiness_gaps(battle))
        self.assertEqual(['result_control'], readiness_gaps(result))
        self.assertEqual(['known_screen'], readiness_gaps(Observation()))

    def test_real_frames_report_executor_gaps(self) -> None:
        cases = (
            ('captures/2026-11-20/key/support-select-correct-ce.png', 'support_identity'),
            ('captures/2026-11-20/key/card-select.png', 'command_card_identity'),
            ('captures/2026-11-20/key/battle-wave1.png', 'turn_boundary'),
            ('captures/2026-11-20/key/result.png', 'result_control'),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = main([path, '--ready', '--json'])
                payload = json.loads(output.getvalue())
                self.assertEqual(2, exit_code)
                self.assertFalse(payload['ready'])
                self.assertIn(expected, payload['missing'])


if __name__ == '__main__':
    unittest.main()
