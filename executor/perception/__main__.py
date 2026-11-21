"""命令行查看一帧画面的感知结果，供离线核对与实机采证使用。

用法：python -m executor.perception <截图> [--fields wave,turn] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from executor.device.base import Frame
from executor.perception.recognizer import Perception


def describe(observation) -> dict:
    return {
        'basePage': observation.base_page.value,
        'variants': sorted(item.value for item in observation.variants),
        'overlays': sorted(item.value for item in observation.overlays),
        'actionable': observation.actionable,
        'controls': [
            {
                'name': control.name,
                'kind': control.kind.value,
                'center': list(control.center),
                'actionable': control.actionable,
                'reason': control.reason,
            }
            for control in observation.controls
        ],
        'fields': {
            name: {'status': field.status.value, 'value': field.value, 'confidence': field.confidence}
            for name, field in observation.fields.items()
        },
        'frameHash': observation.frame_hash,
        'confidence': observation.confidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--fields', default='', help='等待读取的字段，逗号分隔，如 wave,turn')
    parser.add_argument('--timeout', type=float, default=10.0)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    data = args.image.read_bytes()
    frame = Frame(image=data, width=1280, height=720, captured_at=0.0)
    perception = Perception()
    try:
        observation = perception.read(frame)
        names = [name.strip() for name in args.fields.split(',') if name.strip()]
        if names:
            observation = perception.read_fields(frame, names, timeout=args.timeout)
    finally:
        perception.close()

    payload = describe(observation)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"基页 {payload['basePage']} 变体 {payload['variants']} 覆盖层 {payload['overlays']} "
              f"可行动 {payload['actionable']} 置信度 {payload['confidence']}")
        for control in payload['controls']:
            mark = '可点' if control['actionable'] else '不可点'
            print(f"  [{mark}] {control['name']:8} {control['center']}  {control['reason']}")
        for name, field in payload['fields'].items():
            print(f"  字段 {name}: {field['status']} {field['value']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
