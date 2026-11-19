"""画面感知输出：只描述看到了什么，不决定下一步动作。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Anchor:
    text: str
    x: int
    y: int
    confidence: float


@dataclass(frozen=True)
class CardCandidate:
    label: str
    x: int
    y: int
    confidence: float


@dataclass(frozen=True)
class Observation:
    state: str
    anchors: tuple[Anchor, ...] = ()
    cards: tuple[CardCandidate, ...] = ()
    wave: int | None = None
    turn: int | None = None
    uncertain: bool = True
    confidence: float = 0.0
