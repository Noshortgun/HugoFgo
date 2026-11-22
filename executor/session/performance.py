from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import median


NORMAL_CARD = re.compile(r'卡[1-5]$')


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def summarize_runs(run_dirs: list[str | Path]) -> dict:
    normal_intervals: list[float] = []
    attack_to_first: list[float] = []
    batches: list[dict] = []
    runs: list[dict] = []
    for value in run_dirs:
        directory = Path(value)
        events = [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()
                  if line.strip()]
        result_path = directory / 'result.json'
        result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
        runs.append({
            'run_dir': str(directory),
            'status': result.get('status'),
            'reason': result.get('reason'),
            'channel': result.get('device', {}).get('channel'),
        })
        pending_attack: dict | None = None
        previous_card: dict | None = None
        current_batch: dict | None = None
        for event in events:
            event_type = event.get('type')
            data = event.get('data', {})
            if event_type == 'action':
                control = data.get('control', '')
                if control == '攻击':
                    pending_attack = data | {'timestamp': event['timestamp']}
                    previous_card = None
                    continue
                is_card = bool(NORMAL_CARD.fullmatch(control)) or control.startswith('宝具卡')
                if not is_card:
                    previous_card = None
                    continue
                if pending_attack is not None:
                    same_turn = (pending_attack.get('wave'), pending_attack.get('turn')) == (
                        data.get('wave'), data.get('turn'))
                    explicit = data.get('attack_to_first_card_ms')
                    if explicit is not None:
                        attack_to_first.append(float(explicit))
                    elif same_turn:
                        attack_to_first.append((event['timestamp'] - pending_attack['timestamp']) * 1000)
                    pending_attack = None
                interval = data.get('interval_ms')
                same_batch = (previous_card is not None
                              and previous_card.get('batch_id') == data.get('batch_id')
                              and previous_card.get('batch_attempt') == data.get('batch_attempt'))
                same_turn = (previous_card is not None
                             and (previous_card.get('wave'), previous_card.get('turn')) == (
                                 data.get('wave'), data.get('turn')))
                if interval is not None and same_batch:
                    normal_intervals.append(float(interval))
                elif data.get('reason') != 'command_card_batch' and same_turn:
                    normal_intervals.append((event['timestamp'] - previous_card['timestamp']) * 1000)
                previous_card = data | {'timestamp': event['timestamp']}
            elif event_type == 'card_batch_submitted' and data.get('batch_attempt') == 0:
                current_batch = {
                    'batch_id': data.get('batch_id'),
                    'configured_interval_ms': data.get('configured_interval_ms'),
                    'outcome': 'pending',
                    'recovered': False,
                }
                batches.append(current_batch)
            elif (event_type == 'warning' and data.get('reason') == 'action_recovery'
                  and data.get('control') == 'command_card_batch' and current_batch is not None):
                current_batch['outcome'] = 'recovered'
                current_batch['recovered'] = True
            elif event_type == 'action_confirmed' and data.get('stage') == 'attack_end' and current_batch is not None:
                if current_batch['outcome'] == 'pending':
                    current_batch['outcome'] = 'succeeded'
                current_batch = None
            elif event_type in {'finished', 'stopped', 'failed'}:
                if current_batch is not None and current_batch['outcome'] == 'pending':
                    current_batch['outcome'] = 'unconfirmed'
                current_batch = None
    for batch in batches:
        if batch['outcome'] == 'pending':
            batch['outcome'] = 'unconfirmed'
    succeeded = sum(batch['outcome'] == 'succeeded' for batch in batches)
    normal_median = _median(normal_intervals)
    attack_median = _median(attack_to_first)
    return {
        'run_count': len(runs),
        'runs': runs,
        'normal_card_intervals': {
            'count': len(normal_intervals), 'median_ms': normal_median, 'samples_ms': normal_intervals,
            'threshold_ms': 450.0,
        },
        'attack_to_first_card': {
            'count': len(attack_to_first), 'median_ms': attack_median, 'samples_ms': attack_to_first,
            'threshold_ms': 1000.0,
        },
        'timing_thresholds_pass': bool(
            normal_median is not None and normal_median <= 450.0
            and attack_median is not None and attack_median <= 1000.0),
        'initial_card_batches': {
            'total': len(batches),
            'succeeded': succeeded,
            'recovered': sum(batch['recovered'] for batch in batches),
            'unconfirmed': sum(batch['outcome'] == 'unconfirmed' for batch in batches),
            'success_rate': succeeded / len(batches) if batches else None,
            'items': batches,
        },
    }
