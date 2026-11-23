from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest import mock

from executor.device import Frame
from executor.perception import Perception
from executor.replay import manual_job_plan
from executor.session import RunEvent
from scripts.live_monitor import MonitorEvents, MonitorPerception, MonitorState

ROOT = Path(__file__).resolve().parents[3]


class LiveMonitorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = MonitorState(manual_job_plan())
        self.perception = Perception()
        self.monitored = MonitorPerception(self.perception, self.state)
        self.monitored.bind_formation(self.state.plan.formation)
        self.monitored.bind_mystic(self.state.plan.mystic_id)
        self.frame = Frame(
            image=(ROOT / 'captures/2026-11-23/key/manual-w2-t1-cards-14.png').read_bytes(),
            width=1280, height=720, captured_at=time.time())

    def tearDown(self) -> None:
        self.perception.close()

    def test_real_frame_updates_observation_history_and_image(self) -> None:
        self.monitored.read(self.frame)
        self.monitored.read_fields(self.frame, ['crit'], timeout=0)

        snapshot = self.state.snapshot()
        self.assertEqual('ready', snapshot['status'])
        self.assertEqual((10, 100, 80, 30, 90), snapshot['observation']['fields']['crit']['value'])
        self.assertEqual(2, snapshot['revision'])
        self.assertEqual(2, len(snapshot['history']))
        self.assertNotIn('image', snapshot['history'][-1])
        self.assertTrue(self.state.image.startswith(b'\xff\xd8'))
        self.assertTrue(self.state.history_image(snapshot['history'][-1]['id']).startswith(b'\xff\xd8'))
        json.dumps(snapshot, ensure_ascii=False)

    def test_action_event_updates_decision_and_keyframe(self) -> None:
        self.monitored.read(self.frame)
        MonitorEvents(self.state).emit(RunEvent('action', time.time(), {
            'control': '卡4', 'reason': 'command_card_batch', 'wave': 2, 'turn': 2,
            'action_index': 0, 'card_position': 1, 'x': 893, 'y': 535,
        }))

        snapshot = self.state.snapshot()
        self.assertEqual('卡4', snapshot['decision']['data']['control'])
        self.assertEqual('卡4', snapshot['history'][-1]['decision']['data']['control'])
        self.assertEqual('执行动作', snapshot['events'][-1]['label'])

    def test_monitor_annotation_failure_does_not_interrupt_execution(self) -> None:
        with mock.patch('scripts.live_monitor.annotate', side_effect=ValueError('monitor boom')):
            observation = self.monitored.read(self.frame)

        self.assertEqual('card', observation.base_page.value)
        snapshot = self.state.snapshot()
        self.assertEqual('ready', snapshot['status'])
        self.assertIn('monitor boom', snapshot['monitor_error'])
        self.assertTrue(self.state.image.startswith(b'\xff\xd8'))

    def test_perception_stats_are_forwarded(self) -> None:
        self.monitored.read(self.frame)
        self.monitored.read_fields(self.frame, ['crit'], timeout=0)
        self.monitored.read_fields(self.frame, ['crit'], timeout=0)

        stats = self.monitored.stats()
        self.assertEqual(1, stats['field_reads'])
        self.assertEqual(1, stats['field_cache_hits'])


if __name__ == '__main__':
    unittest.main()
