from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from executor.domain import ExecutionPlan


SUMMARY_NAME = 'summary.md'
ACTION_REASONS = {'battle_action', 'command_card_batch', 'skill_animation_skip', 'fake_run'}
STAGE_NAMES = {
    'battle_ready': '战斗恢复可操作',
    'skill_target': '技能目标面板已出现',
    'page': '后续画面已出现',
    'attack_end': '攻击后画面已出现',
}
STATUS_NAMES = {'completed': '正常完成', 'stopped': '已停止', 'failed': '失败停止'}
REASON_NAMES = {
    'completed': '正常完成',
    'manual_stop': '手动停止',
    'process_error': '执行器进程异常',
    'unknown_timeout': '未知画面等待超时',
    'action_confirmation_timeout': '动作后画面确认超时',
    'target_selection_timeout': '技能目标选择超时',
    'card_mismatch': '指令卡与作业不一致',
    'ap_recovery': 'AP 恢复停止',
    'ap_drained': 'AP 已清空',
}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _job_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    action_keys = {
        'member_slot': 'memberSlot', 'player_target_slot': 'playerTargetSlot',
        'enemy_target': 'enemyTarget', 'secondary_selection': 'secondarySelection',
        'swap_out_slot': 'swapOutSlot', 'swap_in_slot': 'swapInSlot',
        'from_target': 'fromTarget', 'to_target': 'toTarget',
    }
    waves = []
    for wave in plan.get('waves', []):
        turns = []
        for turn in wave.get('turns', []):
            actions = []
            for action in turn.get('actions', []):
                converted = dict(action)
                for source, target in action_keys.items():
                    value = converted.pop(source, None)
                    if value is not None:
                        converted[target] = value
                actions.append(converted)
            turns.append({'actions': actions})
        waves.append({'turnCount': len(turns), 'target': wave.get('target'), 'turns': turns})
    return {
        'name': '执行计划快照',
        'formation': [
            {'slot': member.get('slot'), 'name': member.get('name', '')}
            for member in plan.get('formation', [])
        ],
        'waves': waves,
    }


def _history_job(directory: Path) -> Path | None:
    history = directory.parent.parent / 'run-history.jsonl'
    if not history.is_file():
        return None
    try:
        for line in history.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row.get('id') == directory.name and row.get('jobFile'):
                return Path(row['jobFile'])
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return None


def _load_events(directory: Path) -> tuple[list[dict[str, Any]], list[str], bool]:
    path = directory / 'events.jsonl'
    if not path.is_file():
        return [], ['缺少 events.jsonl，无法判定动作是否执行或确认。'], False
    rows = []
    warnings = []
    invalid = 0
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict) and isinstance(row.get('data'), dict):
                    rows.append(row)
                else:
                    invalid += 1
            except json.JSONDecodeError:
                invalid += 1
    except (OSError, UnicodeError) as error:
        return [], [f'events.jsonl 读取失败：{error}'], False
    if invalid:
        warnings.append(f'events.jsonl 有 {invalid} 行无法解析，相关动作结果无法判定。')
    terminal = any(row.get('type') in {'finished', 'stopped', 'failed'} for row in rows)
    if not terminal:
        warnings.append('事件流缺少终端事件，不能确认事件记录是否完整。')
    return rows, warnings, terminal and not invalid


def _load_job(directory: Path, rows: list[dict[str, Any]], job_path: str | Path | None,
              warnings: list[str]) -> dict[str, Any] | None:
    candidates = [Path(job_path) if job_path is not None else None, directory / 'job.json']
    started = next((row for row in rows if row.get('type') == 'run_started'), None)
    if started and started['data'].get('job'):
        candidates.append(Path(started['data']['job']))
    candidates.append(_history_job(directory))
    for candidate in candidates:
        if candidate is None:
            continue
        raw = _read_json(candidate)
        if isinstance(raw, dict):
            return raw
        if candidate.exists():
            warnings.append(f'作业文件无法读取：{candidate}')
    snapshot = _read_json(directory / 'plan.json')
    if isinstance(snapshot, dict):
        return _job_from_plan(snapshot)
    warnings.append('缺少可用作业，动作名称只能使用原始控件名。')
    return None


