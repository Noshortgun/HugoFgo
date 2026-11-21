from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / 'examples/sample-job.json'


def command(run_dir: Path, *extra: str) -> list[str]:
    return [
        sys.executable, '-m', 'executor', str(JOB),
        '--serial', 'fake', '--region', 'CN', '--run-dir', str(run_dir),
        '--battles', '1', '--recovery', 'bronze', '--fake', *extra,
    ]


class ExecutorProcessTest(unittest.TestCase):
    def test_fake_mode_emits_utf8_json_lines_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / 'run'
            result = subprocess.run(
                command(run_dir, '--fake-interval', '0'),
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            types = [row['type'] for row in rows]
            self.assertIn('run_started', types)
            self.assertIn('state_changed', types)
            self.assertIn('action', types)
            self.assertEqual('finished', types[-1])
            self.assertEqual(1, rows[-1]['data']['completed_runs'])
            self.assertTrue((run_dir / 'events.jsonl').is_file())

    def test_stdin_stop_ends_fake_run_without_later_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                command(Path(directory) / 'run', '--fake-interval', '5'),
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8',
            )
            stdout, stderr = process.communicate('stop\n', timeout=20)

            self.assertEqual(1, process.returncode, stderr)
            rows = [json.loads(line) for line in stdout.splitlines()]
            self.assertEqual('stopped', rows[-1]['type'])
            self.assertEqual('manual_stop', rows[-1]['data']['reason'])
            self.assertFalse(any(row['type'] == 'action' for row in rows))


if __name__ == '__main__':
    unittest.main()
