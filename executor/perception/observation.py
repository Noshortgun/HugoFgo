"""画面感知输出：只描述看到了什么，不决定下一步动作。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Box = tuple[int, int, int, int]


class ScreenState(str, Enum):
    """已实机采集并逐屏审查的画面分类。

    MENU 仅在枚举里占位：关卡选择不在 05A 范围（作业从助战选择开始），
    未配置判据，因此永远不会被判出，会落到 UNKNOWN。
    """

    SUPPORT = 'support'
    SUPPORT_CONFIRM = 'support_confirm'
    TEAM = 'team'
    BATTLE = 'battle'
    CARD = 'card'
    RESULT = 'result'
    DROPS = 'drops'
    REPEAT_DIALOG = 'repeat_dialog'
    AP_RECOVERY = 'ap_recovery'
    ORDER_CHANGE = 'order_change'
    COMMAND_SPELL_MENU = 'command_spell_menu'
    COMMAND_SPELL_TARGET = 'command_spell_target'
    COMMAND_SPELL_CONFIRM = 'command_spell_confirm'
    CLASS_INFO = 'class_info'
    BATTLE_MENU = 'battle_menu'
    MASTER_SKILLS = 'master_skills'
    LOADING = 'loading'
    MENU = 'menu'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Anchor:
    """一段 OCR 文字及其包围盒。"""

    text: str
    box: Box
    score: float


@dataclass(frozen=True)
class Button:
    """可点击元素。坐标来自实机标注，1280x720 基准。"""

    label: str
    box: Box


@dataclass(frozen=True)
class CardSlot:
    """指令卡位。暴击星率与克制/抵抗是两块独立显示，缺省表示本屏不显示。"""

    index: int
    box: Box
    crit_star: str | None = None
    affinity: str | None = None


@dataclass(frozen=True)
class Observation:
    """一帧画面的结构化描述。

    fields 的键按状态固定，避免调用方猜：
    - battle：wave、wave_total、turn、enemies_left、target_x、skill_cooldowns（可能缺失）
    - card：cards（卡位序号元组）
    - repeat_dialog：ap_current、ap_max、ap_cost
    - ap_recovery：items（本屏出现的恢复道具名）
    - 读不到必需键时 uncertain=True，并把键名放进 fields['missing']
    """

    state: ScreenState
    anchors: tuple[Anchor, ...] = ()
    buttons: tuple[Button, ...] = ()
    cards: tuple[CardSlot, ...] = ()
    fields: dict[str, Any] = field(default_factory=dict)
    uncertain: bool = True

    def button(self, label: str) -> Button | None:
        for item in self.buttons:
            if item.label == label:
                return item
        return None
