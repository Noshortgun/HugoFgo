"""国服画面识别配置：锚点、优先级、控件布局与字段适用范围都集中在这里。

只包含国服（简体中文）规则；日服、台服的文案与坐标不进入本模块。
锚点本身是模板 patch，几何与阈值由 `build_templates.py` 生成到 `templates/cn/`。
控件坐标优先采用人工标注（`.scratch/annotated`）；随滚动变化的部分不写死，见 recognizer 的动态检测。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from executor.perception.observation import BasePage, Box, ControlKind, Overlay, Variant

REGION = 'cn'
BASE_SIZE = (1280, 720)
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / 'templates' / 'cn'


@dataclass(frozen=True)
class PageRule:
    """基页判据：所有 anchors 命中才算该页；按声明顺序取第一个命中。"""

    page: BasePage
    anchors: tuple[str, ...]


@dataclass(frozen=True)
class OverlayRule:
    """覆盖层判据：anchors 全中且 absent 全不中才成立。"""

    overlay: Overlay
    anchors: tuple[str, ...]
    absent: tuple[str, ...] = ()


@dataclass(frozen=True)
class VariantRule:
    """版式变体判据；blocking 为真时基页控件不可行动。"""

    variant: Variant
    page: BasePage
    anchors: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()


@dataclass(frozen=True)
class ControlSpec:
    """固定布局的可点目标。

    anchor 非空时用它判定可用性；hide_when_missing 为真表示“证据不在 = 按钮不在画面上”，
    直接不输出该控件，否则保留控件但标记不可行动。
    """

    name: str
    kind: ControlKind
    box: Box
    anchor: str | None = None
    hide_when_missing: bool = False


# 顺序即优先级：加载、结算残留、卡牌都要先于 battle 判定。
PAGE_RULES: tuple[PageRule, ...] = (
    PageRule(BasePage.LOADING, ('loading',)),
    PageRule(BasePage.AP_RECOVERY, ('ap_recovery',)),
    PageRule(BasePage.DROPS, ('drops',)),
    PageRule(BasePage.RESULT, ('result',)),
    PageRule(BasePage.SUPPORT, ('support',)),
    PageRule(BasePage.TEAM, ('team',)),
    PageRule(BasePage.CARD, ('card',)),
    PageRule(BasePage.BATTLE, ('battle',)),
    PageRule(BasePage.MENU, ('menu',)),
)

VARIANT_RULES: tuple[VariantRule, ...] = (
    VariantRule(Variant.GRAND_SUPPORT, BasePage.SUPPORT, ('grand_support',)),
    VariantRule(Variant.GRAND_TEAM, BasePage.TEAM, ('grand_team',)),
    VariantRule(Variant.RESULT_BOND, BasePage.RESULT, ('result_bond',)),
    VariantRule(Variant.RESULT_EXP, BasePage.RESULT, ('result_exp',)),
    VariantRule(Variant.DROPS_LOOT, BasePage.DROPS, ('drops',)),
    # 己方单体技能选目标：与令咒目标同样显示“请选择对象”，但没有“是否使用”。
    VariantRule(Variant.SKILL_TARGET, BasePage.BATTLE, ('command_spell_target',),
                absent=('command_spell_use',)),
    # 攻击/技能/换人动画：战斗 HUD 整体消失，不存在任何可点按钮。
    VariantRule(Variant.BATTLE_ANIMATION, BasePage.BATTLE, absent=('battle_chrome', 'attack')),
)

BLOCKING_VARIANTS: frozenset[Variant] = frozenset({Variant.SKILL_TARGET, Variant.BATTLE_ANIMATION})

# 御主技能展开不是模态：从者技能、攻击、敌人、战斗菜单、令咒此时仍然可点（人工核对确认）。
BLOCKING_OVERLAYS: frozenset[Overlay] = frozenset(overlay for overlay in Overlay
                                                  if overlay is not Overlay.MASTER_SKILLS)

OVERLAY_RULES: tuple[OverlayRule, ...] = (
    OverlayRule(Overlay.SUPPORT_CONFIRM, ('support_confirm',)),
    OverlayRule(Overlay.SUPPORT_PARTY_CONFIRM, ('support_party_confirm',)),
    OverlayRule(Overlay.MASTER_EQUIPMENT, ('master_equipment',)),
    OverlayRule(Overlay.RETREAT_CONFIRM, ('retreat_confirm',)),
    OverlayRule(Overlay.REPEAT_DIALOG, ('repeat_dialog',)),
    OverlayRule(Overlay.COMMAND_SPELL_CONFIRM, ('command_spell_confirm',)),
    OverlayRule(Overlay.COMMAND_SPELL_TARGET, ('command_spell_target', 'command_spell_use')),
    OverlayRule(Overlay.COMMAND_SPELL_MENU, ('command_spell_repair',)),
    OverlayRule(Overlay.CLASS_INFO, ('class_info',)),
    OverlayRule(Overlay.BATTLE_MENU, ('battle_menu',), absent=('retreat_confirm',)),
    OverlayRule(Overlay.ORDER_CHANGE, ('order_change',)),
    # 御主技能面板只可能叠在战斗上，同时要求战斗 HUD 在场，避免助战卡片被误判成技能卡。
    OverlayRule(Overlay.MASTER_SKILLS, ('master_skills', 'battle_chrome')),
)

# 这两个全屏界面没有基页锚点：助战编队确认归入 support，御主装备归入 team。
OVERLAY_BASE_FALLBACK: dict[Overlay, BasePage] = {
    Overlay.SUPPORT_PARTY_CONFIRM: BasePage.SUPPORT,
    Overlay.MASTER_EQUIPMENT: BasePage.TEAM,
}

@dataclass(frozen=True)
class FrameEdgeProbe:
    """卡框探针：只数长直线边缘，不看卡面图案，因此与御主礼装、从者立绘无关。"""

    cards: tuple[Box, ...]
    threshold: float
    line_length: int = 30


# 御主技能展开 = 固定位置出现三张带框技能卡。展开时得分 351–613，其它画面最高 166。
FRAME_EDGE_PROBES: dict[str, FrameEdgeProbe] = {
    'master_skills': FrameEdgeProbe(
        cards=((868, 275, 945, 352), (957, 275, 1034, 352), (1046, 274, 1123, 352)),
        threshold=250.0,
    ),
}

# 亮度探针：平均灰度达到下限才算命中，用于“按钮已点亮 / 未置灰”这类颜色证据。
INTENSITY_PROBES: dict[str, tuple[Box, float]] = {
    'order_change_ready': ((478, 591, 803, 661), 150.0),
    # 职阶筛选图标：冠位助战有编制限制时部分职阶置灰（实测置灰 45–68，可点 86–128）。
    'filter_1': ((62, 98, 123, 159), 75.0),
    'filter_2': ((130, 98, 191, 159), 75.0),
    'filter_3': ((197, 98, 258, 159), 75.0),
    'filter_4': ((265, 98, 326, 159), 75.0),
    'filter_5': ((332, 98, 393, 159), 75.0),
    'filter_6': ((400, 98, 461, 159), 75.0),
    'filter_7': ((467, 98, 528, 159), 75.0),
    'filter_8': ((535, 98, 596, 159), 75.0),
    'filter_9': ((602, 98, 663, 159), 75.0),
    'filter_10': ((670, 98, 731, 159), 75.0),
    # AP 道具卡：0 持有的卡片整行变暗（实测 71，可用 150–178）。
    'ap_a_stone': ((315, 120, 981, 265), 100.0),
    'ap_a_gold': ((315, 267, 981, 412), 100.0),
    'ap_a_silver': ((315, 414, 981, 559), 100.0),
    'ap_b_silver': ((315, 143, 981, 267), 100.0),
    'ap_b_bronze': ((315, 290, 981, 415), 100.0),
    'ap_b_copper': ((315, 438, 981, 562), 100.0),
}

_SUPPORT_TOP: tuple[ControlSpec, ...] = (
    ControlSpec('返回', ControlKind.BUTTON, (16, 9, 172, 78)),
    ControlSpec('职阶筛选展开', ControlKind.BUTTON, (6, 96, 53, 162)),
    *(ControlSpec(f'职阶筛选{i}', ControlKind.BUTTON, box, anchor=f'filter_{i}')
      for i, box in enumerate([
          (62, 98, 123, 159), (130, 98, 191, 159), (197, 98, 258, 159), (265, 98, 326, 159),
          (332, 98, 393, 159), (400, 98, 461, 159), (467, 98, 528, 159), (535, 98, 596, 159),
          (602, 98, 663, 159), (670, 98, 731, 159)], start=1)),
    ControlSpec('职阶情报', ControlKind.BUTTON, (742, 101, 806, 159)),
    ControlSpec('技能显示切换', ControlKind.BUTTON, (812, 97, 883, 163)),
    ControlSpec('列表更新', ControlKind.BUTTON, (888, 98, 972, 162)),
    ControlSpec('排序器', ControlKind.BUTTON, (1104, 106, 1206, 152)),
    ControlSpec('升降序切换', ControlKind.BUTTON, (1221, 89, 1271, 154)),
)

PAGE_CONTROLS: dict[BasePage, tuple[ControlSpec, ...]] = {
    BasePage.MENU: (),
    # 助战卡片行随滚动移动，不写死；由 recognizer 逐帧检测后追加。
    BasePage.SUPPORT: _SUPPORT_TOP,
    BasePage.TEAM: (
        ControlSpec('返回', ControlKind.BUTTON, (16, 9, 172, 78)),
        *(ControlSpec(f'从者卡第{i}位', ControlKind.CARD, box) for i, box in enumerate([
            (36, 140, 229, 620), (236, 140, 429, 620), (436, 140, 629, 620),
            (651, 140, 844, 620), (851, 140, 1044, 620), (1051, 140, 1244, 620)], start=1)),
        ControlSpec('御主装备', ControlKind.BUTTON, (18, 645, 183, 713)),
        ControlSpec('变更配置', ControlKind.BUTTON, (184, 647, 260, 710)),
        ControlSpec('自动编队', ControlKind.BUTTON, (261, 647, 336, 710)),
        ControlSpec('队伍编成', ControlKind.BUTTON, (339, 650, 410, 708)),
        ControlSpec('职阶确认', ControlKind.BUTTON, (414, 647, 489, 710)),
        ControlSpec('特效详情设定', ControlKind.BUTTON, (493, 647, 584, 710)),
        ControlSpec('自动变还', ControlKind.BUTTON, (589, 647, 665, 710)),
        ControlSpec('开始任务', ControlKind.BUTTON, (1087, 637, 1243, 706)),
    ),
    # 战斗 HUD 整体消失时（攻击/技能/换人动画）这些按钮都不存在，一律不输出。
    BasePage.BATTLE: (
        ControlSpec('攻击', ControlKind.BUTTON, (1043, 515, 1230, 702), anchor='attack',
                    hide_when_missing=True),
        ControlSpec('战斗菜单', ControlKind.BUTTON, (1141, 162, 1245, 250), anchor='battle_chrome',
                    hide_when_missing=True),
        ControlSpec('御主技能', ControlKind.BUTTON, (1139, 271, 1248, 365), anchor='battle_chrome',
                    hide_when_missing=True),
        ControlSpec('令咒', ControlKind.BUTTON, (1000, 20, 1122, 133), anchor='battle_chrome',
                    hide_when_missing=True),
        *(ControlSpec(f'从者{slot}技能{skill}', ControlKind.SKILL,
                      (x, 548, x + 61, 610), anchor='battle_chrome', hide_when_missing=True)
          for slot, base_x in ((1, 43), (2, 365), (3, 675))
          for skill, x in ((1, base_x), (2, base_x + 87), (3, base_x + 174))),
        *(ControlSpec(f'敌方{i}', ControlKind.ENEMY, box, anchor='battle_chrome', hide_when_missing=True)
          for i, box in enumerate([(5, 0, 258, 108), (262, 0, 498, 108), (505, 0, 745, 108)], start=1)),
    ),
    BasePage.CARD: (
        *(ControlSpec(f'卡{i}', ControlKind.CARD, box) for i, box in enumerate([
            (46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
            (811, 415, 976, 655), (1066, 415, 1231, 655)], start=1)),
        ControlSpec('战斗速度', ControlKind.BUTTON, (1061, 36, 1209, 96)),
        ControlSpec('返回选卡', ControlKind.BUTTON, (1140, 662, 1262, 705)),
    ),
    BasePage.RESULT: (),
    BasePage.DROPS: (
        ControlSpec('下一步', ControlKind.BUTTON, (948, 606, 1272, 676)),
        ControlSpec('显示切换', ControlKind.BUTTON, (1043, 35, 1161, 68)),
    ),
    # 道具卡按实际滚动位置分两套布局，由 ap_stone / ap_bronze 锚点选择。
    BasePage.AP_RECOVERY: (
        ControlSpec('兑换青铜树苗', ControlKind.BUTTON, (807, 580, 973, 654)),
        ControlSpec('关闭', ControlKind.BUTTON, (527, 589, 753, 647)),
    ),
    BasePage.LOADING: (),
    BasePage.UNKNOWN: (),
}

AP_ITEM_LAYOUTS: dict[str, tuple[ControlSpec, ...]] = {
    'ap_stone': (
        ControlSpec('圣晶石', ControlKind.ITEM, (315, 120, 981, 265), anchor='ap_a_stone'),
        ControlSpec('黄金果实', ControlKind.ITEM, (315, 267, 981, 412), anchor='ap_a_gold'),
        ControlSpec('白银果实', ControlKind.ITEM, (315, 414, 981, 559), anchor='ap_a_silver'),
    ),
    'ap_bronze': (
        ControlSpec('白银果实', ControlKind.ITEM, (315, 143, 981, 267), anchor='ap_b_silver'),
        ControlSpec('青铜果实', ControlKind.ITEM, (315, 290, 981, 415), anchor='ap_b_bronze'),
        ControlSpec('赤铜果实', ControlKind.ITEM, (315, 438, 981, 562), anchor='ap_b_copper'),
    ),
}

VARIANT_CONTROLS: dict[Variant, tuple[ControlSpec, ...]] = {
    Variant.GRAND_SUPPORT: _SUPPORT_TOP,
    Variant.GRAND_TEAM: tuple(
        ControlSpec('返回', ControlKind.BUTTON, (6, 5, 178, 85)) if spec.name == '返回'
        else ControlSpec('开始任务', ControlKind.BUTTON, (1086, 635, 1248, 707)) if spec.name == '开始任务'
        else spec
        for spec in PAGE_CONTROLS[BasePage.TEAM]
    ),
}

# 变体额外追加的控件（不是替换基页布局）。
VARIANT_EXTRA_CONTROLS: dict[Variant, tuple[ControlSpec, ...]] = {
    Variant.SKILL_TARGET: (
        ControlSpec('目标从者1', ControlKind.TARGET, (218, 269, 442, 499)),
        ControlSpec('目标从者2', ControlKind.TARGET, (518, 269, 755, 499)),
        ControlSpec('目标从者3', ControlKind.TARGET, (832, 269, 1056, 499)),
        ControlSpec('关闭', ControlKind.BUTTON, (1056, 109, 1136, 194)),
    ),
}

OVERLAY_CONTROLS: dict[Overlay, tuple[ControlSpec, ...]] = {
    Overlay.SUPPORT_CONFIRM: (
        ControlSpec('是', ControlKind.BUTTON, (708, 534, 969, 592)),
        ControlSpec('否', ControlKind.BUTTON, (313, 534, 572, 592)),
    ),
    # 这两个界面正常流程不会出现，只需要能关掉。
    Overlay.SUPPORT_PARTY_CONFIRM: (
        ControlSpec('关闭', ControlKind.BUTTON, (6, 9, 172, 78)),
    ),
    Overlay.MASTER_EQUIPMENT: (
        ControlSpec('关闭', ControlKind.BUTTON, (6, 9, 172, 78)),
    ),
    Overlay.RETREAT_CONFIRM: (
        # 按钮框按亮区实测，中心即文字中心。
        ControlSpec('决定', ControlKind.BUTTON, (720, 335, 1042, 401)),
        ControlSpec('取消', ControlKind.BUTTON, (237, 335, 559, 401)),
    ),
    Overlay.REPEAT_DIALOG: (
        ControlSpec('连续出击', ControlKind.BUTTON, (709, 554, 970, 612)),
        ControlSpec('关闭', ControlKind.BUTTON, (313, 554, 574, 612)),
        ControlSpec('开关星光之砂', ControlKind.BUTTON, (437, 428, 595, 513)),
        ControlSpec('开关牵绊2倍', ControlKind.BUTTON, (686, 427, 845, 518)),
    ),
    Overlay.ORDER_CHANGE: (
        ControlSpec('先发第1位', ControlKind.TARGET, (36, 250, 229, 565)),
        ControlSpec('先发第2位', ControlKind.TARGET, (236, 250, 429, 565)),
        ControlSpec('先发第3位', ControlKind.TARGET, (436, 250, 629, 565)),
        ControlSpec('候补第1位', ControlKind.TARGET, (651, 250, 844, 565)),
        ControlSpec('候补第2位', ControlKind.TARGET, (851, 250, 1044, 565)),
        ControlSpec('候补第3位', ControlKind.TARGET, (1051, 250, 1244, 565)),
        ControlSpec('进行更替', ControlKind.BUTTON, (478, 591, 803, 661), anchor='order_change_ready'),
    ),
    Overlay.COMMAND_SPELL_MENU: (
        ControlSpec('宝具解放', ControlKind.BUTTON, (246, 296, 1033, 422)),
        ControlSpec('灵基修复', ControlKind.BUTTON, (246, 446, 1033, 572)),
        ControlSpec('关闭', ControlKind.BUTTON, (1056, 109, 1136, 194)),
    ),
    Overlay.COMMAND_SPELL_TARGET: (
        ControlSpec('目标从者1', ControlKind.TARGET, (178, 233, 452, 558)),
        ControlSpec('目标从者2', ControlKind.TARGET, (480, 233, 757, 558)),
        ControlSpec('目标从者3', ControlKind.TARGET, (845, 233, 1085, 558)),
        ControlSpec('关闭', ControlKind.BUTTON, (1056, 109, 1136, 194)),
    ),
    Overlay.COMMAND_SPELL_CONFIRM: (
        ControlSpec('决定', ControlKind.BUTTON, (692, 399, 958, 461)),
        ControlSpec('取消', ControlKind.BUTTON, (318, 400, 584, 461)),
    ),
    Overlay.CLASS_INFO: (
        ControlSpec('关闭', ControlKind.BUTTON, (348, 532, 607, 590)),
        ControlSpec('职阶星图分值效果', ControlKind.BUTTON, (673, 531, 932, 589)),
    ),
    Overlay.BATTLE_MENU: (
        ControlSpec('撤退', ControlKind.BUTTON, (752, 214, 1077, 283)),
        ControlSpec('开关技能使用确认', ControlKind.BUTTON, (866, 355, 932, 405)),
        ControlSpec('开关宝具动画匀速', ControlKind.BUTTON, (1093, 355, 1159, 405)),
        ControlSpec('开关缩短敌人消失', ControlKind.BUTTON, (866, 426, 932, 474)),
        ControlSpec('战利品一览', ControlKind.BUTTON, (419, 556, 611, 614)),
        ControlSpec('音量设定', ControlKind.BUTTON, (670, 557, 860, 613)),
    ),
    Overlay.MASTER_SKILLS: (
        ControlSpec('御主技能1', ControlKind.BUTTON, (868, 275, 945, 352)),
        ControlSpec('御主技能2', ControlKind.BUTTON, (957, 275, 1034, 352)),
        ControlSpec('御主技能3', ControlKind.BUTTON, (1046, 274, 1123, 352)),
    ),
}

# 助战卡片行的动态检测参数：卡片比背景亮，按行平均亮度分段。
SUPPORT_ROW_REGION: Box = (40, 150, 1216, 720)
SUPPORT_ROW_MIN_MEAN = 120.0
SUPPORT_ROW_MIN_HEIGHT = 60

# 字段只在明确需要读取的页面上出现；其余页面一律 NOT_APPLICABLE。
FIELD_PAGES: dict[str, frozenset[BasePage]] = {
    'wave': frozenset({BasePage.BATTLE, BasePage.CARD}),
    'turn': frozenset({BasePage.BATTLE, BasePage.CARD}),
    'np': frozenset({BasePage.BATTLE, BasePage.CARD}),
    'ap': frozenset({BasePage.AP_RECOVERY}),
}

# OCR 只读这些区域；区域用 1280×720 基准坐标。
WAVE_REGION: Box = (760, 0, 1060, 50)
TURN_REGION: Box = (760, 80, 1060, 140)
NP_REGION: Box = (0, 640, 1100, 720)

OCR_MIN_CONFIDENCE = 0.5

WAVE_PATTERN = r'(\d+)\s*/\s*(\d+)'
TURN_PATTERN = r'(\d+)\s*回合'
AP_PATTERN = r'行动力现在[：:]\s*(\d+)\s*/\s*(\d+)'
NP_PATTERN = r'^(\d+)\s*%$'
TURN_FALLBACK_REGION: Box = (900, 80, 980, 120)
