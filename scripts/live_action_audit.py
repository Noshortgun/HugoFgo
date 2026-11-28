#!/usr/bin/env python
"""实机动作审计：按场、面、回合对照作业控件、宝具槽位与卡序。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from executor.core.runner import card_requests
from executor.domain import TurnPlan
from executor.domain.compiler import compile_job


def load_events(run_dir: Path) -> list[dict]:
    path = run_dir / 'events.jsonl'
    if not path.exists():
        raise SystemExit(f'找不到 {path}')
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def planned_turn(turn: TurnPlan) -> tuple[list[str], list[tuple[int, int]],
                                            list[tuple[int, str, int | None, int]]]:
    controls: list[str] = []
    for action in turn.actions:
        if action.kind == 'target':
            target = action.enemy_target if action.enemy_target is not None else action.to_target
            controls.append(f'敌方{target}')
        elif action.kind == 'skill':
            controls.append(f'从者{action.member_slot + 1}技能{action.skill + 1}')
            if action.secondary_selection:
                controls.append(f'追加选项{action.secondary_selection["index"] + 1}')
            target = action.player_target_slot
            if target is not None and target != action.member_slot:
                controls.append(f'目标从者{target + 1}')
        elif action.kind == 'master':
            controls.extend(['御主技能', f'御主技能{action.skill + 1}'])
            if action.player_target_slot is not None:
                controls.append(f'目标从者{action.player_target_slot + 1}')
        elif action.kind == 'attack':
            controls.append('攻击')
    requests = card_requests(turn)
    np_cards = [(request.servant_slot, position)
                for position, request in enumerate(requests, start=1) if request.noble_phantasm]
    normal_cards = [(request.servant_slot, request.card_type, request.deck_index, position)
                    for position, request in enumerate(requests, start=1) if not request.noble_phantasm]
    return controls, np_cards, normal_cards


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
    compiled = compile_job(job_path.read_text(encoding='utf-8'))
    if compiled.plan is None:
        detail = '；'.join(f'{item.path}: {item.message}' for item in compiled.diagnostics)
        raise SystemExit(f'作业无法编译，不能审计：{job_path}：{detail}')
    job = compiled.plan
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
    all_ok = result.get('status') == 'completed' or result.get('reason') == 'card_mismatch'
    for battle_number in range(1, battle_count + 1):
        wave_reports = []
        battle_ok = True
        for wave_index, wave in enumerate(job.waves, start=1):
            for turn_index, turn in enumerate(wave.turns, start=1):
                key = (battle_number, wave_index, turn_index)
                expected, expected_np, expected_normal = planned_turn(turn)
                got = actual.get(key, [])
                submitted = batches.get(key, [])
                card_actions = cards.get(key, [])
                actual_np = []
                actual_normal = []
                for card in card_actions:
                    if card.get('control', '').startswith('宝具卡'):
                        centers = (420, 635, 855)
                        slot = min(range(3), key=lambda index: abs(card.get('x', -10000) - centers[index]))
                        actual_np.append((slot, card.get('card_position')))
                    else:
                        actual_normal.append((card.get('servant_slot'), card.get('card_type'),
                                              card.get('deck_index'), card.get('card_position')))
                missing = _ordered_missing(expected, got)
                card_stop = result.get('reason') == 'card_mismatch'
                np_ok = card_stop or actual_np == expected_np
                fixed_normal = (turn.attack is not None and turn.attack.mode in {'verify', 'prefer'}
                                and bool(turn.attack.cards))
                normal_ok = card_stop or not fixed_normal or actual_normal == expected_normal
                batch_ok = card_stop or (len(submitted) == 1 and len(submitted[0].get('controls', [])) == 3
                                         and len(card_actions) == 3)
                ok = not missing and np_ok and normal_ok and batch_ok
                battle_ok = battle_ok and ok
                wave_reports.append({
                    'wave': wave_index, 'turn': turn_index, 'ok': ok,
                    'expected_controls': expected, 'actual_controls': got, 'missing_controls': missing,
                    'expected_np': expected_np, 'actual_np': actual_np, 'np_ok': np_ok,
                    'expected_normal': expected_normal, 'actual_normal': actual_normal, 'normal_ok': normal_ok,
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
            print(f'    普通卡（槽位/卡色/牌库索引/卡位）：计划 {turn["expected_normal"]}；'
                  f'实际 {turn["actual_normal"]}')
            if turn['missing_controls']:
                print(f'    缺少：{turn["missing_controls"]}')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