def _timestamp(row: dict[str, Any]) -> float | None:
    value = row.get('timestamp')
    return float(value) if isinstance(value, (int, float)) else None


def _relative(timestamp: float | None, started: float | None) -> str:
    if timestamp is None or started is None:
        return '时间未知'
    return f'+{max(0.0, timestamp - started):.3f} 秒'


def _readable_time(timestamp: float | None) -> str:
    if timestamp is None:
        return '未知（缺少时间证据）'
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def _action_suffix(action: dict[str, Any]) -> str:
    kind = action.get('kind')
    skill = action.get('skill')
    if kind == 'skill' and isinstance(skill, int):
        return f'从者 {skill + 1} 技能'
    if kind == 'master' and isinstance(skill, int):
        return f'御主 {skill + 1} 技能'
    if kind == 'attack':
        return '宝具'
    return str(kind or '动作')


def _action_title(action: dict[str, Any]) -> str:
    title = str(action.get('title') or '未命名动作')
    return f'{title}［{_action_suffix(action)}］'


def _battle_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = 0
    result = []
    for row in rows:
        battle = max(1, completed + 1)
        if row.get('type') == 'action' and row['data'].get('control') == '连续出击' and completed:
            battle = completed
        result.append((battle, row))
        if row.get('type') == 'battle_completed':
            value = row['data'].get('completed_runs')
            if isinstance(value, int):
                completed = max(completed, value)
    return result


def _same_position(row: dict[str, Any], wave: int, turn: int, action_index: int) -> bool:
    data = row['data']
    return (data.get('wave') == wave and data.get('turn') == turn
            and data.get('action_index') == action_index)


def _confirmation(rows: list[tuple[int, dict[str, Any]]], battle: int, wave: int, turn: int,
                  action_index: int, clicked_at: float | None) -> list[dict[str, Any]]:
    matches = []
    for event_battle, row in rows:
        if (event_battle == battle and row.get('type') == 'action_confirmed'
                and _same_position(row, wave, turn, action_index)):
            timestamp = _timestamp(row)
            if clicked_at is None or timestamp is None or timestamp >= clicked_at:
                matches.append(row)
    return matches


def _confirmation_stages(confirmations: list[dict[str, Any]]) -> str:
    return '、'.join(dict.fromkeys(
        STAGE_NAMES.get(str(row['data'].get('stage')), str(row['data'].get('stage')))
        for row in confirmations))


def _click_matches(action: dict[str, Any], row: dict[str, Any]) -> bool:
    data = row['data']
    reason = data.get('reason')
    if reason not in ACTION_REASONS:
        return False
    if reason == 'fake_run':
        return True
    control = str(data.get('control') or '')
    kind = action.get('kind')
    if kind == 'skill':
        return ((control.startswith('从者') and '技能' in control)
                or control.startswith('目标从者') or control == '战斗跳过')
    if kind == 'master':
        return control.startswith('御主技能') or control.startswith('目标从者') or control == '战斗跳过'
    if kind == 'orderChange':
        return (control.startswith('御主技能') or control.startswith('先发第')
                or control.startswith('候补第') or control in {'进行更替', '战斗跳过'})
    if kind == 'target':
        return control.startswith('敌方')
    if kind == 'spell':
        return (control in {'令咒', '灵基修复', '宝具解放', '决定', '战斗跳过'}
                or control.startswith('目标从者'))
    if kind == 'attack':
        return data.get('noble_phantasm') is True and data.get('servant_slot') == action.get('memberSlot')
    return False


def _control_list(clicks: list[dict[str, Any]]) -> str:
    controls = []
    for row in clicks:
        control = str(row['data'].get('control') or '未命名控件')
        if control not in controls:
            controls.append(control)
    return '、'.join(controls)


