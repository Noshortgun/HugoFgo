import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from executor.domain.compiler import compile_job


ROOT = Path(__file__).resolve().parents[3]


class CompileJobTest(unittest.TestCase):
    def valid_job(self) -> dict:
        return json.loads((ROOT / 'executor/domain/fixtures/valid-multi-turn.json').read_text(encoding='utf-8'))

    def test_sample_job_compiles(self):
        text = (ROOT / 'examples/sample-job.json').read_text(encoding='utf-8')

        result = compile_job(text)

        self.assertEqual([], result.diagnostics)
        self.assertIsNotNone(result.plan)
        self.assertEqual([2, 1, 1], [len(wave.turns) for wave in result.plan.waves])

    def test_real_job_keeps_all_waves_and_turns(self):
        text = (ROOT / 'jobs/娄希、娄希、奥伯龙.json').read_text(encoding='utf-8')

        result = compile_job(text)

        self.assertEqual([], result.diagnostics)
        self.assertIsNotNone(result.plan)
        self.assertEqual(3, len(result.plan.waves))
        self.assertEqual([1, 1, 1], [len(wave.turns) for wave in result.plan.waves])

    def test_multi_turn_fixture_preserves_actions_and_card_order(self):
        text = (ROOT / 'executor/domain/fixtures/valid-multi-turn.json').read_text(encoding='utf-8')

        result = compile_job(text)

        self.assertEqual([], result.diagnostics)
        plan = result.plan
        self.assertIsNotNone(plan)
        self.assertEqual([2, 1], [len(wave.turns) for wave in plan.waves])

        first = plan.waves[0].turns[0]
        self.assertEqual(['orderChange', 'skill', 'master', 'attack'], [action.kind for action in first.actions])
        self.assertEqual((0, 3), (first.actions[0].swap_out_slot, first.actions[0].swap_in_slot))
        self.assertEqual((0, 1), (first.actions[1].member_slot, first.actions[1].player_target_slot))
        self.assertEqual('noblePhantasmCardType', first.actions[1].secondary_selection['type'])
        self.assertEqual(1, first.actions[2].player_target_slot)
        self.assertEqual({0: 2, 3: 'auto'}, first.np_positions)
        self.assertEqual('prefer', first.attack.mode)
        self.assertEqual([(0, 'arts', None), (1, 'buster', None), (2, 'buster', 0)],
                         [(card.slot, card.type, card.deck_index) for card in first.attack.cards])

        second = plan.waves[0].turns[1]
        self.assertEqual((3, 2), (second.actions[0].from_target, second.actions[0].to_target))
        self.assertEqual('spell', plan.waves[1].turns[0].actions[0].kind)

    def test_out_of_range_slot_is_diagnosed_with_location(self):
        text = (ROOT / 'executor/domain/fixtures/invalid-slot.json').read_text(encoding='utf-8')

        result = compile_job(text)

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        diagnostic = result.diagnostics[0]
        self.assertEqual((1, 1), (diagnostic.wave, diagnostic.turn))
        self.assertIn('memberSlot', diagnostic.path)
        self.assertIn('槽位越界', diagnostic.message)

    def test_unknown_action_kind_is_diagnosed(self):
        text = (ROOT / 'executor/domain/fixtures/unsupported-action.json').read_text(encoding='utf-8')

        result = compile_job(text)

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        diagnostic = result.diagnostics[0]
        self.assertEqual((1, 1), (diagnostic.wave, diagnostic.turn))
        self.assertIn('kind', diagnostic.path)
        self.assertIn('不支持的动作', diagnostic.message)

    def test_empty_slot_reference_is_diagnosed(self):
        job = self.valid_job()
        job['waves'][0]['turns'][0]['actions'][1]['memberSlot'] = 4

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        self.assertIn('memberSlot', result.diagnostics[0].path)
        self.assertIn('空槽位', result.diagnostics[0].message)

    def test_unknown_secondary_selection_type_is_diagnosed(self):
        job = self.valid_job()
        job['waves'][0]['turns'][0]['actions'][1]['secondarySelection']['type'] = 'mystery'

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        self.assertIn('secondarySelection.type', result.diagnostics[0].path)
        self.assertIn('不支持的二次选择类型', result.diagnostics[0].message)

    def test_turn_count_mismatch_is_diagnosed(self):
        job = self.valid_job()
        job['waves'][0]['turnCount'] = 3

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        self.assertEqual(1, result.diagnostics[0].wave)
        self.assertIn('turnCount', result.diagnostics[0].path)
        self.assertIn('回合数', result.diagnostics[0].message)

    def test_invalid_enemy_count_and_version_are_diagnosed(self):
        job = self.valid_job()
        job['enemyCount'] = 4
        job['version'] = 2

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        paths = [diagnostic.path for diagnostic in result.diagnostics]
        self.assertIn('$.enemyCount', paths)
        self.assertIn('$.version', paths)

    def test_cli_reports_diagnostics_and_exits_2(self):
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'executor.domain',
             str(ROOT / 'executor/domain/fixtures/invalid-slot.json')],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )

        self.assertEqual(2, result.returncode, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn('作业无效', output)
        self.assertIn('第 1 面', output)
        self.assertIn('回合 1', output)
        self.assertIn('memberSlot', output)

    def test_cli_json_outputs_full_plan(self):
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'executor.domain',
             str(ROOT / 'executor/domain/fixtures/valid-multi-turn.json'), '--json'],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )

        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(2, len(plan['waves']))
        self.assertEqual([2, 1], [len(wave['turns']) for wave in plan['waves']])
        self.assertEqual('orderChange', plan['waves'][0]['turns'][0]['actions'][0]['kind'])


if __name__ == '__main__':
    unittest.main()
