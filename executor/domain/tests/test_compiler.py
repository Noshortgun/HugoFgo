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

    def test_real_job_preserves_support_requirements(self):
        text = (ROOT / 'jobs/娄希、娄希、奥伯龙.json').read_text(encoding='utf-8')

        result = compile_job(text)

        self.assertEqual([], result.diagnostics)
        support = result.plan.formation[0]
        self.assertEqual((0, '娄希', 705000, 'Berserker'),
                         (support.slot, support.name, support.svt_id, support.role))
        self.assertTrue(support.support)
        self.assertTrue(support.support_required)
        self.assertFalse(support.grand_required)
        self.assertEqual(('迦勒底午茶时光', 9403520), (support.ce, support.ce_id))

    def test_multi_turn_fixture_preserves_actions_and_card_order(self):
        text = (ROOT / 'executor/domain/fixtures/valid-multi-turn.json').read_text(encoding='utf-8')

        result = compile_job(text)

        self.assertEqual([], result.diagnostics)
        plan = result.plan
        self.assertIsNotNone(plan)
        self.assertEqual([2, 1], [len(wave.turns) for wave in plan.waves])
        self.assertEqual((True, ('卫宫', '玛修·基列莱特', '吉尔伽美什'), None, 'buster'),
                         (plan.strategy.allow_np, plan.strategy.servant_order,
                          plan.strategy.servant_order_slots, plan.strategy.cards))

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

    def test_conflicting_np_positions_are_diagnosed(self):
        job = self.valid_job()
        job['waves'][0]['turns'][0]['npPositions'] = {'0': 1, '3': 1}

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        diagnostic = result.diagnostics[0]
        self.assertEqual((1, 1), (diagnostic.wave, diagnostic.turn))
        self.assertIn('npPositions', diagnostic.path)
        self.assertIn('宝具卡序冲突', diagnostic.message)

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

    def test_schema_rejects_boolean_version_and_empty_strategy(self):
        job = self.valid_job()
        job['version'] = True
        job['strategy'] = {}

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        paths = {diagnostic.path for diagnostic in result.diagnostics}
        self.assertIn('$.version', paths)
        self.assertIn('$.strategy.allowNp', paths)
        self.assertIn('$.strategy.servantOrder', paths)

    def test_formation_name_must_be_string(self):
        job = self.valid_job()
        job['formation'][0]['name'] = 123

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(['$.formation[0].name'], [diagnostic.path for diagnostic in result.diagnostics])

    def test_wave_strategy_override_and_servant_slots_compile(self):
        job = self.valid_job()
        job['strategy']['servantOrderSlots'] = [3, 1, 2]
        override = {**job['strategy'], 'allowNp': False, 'cards': 'quick',
                    'servantOrderSlots': [1, 3, 2],
                    'servantOrder': ['玛修·基列莱特', '卫宫', '吉尔伽美什']}
        job['strategyByWave'] = {'1': override, '2': None}

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertEqual([], result.diagnostics)
        self.assertEqual((3, 1, 2), result.plan.strategy.servant_order_slots)
        self.assertEqual((False, 'quick', (1, 3, 2)),
                         (result.plan.strategy_by_wave[1].allow_np,
                          result.plan.strategy_by_wave[1].cards,
                          result.plan.strategy_by_wave[1].servant_order_slots))
        self.assertIsNone(result.plan.strategy_by_wave[2])

    def test_strategy_order_accepts_affinity(self):
        job = self.valid_job()
        job['strategy']['strategyOrder'] = ['affinity', 'servant', 'cards', 'crit']

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertEqual([], result.diagnostics)
        self.assertEqual(('affinity', 'servant', 'cards', 'crit'), result.plan.strategy.strategy_order)

    def test_fallback_strategy_rejects_empty_slots(self):
        job = self.valid_job()
        job['strategy']['servantOrderSlots'] = [4]
        job['strategyByWave'] = {
            '1': {**job['strategy'], 'servantOrderSlots': [4]},
        }

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        paths = {diagnostic.path for diagnostic in result.diagnostics}
        self.assertIn('$.strategy.servantOrderSlots[0]', paths)
        self.assertIn('$.strategyByWave.1.servantOrderSlots[0]', paths)
        self.assertTrue(all('空槽位' in diagnostic.message for diagnostic in result.diagnostics))

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