def _completed_runs(result: dict[str, Any], rows: list[dict[str, Any]]) -> int | None:
    progress = result.get('progress')
    if isinstance(progress, dict) and isinstance(progress.get('completed_runs'), int):
        return progress['completed_runs']
    if isinstance(result.get('completed_runs'), int):
        return result['completed_runs']
    values = [row['data'].get('completed_runs') for row in rows
              if row.get('type') == 'battle_completed' and isinstance(row['data'].get('completed_runs'), int)]
    return max(values) if values else None


def render_summary(directory: str | Path, *, job_path: str | Path | None = None,
                   plan: ExecutionPlan | None = None) -> str:
    root = Path(directory)
    rows, warnings, complete_events = _load_events(root)
    result = _read_json(root / 'result.json')
    if not isinstance(result, dict):
        result = {}
        warnings.append('缺少或无法读取 result.json，运行状态与完成场数未知。')
    if plan is not None:
        _write_json(root / 'plan.json', asdict(plan))
        job = _job_from_plan(asdict(plan))
    else:
        job = _load_job(root, rows, job_path, warnings)
    positioned = _battle_rows(rows)
    event_battles = [battle for battle, row in positioned
                     if row.get('type') in {'action', 'wave_started', 'turn_started'}]
    battle_count = max(event_battles, default=_completed_runs(result, rows) or 1)
    started = result.get('started_at') if isinstance(result.get('started_at'), (int, float)) else None
    if started is None:
        started = next((timestamp for row in rows if (timestamp := _timestamp(row)) is not None), None)
    lines = [
        '# 运行动作摘要',
        '',
        '本摘要只整理输入与画面证据。已点击不代表游戏已受理；画面已确认不代表技能、宝具或其他游戏效果已生效。',
        '所有动作均没有独立生效确认。',
        '',
        f'- 作业：{job.get("name") or "未命名作业"}' if job else '- 作业：证据缺失',
        f'- 开始时间：{_readable_time(started)}',
        f'- 证据完整性：{"；".join(warnings) if warnings else "事件、结果与作业证据可读取"}',
        '',
    ]
    unexecuted = []
    if job:
        for battle in range(1, battle_count + 1):
            lines.extend([f'## 第 {battle} 场', ''])
            unpositioned = [row for event_battle, row in positioned
                            if event_battle == battle and row.get('type') == 'action'
                            and (not isinstance(row['data'].get('wave'), int)
                                 or not isinstance(row['data'].get('turn'), int))]
            if unpositioned:
                lines.extend(['### 场次准备与衔接', ''])
                for row in unpositioned:
                    lines.append(
                        f'- {_relative(_timestamp(row), started)}：已点击 `{row["data"].get("control")}`；'
                        '画面确认未关联；没有独立生效确认。')
                lines.append('')
            for wave_index, wave in enumerate(job.get('waves', []), 1):
                for turn_index, turn in enumerate(wave.get('turns', []), 1):
                    lines.extend([f'### 第 {wave_index} 面 · 第 {turn_index} 回合', ''])
                    used_ids = set()
                    actions = turn.get('actions', [])
                    for action_index, action in enumerate(actions):
                        clicks = []
                        for event_battle, row in positioned:
                            if event_battle != battle or row.get('type') != 'action':
                                continue
                            same_position = _same_position(row, wave_index, turn_index, action_index)
                            attack_position = (action.get('kind') == 'attack'
                                               and row['data'].get('wave') == wave_index
                                               and row['data'].get('turn') == turn_index
                                               and row['data'].get('action_index') == len(actions))
                            if not same_position and not attack_position:
                                continue
                            if not _click_matches(action, row):
                                continue
                            clicks.append(row)
                            used_ids.add(id(row))
                        title = _action_title(action)
                        if clicks:
                            clicked_at = _timestamp(clicks[0])
                            lines.append(f'{action_index + 1}. {title} — {_relative(clicked_at, started)}')
                            lines.append(f'   - 已点击：{len(clicks)} 次；输入：{_control_list(clicks)}。')
                            confirmation_index = (len(actions) if action.get('kind') == 'attack'
                                                  and any(row['data'].get('action_index') == len(actions)
                                                          for row in clicks) else action_index)
                            confirmations = _confirmation(
                                positioned, battle, wave_index, turn_index, confirmation_index, clicked_at)
                            if confirmations:
                                lines.append(f'   - 画面已确认：{_confirmation_stages(confirmations)}。')
                            else:
                                lines.append('   - 画面未确认。')
                            lines.append('   - 生效确认：没有独立生效确认。')
                        else:
                            lines.append(f'{action_index + 1}. {title}')
                            if complete_events:
                                lines.append('   - 未执行：截至终端事件没有对应输入。')
                                unexecuted.append(f'第 {battle} 场 / 第 {wave_index} 面 / 第 {turn_index} 回合：{title}')
                            else:
                                lines.append('   - 无法判定：事件证据不完整。')
                            lines.append('   - 生效确认：没有独立生效确认。')
                        lines.append('')
                    extras = []
                    for event_battle, row in positioned:
                        if (event_battle == battle and row.get('type') == 'action' and id(row) not in used_ids
                                and row['data'].get('wave') == wave_index
                                and row['data'].get('turn') == turn_index):
                            extras.append(row)
                    if extras:
                        lines.append('- 其他已发送输入：')
                        for row in extras:
                            data = row['data']
                            confirmations = _confirmation(
                                positioned, battle, wave_index, turn_index,
                                data.get('action_index'), _timestamp(row))
                            confirmations = [item for item in confirmations
                                             if item['data'].get('stage') in {'page', 'attack_end'}][:1]
                            if confirmations:
                                confirmation = f'画面已确认：{_confirmation_stages(confirmations)}'
                            else:
                                confirmation = '画面未确认'
                            lines.append(
                                f'  - {_relative(_timestamp(row), started)}：已点击 `{data.get("control")}`；'
                                f'{confirmation}；没有独立生效确认。')
                        lines.append('')
    else:
        lines.extend(['## 已记录输入', ''])
        for battle, row in positioned:
            if row.get('type') == 'action':
                data = row['data']
                lines.append(
                    f'- 第 {battle} 场 / 第 {data.get("wave", "?")} 面 / 第 {data.get("turn", "?")} 回合 / '
                    f'{_relative(_timestamp(row), started)}：已点击 `{data.get("control")}`；没有独立生效确认。')
        lines.append('')
    status = str(result.get('status') or 'unknown')
    reason = str(result.get('reason') or 'unknown')
    status_name = '手动停止' if reason == 'manual_stop' else STATUS_NAMES.get(status, '状态未知')
    completed = _completed_runs(result, rows)
    lines.extend([
        '## 运行汇总',
        '',
        f'- 运行状态：{status_name}',
        f'- 停止原因：{REASON_NAMES.get(reason, "未映射原因")}（`{reason}`）',
        f'- 完成场数：{completed if completed is not None else "未知"}',
        '- 未执行的计划动作：',
    ])
    if not complete_events:
        lines.append('  - 无法判定：事件缺失或不完整。')
    elif unexecuted:
        lines.extend(f'  - {item}' for item in unexecuted)
    else:
        lines.append('  - 无（按现有事件记录）。')
    lines.append('')
    return '\n'.join(lines)


def write_run_summary(directory: str | Path, job_path: str | Path | None = None, *,
                      plan: ExecutionPlan | None = None) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    output = root / SUMMARY_NAME
    output.write_text(render_summary(root, job_path=job_path, plan=plan), encoding='utf-8')
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='根据运行证据重新生成中文动作摘要，不修改 events.jsonl')
    parser.add_argument('run_dir', type=Path, help='运行证据目录')
    parser.add_argument('--job', type=Path, help='作业 JSON；缺省读取运行快照或历史记录')
    args = parser.parse_args(argv)
    output = write_run_summary(args.run_dir, args.job)
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
