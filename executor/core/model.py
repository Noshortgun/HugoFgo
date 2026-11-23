from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from executor.core.ap_policy import RecoveryPolicy


class RunStatus(str, Enum):
    COMPLETED = 'completed'
    STOPPED = 'stopped'
    FAILED = 'failed'


@dataclass(frozen=True)
class RunOptions:
    run_count: int = 1
    recovery: RecoveryPolicy = field(default_factory=RecoveryPolicy)
    stable_seconds: float = 0.5
    coordinate_tolerance: int = 8
    action_timeout: float = 30.0
    page_timeout: float = 30.0
    battle_timeout: float = 300.0
    poll_interval: float = 0.05
    run_dir: str | None = None
    write_evidence: bool = True

    def __post_init__(self) -> None:
        if self.run_count < 1:
            raise ValueError('连续出击场数必须至少为 1')
        if min(self.stable_seconds, self.action_timeout, self.page_timeout,
               self.battle_timeout, self.poll_interval) < 0:
            raise ValueError('运行时间参数不能为负数')
        if self.coordinate_tolerance < 0:
            raise ValueError('坐标容差不能为负数')


@dataclass
class RunProgress:
    wave: int = 1
    turn: int = 1
    action_index: int = 0
    completed_runs: int = 0
    recovery_used: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    reason: str
    progress: RunProgress
    evidence_dir: str | None = None
    perception_stats: dict | None = None
