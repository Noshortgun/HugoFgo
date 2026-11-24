"""作业编译后的执行计划。只描述结构，不包含坐标或设备操作。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Diagnostic:
    path: str
    message: str
    wave: int | None = None
    turn: int | None = None


@dataclass(frozen=True)
class ActionPlan:
    kind: str
    title: str
    detail: str
    icon: str
    skill: int | None = None
    member_slot: int | None = None
    player_target_slot: int | None = None
    enemy_target: int | None = None
    secondary_selection: dict[str, Any] | None = None
    swap_out_slot: int | None = None
    swap_in_slot: int | None = None
    from_target: int | None = None
    to_target: int | None = None


@dataclass(frozen=True)
class AttackCardPlan:
    slot: int
    type: str
    deck_index: int | None = None


@dataclass(frozen=True)
class AttackPlan:
    mode: str
    cards: tuple[AttackCardPlan, ...]


@dataclass(frozen=True)
class TurnPlan:
    actions: tuple[ActionPlan, ...] = ()
    np_positions: dict[int, int | str] = field(default_factory=dict)
    attack: AttackPlan | None = None


@dataclass(frozen=True)
class WavePlan:
    number: int
    target: int
    turns: tuple[TurnPlan, ...]


@dataclass(frozen=True)
class FormationSlotPlan:
    slot: int
    name: str
    svt_id: int | None = None
    limit_count: int | None = None
    np_id: int | None = None
    role: str | None = None
    support: bool = False
    support_required: bool = False
    grand_required: bool = False
    ce: str | None = None
    ce_id: int | None = None
    grand_ce: dict[str, Any] | None = None
    grand_ce_ids: dict[str, Any] | None = None


@dataclass(frozen=True)
class FallbackStrategy:
    allow_np: bool = False
    servant_order: tuple[str, ...] = ()
    servant_order_slots: tuple[int, ...] | None = None
    cards: str = 'buster'
    color_priority: tuple[str, ...] = ('buster', 'arts', 'quick')
    strategy_order: tuple[str, ...] = ('servant', 'cards', 'affinity', 'crit')


@dataclass(frozen=True)
class ExecutionPlan:
    mystic_id: str
    enemy_count: int
    waves: tuple[WavePlan, ...]
    formation: tuple[FormationSlotPlan, ...]
    strategy: FallbackStrategy = field(default_factory=FallbackStrategy)
    strategy_by_wave: dict[int, FallbackStrategy | None] = field(default_factory=dict)


@dataclass
class CompileResult:
    plan: ExecutionPlan | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
