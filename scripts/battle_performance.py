#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from executor.session.performance import summarize_runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='按同一事件口径汇总普通选卡、攻击到首卡与初始三卡提交结果')
    parser.add_argument('run_dirs', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--enforce-thresholds', action='store_true')
    args = parser.parse_args(argv)
    report = summarize_runs(args.run_dirs)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    return 0 if not args.enforce_thresholds or report['timing_thresholds_pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
