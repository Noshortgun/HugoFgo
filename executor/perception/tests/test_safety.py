"""覆盖层、残留元素与“第三张卡之后”的动作安全。"""
from __future__ import annotations

import unittest

from executor.perception import Perception
from executor.perception.tests.fixtures import load_frame


class SafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.perception = Perception(ocr_reader=None)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.perception.close()

    def read(self, relative: str):
        return self.perception.read(load_frame(relative))

    def test_third_card_submitted_stops_card_selection(self) -> None:
        for relative in (
            'captures/2026-11-19/13-cards-selected.png',
            'captures/2026-11-19/19-cards2-selected.png',
            'captures/2026-11-19/25-cards3-selected.png',
        ):
            with self.subTest(relative=relative):
                observation = self.read(relative)
                self.assertEqual('battle', observation.base_page.value)
                self.assertEqual({'animation'}, {item.value for item in observation.variants})
                self.assertEqual((), observation.controls)
                for name in ('卡1', '卡2', '卡3', '卡4', '卡5', '攻击'):
                    control = observation.control(name)
                    self.assertTrue(control is None or not control.actionable,
                                    f'{relative} 仍输出可点的 {name}')

    def test_retreat_confirm_is_not_battle_menu(self) -> None:
        observation = self.read('captures/2026-11-19-mystic/key/12-retreat-confirm.png')

        self.assertEqual({'retreat_confirm'}, {item.value for item in observation.overlays})
        self.assertIsNone(observation.control('撤退'))
        self.assertTrue(observation.control('决定').actionable)
        self.assertTrue(observation.control('取消').actionable)

    def test_skill_target_is_not_command_spell_target(self) -> None:
        skill = self.read('captures/2026-11-19-ember/key/09-skill-target.png')
        spell = self.read('captures/2026-11-20/key/72-command-spell-target-select.png')

        self.assertEqual({'skill_target'}, {item.value for item in skill.variants})
        self.assertNotIn('command_spell_target', {item.value for item in skill.overlays})
        self.assertIn('command_spell_target', {item.value for item in spell.overlays})
        self.assertEqual(frozenset(), spell.variants)
        for name in ('目标从者1', '目标从者2', '目标从者3'):
            self.assertTrue(skill.control(name).actionable, name)

    def test_result_page_keeps_battle_residue_out_of_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/result.png')

        self.assertEqual('result', observation.base_page.value)
        self.assertGreaterEqual(observation.evidence['battle'], 0.58)
        self.assertEqual((), observation.controls)
        self.assertFalse(observation.actionable)

    def test_master_skills_does_not_block_battle_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/60-current-master-expanded.png')

        self.assertEqual('battle', observation.base_page.value)
        for name in ('攻击', '战斗菜单', '令咒', '从者1技能1', '敌方1', '御主技能3'):
            self.assertTrue(observation.control(name).actionable, name)

    def test_modal_overlay_hides_base_controls(self) -> None:
        observation = self.read('captures/2026-11-20/key/70-command-spell-open.png')

        for name in ('攻击', '战斗菜单', '令咒', '从者1技能1'):
            self.assertIsNone(observation.control(name), name)
        for name in ('宝具解放', '灵基修复', '关闭'):
            self.assertTrue(observation.control(name).actionable, name)

    def test_card_page_only_offers_confirmed_cards(self) -> None:
        observation = self.read('captures/2026-11-20/key/card-select.png')

        cards = [control for control in observation.controls if control.name.startswith('卡')]
        self.assertEqual(5, len(cards))
        self.assertTrue(all(control.actionable for control in cards))
        self.assertTrue(all(control.center[1] > 400 for control in cards))

    def test_attack_animation_exposes_no_controls(self) -> None:
        observation = self.read('captures/2026-11-19-ember/36-attack-animation.png')

        self.assertEqual('battle', observation.base_page.value)
        self.assertEqual({'animation'}, {item.value for item in observation.variants})
        self.assertEqual((), observation.controls)
        self.assertFalse(observation.actionable)

    def test_only_existing_servants_and_enemies_are_listed(self) -> None:
        solo = self.read('captures/2026-11-19/07-battle-loading.png')

        self.assertEqual('battle', solo.base_page.value)
        listed = {item.name for item in solo.controls}
        self.assertTrue({'从者1技能1', '从者1技能2', '从者1技能3', '敌方1'} <= listed)
        self.assertEqual(set(), {name for name in listed if name.startswith(('从者2', '从者3'))})
        self.assertNotIn('敌方2', listed)
        self.assertNotIn('敌方3', listed)

    def test_np_cards_follow_card_type_emblems(self) -> None:
        quick = self.read('captures/2026-11-20/key/card-select.png')
        arts = self.read('captures/2026-11-19-ember/key/10-np-card-select.png')
        both = self.read('captures/2026-11-19-ember/key/11-np-selected.png')
        none = self.read('captures/2026-11-19-ember/key/06-card-select.png')

        def np_names(observation):
            return [item.name for item in observation.controls if item.name.startswith('宝具卡')]

        self.assertEqual(['宝具卡1'], np_names(quick))
        self.assertEqual(['宝具卡1'], np_names(arts))
        self.assertEqual(['宝具卡1', '宝具卡2'], np_names(both))
        self.assertEqual([], np_names(none))
        self.assertEqual('np_slot:B', quick.control('宝具卡1').reason)
        self.assertEqual('np_slot:A', arts.control('宝具卡1').reason)

        # 宝具卡的框必须和指令卡差不多大，不能把搜索窗当输出框。
        command = quick.control('卡1').box
        for control in (quick.control('宝具卡1'), arts.control('宝具卡1')):
            x1, y1, x2, y2 = control.box
            self.assertLessEqual(abs((x2 - x1) - (command[2] - command[0])), 60, control.name)
            self.assertLessEqual(abs((y2 - y1) - (command[3] - command[1])), 60, control.name)

    def test_holdout_findings(self) -> None:
        gate = self.read('captures/2026-11-21/key/gate.png')
        glow = self.read('captures/2026-11-21/key/card-np-blue-glow.png')
        retreat = self.read('captures/2026-11-21/key/battle-retreat-lower.png')

        self.assertEqual('menu', gate.base_page.value)
        self.assertEqual({'gate'}, {item.value for item in gate.variants})
        self.assertTrue(gate.control('每日任务').actionable)

        self.assertEqual(['宝具卡1'], [item.name for item in glow.controls
                                       if item.name.startswith('宝具卡')])

        self.assertEqual({'决定', '取消'}, {item.name for item in retreat.controls})
        self.assertLess(retreat.control('取消').box[1], 420)

    def test_story_and_skip_dialog(self) -> None:
        story = self.read('captures/2026-11-21/key/story.png')
        dialog = self.read('captures/2026-11-21/key/story-skip-confirm.png')

        self.assertEqual('story', story.base_page.value)
        self.assertTrue(story.control('跳过').actionable)
        self.assertEqual('story', dialog.base_page.value)
        self.assertEqual({'story_skip_confirm'}, {item.value for item in dialog.overlays})
        self.assertTrue(dialog.control('是').actionable)
        self.assertTrue(dialog.control('否').actionable)

    def test_live_session_screens(self) -> None:
        friend = self.read('captures/2026-11-21/key/friend-request.png')
        bond = self.read('captures/2026-11-21/key/result-bond.png')
        repeat = self.read('captures/2026-11-21/key/repeat-over-support.png')

        self.assertEqual('support', friend.base_page.value)
        self.assertEqual(frozenset(), friend.variants)
        self.assertEqual({'friend_request'}, {item.value for item in friend.overlays})
        self.assertEqual(['结束', '申请好友'], [item.name for item in friend.controls])
        self.assertTrue(friend.control('结束').actionable)
        self.assertFalse(friend.control('申请好友').actionable)

        self.assertEqual('result', bond.base_page.value)
        self.assertEqual({'bond'}, {item.value for item in bond.variants})
        self.assertEqual((), bond.controls)

        # 弹窗盖住了底下的好友申请页：基页未知，但弹窗按钮必须可用。
        self.assertEqual('unknown', repeat.base_page.value)
        actionable = {item.name for item in repeat.controls if item.actionable}
        self.assertTrue({'连续出击', '关闭'} <= actionable)
        self.assertIsNone(repeat.control('开关星光之砂'))
        switch = repeat.control('开关牵绊2倍')
        self.assertFalse(switch.actionable)
        # 开关位置跟随文案锚点，而不是写死的旧坐标。
        self.assertTrue(480 <= switch.box[0] <= 620, switch.box)
        self.assertTrue(600 <= switch.box[2] <= 800, switch.box)

    def test_six_enemy_rows_are_listed_in_order(self) -> None:
        observation = self.read('captures/2026-11-21/key/battle-six-enemies.png')

        enemies = [item.name for item in observation.controls if item.name.startswith('敌方')]
        self.assertEqual([f'敌方{i}' for i in range(1, 7)], enemies)
        self.assertLess(observation.control('敌方1').box[1], observation.control('敌方4').box[1])

    def test_support_rows_follow_scroll(self) -> None:
        cases = {
            'captures/2026-11-20/key/support-select-correct-ce.png': 3,
            'captures/2026-11-20/key/support-select-wrong-ce.png': 4,  # 顶部露出被滚上去的半行
            'captures/2026-11-20/key/163-grand-support.png': 3,
            'captures/2026-11-20/key/164-current.png': 3,
        }
        for relative, expected in cases.items():
            with self.subTest(relative=relative):
                observation = self.read(relative)
                rows = [item for item in observation.controls if item.kind.value == 'row']
                self.assertEqual(expected, len(rows))
                for row in rows:
                    self.assertGreaterEqual(row.box[3] - row.box[1], 60)
                for first, second in zip(rows, rows[1:]):
                    self.assertEqual(first.box[3], second.box[1])

    def test_selected_arts_and_buster_pair_is_detected(self) -> None:
        for relative in ('captures/2026-11-21/key/card-np-selected-arts-buster.png',
                         'captures/2026-11-21/key/card-np-selected-chain.png'):
            with self.subTest(relative=relative):
                observation = self.read(relative)
                names = [item.name for item in observation.controls if item.name.startswith('宝具卡')]
                self.assertEqual(['宝具卡1', '宝具卡2'], names)

    def test_selected_np_pair_is_detected(self) -> None:
        observation = self.read('captures/2026-11-21/key/card-np-selected-pair.png')

        self.assertEqual(['宝具卡1', '宝具卡2'],
                         [item.name for item in observation.controls if item.name.startswith('宝具卡')])

    def test_selected_buster_np_card_is_detected(self) -> None:
        observation = self.read('captures/2026-11-21/key/card-np-buster-selected.png')

        self.assertEqual(['宝具卡1', '宝具卡2'],
                         [item.name for item in observation.controls if item.name.startswith('宝具卡')])

    def test_buster_np_card_is_detected(self) -> None:
        observation = self.read('captures/2026-11-21/key/card-np-buster.png')

        self.assertEqual(['宝具卡1', '宝具卡2'],
                         [item.name for item in observation.controls if item.name.startswith('宝具卡')])

    def test_effect_text_does_not_create_np_cards(self) -> None:
        observation = self.read('captures/2026-11-21/key/card-np-effect-text.png')

        self.assertEqual(['宝具卡1'], [item.name for item in observation.controls
                                       if item.name.startswith('宝具卡')])

    def test_small_arts_np_card_is_detected(self) -> None:
        observation = self.read('captures/2026-11-21/key/card-np-arts-small.png')

        self.assertTrue(observation.control('宝具卡1').actionable)
        self.assertIsNone(observation.control('宝具卡2'))

    def test_purple_enemy_bar_and_effect_ring(self) -> None:
        purple = self.read('captures/2026-11-21/key/battle-purple-hp.png')
        effect = self.read('captures/2026-11-21/key/card-effect-ring.png')

        self.assertTrue(purple.control('敌方3').actionable)
        names = [item.name for item in effect.controls if item.name.startswith('宝具卡')]
        self.assertEqual([], names)

    def test_order_change_needs_both_selections(self) -> None:
        waiting = self.read('captures/2026-11-20/key/61-order-change-select.png')
        ready = self.read('captures/2026-11-19-mystic/key/07-swap-in-selected.png')

        self.assertFalse(waiting.control('进行更替').actionable)
        self.assertTrue(ready.control('进行更替').actionable)


if __name__ == '__main__':
    unittest.main()
