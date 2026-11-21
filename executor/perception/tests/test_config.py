"""国服识别配置的自检：模板完整、坐标合法、不夹带其他区服规则。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from executor.perception import BasePage, Overlay, evidence
from executor.perception.regions import cn

ROOT = Path(__file__).resolve().parents[3]


class ConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = evidence.load_templates()
        cls.manifest = json.loads((cn.TEMPLATE_DIR / 'manifest.json').read_text(encoding='utf-8'))

    def test_every_anchor_has_a_template(self) -> None:
        names = set()
        for rule in cn.PAGE_RULES:
            names.update(rule.anchors)
        for rule in cn.OVERLAY_RULES:
            names.update(rule.anchors)
            names.update(rule.absent)
        for rule in cn.VARIANT_RULES:
            names.update(rule.anchors)
        names.update(cn.AP_ITEM_LAYOUTS)
        names.update(cn.BATTLE_SLOTS)
        layouts = (*cn.PAGE_CONTROLS.values(), *cn.OVERLAY_CONTROLS.values(),
                   *cn.VARIANT_CONTROLS.values(), *cn.VARIANT_EXTRA_CONTROLS.values(),
                   *cn.AP_ITEM_LAYOUTS.values())
        for spec in layouts:
            for control in spec:
                if control.anchor is not None:
                    names.add(control.anchor)

        self.assertEqual(set(cn.INTENSITY_PROBES) | set(cn.FRAME_EDGE_PROBES) | set(cn.SLOT_PROBES),
                         names - set(self.templates))
        self.assertEqual(set(), set(self.templates) - names)

    def test_control_boxes_stay_inside_the_base_frame(self) -> None:
        width, height = cn.BASE_SIZE
        specs = [*cn.PAGE_CONTROLS.values(), *cn.VARIANT_CONTROLS.values(),
                 *cn.OVERLAY_CONTROLS.values()]
        for layout in specs:
            for control in layout:
                with self.subTest(control=control.name):
                    x1, y1, x2, y2 = control.box
                    self.assertTrue(0 <= x1 < x2 <= width)
                    self.assertTrue(0 <= y1 < y2 <= height)

    def test_every_page_and_overlay_has_a_control_layout(self) -> None:
        for page in BasePage:
            self.assertIn(page, cn.PAGE_CONTROLS)
        for overlay in Overlay:
            self.assertIn(overlay, cn.OVERLAY_CONTROLS)

    def test_config_only_ships_the_cn_region(self) -> None:
        self.assertEqual('cn', cn.REGION)
        regions = Path(cn.__file__).parent
        self.assertEqual({'__init__.py', '__pycache__', 'cn.py'},
                         {path.name for path in regions.iterdir()})
        self.assertEqual(ROOT / 'executor/perception/templates/cn', cn.TEMPLATE_DIR)
        self.assertEqual({'cn'}, {path.name for path in cn.TEMPLATE_DIR.parent.iterdir()})

    def test_master_skills_uses_layout_not_card_art(self) -> None:
        # 御主技能识别只依赖卡框边缘，不能退回到绑定某个礼装图标的模板。
        self.assertIn('master_skills', cn.FRAME_EDGE_PROBES)
        self.assertNotIn('master_skills', self.templates)

    def test_overlays_are_blocking_except_master_skills(self) -> None:
        # 御主技能展开时从者技能、攻击、敌人、战斗菜单、令咒仍然可点（人工核对）。
        self.assertEqual(frozenset(Overlay) - {Overlay.MASTER_SKILLS}, cn.BLOCKING_OVERLAYS)


if __name__ == '__main__':
    unittest.main()
