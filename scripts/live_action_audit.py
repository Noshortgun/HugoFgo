#!/usr/bin/env python
"""实机动作审计：把一次运行的 action 事件按面/回合展开，和作业计划逐项对照。

用法：
    cv/.venv/Scripts/python.exe .scratch/live_action_audit.py <run_dir> [job.json]

输出每个面/回合的实际动作清单，以及作业计划中该回合应执行的动作。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_events(run_dir: Path) -> list[dict]:
    path = run_dir / 'events.jsonl'
    if not path.exists():
        raise SystemExit(f'找不到 {path}')
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def planned(job_path: Path) -> dict[tuple[int, int], list[str]]:
    job = json.loads(job_path.read_text(encoding='utf-8'))
    plan: dict[tuple[int, int], list[str]] = {}
    for index, wave in enumerate(job.get('waves', []), start=1):
        for turn_index, turn in enumerate(wave.get('turns', []), start=1):
            plan[(index, turn_index)] = [action.get('title', '?') for action in turn.get('actions', [])]
    return plan


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run_dir = Path(sys.argv[1])
    job_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('jobs/娄希、娄希、奥伯龙.json')
    plan = planned(job_path) if job_path.exists() else {}

    current = (1, 1)
    actual: dict[tuple[int, int], list[str]] = {}
    order: list[tuple[int, int]] = []
    for event in load_events(run_dir):
        data = event.get('data', {})
        if event['type'] in ('wave_started', 'turn_started'):
            key = (data.get('wave', current[0]), data.get('turn', current[1]))
            current = key
            if key not in order:
                order.append(key)
        elif event['type'] == 'action':
            control = data.get('control', '?')
            if data.get('reason') == 'stable_actionable' or data.get('reason', '').endswith('fallback'):
                actual.setdefault(current, []).append(control)

    print(f'运行目录：{run_dir}')
    print(f'作业计划：{job_path}')
    print()
    for key in sorted(set(order) | set(actual)):
        wave, turn = key
        expected = plan.get((wave, turn))
        got = actual.get(key, [])
        mark = ''
        if expected is not None:
            missing = [item for item in expected if item not in got]
            mark = 'OK' if not missing else f'缺：{missing}'
        print(f'第 {wave} 面 第 {turn} 回合  {mark}')
        print(f'  计划：{expected if expected is not None else "（未编排，走补刀）"}')
        print(f'  实际：{got}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
