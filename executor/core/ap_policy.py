from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RecoveryItem(str, Enum):
    SAINT_QUARTZ = 'saint_quartz'
    GOLD = 'gold'
    SILVER = 'silver'
    BRONZE = 'bronze'
    COPPER = 'copper'


CONTROL_NAMES = {
    RecoveryItem.SAINT_QUARTZ: '圣晶石',
    RecoveryItem.GOLD: '黄金果实',
    RecoveryItem.SILVER: '白银果实',
    RecoveryItem.BRONZE: '青铜果实',
    RecoveryItem.COPPER: '赤铜果实',
}


@dataclass(frozen=True)
class RecoveryPolicy:
    allowed: frozenset[RecoveryItem] = frozenset({RecoveryItem.BRONZE})
    drain_after_turns: bool = False
    priority: tuple[RecoveryItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'allowed', frozenset(self.allowed))
        object.__setattr__(self, 'priority', tuple(item for item in self.priority if item in self.allowed))


@dataclass(frozen=True)
class RunSettings:
    battles: int = 1
    recovery: RecoveryPolicy = field(default_factory=RecoveryPolicy)

    def __post_init__(self) -> None:
        if self.battles < 1:
            raise ValueError('连续出击场数必须至少为 1')


def select_recovery(policy: RecoveryPolicy, available: tuple[str, ...] | list[str]) -> RecoveryItem | None:
    visible = set(available)
    return next((item for item in (*policy.priority, *RecoveryItem)
                 if item in policy.allowed and CONTROL_NAMES[item] in visible), None)
