"""作业编译命令行：打印计划摘要或全部诊断。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from executor.domain.compiler import compile_job
from executor.domain.model import Diagnostic, ExecutionPlan


def _location(diagnostic: Diagnostic) -> str:
    if diagnostic.wave is not None and diagnostic.turn is not None:
        return f'第 {diagnostic.wave} 面 · 回合 {diagnostic.turn}'
    if diagnostic.wave is not None:
        return f'第 {diagnostic.wave} 面'
    return '作业'


def _print_summary(plan: ExecutionPlan) -> None:
    turn_count = sum(len(wave.turns) for wave in plan.waves)
    print(f'作业可编译：{len(plan.waves)} 面，{turn_count} 回合，敌方 {plan.enemy_count}')
    for wave in plan.waves:
        print(f'第 {wave.number} 面 · 目标 {wave.target} · {len(wave.turns)} 回合')
        for turn_number, turn in enumerate(wave.turns, start=1):
            print(f'  回合 {turn_number}：{len(turn.actions)} 个动作')
            for action in turn.actions:
                print(f'    - {action.kind}：{action.title}')
            if turn.np_positions:
                positions = '，'.join(f'{slot}→{position}' for slot, position in turn.np_positions.items())
                print(f'    宝具卡序：{positions}')
            if turn.attack is not None:
                cards = '，'.join(f'{card.slot}:{card.type}' for card in turn.attack.cards)
                print(f'    攻击：{turn.attack.mode}' + (f'（{cards}）' if cards else ''))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(prog='python -m executor.domain', description='编译并检查作业 JSON')
    parser.add_argument('path', help='作业文件路径')
    parser.add_argument('--json', action='store_true', help='输出完整执行计划 JSON')
    args = parser.parse_args(argv)

    try:
        text = Path(args.path).read_text(encoding='utf-8')
    except OSError as error:
        print(f'无法读取作业文件：{error}', file=sys.stderr)
        return 2

    result = compile_job(text)
    if result.diagnostics:
        print('作业无效：', file=sys.stderr)
        for diagnostic in result.diagnostics:
            print(f'- [{_location(diagnostic)}] {diagnostic.path}: {diagnostic.message}', file=sys.stderr)
        return 2
    if result.plan is None:
        print('作业无效：未生成执行计划', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(result.plan), ensure_ascii=False, indent=2))
    else:
        _print_summary(result.plan)
    return 0


if __name__ == '__main__':
    sys.exit(main())
