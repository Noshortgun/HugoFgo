from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from executor.session.performance import summarize_runs


def action(timestamp: float, control: str, **data) -> dict:
    return {'type': 'action', 'timestamp': timestamp,
            'data': {'control': control, 'wave': 1, 'turn': 1, **data}}


def event(event_type: str, timestamp: float, **data) -> dict:
    return {'type': event_type, 'timestamp': timestamp, 'data': data}


def write_run(root: Path, name: str, events: list[dict]) -> Path:
    directory = root / name
    directory.mkdir()
    (directory / 'events.jsonl').write_text(
        ''.join(json.dumps(item) + '\n' for item in events), encoding='utf-8')
    (directory / 'result.json').write_text(json.dumps({
        'status': 'completed', 'reason': 'completed', 'device': {'channel': 'adb'},
    }), encoding='utf-8')
    return directory


class BattlePerformanceTest(unittest.TestCase):
    def test_same_metric_covers_legacy_and_batched_event_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batched = write_run(root, 'batched', [
                action(10.0, '攻击', input_finished_at=10.05),
                action(10.55, '宝具卡1', reason='command_card_batch', batch_id=1,
                       batch_attempt=0, card_position=1, attack_to_first_card_ms=500.0),
                action(10.75, '卡1', reason='command_card_batch', batch_id=1,
                       batch_attempt=0, card_position=2, interval_ms=200.0),
                action(11.05, '卡2', reason='command_card_batch', batch_id=1,
                       batch_attempt=0, card_position=3, interval_ms=300.0),
                event('card_batch_submitted', 11.05, batch_id=1, batch_attempt=0,
                      configured_interval_ms=200.0),
                event('action_confirmed', 12.0, stage='attack_end'),
                action(20.0, '攻击', input_finished_at=20.0),
                action(20.8, '卡1', reason='command_card_batch', batch_id=2,
                       batch_attempt=0, card_position=1, attack_to_first_card_ms=800.0),
                action(21.0, '宝具卡1', reason='command_card_batch', batch_id=2,
                       batch_attempt=0, card_position=2, interval_ms=200.0),
                action(21.3, '卡2', reason='command_card_batch', batch_id=2,
                       batch_attempt=0, card_position=3, interval_ms=300.0),
                event('card_batch_submitted', 21.3, batch_id=2, batch_attempt=0,
                      configured_interval_ms=200.0),
                event('warning', 23.3, reason='action_recovery', control='command_card_batch'),
                event('card_batch_submitted', 24.0, batch_id=3, batch_attempt=1,
                      configured_interval_ms=200.0),
                event('action_confirmed', 25.0, stage='attack_end'),
                event('finished', 26.0),
            ])
            legacy = write_run(root, 'legacy', [
                action(30.0, '攻击'),
                action(31.2, '宝具卡1', reason='stable_actionable'),
                action(31.7, '卡1', reason='stable_actionable'),
                action(32.0, '卡2', reason='stable_actionable'),
                action(40.0, '攻击'),
                action(40.6, '卡1', reason='stable_actionable'),
                action(40.8, '卡2', reason='stable_actionable'),
                action(41.2, '卡3', reason='stable_actionable'),
                event('finished', 42.0),
            ])

            report = summarize_runs([batched, legacy])

        self.assertEqual(2, report['run_count'])
        self.assertEqual(8, report['normal_card_intervals']['count'])
        self.assertEqual(300.0, report['normal_card_intervals']['median_ms'])
        self.assertEqual(4, report['attack_to_first_card']['count'])
        self.assertAlmostEqual(700.0, report['attack_to_first_card']['median_ms'])
        self.assertTrue(report['timing_thresholds_pass'])
        self.assertEqual(2, report['initial_card_batches']['total'])
        self.assertEqual(1, report['initial_card_batches']['succeeded'])
        self.assertEqual(1, report['initial_card_batches']['recovered'])
        self.assertEqual(0.5, report['initial_card_batches']['success_rate'])

    def test_thresholds_include_exact_boundaries_and_reject_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary = write_run(root, 'boundary', [
                action(1.0, '攻击'),
                action(2.0, '卡1', attack_to_first_card_ms=1000.0),
                action(2.45, '卡2', interval_ms=450.0),
                event('finished', 3.0),
            ])
            overflow = write_run(root, 'overflow', [
                action(1.0, '攻击'),
                action(2.0, '卡1', attack_to_first_card_ms=1000.01),
                action(2.45, '卡2', interval_ms=450.0),
                event('finished', 3.0),
            ])

            self.assertTrue(summarize_runs([boundary])['timing_thresholds_pass'])
            self.assertFalse(summarize_runs([overflow])['timing_thresholds_pass'])

    def test_empty_or_missing_samples_never_pass_performance_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = write_run(Path(directory), 'empty', [event('finished', 1.0)])

            report = summarize_runs([run_dir])

        self.assertFalse(report['timing_thresholds_pass'])
        self.assertIsNone(report['normal_card_intervals']['median_ms'])
        self.assertIsNone(report['attack_to_first_card']['median_ms'])
        self.assertIsNone(report['initial_card_batches']['success_rate'])


if __name__ == '__main__':
    unittest.main()
