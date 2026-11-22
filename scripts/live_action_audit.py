#!/usr/bin/env python
"""实机动作审计：按场、面、回合对照作业控件、宝具槽位与卡序。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_events(run_dir: Path) -> list[dict]:
    path = run_dir / 'events.jsonl'
    if not path.exists():
        raise SystemExit(f'找不到 {path}')
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def planned_turn(turn: dict) -> tuple[list[str], list[tuple[int, int]]]:
    controls: list[str] = []
    automatic: list[int] = []
    for action in turn.get('actions', []):
        kind = action.get('kind')
        if kind == 'target':
            target = action.get('enemyTarget', action.get('toTarget'))
            controls.append(f'敌方{target}')
        elif kind == 'skill':
            member = action.get('memberSlot')
            controls.append(f'从者{member + 1}技能{action.get("skill") + 1}')
            target = action.get('playerTargetSlot')
            if target is not None and target != member:
                controls.append(f'目标从者{target + 1}')
        elif kind == 'master':
            controls.extend(['御主技能', f'御主技能{action.get("skill") + 1}'])
            target = action.get('playerTargetSlot')
            if target is not None:
                controls.append(f'目标从者{target + 1}')
        elif kind == 'attack':
            controls.append('攻击')
            member = action.get('memberSlot')
            if member is not None and member not in automatic:
                automatic.append(member)
    requests: list[tuple[int, int] | None] = [None, None, None]
    for slot_text, position in turn.get('npPositions', {}).items():
        slot = int(slot_text)
        if position == 'auto':
            if slot not in automatic:
                automatic.append(slot)
        elif isinstance(position, int) and 1 <= position <= 3:
            requests[position - 1] = (slot, position)
            if slot in automatic:
                automatic.remove(slot)
    for slot in automatic:
        for index, request in enumerate(requests):
            if request is None:
                requests[index] = (slot, index + 1)
                break
    return controls, [request for request in requests if request is not None]


def _ordered_missing(expected: list[str], actual: list[str]) -> list[str]:
    remaining = list(actual)
    missing = []
    for control in expected:
        if control in remaining:
            remaining = remaining[remaining.index(control) + 1:]
        else:
            missing.append(control)
    return missing


def audit(run_dir: Path, job_path: Path) -> dict:
    job = json.loads(job_path.read_text(encoding='utf-8'))
    result_path = run_dir / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
    actual: dict[tuple[int, int, int], list[str]] = {}
    cards: dict[tuple[int, int, int], list[dict]] = {}
    batches: dict[tuple[int, int, int], list[dict]] = {}
    battle = 1
    current_wave = 1
    current_turn = 1
    for event in load_events(run_dir):
        data = event.get('data', {})
        event_type = event.get('type')
        if event_type == 'wave_started':
            current_wave = data.get('wave', current_wave)
            current_turn = data.get('turn', 1)
        elif event_type == 'turn_started':
            current_wave = data.get('wave', current_wave)
            current_turn = data.get('turn', current_turn)
        elif event_type == 'action':
            control = data.get('control', '?')
            if control == '连续出击':
                battle += 1
                current_wave = 1
                current_turn = 1
                continue
            wave = data.get('wave', current_wave)
            turn = data.get('turn', current_turn)
            key = (battle, wave, turn)
            reason = data.get('reason', '')
            if reason in {'stable_actionable', 'battle_action'} or reason.endswith('fallback'):
                actual.setdefault(key, []).append(control)
            if reason == 'command_card_batch':
                cards.setdefault(key, []).append(data | {'control': control})
        elif event_type == 'card_batch_submitted' and data.get('batch_attempt') == 0:
            key = (battle, data.get('wave', current_wave), data.get('turn', current_turn))
            batches.setdefault(key, []).append(data)

    completed = result.get('progress', {}).get('completed_runs', 0)
    battle_count = max([completed, battle, *(key[0] for key in actual)] or [0])
    battle_reports = []
    all_ok = result.get('status') == 'completed'
    for battle_number in range(1, battle_count + 1):
        wave_reports = []
        battle_ok = True
        for wave_index, wave in enumerate(job.get('waves', []), start=1):
            for turn_index, turn in enumerate(wave.get('turns', []), start=1):
                key = (battle_number, wave_index, turn_index)
                expected, expected_np = planned_turn(turn)
                got = actual.get(key, [])
                submitted = batches.get(key, [])
                card_actions = cards.get(key, [])
                actual_np = []
                for card in card_actions:
                    if not card.get('control', '').startswith('宝具卡'):
                        continue
                    centers = (420, 635, 855)
                    slot = min(range(3), key=lambda index: abs(card.get('x', -10000) - centers[index]))
                    actual_np.append((slot, card.get('card_position')))
                missing = _ordered_missing(expected, got)
                np_ok = actual_np == expected_np
                batch_ok = (len(submitted) == 1 and len(submitted[0].get('controls', [])) == 3
                            and len(card_actions) == 3)
                ok = not missing and np_ok and batch_ok
                battle_ok = battle_ok and ok
                wave_reports.append({
                    'wave': wave_index, 'turn': turn_index, 'ok': ok,
                    'expected_controls': expected, 'actual_controls': got, 'missing_controls': missing,
                    'expected_np': expected_np, 'actual_np': actual_np,
                    'batch_count': len(submitted), 'card_action_count': len(card_actions),
                })
        battle_reports.append({'battle': battle_number, 'ok': battle_ok, 'turns': wave_reports})
        all_ok = all_ok and battle_ok
    return {'ok': all_ok, 'battle_count': battle_count, 'completed_runs': completed,
            'status': result.get('status'), 'reason': result.get('reason'), 'battles': battle_reports}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run_dir = Path(sys.argv[1])
    job_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('jobs/娄希、娄希、奥伯龙.json')
    report = audit(run_dir, job_path)
    print(f'运行目录：{run_dir}')
    print(f'作业计划：{job_path}')
    print(f'结果：{report["status"]}/{report["reason"]}；完成场数 {report["completed_runs"]}')
    for battle in report['battles']:
        print(f'第 {battle["battle"]} 场：{"OK" if battle["ok"] else "FAIL"}')
        for turn in battle['turns']:
            print(f'  第 {turn["wave"]} 面 第 {turn["turn"]} 回合  {"OK" if turn["ok"] else "FAIL"}')
            print(f'    计划控件：{turn["expected_controls"]}')
            print(f'    实际控件：{turn["actual_controls"]}')
            print(f'    宝具（槽位/卡位）：计划 {turn["expected_np"]}；实际 {turn["actual_np"]}')
            if turn['missing_controls']:
                print(f'    缺少：{turn["missing_controls"]}')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
