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

    def test_sparse_formation_compiles_and_materializes_empty_slots(self):
        job = self.valid_job()
        specified = job['formation'][0]
        job['formation'] = [specified]
        job['strategy']['servantOrder'] = [specified['name']]
        job['strategy'].pop('servantOrderSlots', None)
        job['waves'][0]['turns'][0]['actions'] = []
        job['waves'][0]['turns'][0]['npPositions'] = {}
        job['waves'][0]['turns'][0]['attack'] = {'mode': 'strategy', 'cards': []}
        job['waves'][0]['turns'][1]['actions'] = []
        job['waves'][1]['turns'][0]['actions'] = []

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertEqual([], result.diagnostics)
        self.assertEqual([(0, specified['name']), *((slot, '') for slot in range(1, 6))],
                         [(member.slot, member.name) for member in result.plan.formation])

    def test_rejects_jobs_with_more_than_three_waves(self):
        job = self.valid_job()
        job['waves'].extend([job['waves'][0], job['waves'][0]])

        result = compile_job(json.dumps(job))

        self.assertIsNone(result.plan)
        self.assertIn('$.waves', [diagnostic.path for diagnostic in result.diagnostics])

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

    def test_second_order_change_in_one_battle_is_diagnosed(self):
        job = self.valid_job()
        second = dict(job['waves'][0]['turns'][0]['actions'][0])
        second['swapOutSlot'] = 1
        second['swapInSlot'] = 3
        job['waves'][1]['turns'][0]['actions'].insert(0, second)

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        diagnostic = result.diagnostics[0]
        self.assertEqual((2, 1), (diagnostic.wave, diagnostic.turn))
        self.assertEqual('$.waves[1].turns[0].actions[0].kind', diagnostic.path)
        self.assertIn('每场战斗最多一次', diagnostic.message)

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

    def one_turn_job(self, actions: list[dict], formation: list[dict] | None = None) -> dict:
        job = self.valid_job()
        if formation is not None:
            job['formation'] = formation
        job['strategy']['servantOrder'] = [entry['name'] for entry in job['formation']
                                           if entry['name'].strip()]
        job['strategy'].pop('servantOrderSlots', None)
        job['strategyByWave'] = {}
        job['waves'] = [{'turnCount': 1, 'target': 1, 'turns': [{'actions': actions}]}]
        return job

    def test_fixed_identity_references_reject_empty_slots(self):
        job = self.valid_job()
        job = self.one_turn_job([
            {'kind': 'skill', 'title': '', 'detail': '', 'icon': '', 'skill': 0,
             'memberSlot': 4, 'playerTargetSlot': 4},
            {'kind': 'orderChange', 'title': '', 'detail': '', 'icon': '',
             'swapOutSlot': 4, 'swapInSlot': 5},
        ], job['formation'])

        result = compile_job(json.dumps(job))

        self.assertIsNone(result.plan)
        self.assertEqual({
            '$.waves[0].turns[0].actions[0].memberSlot',
        }, {diagnostic.path for diagnostic in result.diagnostics
            if diagnostic.path.startswith('$.waves[0].turns[0].actions')})

    def test_empty_slot_targets_and_swaps_are_allowed(self):
        job = self.valid_job()
        job = self.one_turn_job([
            {'kind': 'skill', 'title': '指定从者技能', 'detail': '', 'icon': '', 'skill': 0,
             'memberSlot': 0, 'playerTargetSlot': 5},
            {'kind': 'orderChange', 'title': '任意从者换人', 'detail': '', 'icon': '',
             'swapOutSlot': 0, 'swapInSlot': 5},
        ], job['formation'])

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertEqual([], result.diagnostics)
        self.assertIsNotNone(result.plan)
        actions = result.plan.waves[0].turns[0].actions
        self.assertEqual(5, actions[0].player_target_slot)
        self.assertEqual((0, 5), (actions[1].swap_out_slot, actions[1].swap_in_slot))

    def test_any_support_compiles_without_identity_and_keeps_ce(self):
        job = self.valid_job()
        job = self.one_turn_job([], [
            *job['formation'][:5],
            {'slot': 5, 'name': '', 'support': True, 'supportRequired': False,
             'ce': '午茶', 'ceId': 9403520, 'svtId': 705000, 'role': 'Berserker'},
        ])

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertEqual([], result.diagnostics)
        support = result.plan.formation[5]
        self.assertEqual(('', True, False, '午茶', 9403520),
                         (support.name, support.support, support.support_required,
                          support.ce, support.ce_id))
        self.assertEqual((None, None), (support.svt_id, support.role))

    def test_legacy_any_support_without_flag_is_recognized(self):
        job = self.valid_job()
        job = self.one_turn_job([], [
            *job['formation'][:5],
            {'slot': 5, 'name': '  ', 'support': True, 'ceId': 9403520},
        ])

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertEqual([], result.diagnostics)
        support = result.plan.formation[5]
        self.assertTrue(support.support)
        self.assertFalse(support.support_required)
        self.assertEqual(9403520, support.ce_id)

    def test_multiple_support_slots_are_rejected(self):
        job = self.valid_job()
        job['formation'][0]['support'] = True
        job['formation'][1]['support'] = True
        job['formation'][1]['supportRequired'] = True

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(['$.formation[1].support'],
                         [diagnostic.path for diagnostic in result.diagnostics
                          if diagnostic.path.startswith('$.formation')])
        self.assertIn('只能有一个助战槽', result.diagnostics[0].message)

    def test_servant_skill_and_np_attack_require_member_slot(self):
        job = self.valid_job()
        actions = job['waves'][0]['turns'][0]['actions']
        skill = next(action for action in actions if action['kind'] == 'skill')
        attack = next(action for action in actions if action['kind'] == 'attack')
        del skill['memberSlot']
        del attack['memberSlot']

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(2, len(result.diagnostics))
        self.assertTrue(all(diagnostic.path.endswith('.memberSlot') for diagnostic in result.diagnostics))
        self.assertTrue(all(diagnostic.wave == 1 and diagnostic.turn == 1
                            for diagnostic in result.diagnostics))

    def test_unknown_secondary_selection_type_is_diagnosed(self):
        job = self.valid_job()
        job['waves'][0]['turns'][0]['actions'][1]['secondarySelection']['type'] = 'mystery'

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        self.assertIn('secondarySelection.type', result.diagnostics[0].path)
        self.assertIn('不支持的二次选择类型', result.diagnostics[0].message)

    def test_secondary_selection_requires_matching_option_index_and_value(self) -> None:
        job = self.valid_job()
        selection = job['waves'][0]['turns'][0]['actions'][1]['secondarySelection']
        selection['index'] = 2

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        self.assertIn('secondarySelection.index', result.diagnostics[0].path)
        self.assertIn('没有对应候选', result.diagnostics[0].message)

        selection['index'] = 0
        selection['value'] = 2
        result = compile_job(json.dumps(job, ensure_ascii=False))
        self.assertEqual(1, len(result.diagnostics))
        self.assertIn('secondarySelection.value', result.diagnostics[0].path)

    def test_secondary_selection_requires_candidate_options(self) -> None:
        job = self.valid_job()
        selection = job['waves'][0]['turns'][0]['actions'][1]['secondarySelection']
        selection.pop('options')

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        self.assertIn('secondarySelection.options', result.diagnostics[0].path)
        self.assertIn('缺少候选选项', result.diagnostics[0].message)

    def test_legacy_string_card_selection_is_rejected(self) -> None:
        job = self.valid_job()
        selection = job['waves'][0]['turns'][0]['actions'][1]['secondarySelection']
        selection['value'] = 'arts'
        selection['options'] = [{'value': 'arts', 'label': '蓝卡宝具'}]

        result = compile_job(json.dumps(job, ensure_ascii=False))

        self.assertIsNone(result.plan)
        self.assertEqual(1, len(result.diagnostics))
        self.assertIn('旧字符串卡色值', result.diagnostics[0].message)

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
