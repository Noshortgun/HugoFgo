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
    def test_region_is_required_and_only_cn_is_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = command(Path(directory) / 'run', '--fake-interval', '0')
            missing = [arg for arg in base if arg not in {'--region', 'CN'}]
            result = subprocess.run(
                missing, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn('--region', result.stderr)

            for region in ('JP', 'TW'):
                with self.subTest(region=region):
                    result = subprocess.run(
                        [region if arg == 'CN' else arg for arg in base],
                        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
                    )
                    self.assertEqual(2, result.returncode)
                    self.assertIn('当前仅支持 CN', result.stderr)

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

    def test_completed_run_appends_a_persistent_history_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / 'logs' / 'farm' / 'run-completed'
            result = subprocess.run(
                command(run_dir, '--fake-interval', '0'),
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            history = root / 'logs' / 'run-history.jsonl'
            records = [json.loads(line) for line in history.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(1, len(records))
            record = records[0]
            self.assertEqual('run-completed', record['id'])
            self.assertEqual('换人 + 固定卡序 测试样本', record['jobName'])
            self.assertEqual(str(JOB.resolve()), record['jobFile'])
            self.assertEqual('fake', record['serial'])
            self.assertEqual('CN', record['region'])
            self.assertEqual('completed', record['result'])
            self.assertEqual(1, record['battlesCompleted'])
            self.assertEqual({}, record['recoveryUsed'])
            self.assertEqual(str(run_dir.resolve()), record['runDir'])
            self.assertIsNone(record['error'])
            self.assertGreaterEqual(record['finishedAt'], record['startedAt'])
            self.assertEqual(record['finishedAt'] - record['startedAt'], record['durationMs'])

    def test_stdin_stop_ends_fake_run_without_later_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                command(Path(directory) / 'logs' / 'farm' / 'run-stopped', '--fake-interval', '5'),
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8',
            )
            stdout, stderr = process.communicate('stop\n', timeout=20)

            self.assertEqual(1, process.returncode, stderr)
            rows = [json.loads(line) for line in stdout.splitlines()]
            self.assertEqual('stopped', rows[-1]['type'])
            self.assertEqual('manual_stop', rows[-1]['data']['reason'])
            self.assertFalse(any(row['type'] == 'action' for row in rows))
            history = Path(directory) / 'logs' / 'run-history.jsonl'
            record = json.loads(history.read_text(encoding='utf-8').strip())
            self.assertEqual('stopped', record['result'])
            self.assertEqual('manual_stop', record['reason'])
            self.assertIsNone(record['error'])

    def test_failed_run_records_the_error_and_evidence_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / 'logs' / 'farm' / 'run-failed'
            result = subprocess.run(
                [str(root / 'missing.json') if arg == str(JOB) else arg for arg in command(run_dir)],
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
            )

            self.assertEqual(1, result.returncode)
            history = root / 'logs' / 'run-history.jsonl'
            record = json.loads(history.read_text(encoding='utf-8').strip())
            self.assertEqual('failed', record['result'])
            self.assertEqual('process_error', record['reason'])
            self.assertTrue(record['error'])
            self.assertEqual(str(run_dir.resolve()), record['runDir'])


if __name__ == '__main__':
    unittest.main()
