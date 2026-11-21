from __future__ import annotations

import json
import time
from pathlib import Path

from executor.session.events import EventSink, RunEvent


class RunHistoryRecorder(EventSink):
    def __init__(self, run_dir: str | Path, *, job_name: str, job_file: str | Path,
                 serial: str, region: str) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.history_path = self.run_dir.parent.parent / 'run-history.jsonl'
        self.job_name = job_name
        self.job_file = str(Path(job_file).resolve())
        self.serial = serial
        self.region = region
        self.started_at_ms = int(time.time() * 1000)
        self.battles_completed = 0
        self.recovery_used: dict[str, int] = {}
        self.written = False

    def emit(self, event: RunEvent) -> None:
        if event.type == 'recovery_used':
            item = event.data.get('item')
            count = event.data.get('count')
            if isinstance(item, str) and isinstance(count, int):
                self.recovery_used[item] = count
            return
        if event.type not in {'finished', 'stopped', 'failed'} or self.written:
            return
        if event.type == 'finished':
            completed = event.data.get('completed_runs')
            if isinstance(completed, int):
                self.battles_completed = completed
        result = {'finished': 'completed', 'stopped': 'stopped', 'failed': 'failed'}[event.type]
        reason = str(event.data.get('reason') or result)
        error_value = event.data.get('message') or (reason if event.type == 'failed' else None)
        finished_at_ms = max(self.started_at_ms, int(event.timestamp * 1000))
        record = {
            'id': self.run_dir.name,
            'jobName': self.job_name,
            'jobFile': self.job_file,
            'serial': self.serial,
            'region': self.region,
            'startedAt': self.started_at_ms,
            'finishedAt': finished_at_ms,
            'durationMs': finished_at_ms - self.started_at_ms,
            'result': result,
            'reason': reason,
            'battlesCompleted': self.battles_completed,
            'recoveryUsed': dict(self.recovery_used),
            'runDir': str(self.run_dir),
            'error': str(error_value) if error_value is not None else None,
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            stream.flush()
        self.written = True
