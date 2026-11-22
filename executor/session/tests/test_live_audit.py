from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.live_action_audit import audit


class LiveActionAuditTest(unittest.TestCase):
    def test_multiple_battles_are_checked_independently(self) -> None:
        job = {
            'waves': [{'turns': [{'actions': [
                {'kind': 'target', 'toTarget': 2},
                {'kind': 'skill', 'memberSlot': 1, 'skill': 2, 'playerTargetSlot': 1},
                {'kind': 'attack', 'memberSlot': 1},
            ]}]}],
        }
        events = []

        def add_action(battle: int, control: str, **data) -> None:
            events.append({'type': 'action', 'timestamp': float(len(events)),
                           'data': {'control': control, 'reason': 'battle_action',
                                    'wave': 1, 'turn': 1, **data}})

        for battle in (1, 2):
            if battle == 2:
                add_action(battle, '连续出击')
            add_action(battle, '敌方2')
            add_action(battle, '从者2技能3')
            add_action(battle, '攻击')
            add_action(battle, '宝具卡2', reason='command_card_batch', card_position=1, x=635)
            add_action(battle, '卡2', reason='command_card_batch', card_position=2, x=384)
            add_action(battle, '卡3', reason='command_card_batch', card_position=3, x=638)
            events.append({'type': 'card_batch_submitted', 'timestamp': float(len(events)),
                           'data': {'batch_attempt': 0, 'wave': 1, 'turn': 1,
                                    'controls': ['宝具卡2', '卡2', '卡3']}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'job.json').write_text(json.dumps(job), encoding='utf-8')
            (root / 'events.jsonl').write_text(
                ''.join(json.dumps(item) + '\n' for item in events), encoding='utf-8')
            (root / 'result.json').write_text(json.dumps({
                'status': 'completed', 'progress': {'completed_runs': 2},
            }), encoding='utf-8')

            report = audit(root, root / 'job.json')
            self.assertTrue(report['ok'], report)
            self.assertEqual(2, report['battle_count'])

            events.pop(-6)
            (root / 'events.jsonl').write_text(
                ''.join(json.dumps(item) + '\n' for item in events), encoding='utf-8')
            report = audit(root, root / 'job.json')
            self.assertFalse(report['ok'])
            self.assertFalse(report['battles'][1]['ok'])


if __name__ == '__main__':
    unittest.main()
