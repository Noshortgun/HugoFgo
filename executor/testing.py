from __future__ import annotations

from dataclasses import replace
from unittest import mock

from executor.core import RunOptions, run
from executor.device.base import Frame
from executor.device.fake import FakeDevice
from executor.domain import ExecutionPlan
from executor.perception import Observation
from executor.session import MemoryEventSink


class ScriptedPerception:
    def __init__(self, observations, *, preserve_hashes: bool = False) -> None:
        self.observations = tuple(observations)
        self.preserve_hashes = preserve_hashes

    def observation(self, frame: Frame) -> Observation:
        observation = self.observations[int(frame.image)]
        if self.preserve_hashes:
            return observation
        return replace(observation, frame_hash=f'{observation.frame_hash}:{frame.image.decode()}')

    def read(self, frame: Frame) -> Observation:
        return self.observation(frame)

    def read_fields(self, frame: Frame, names, *, timeout=None) -> Observation:
        return self.observation(frame)


def scenario_frames(times, expected_count: int | None = None) -> list[Frame]:
    if expected_count is not None and len(times) != expected_count:
        raise ValueError(f'观察数与时间数必须一致：{expected_count} != {len(times)}')
    return [Frame(image=str(index).encode(), width=1280, height=720, captured_at=at)
            for index, at in enumerate(times)]


def run_scenario(observations, times, plan: ExecutionPlan, *, options=None,
                 required_controls: dict[int, tuple[str, ...]] | None = None):
    device = FakeDevice(scenario_frames(times, len(observations)))
    for index, names in (required_controls or {}).items():
        require_controls(observations[index], *names)
    events = MemoryEventSink()
    run_options = options or RunOptions(write_evidence=False)
    perception = ScriptedPerception(observations)
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        result = run(plan, device, perception, events, options=run_options)
    return result, device, events


def require_controls(observation: Observation, *names: str) -> None:
    for name in names:
        control = observation.control(name)
        if control is None or not control.actionable:
            raise ValueError(f'场景缺少可行动控件：{name}')
