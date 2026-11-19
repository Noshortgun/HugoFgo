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
class ExecutionPlan:
    mystic_id: str
    enemy_count: int
    waves: tuple[WavePlan, ...]


@dataclass
class CompileResult:
    plan: ExecutionPlan | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
