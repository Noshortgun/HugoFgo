"""画面感知命令行：对单张图片或一个目录做识别，打印状态与关键字段。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from executor.device.base import Frame
from executor.perception import Perception


def _read(path: Path, perception: Perception):
    frame = Frame(image=path.read_bytes(), width=1280, height=720, captured_at=0.0)
    return perception.read(frame)


def _print(observation) -> None:
    print(f'状态：{observation.state.value}'
          + ('（不确定）' if observation.uncertain else ''))
    if observation.fields:
        fields = {key: value for key, value in observation.fields.items()}
        print('字段：' + json.dumps(fields, ensure_ascii=False))
    if observation.cards:
        cards = '，'.join(
            f'卡{card.index}:{card.crit_star or "-"}/{card.affinity or "-"}'
            for card in observation.cards)
        print(f'指令卡：{cards}')
    if observation.buttons:
        labels = '，'.join(button.label for button in observation.buttons)
        print(f'按钮 {len(observation.buttons)} 个：{labels}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='画面感知：识别截图属于哪个界面')
    parser.add_argument('--image', type=Path, help='单张图片')
    parser.add_argument('--dir', type=Path, help='目录下所有 png/jpg')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args(argv)

    perception = Perception()

    if args.image:
        observation = _read(args.image, perception)
        if args.json:
            print(json.dumps(asdict(observation), ensure_ascii=False, default=str))
        else:
            _print(observation)
        return 0

    if args.dir:
        files = sorted(path for path in args.dir.iterdir()
                       if path.suffix.lower() in {'.png', '.jpg', '.jpeg'})
        if not files:
            print(f'{args.dir} 里没有图片', file=sys.stderr)
            return 2
        rows = []
        for path in files:
            observation = _read(path, perception)
            rows.append((path.name, observation.state.value, observation.uncertain))
            if args.json:
                print(json.dumps({'file': path.name, 'state': observation.state.value,
                                  'uncertain': observation.uncertain,
                                  'fields': observation.fields}, ensure_ascii=False, default=str))
            else:
                print(f'{path.name:<40} {observation.state.value}'
                      + ('（不确定）' if observation.uncertain else ''))
        if not args.json:
            unknown = [name for name, state, _ in rows if state == 'unknown']
            print(f'\n共 {len(rows)} 张，unknown {len(unknown)} 张')
        return 0

    parser.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
