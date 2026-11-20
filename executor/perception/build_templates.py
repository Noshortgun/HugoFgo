"""从验证集生成各状态的签名模板：状态判定用模板匹配，不做全图 OCR。"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'templates'

# 每个状态取一处“只有这个状态才有”的文字/图案区域，坐标来自实机标注
SIGNATURES = {
    'support': ('support-select-correct-ce', (1035, 5, 1275, 65)),
    'support_confirm': ('support-list-update-confirm', (510, 330, 755, 385)),
    'team': ('team-confirm', (1045, 5, 1275, 65)),
    'battle': ('battle-wave1', (1060, 555, 1205, 645)),
    'card': ('card-select', (1050, 60, 1220, 110)),
    'result': ('result', (500, 25, 780, 95)),
    'drops': ('drops', (130, 25, 400, 80)),
    'repeat_dialog': ('repeat-dialog', (460, 90, 810, 135)),
    'ap_recovery': ('ap-recovery-bronze', (550, 25, 730, 80)),
    'order_change': ('61-order-change-select', (360, 145, 920, 185)),
    'command_spell_menu': ('70-command-spell-open', (280, 300, 430, 350)),
    'command_spell_target': ('72-command-spell-target-select', (550, 165, 740, 220)),
    'command_spell_confirm': ('73-command-spell-confirm', (450, 296, 995, 340)),
    'class_info': ('09-class-filter', (375, 122, 470, 160)),
    'battle_menu': ('55-battle-menu', (745, 205, 1085, 290)),
    'loading': ('12-battle-start', (555, 160, 725, 225)),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for state, (name, box) in SIGNATURES.items():
        source = ROOT / 'captures/2026-11-20/key' / f'{name}.jpg'
        image = cv2.imdecode(np.fromfile(str(source), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise SystemExit(f'缺少参考截图：{source}')
        x1, y1, x2, y2 = box
        template = image[y1:y2, x1:x2]
        target = OUT / f'{state}.png'
        target.write_bytes(cv2.imencode('.png', template)[1].tobytes())
        index[state] = {'box': list(box), 'source': f'{name}.jpg', 'template': target.name}
    (OUT / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'生成 {len(index)} 个状态模板 → {OUT}')


if __name__ == '__main__':
    main()
