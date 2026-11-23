from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.live_action_audit import audit


ROOT = Path(__file__).resolve().parents[3]


def make_job() -> dict:
    job = json.loads((ROOT / 'executor/domain/fixtures/valid-multi-turn.json').read_text(encoding='utf-8'))
    job['waves'] = [{
        'turnCount': 1,
        'target': 2,
        'turns': [{
            'actions': [
                {'kind': 'target', 'title': '目标', 'detail': '', 'icon': '', 'toTarget': 2},
                {'kind': 'skill', 'title': '技能', 'detail': '', 'icon': '',
                 'memberSlot': 1, 'skill': 2, 'playerTargetSlot': 1},
                {'kind': 'attack', 'title': '宝具', 'detail': '', 'icon': '', 'memberSlot': 1},
            ],
            'npPositions': {'1': 2},
            'attack': {'mode': 'verify', 'cards': [
                {'slot': 0, 'type': 'arts'},
                {'slot': 2, 'type': 'buster'},
            ]},
        }],
    }]
    return job


def action(control: str, **data) -> dict:
    return {'type': 'action', 'timestamp': 0.0,
            'data': {'control': control, 'reason': 'battle_action', 'wave': 1, 'turn': 1, **data}}


def card(control: str, position: int, slot: int, card_type: str, x: int) -> dict:
    return action(control, reason='command_card_batch', card_position=position,
                  servant_slot=slot, card_type=card_type, deck_index=None,
                  noble_phantasm=control.startswith('宝具卡'), x=x)


def planned_events() -> list[dict]:
    return [
        action('敌方2'),
        action('从者2技能3'),
        action('攻击'),
        card('卡1', 1, 0, 'arts', 384),
        card('宝具卡2', 2, 1, 'unknown', 635),
        card('卡3', 3, 2, 'buster', 638),
        {'type': 'card_batch_submitted', 'timestamp': 0.0,
         'data': {'batch_attempt': 0, 'wave': 1, 'turn': 1,
                  'controls': ['卡1', '宝具卡2', '卡3']}},
    ]


def write_run(root: Path, events: list[dict], completed: int) -> None:
    (root / 'job.json').write_text(json.dumps(make_job()), encoding='utf-8')
    (root / 'events.jsonl').write_text(
        ''.join(json.dumps(item) + '\n' for item in events), encoding='utf-8')
    (root / 'result.json').write_text(json.dumps({
        'status': 'completed', 'progress': {'completed_runs': completed},
    }), encoding='utf-8')


class LiveActionAuditTest(unittest.TestCase):
    def test_multiple_battles_are_checked_independently(self) -> None:
        events = []
        for battle in (1, 2):
            if battle == 2:
                events.append(action('连续出击'))
            events.extend(planned_events())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, events, 2)

            report = audit(root, root / 'job.json')
            self.assertTrue(report['ok'], report)
            self.assertEqual(2, report['battle_count'])

            events.pop(-6)
            write_run(root, events, 2)
            report = audit(root, root / 'job.json')
            self.assertFalse(report['ok'])
            self.assertFalse(report['battles'][1]['ok'])

    def test_normal_card_order_is_checked(self) -> None:
        events = planned_events()
        events[3], events[5] = events[5], events[3]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, events, 1)

            report = audit(root, root / 'job.json')

            turn = report['battles'][0]['turns'][0]
            self.assertFalse(report['ok'], report)
            self.assertFalse(turn['normal_ok'])
            self.assertTrue(turn['np_ok'])

    def test_np_order_is_checked(self) -> None:
        events = planned_events()
        events[4]['data']['card_position'] = 3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, events, 1)

            report = audit(root, root / 'job.json')

            turn = report['battles'][0]['turns'][0]
            self.assertFalse(report['ok'], report)
            self.assertFalse(turn['np_ok'])


if __name__ == '__main__':
    unittest.main()
