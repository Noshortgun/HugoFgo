from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from executor.session import JsonlEventSink, RunEvent


class JsonlEventSinkTest(unittest.TestCase):
    def test_evidence_module_can_be_imported_before_core(self) -> None:
        result = subprocess.run(
            [sys.executable, '-c', 'from executor.session.evidence import RunEvidence'],
            cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_events_are_written_as_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            sink = JsonlEventSink(path)
            sink.emit(RunEvent('action', 12.5, {'control': '攻击', 'x': 1132}))
            sink.close()

            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

        self.assertEqual([{'type': 'action', 'timestamp': 12.5,
                           'data': {'control': '攻击', 'x': 1132}}], rows)


if __name__ == '__main__':
    unittest.main()
