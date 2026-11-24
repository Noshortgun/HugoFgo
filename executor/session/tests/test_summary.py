from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from executor.session.summary import write_run_summary


ROOT = Path(__file__).resolve().parents[3]


def make_job() -> dict:
    skills = [
        ('人类魔力工厂 A', 0),
        ('相位滑剑 B+', 1),
        ('原理血戒 B', 2),
    ]
    actions = [
        {
            'kind': 'skill', 'title': f'谜之代行者C.I.E.L · {title}', 'detail': '',
            'icon': '', 'memberSlot': 0, 'skill': skill,
        }
        for title, skill in skills
    ]
    actions.append({
        'kind': 'attack', 'title': '谜之代行者C.I.E.L · 第七圣典·断罪死',
        'detail': '', 'icon': '', 'memberSlot': 0,
    })
    return {
        'version': 1,
        'name': '希耶尔测试作业',
        'formation': [{'slot': 0, 'name': '谜之代行者C.I.E.L'}],
        'waves': [{'turnCount': 1, 'target': 3, 'turns': [{'actions': actions}]}],
    }


def event(event_type: str, timestamp: float, **data) -> dict:
    return {'type': event_type, 'timestamp': timestamp, 'data': data}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class RunSummaryTest(unittest.TestCase):
    def test_stopped_run_distinguishes_clicks_confirmations_and_unexecuted_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / 'job.json', make_job())
            rows = [
                event('action', 98.0, control='助战列表向下', reason='support_search', x=640, y=620),
                event('action', 99.0, control='助战卡片第3行', reason='battle_action',
                      wave=1, turn=1, action_index=0),
                event('action_confirmed', 99.5, stage='page', wait_ms=500.0,
                      wave=1, turn=1, action_index=0),
                event('action', 100.0, control='从者1技能1', reason='battle_action',
                      wave=1, turn=1, action_index=0),
                event('action_confirmed', 101.0, stage='battle_ready', wait_ms=500.0,
                      wave=1, turn=1, action_index=0),
                event('action', 102.0, control='从者1技能2', reason='battle_action',
                      wave=1, turn=1, action_index=1),
                event('action_confirmed', 103.0, stage='battle_ready', wait_ms=500.0,
                      wave=1, turn=1, action_index=1),
                event('action', 104.0, control='从者1技能3', reason='battle_action',
                      wave=1, turn=1, action_index=2),
                event('stopped', 105.0, reason='manual_stop'),
            ]
            original = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
            (root / 'events.jsonl').write_text(original, encoding='utf-8')
            write_json(root / 'result.json', {
                'status': 'stopped', 'reason': 'manual_stop',
                'progress': {'completed_runs': 0}, 'started_at': 100.0, 'finished_at': 105.0,
            })

            output = write_run_summary(root)

            self.assertEqual(root / 'summary.md', output)
            self.assertEqual(original, (root / 'events.jsonl').read_text(encoding='utf-8'))
            text = output.read_text(encoding='utf-8')
            self.assertIn('# 运行动作摘要', text)
            self.assertIn('第 1 场', text)
            self.assertIn('第 1 面 · 第 1 回合', text)
            self.assertIn('人类魔力工厂 A', text)
            self.assertIn('+4.000 秒', text)
            self.assertNotIn('104.0', text)
            third_skill = text.split('原理血戒 B', 1)[1]
            self.assertIn('已点击', third_skill)
            self.assertIn('画面未确认', third_skill)
            self.assertIn('没有独立生效确认', third_skill)
            self.assertNotIn('成功发动', text)
            self.assertNotIn('漏点', text)
            first_skill = text.split('人类魔力工厂 A', 1)[1].split('相位滑剑', 1)[0]
            self.assertIn('画面已确认：战斗恢复可操作', first_skill)
            self.assertIn('已点击：1 次', first_skill)
            self.assertNotIn('助战卡片', first_skill)
            self.assertIn('场次准备与衔接', text)
            self.assertIn('助战列表向下', text)
            support_line = next(line for line in text.splitlines() if '助战卡片第3行' in line)
            self.assertIn('画面已确认', support_line)
            self.assertNotIn('战斗恢复可操作', support_line)
            self.assertIn('运行状态：手动停止', text)
            self.assertIn('完成场数：0', text)
            self.assertIn('未执行的计划动作', text)
            self.assertIn('第七圣典·断罪死', text)

    def test_completed_run_uses_np_card_as_attack_without_creating_an_extra_battle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / 'job.json', make_job())
            rows = [
                event('action', 100.0 + index, control=f'从者1技能{index + 1}',
                      reason='battle_action', wave=1, turn=1, action_index=index)
                for index in range(3)
            ]
            rows.extend([
                event('action', 104.0, control='宝具卡1', reason='command_card_batch',
                      wave=1, turn=1, action_index=4, servant_slot=0, noble_phantasm=True),
                event('action_confirmed', 105.0, stage='attack_end', wave=1, turn=1, action_index=4),
                event('battle_completed', 106.0, completed_runs=1),
                event('finished', 107.0, completed_runs=1),
            ])
            (root / 'events.jsonl').write_text(
                ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
            write_json(root / 'result.json', {
                'status': 'completed', 'reason': 'completed',
                'progress': {'completed_runs': 1}, 'started_at': 100.0, 'finished_at': 107.0,
            })

            text = write_run_summary(root).read_text(encoding='utf-8')

            self.assertNotIn('第 2 场', text)
            attack = text.split('第七圣典·断罪死', 1)[1]
            self.assertIn('已点击：1 次', attack)
            self.assertIn('画面已确认：攻击后画面已出现', attack)
            self.assertIn('无（按现有事件记录）', text)

    def test_cli_regenerates_summary_and_missing_events_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / 'input-job.json'
            write_json(job, make_job())
            write_json(root / 'result.json', {
                'status': 'failed', 'reason': 'process_error',
                'progress': {'completed_runs': 0},
            })

            completed = subprocess.run(
                [sys.executable, '-m', 'executor.session.summary', str(root), '--job', str(job)],
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            text = (root / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('缺少 events.jsonl', text)
            self.assertIn('无法判定', text)
            self.assertIn('运行状态：失败停止', text)
            self.assertFalse((root / 'events.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
