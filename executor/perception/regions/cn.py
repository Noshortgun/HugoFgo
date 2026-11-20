"""国服画面识别配置：锚点、优先级、控件布局与字段适用范围都集中在这里。

只包含国服（简体中文）规则；日服、台服的文案与坐标不进入本模块。
锚点本身是模板 patch，几何与阈值由 `build_templates.py` 生成到 `templates/cn/`。
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
    variant: Variant
    page: BasePage
    anchors: tuple[str, ...]


@dataclass(frozen=True)
class ControlSpec:
    """固定布局的可点目标；anchor 非空时，只有该锚点命中才算可行动。"""

    name: str
    kind: ControlKind
    box: Box
    anchor: str | None = None


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
)

# support_party_confirm 目前没有可用样本，等补采后再加规则；不要用“助战编队确认”
# 这个按钮文案硬判，冠位助战页本身就带这个按钮。
OVERLAY_RULES: tuple[OverlayRule, ...] = (
    OverlayRule(Overlay.SUPPORT_CONFIRM, ('support_confirm',)),
    OverlayRule(Overlay.RETREAT_CONFIRM, ('retreat_confirm',)),
    OverlayRule(Overlay.REPEAT_DIALOG, ('repeat_dialog',)),
    OverlayRule(Overlay.COMMAND_SPELL_CONFIRM, ('command_spell_confirm',)),
    OverlayRule(Overlay.COMMAND_SPELL_TARGET, ('command_spell_target', 'command_spell_use')),
    OverlayRule(Overlay.COMMAND_SPELL_MENU, ('command_spell_repair',)),
    OverlayRule(Overlay.CLASS_INFO, ('class_info',)),
    OverlayRule(Overlay.BATTLE_MENU, ('battle_menu',), absent=('retreat_confirm',)),
    OverlayRule(Overlay.ORDER_CHANGE, ('order_change',)),
    OverlayRule(Overlay.MASTER_SKILLS, ('master_skills',)),
)

# 覆盖层是模态的：出现时基页控件一律不可行动，只暴露覆盖层自己的控件。
BLOCKING_OVERLAYS: frozenset[Overlay] = frozenset(Overlay)

# 亮度探针：平均灰度达到下限才算命中，用于“按钮已点亮”这类颜色证据。
INTENSITY_PROBES: dict[str, tuple[Box, float]] = {
    'order_change_ready': ((478, 591, 803, 661), 150.0),
}

PAGE_CONTROLS: dict[BasePage, tuple[ControlSpec, ...]] = {
    BasePage.MENU: (),
    BasePage.SUPPORT: (
        ControlSpec('返回', ControlKind.BUTTON, (16, 9, 172, 78)),
        ControlSpec('列表更新', ControlKind.BUTTON, (888, 98, 972, 162)),
        ControlSpec('助战卡片第1行', ControlKind.ROW, (40, 168, 1216, 343)),
        ControlSpec('助战卡片第2行', ControlKind.ROW, (40, 346, 1216, 539)),
        ControlSpec('助战卡片第3行', ControlKind.ROW, (40, 546, 1216, 715)),
    ),
    BasePage.TEAM: (
        ControlSpec('返回', ControlKind.BUTTON, (16, 9, 172, 78)),
        ControlSpec('开始任务', ControlKind.BUTTON, (1087, 637, 1243, 706)),
    ),
    BasePage.BATTLE: (
        ControlSpec('攻击', ControlKind.BUTTON, (1043, 515, 1230, 702), anchor='attack'),
        ControlSpec('战斗菜单', ControlKind.BUTTON, (1141, 162, 1245, 250)),
        ControlSpec('御主技能', ControlKind.BUTTON, (1139, 271, 1248, 365)),
        ControlSpec('令咒', ControlKind.BUTTON, (1000, 20, 1122, 133)),
    ),
    BasePage.CARD: (
        ControlSpec('卡1', ControlKind.CARD, (46, 415, 211, 655)),
        ControlSpec('卡2', ControlKind.CARD, (301, 415, 466, 655)),
        ControlSpec('卡3', ControlKind.CARD, (556, 415, 721, 655)),
        ControlSpec('卡4', ControlKind.CARD, (811, 415, 976, 655)),
        ControlSpec('卡5', ControlKind.CARD, (1066, 415, 1231, 655)),
        ControlSpec('战斗速度', ControlKind.BUTTON, (1061, 36, 1209, 96)),
        ControlSpec('返回选卡', ControlKind.BUTTON, (1140, 662, 1262, 705)),
    ),
    BasePage.RESULT: (),
    BasePage.DROPS: (
        ControlSpec('下一步', ControlKind.BUTTON, (948, 606, 1272, 676)),
    ),
    BasePage.AP_RECOVERY: (
        ControlSpec('兑换青铜树苗', ControlKind.BUTTON, (807, 580, 973, 654)),
        ControlSpec('关闭', ControlKind.BUTTON, (527, 589, 753, 647)),
    ),
    BasePage.LOADING: (),
    BasePage.UNKNOWN: (),
}

VARIANT_CONTROLS: dict[Variant, tuple[ControlSpec, ...]] = {
    Variant.GRAND_SUPPORT: (
        ControlSpec('返回', ControlKind.BUTTON, (11, 8, 173, 79)),
        ControlSpec('列表更新', ControlKind.BUTTON, (891, 102, 975, 162)),
        ControlSpec('助战卡片第1行', ControlKind.ROW, (40, 189, 1216, 382)),
        ControlSpec('助战卡片第2行', ControlKind.ROW, (40, 389, 1216, 582)),
        ControlSpec('助战卡片第3行', ControlKind.ROW, (40, 589, 1216, 702)),
    ),
    Variant.GRAND_TEAM: (
        ControlSpec('返回', ControlKind.BUTTON, (6, 5, 178, 85)),
        ControlSpec('开始任务', ControlKind.BUTTON, (1086, 635, 1248, 707)),
    ),
}

OVERLAY_CONTROLS: dict[Overlay, tuple[ControlSpec, ...]] = {
    Overlay.SUPPORT_CONFIRM: (
        ControlSpec('是', ControlKind.BUTTON, (708, 534, 969, 592)),
        ControlSpec('否', ControlKind.BUTTON, (313, 534, 572, 592)),
    ),
    Overlay.RETREAT_CONFIRM: (
        ControlSpec('决定', ControlKind.BUTTON, (692, 340, 958, 400)),
        ControlSpec('取消', ControlKind.BUTTON, (318, 340, 584, 400)),
    ),
    Overlay.REPEAT_DIALOG: (
        ControlSpec('连续出击', ControlKind.BUTTON, (709, 554, 970, 612)),
        ControlSpec('关闭', ControlKind.BUTTON, (313, 554, 574, 612)),
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
    ),
    Overlay.BATTLE_MENU: (
        ControlSpec('撤退', ControlKind.BUTTON, (752, 214, 1077, 283)),
        ControlSpec('战利品一览', ControlKind.BUTTON, (419, 556, 611, 614)),
        ControlSpec('音量设定', ControlKind.BUTTON, (670, 557, 860, 613)),
    ),
    Overlay.MASTER_SKILLS: (
        ControlSpec('御主技能1', ControlKind.BUTTON, (868, 275, 945, 352)),
        ControlSpec('御主技能2', ControlKind.BUTTON, (957, 275, 1034, 352)),
        ControlSpec('御主技能3', ControlKind.BUTTON, (1046, 274, 1123, 352)),
    ),
    Overlay.SUPPORT_PARTY_CONFIRM: (),
}

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
