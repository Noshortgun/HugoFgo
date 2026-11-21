from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from executor.replay import get_case, main, run_case


class ExecutorReplayTest(unittest.TestCase):
    def test_support_to_completion_replays_real_cn_screens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('full-loop'), Path(directory))

        self.assertEqual('completed', report.result.status.value)
        self.assertEqual('completed', report.result.reason)
        self.assertEqual([
            '助战卡片第2行', '开始任务', '攻击', '卡1', '卡2', '卡4',
            '结算推进', '结算推进', '下一步',
        ], [event.data['control'] for event in report.events.events if event.type == 'action'])
        self.assertNotIn('连续出击', report.device.actions)

    def test_cli_returns_zero_and_writes_a_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'

            exit_code = main(['--only', 'full-loop', '--report', str(output)])

            self.assertEqual(0, exit_code)
            self.assertTrue(output.is_file())


if __name__ == '__main__':
    unittest.main()
