from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from executor.session import RunEvent
from executor.session.history import RunHistoryRecorder


class RunHistoryRecorderTest(unittest.TestCase):
    def test_recovery_counts_are_captured_once_at_the_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / 'logs/farm/run-1'
            recorder = RunHistoryRecorder(run_dir, job_name='每日宝库', job_file='jobs/daily.json',
                                          serial='emulator-5554', region='CN')
            recorder.emit(RunEvent('recovery_used', time.time(), {'item': 'bronze', 'count': 1}))
            recorder.emit(RunEvent('recovery_used', time.time(), {'item': 'copper', 'count': 2}))
            recorder.emit(RunEvent('finished', time.time(), {'completed_runs': 3}))
            recorder.emit(RunEvent('finished', time.time(), {'completed_runs': 3}))

            records = [json.loads(line) for line in recorder.history_path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(1, len(records))
            self.assertEqual({'bronze': 1, 'copper': 2}, records[0]['recoveryUsed'])
            self.assertEqual(3, records[0]['battlesCompleted'])


if __name__ == '__main__':
    unittest.main()
