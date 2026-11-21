from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from executor.core import run
from executor.core.smoke import FrameBudget, main
from executor.core.tests.test_runner import button, observe, scripted, single_wave_plan
from executor.device.__main__ import make_png
from executor.perception import BasePage
from executor.session import MemoryEventSink, RunControl


class FrameBudgetTest(unittest.TestCase):
    def test_budget_stops_before_an_extra_tap(self) -> None:
        battle = observe(BasePage.BATTLE, button('攻击'))
        device, perception = scripted([battle, battle, battle], [0.0, 0.5, 1.0])
        control = RunControl()
        budget = FrameBudget(device, max_frames=1, control=control)

        result = run(single_wave_plan(), budget, perception, MemoryEventSink())

        self.assertEqual('manual_stop', result.reason)
        self.assertEqual([], device.actions)


class SmokeCliTest(unittest.TestCase):
    def test_cli_returns_success_only_for_the_expected_bounded_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = root / 'frames'
            frames.mkdir()
            for index in range(2):
                (frames / f'{index}.png').write_bytes(make_png(1280, 720))
            job = root / 'job.json'
            job.write_text(json.dumps({
                'version': 1,
                'mysticId': 'default',
                'enemyCount': 3,
                'formation': [{'slot': slot, 'name': f'从者{slot}'} for slot in range(3)],
                'strategy': {
                    'allowNp': False,
                    'servantOrder': [],
                    'cards': 'balanced',
                    'colorPriority': ['buster', 'arts', 'quick'],
                    'strategyOrder': ['servant', 'cards', 'crit'],
                },
                'waves': [{
                    'turnCount': 1,
                    'target': 1,
                    'turns': [{
                        'actions': [],
                        'attack': {
                            'mode': 'verify',
                            'cards': [
                                {'slot': 0, 'type': 'arts'},
                                {'slot': 1, 'type': 'arts'},
                                {'slot': 2, 'type': 'arts'},
                            ],
                        },
                    }],
                }],
            }), encoding='utf-8')

            exit_code = main([
                str(job), '--frame-dir', str(frames), '--expected-stop', 'manual_stop',
                '--max-frames', '2', '--run-dir', str(root / 'run'),
            ])

            self.assertEqual(1, exit_code)


if __name__ == '__main__':
    unittest.main()
