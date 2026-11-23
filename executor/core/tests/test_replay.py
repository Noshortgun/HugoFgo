from __future__ import annotations

import json
import os
import subprocess
import sys
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
            '助战卡片第2行', '开始任务', '攻击', '卡1', '卡2', '卡4', '战斗跳过',
            '结算推进', '结算推进', '下一步',
        ], [event.data['control'] for event in report.events.events if event.type == 'action'])
        self.assertEqual([(628, 431), (1165, 672), (1136, 608), (128, 535),
                          (384, 535), (894, 535), (1240, 420), (1130, 660), (1130, 660), (1110, 641)],
                         [(action['x'], action['y']) for action in report.device.actions])
        self.assertIn('animation', {variant.value for observation in report.observations
                                    for variant in observation.variants})
        self.assertNotIn('连续出击', [event.data['control'] for event in report.events.events
                                      if event.type == 'action'])

    def test_drops_fallback_uses_only_the_annotated_coordinate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('drops-fallback'), Path(directory))

        self.assertEqual('completed', report.result.reason)
        self.assertEqual((1130, 660), (report.device.actions[0]['x'], report.device.actions[0]['y']))
        action = next(event for event in report.events.events if event.type == 'action')
        self.assertEqual('drops_fallback', action.data['reason'])

    def test_support_search_refreshes_then_selects_the_matching_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('support-search-refresh'), Path(directory))

        self.assertEqual(['助战列表向下', '列表更新', '是', '助战卡片第2行'],
                         [event.data['control'] for event in report.events.events
                          if event.type == 'action'])
        self.assertEqual(['swipe', 'tap', 'tap', 'tap'],
                         [action['action'] for action in report.device.actions])

    def test_support_refresh_cooldown_closes_then_resumes_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('support-refresh-cooldown'), Path(directory))

        self.assertEqual('unknown_timeout', report.result.reason)
        self.assertEqual(['助战列表向下', '列表更新', '关闭', '助战卡片第2行'],
                         [event.data['control'] for event in report.events.events
                          if event.type == 'action'])
        self.assertEqual((640, 565), (report.device.actions[2]['x'], report.device.actions[2]['y']))

    def test_battle_status_detail_closes_then_target_action_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('battle-status-detail'), Path(directory))

        self.assertEqual('completed', report.result.reason)
        self.assertEqual(['敌方2', '关闭', '攻击', '卡1', '卡2', '卡4', '战斗跳过',
                          '结算推进', '结算推进', '下一步'],
                         [event.data['control'] for event in report.events.events
                          if event.type == 'action'])
        self.assertEqual((1000, 650), (report.device.actions[1]['x'], report.device.actions[1]['y']))

    def test_two_runs_repeat_once_and_never_tap_after_last_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('repeat-two-runs'), Path(directory))

        controls = [event.data['control'] for event in report.events.events if event.type == 'action']
        self.assertEqual(2, report.result.progress.completed_runs)
        self.assertEqual(1, controls.count('连续出击'))
        self.assertEqual(1, controls.count('开始任务'))
        self.assertEqual('finished', report.events.events[-1].type)

    def test_stale_result_frame_cannot_confirm_or_repeat_an_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('stale-result-frame'), Path(directory))

        self.assertEqual('unknown_timeout', report.result.reason)
        self.assertEqual(['结算推进'], [event.data['control'] for event in report.events.events
                                        if event.type == 'action'])
        self.assertEqual(1, len(report.device.actions))

    def test_skill_animation_is_confirmed_before_attack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('skill-action-loop'), Path(directory))

        controls = [event.data['control'] for event in report.events.events if event.type == 'action']
        self.assertLess(controls.index('从者1技能1'), controls.index('攻击'))
        self.assertEqual(1, report.result.progress.action_index)
        self.assertEqual('completed', report.result.reason)

    def test_drain_mode_completes_at_real_ap_screen_without_using_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('ap-drain-complete'), Path(directory))

        self.assertEqual('ap_drained', report.result.reason)
        self.assertEqual(['下一步', '连续出击'],
                         [event.data['control'] for event in report.events.events
                          if event.type == 'action'])
        self.assertFalse(any(event.type == 'recovery_used' for event in report.events.events))

    def test_ap_recovery_uses_default_bronze_or_stops_with_empty_whitelist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bronze = run_case(get_case('ap-default-bronze'), Path(directory))
        self.assertEqual([{'item': 'bronze', 'count': 1}],
                         [dict(event.data) for event in bronze.events.events
                          if event.type == 'recovery_used'])
        self.assertEqual((648, 352), (bronze.device.actions[0]['x'], bronze.device.actions[0]['y']))

        with tempfile.TemporaryDirectory() as directory:
            scrolled = run_case(get_case('ap-scroll-bronze'), Path(directory))
        self.assertEqual(['swipe', 'tap'], [action['action'] for action in scrolled.device.actions])
        self.assertEqual(['AP 恢复列表向下', '青铜果实'],
                         [event.data['control'] for event in scrolled.events.events
                          if event.type == 'action'])

        with tempfile.TemporaryDirectory() as directory:
            stopped = run_case(get_case('ap-empty-whitelist'), Path(directory))
            self.assertTrue((Path(stopped.result.evidence_dir) / 'failure.png').is_file())
        self.assertEqual('ap_recovery', stopped.result.reason)
        self.assertEqual([], stopped.device.actions)

    def test_misopened_support_screens_close_in_order(self) -> None:
        for name, expected in (
            ('grand-page-recovery', ['关闭', '助战卡片第2行']),
            ('friend-request-recovery', ['结束', '助战卡片第2行']),
        ):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                report = run_case(get_case(name), Path(directory))
                self.assertEqual(expected, [event.data['control'] for event in report.events.events
                                            if event.type == 'action'])
                self.assertEqual('unknown_timeout', report.result.reason)

    def test_class_info_is_closed_before_support_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('class-info-recovery'), Path(directory))

        self.assertEqual(['关闭', '助战卡片第2行'],
                         [event.data['control'] for event in report.events.events
                          if event.type == 'action'])
        self.assertEqual('unknown_timeout', report.result.reason)

    def test_support_resume_then_unknown_stop_preserves_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('resume-support-unknown'), Path(directory))

            self.assertEqual('unknown_timeout', report.result.reason)
            self.assertEqual(['助战卡片第2行', '开始任务'],
                             [event.data['control'] for event in report.events.events
                              if event.type == 'action'])
            self.assertTrue((Path(report.result.evidence_dir) / 'failure.png').is_file())
            self.assertEqual('unknown_timeout', report.events.events[-1].data['reason'])

    def test_battle_resume_uses_confirmed_boundary_then_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('resume-battle'), Path(directory))

        self.assertEqual('completed', report.result.reason)
        self.assertEqual(['攻击', '卡1', '卡2', '卡4', '结算推进', '结算推进', '下一步'],
                         [event.data['control'] for event in report.events.events
                          if event.type == 'action'])

    def test_result_page_resume_finishes_without_repeating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_case(get_case('resume-result'), Path(directory))

        self.assertEqual('completed', report.result.reason)
        self.assertEqual(1, report.result.progress.completed_runs)
        self.assertEqual(['结算推进', '结算推进', '下一步'],
                         [event.data['control'] for event in report.events.events
                          if event.type == 'action'])

    def test_cli_returns_zero_and_writes_a_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'

            exit_code = main(['--only', 'full-loop', '--report', str(output)])

            self.assertEqual(0, exit_code)
            self.assertTrue(output.is_file())

    def test_cli_filter_runs_legacy_strategy_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            completed = subprocess.run(
                [sys.executable, '-m', 'executor.replay', '--filter', 'strategy', '--output', str(output)],
                capture_output=True, text=True, encoding='utf-8', timeout=120,
                env={**os.environ, 'PYTHONUTF8': '1'},
            )

            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertGreater(report['total'], 0)
            self.assertEqual(report['total'], report['passed'])
            self.assertTrue(all('strategy' in case['name'] for case in report['cases']))


if __name__ == '__main__':
    unittest.main()
