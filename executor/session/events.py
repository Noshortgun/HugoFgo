from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol


@dataclass(frozen=True)
class RunEvent:
    type: str
    timestamp: float
    data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'data', MappingProxyType(dict(self.data)))


class EventSink(Protocol):
    def emit(self, event: RunEvent) -> None: ...


class RunControl:
    def __init__(self) -> None:
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()


class JsonlEventSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a', encoding='utf-8')

    def emit(self, event: RunEvent) -> None:
        row = {'type': event.type, 'timestamp': event.timestamp, 'data': dict(event.data)}
        self.stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.stream.flush()

    def close(self) -> None:
        self.stream.close()


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def emit(self, event: RunEvent) -> None:
        self.events.append(event)
