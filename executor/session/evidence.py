from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

from executor.device.base import Device, DeviceError, Frame, FrameFormat
from executor.domain import ExecutionPlan
from executor.perception import Observation
from executor.session.events import EventSink, JsonlEventSink, RunEvent

if TYPE_CHECKING:
    from executor.core.model import RunResult


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def observation_data(observation: Observation) -> dict:
    return {
        'base_page': observation.base_page.value,
        'variants': sorted(item.value for item in observation.variants),
        'overlays': sorted(item.value for item in observation.overlays),
        'controls': [
            {
                'name': control.name,
                'kind': control.kind.value,
                'box': control.box,
                'actionable': control.actionable,
                'reason': control.reason,
                'card': asdict(control.card) if control.card is not None else None,
                'support': asdict(control.support) if control.support is not None else None,
            }
            for control in observation.controls
        ],
        'fields': {
            name: {'status': field.status.value, 'value': field.value, 'confidence': field.confidence}
            for name, field in observation.fields.items()
        },
        'frame_hash': observation.frame_hash,
        'confidence': observation.confidence,
        'evidence': dict(observation.evidence),
    }


class RunEvidence:
    def __init__(self, directory: str | Path, plan: ExecutionPlan, device: Device,
                 events: EventSink) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.plan = plan
        self.device = device
        self.downstream = events
        self.jsonl = JsonlEventSink(self.directory / 'events.jsonl')
        self.latest: tuple[Frame, Observation] | None = None
        self.recent_events: list[dict] = []
        self.started_at = time.time()
        self.closed = False

    def emit(self, event: RunEvent) -> None:
        if event.type in {'failed', 'stopped'} and 'evidence_dir' not in event.data:
            event = RunEvent(event.type, event.timestamp,
                             {**event.data, 'evidence_dir': str(self.directory)})
        self.downstream.emit(event)
        self.jsonl.emit(event)
        self.recent_events.append({'type': event.type, 'timestamp': event.timestamp, 'data': dict(event.data)})
        self.recent_events = self.recent_events[-20:]

    def observe(self, frame: Frame, observation: Observation) -> None:
        self.latest = (frame, observation)

    def close(self) -> None:
        if not self.closed:
            self.jsonl.close()
            self.closed = True

    def finish(self, result: RunResult) -> RunResult:
        observation_path = None
        failure_path = None
        if self.latest is not None:
            frame, observation = self.latest
            observation_path = self.directory / 'observation.json'
            write_json(observation_path, observation_data(observation))
            if result.status.value != 'completed':
                suffix = 'png' if frame.format is FrameFormat.PNG else 'bgr24'
                failure_path = self.directory / f'failure.{suffix}'
                failure_path.write_bytes(frame.image)
        device = {}
        if self.latest is not None:
            try:
                metrics = asdict(self.device.metrics())
                metrics['region'] = metrics['region'].value
                device = metrics
            except DeviceError as error:
                device = {'metrics_error': str(error)}
        config = getattr(self.device, 'config', None)
        if config is not None:
            device['channel'] = config.channel.value
        if self.latest is not None:
            frame, _ = self.latest
            device['last_frame_id'] = frame.frame_id
            device['last_session_id'] = frame.session_id
        write_json(self.directory / 'result.json', {
            'status': result.status.value,
            'reason': result.reason,
            'progress': asdict(result.progress),
            'job': {
                'mystic_id': self.plan.mystic_id,
                'enemy_count': self.plan.enemy_count,
                'waves': [
                    {'number': wave.number, 'target': wave.target, 'turn_count': len(wave.turns)}
                    for wave in self.plan.waves
                ],
            },
            'device': device,
            'started_at': self.started_at,
            'finished_at': time.time(),
            'observation': str(observation_path) if observation_path is not None else None,
            'failure_frame': str(failure_path) if failure_path is not None else None,
            'recent_events': self.recent_events,
        })
        self.close()
        return replace(result, evidence_dir=str(self.directory))
