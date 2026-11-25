"""画面感知输出：只描述看到了什么，不决定下一步动作。

基页、画面变体与覆盖层彼此独立：战斗里可以叠着御主技能面板，结算页也会残留战斗 HUD。
可操作性单独表达，`battle` 不代表攻击按钮可用，`card` 也不代表还能继续选卡。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class BasePage(str, Enum):
    """画面所属的主页面，与覆盖层分开。"""

    MENU = 'menu'
    SUPPORT = 'support'
    TEAM = 'team'
    BATTLE = 'battle'
    CARD = 'card'
    RESULT = 'result'
    DROPS = 'drops'
    AP_RECOVERY = 'ap_recovery'
    LOADING = 'loading'
    STORY = 'story'
    UNKNOWN = 'unknown'


class Variant(str, Enum):
    """同一基页下的版式变体；变体不扩张执行器状态机。"""

    GRAND_SUPPORT = 'grand_support'
    GRAND_TEAM = 'grand_team'
    RESULT_BOND = 'bond'
    RESULT_EXP = 'exp'
    DROPS_LOOT = 'loot'
    SKILL_TARGET = 'skill_target'
    BATTLE_ANIMATION = 'animation'


class Overlay(str, Enum):
    """叠加在基页之上的面板或弹窗；可与基页并存，覆盖基页的可操作性。"""

    SUPPORT_CONFIRM = 'support_confirm'
    SUPPORT_REFRESH_COOLDOWN = 'support_refresh_cooldown'
    SUPPORT_PARTY_CONFIRM = 'support_party_confirm'
    REPEAT_DIALOG = 'repeat_dialog'
    AP_RECOVERY_CONFIRM = 'ap_recovery_confirm'
    ORDER_CHANGE = 'order_change'
    COMMAND_SPELL_MENU = 'command_spell_menu'
    COMMAND_SPELL_TARGET = 'command_spell_target'
    COMMAND_SPELL_CONFIRM = 'command_spell_confirm'
    CLASS_INFO = 'class_info'
    BATTLE_STATUS_CHANGE_DETAIL = 'battle_status_change_detail'
    BATTLE_MENU = 'battle_menu'
    MASTER_SKILLS = 'master_skills'
    RETREAT_CONFIRM = 'retreat_confirm'
    MASTER_EQUIPMENT = 'master_equipment'
    FRIEND_REQUEST = 'friend_request'
    STORY_SKIP_CONFIRM = 'story_skip_confirm'


class ControlKind(str, Enum):
    BUTTON = 'button'
    CARD = 'card'
    TARGET = 'target'
    ROW = 'row'
    SKILL = 'skill'
    ENEMY = 'enemy'
    ITEM = 'item'


class FieldStatus(str, Enum):
    """字段的读取状态；“不需要读”和“读取失败”必须区分。"""

    NOT_APPLICABLE = 'not_applicable'
    NOT_READ = 'not_read'
    UNCERTAIN = 'uncertain'
    CONFIRMED = 'confirmed'


Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class CommandCardIdentity:
    servant_slot: int
    card_type: str
    deck_index: int | None = None
    noble_phantasm: bool = False
    svt_id: int | None = None
    support: bool = False
    crit_rate: float | None = None
    affinity: str | None = None


@dataclass(frozen=True)
class SupportCandidate:
    name: str
    svt_id: int | None = None
    role: str | None = None
    ce: str | None = None
    ce_id: int | None = None
    grand: bool = False
    grand_ce: dict[str, object] | None = None
    grand_ce_ids: dict[str, object] | None = None


@dataclass(frozen=True)
class Control:
    """画面上的一个可点目标。box 是 1280×720 基准坐标下的 (x1, y1, x2, y2)。"""

    name: str
    kind: ControlKind
    box: Box
    actionable: bool
    reason: str = ''
    card: CommandCardIdentity | None = None
    support: SupportCandidate | None = None
    on_cooldown: bool | None = None
    """技能冷却测量：True 冷却中，False 可用，None 表示这一帧没有测量。"""

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.box
        return (round((x1 + x2) / 2), round((y1 + y2) / 2))


@dataclass(frozen=True)
class Field:
    """一个按需读取的字段值。只有 CONFIRMED 的值可以直接驱动动作。"""

    status: FieldStatus
    value: object | None = None
    confidence: float | None = None

    @property
    def usable(self) -> bool:
        return self.status is FieldStatus.CONFIRMED


@dataclass(frozen=True)
class Observation:
    """一次画面读取的快照。构造后不可修改，字段结果只属于该帧。"""

    base_page: BasePage = BasePage.UNKNOWN
    variants: frozenset[Variant] = frozenset()
    overlays: frozenset[Overlay] = frozenset()
    controls: tuple[Control, ...] = ()
    fields: Mapping[str, Field] = field(default_factory=dict)
    frame_hash: str = ''
    confidence: float = 0.0
    evidence: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'variants', frozenset(self.variants))
        object.__setattr__(self, 'overlays', frozenset(self.overlays))
        object.__setattr__(self, 'controls', tuple(self.controls))
        object.__setattr__(self, 'fields', MappingProxyType(dict(self.fields)))
        object.__setattr__(self, 'evidence', MappingProxyType(dict(self.evidence)))

    @property
    def actionable(self) -> bool:
        return any(control.actionable for control in self.controls)

    def control(self, name: str) -> Control | None:
        return next((control for control in self.controls if control.name == name), None)

    def field(self, name: str) -> Field | None:
        return self.fields.get(name)

    def with_fields(self, fields: Mapping[str, Field]) -> Observation:
        merged = dict(self.fields)
        merged.update(fields)
        return replace(self, fields=merged)
