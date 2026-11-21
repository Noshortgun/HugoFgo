"""感知调参实验台：先出特征表，再改阈值。

对每帧打印识别结果和关键特征（基页/覆盖层/宝具卡槽位/敌人血条），
可选与回归期望对比。用来替代“看一张图改一次阈值”的做法。

用法：
  cv/.venv/Scripts/python.exe scripts/perception_lab.py <图片或目录...> [--np] [--enemies]
  cv/.venv/Scripts/python.exe scripts/perception_lab.py --fixtures [--np] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from executor.perception import Perception
from executor.perception.evidence import decode_frame, emblem_candidates
from executor.perception.regions import cn
from executor.perception.tests.fixtures import load_frame

FIXTURES = ROOT / 'executor/perception/fixtures/screens-cn.json'


def np_features(image) -> dict:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    result = {}
    for slot, search in cn.NP_SEARCH.items():
        face_box = cn.NP_SLOTS[slot]
        x1, y1, x2, y2 = face_box
        face = hsv[y1:y2, x1:x2]
        fraction = float(((face[..., 2] > 165) & (face[..., 1] < 110)).mean())
        candidates = emblem_candidates(image, search, face_box)
        best = max(candidates, key=lambda item: item['area'], default=None)
        result[slot] = {'face': fraction, 'candidate': best}
    return result


def describe(path: Path, perception: Perception, show_np: bool, show_enemies: bool) -> dict:
    observation = perception.read(load_frame(str(path)))
    row = {
        'file': str(path),
        'base': observation.base_page.value,
        'variants': sorted(item.value for item in observation.variants),
        'overlays': sorted(item.value for item in observation.overlays),
        'actionable': observation.actionable,
        'controls': [item.name for item in observation.controls],
    }
    if show_np and observation.base_page.value == 'card':
        row['np'] = np_features(decode_frame(load_frame(str(path))))
    if show_enemies and observation.base_page.value in ('battle', 'card'):
        from executor.perception.evidence import find_enemy_bars
        row['enemyBars'] = find_enemy_bars(decode_frame(load_frame(str(path))))
    return row


def print_row(row: dict, show_np: bool) -> None:
    name = Path(row['file']).name
    overlays = ','.join(row['overlays']) or '-'
    print(f"{name:42} {row['base']:11} v={row['variants']} o={overlays} act={row['actionable']}")
    if show_np and 'np' in row:
        for slot, feature in row['np'].items():
            candidate = feature['candidate']
            if candidate is None:
                print(f'  {slot}: face={feature["face"]:.2f} 无纹章候选')
            else:
                print(f"  {slot}: face={feature['face']:.2f} fam={candidate['family']} "
                      f"area={candidate['area']:.0f} {candidate['width']}x{candidate['height']} "
                      f"aspect={candidate['aspect']:.2f} fill={candidate['fill']:.2f} "
                      f"bottom={candidate['bottom']} ratio={candidate['center_ratio']:.2f} "
                      f"hit={candidate['hit']}")
    if 'enemyBars' in row:
        print(f"  敌人血条 {len(row['enemyBars'])} 条：{row['enemyBars']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('targets', nargs='*')
    parser.add_argument('--fixtures', action='store_true', help='跑 fixtures/screens-cn.json 的全部帧')
    parser.add_argument('--np', action='store_true', help='打印宝具卡槽位特征')
    parser.add_argument('--enemies', action='store_true', help='打印敌人血条')
    parser.add_argument('--json', action='store_true', help='输出 JSON 而不是表格')
    args = parser.parse_args()

    if args.fixtures:
        data = json.loads(FIXTURES.read_text(encoding='utf-8'))
        paths = [ROOT / case['file'] for group in ('regression', 'crossSession') for case in data[group]]
    else:
        if not args.targets:
            parser.error('需要图片/目录，或 --fixtures')
        paths = []
        for target in args.targets:
            path = Path(target)
            paths.extend(sorted(path.glob('*.png')) if path.is_dir() else [path])

    perception = Perception()
    rows = []
    try:
        for path in paths:
            if not path.exists():
                print(f'缺图：{path}', file=sys.stderr)
                continue
            row = describe(path, perception, args.np, args.enemies)
            rows.append(row)
            if not args.json:
                print_row(row, args.np)
    finally:
        perception.close()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
