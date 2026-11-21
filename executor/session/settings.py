from __future__ import annotations

import json
from pathlib import Path

from executor.core.ap_policy import RecoveryItem, RecoveryPolicy, RunSettings


def load_settings(path: str | Path) -> RunSettings:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return RunSettings()
    recovery = data['recovery']
    return RunSettings(
        battles=data['battles'],
        recovery=RecoveryPolicy(
            allowed=frozenset(RecoveryItem(item) for item in recovery['allowed']),
            drain_after_turns=recovery['drain_after_turns'],
        ),
    )


def save_settings(path: str | Path, settings: RunSettings) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = {
        'battles': settings.battles,
        'recovery': {
            'allowed': sorted(item.value for item in settings.recovery.allowed),
            'drain_after_turns': settings.recovery.drain_after_turns,
        },
    }
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination)
