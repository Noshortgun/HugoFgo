"""从已入库截图裁剪国服锚点模板，生成 `templates/cn/` 与清单。

运行：python -m executor.perception.build_templates
每次只读截图、重写模板，不调用设备。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from executor.perception.digits import digit_mask, normalize_glyph
from executor.perception.regions import cn

ROOT = Path(__file__).resolve().parents[2]
PAD = 2


@dataclass(frozen=True)
class AnchorSource:
    name: str
    source: str
    box: tuple[int, int, int, int]
    margin: int | tuple[int, int, int, int]
    threshold: float
    pad: int = PAD


@dataclass(frozen=True)
class DigitSource:
    style: str
    digit: int
    source: str
    box: tuple[int, int, int, int]
    variant: str | None = None


DIGITS: tuple[DigitSource, ...] = (
    DigitSource('wave', 1, 'captures/2026-11-23/key/manual-w1-t1-after-s3-07.png', (888, 17, 896, 34)),
    DigitSource('wave', 2, 'captures/2026-11-23/key/manual-w2-t1-start-10.png', (884, 17, 897, 34)),
    DigitSource('wave', 3, 'captures/2026-11-23/key/manual-w3-t1-start-15.png', (885, 17, 897, 34)),
    DigitSource('turn', 1, 'captures/2026-11-23/key/manual-w1-t1-after-s3-07.png', (911, 87, 918, 104)),
    DigitSource('turn', 2, 'captures/2026-11-23/key/manual-w2-t1-start-10.png', (907, 86, 919, 104)),
    DigitSource('turn', 2, 'captures/2026-11-23/key/live-battle-w2-turn2.png', (907, 87, 919, 104), 'live'),
    DigitSource('turn', 3, 'captures/2026-11-23/key/manual-w3-t1-start-15.png', (907, 86, 919, 104)),
    DigitSource('turn', 4, 'captures/2026-11-22/key/battle-w2-t4.png', (905, 87, 918, 104)),
    DigitSource('turn', 5, 'captures/2026-11-22/key/battle-w3-t5.png', (906, 87, 918, 104)),
    DigitSource('turn', 6, 'captures/2026-11-22/key/battle-w3-t6.png', (905, 86, 918, 104)),
    DigitSource('turn', 7, 'captures/2026-11-22/key/battle-w3-t7.png', (907, 87, 919, 104)),
    DigitSource('np', 0, 'captures/2026-11-23/key/manual-w1-t1-after-s3-07.png', (258, 661, 269, 675)),
    DigitSource('np', 0, 'captures/2026-11-22/key/card-crit-40-100-70-30-40.png', (245, 660, 256, 676), 'dim'),
    DigitSource('np', 1, 'captures/2026-11-23/key/manual-w1-t1-after-s3-07.png', (552, 661, 558, 675)),
    DigitSource('np', 1, 'captures/2026-11-22/key/card-crit-40-100-70-30-40.png', (235, 661, 242, 675), 'dim'),
    DigitSource('np', 2, 'captures/2026-11-20/key/battle-wave1.png', (245, 661, 255, 675)),
    DigitSource('np', 3, 'captures/2026-11-22/key/card-crit-40-100-70-30-40.png', (894, 660, 905, 676)),
    DigitSource('np', 4, 'captures/2026-11-23/key/manual-w1-t1-after-s3-07.png', (244, 661, 255, 675)),
    DigitSource('np', 5, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (564, 661, 575, 676)),
    DigitSource('np', 6, 'captures/2026-11-23/key/card-np-12-94-60.png', (879, 660, 890, 676)),
    DigitSource('np', 7, 'captures/2026-11-22/key/card-crit-40-100-70-30-40.png', (259, 661, 269, 675)),
    DigitSource('np', 8, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (880, 660, 891, 676)),
    DigitSource('np', 9, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (576, 660, 587, 676)),
    DigitSource('crit', 0, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (113, 365, 133, 380)),
    DigitSource('crit', 1, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (89, 365, 102, 380)),
    DigitSource('crit', 2, 'captures/2026-11-19-ember/key/10-np-card-select.png', (88, 362, 105, 377)),
    DigitSource('crit', 3, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (858, 367, 873, 382)),
    DigitSource('crit', 4, 'captures/2026-11-22/key/card-crit-50-60-100-40-30.png', (858, 371, 872, 385)),
    DigitSource('crit', 5, 'captures/2026-11-19-ember/key/10-np-card-select.png', (599, 362, 616, 377)),
    DigitSource('crit', 6, 'captures/2026-11-22/key/card-crit-50-60-100-40-30.png', (342, 370, 360, 385)),
    DigitSource('crit', 7, 'captures/2026-11-22/key/card-crit-40-100-70-30-40.png', (600, 368, 618, 382)),
    DigitSource('crit', 8, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (599, 368, 617, 383)),
    DigitSource('crit', 9, 'captures/2026-11-23/key/manual-w2-t1-cards-14.png', (1115, 366, 1134, 381)),
)


SOURCES: tuple[AnchorSource, ...] = (
    AnchorSource('menu', 'captures/2026-11-19-ember/key/01-daily-quest-list.png', (1060, 6, 1272, 56), 28, 0.55),
    AnchorSource('support', 'captures/2026-11-20/key/support-select-correct-ce.png', (1040, 9, 1269, 62), 30, 0.6),
    # 冠位从者的徽记随滚动出现在不同行，纵向给足余量；只取文字本体，不带卡片背景。
    AnchorSource('grand_support', 'captures/2026-11-20/key/163-grand-support.png', (110, 560, 153, 574), (32, 400, 30, 140), 0.7, pad=0),
    AnchorSource('support_confirm', 'captures/2026-11-20/key/support-list-update-confirm.png', (524, 340, 743, 377), 30, 0.6),
    AnchorSource('support_refresh_cooldown', 'captures/2026-11-22/key/support-refresh-cooldown.png', (555, 84, 724, 124), 30, 0.6),
    AnchorSource('team', 'captures/2026-11-20/key/team-confirm.png', (1106, 654, 1227, 689), 25, 0.6),
    AnchorSource('grand_team', 'captures/2026-11-20/key/165-grand-team.png', (605, 27, 675, 63), 25, 0.62),
    AnchorSource('battle', 'captures/2026-11-20/key/battle-wave1.png', (784, 12, 853, 35), 30, 0.58),
    AnchorSource('attack', 'captures/2026-11-20/key/battle-wave1.png', (1069, 564, 1196, 637), 25, 0.6),
    AnchorSource('card', 'captures/2026-11-20/key/card-select.png', (1084, 74, 1187, 102), 25, 0.6),
    AnchorSource('result', 'captures/2026-11-20/key/result.png', (529, 34, 749, 87), 25, 0.62),
    AnchorSource('drops', 'captures/2026-11-20/key/drops.png', (155, 36, 335, 69), 25, 0.6),
    AnchorSource('ap_recovery', 'captures/2026-11-20/key/ap-recovery.png', (571, 37, 709, 68), 25, 0.6),
    AnchorSource('ap_recovery_confirm', 'captures/2026-11-22/key/ap-recovery-confirm-bronze.png',
                 (500, 48, 780, 84), 30, 0.6),
    AnchorSource('ap_recovery_confirm_bronze', 'captures/2026-11-22/key/ap-recovery-confirm-bronze.png',
                 (450, 116, 600, 154), 25, 0.6),
    AnchorSource('storm_pod_insufficient', 'captures/2026-11-26/key/storm-pod-insufficient.png',
                 (505, 232, 775, 313), 30, 0.6),
    AnchorSource('loading', 'captures/2026-11-20/key/12-battle-start.png', (927, 676, 1012, 706), 25, 0.6),
    AnchorSource('repeat_dialog', 'captures/2026-11-20/key/repeat-dialog.png', (477, 98, 793, 127), 30, 0.6),
    AnchorSource('order_change', 'captures/2026-11-20/key/61-order-change-select.png', (585, 606, 697, 644), 25, 0.6),
    AnchorSource('command_spell_repair', 'captures/2026-11-20/key/70-command-spell-open.png', (288, 461, 415, 494), 25, 0.6),
    AnchorSource('command_spell_target', 'captures/2026-11-20/key/72-command-spell-target-select.png', (570, 176, 717, 211), 30, 0.6),
    AnchorSource('command_spell_use', 'captures/2026-11-20/key/72-command-spell-target-select.png', (459, 310, 587, 348), 30, 0.6),
    AnchorSource('command_spell_confirm', 'captures/2026-11-20/key/73-command-spell-confirm.png', (459, 308, 984, 347), 25, 0.62),
    AnchorSource('class_info', 'captures/2026-11-20/key/09-class-filter.png', (385, 130, 458, 151), 25, 0.62),
    AnchorSource('battle_status_change_detail', 'captures/2026-11-22/key/battle-status-change-detail.png', (540, 174, 740, 211), 30, 0.6),
    AnchorSource('battle_menu', 'captures/2026-11-20/key/55-battle-menu.png', (708, 365, 855, 395), 25, 0.6),
        AnchorSource('battle_chrome', 'captures/2026-11-20/key/battle-wave1.png', (1141, 162, 1245, 250), 25, 0.6),
    AnchorSource('support_party_confirm', 'captures/2026-11-19-ember/34-grand-support-confirm.png', (965, 12, 1267, 57), 25, 0.6),
    AnchorSource('master_equipment', 'captures/2026-11-19-mystic/key/01-mystic-combat.png', (1049, 7, 1266, 61), 25, 0.6),
    AnchorSource('result_bond', 'captures/2026-11-20/key/result.png', (80, 169, 300, 205), 30, 0.6),
    AnchorSource('result_exp', 'captures/2026-11-20/key/result-exp.png', (651, 186, 831, 225), 30, 0.6),
    AnchorSource('ap_stone', 'captures/2026-11-20/key/ap-recovery.png', (456, 130, 544, 164), 30, 0.6),
    AnchorSource('ap_bronze', 'captures/2026-11-20/key/ap-recovery-bronze.png', (455, 299, 574, 340), 30, 0.6),
    AnchorSource('repeat_star', 'captures/2026-11-20/key/repeat-dialog.png', (455, 425, 580, 452), (180, 12, 180, 12), 0.6),
    AnchorSource('repeat_bond_boost', 'captures/2026-11-20/key/repeat-dialog.png', (695, 425, 835, 452), (180, 12, 180, 12), 0.6),
    AnchorSource('story_skip', 'captures/2026-11-21/key/story.png', (1159, 22, 1223, 59), 20, 0.6),
    AnchorSource('story_skip_confirm', 'captures/2026-11-21/key/story-skip-confirm.png', (458, 300, 809, 339), 25, 0.6),
    AnchorSource('friend_request', 'captures/2026-11-21/key/friend-request.png', (509, 207, 757, 244), 25, 0.6),
    AnchorSource('retreat_confirm', 'captures/2026-11-19-mystic/key/12-retreat-confirm.png', (552, 270, 719, 302), 35, 0.6),
)


def read_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f'无法读取截图：{path}')
    if (image.shape[1], image.shape[0]) != cn.BASE_SIZE:
        image = cv2.resize(image, cn.BASE_SIZE, interpolation=cv2.INTER_AREA)
    return image


EMBLEMS = (
    # (name, source, box, threshold)
    ('np_arts', 'captures/2026-11-19-ember/key/11-np-selected.png', (330, 250, 430, 350), 0.65),
    # 冷却中图标的「剩余」二字，数字逐技能不同，不参与匹配。
    ('skill_cooldown', 'captures/2026-11-23/key/skill-cooldowns.png', (213, 601, 246, 616), 0.6),
)


def build_emblems() -> dict:
    emblems = {}
    for name, source, box, threshold in EMBLEMS:
        image = read_image(ROOT / source)
        x1, y1, x2, y2 = box
        patch = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        ok, buffer = cv2.imencode('.png', patch)
        if not ok:
            raise SystemExit(f'无法编码纹章模板：{name}')
        (cn.TEMPLATE_DIR / f'{name}.png').write_bytes(buffer.tobytes())
        emblems[name] = {'file': f'{name}.png', 'source': source, 'threshold': threshold}
    return emblems


def build_digits() -> dict:
    directory = cn.TEMPLATE_DIR / 'digits'
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob('*.png'):
        stale.unlink()
    digits = {}
    for source in DIGITS:
        image = read_image(ROOT / source.source)
        x1, y1, x2, y2 = source.box
        patch = normalize_glyph(digit_mask(image[y1:y2, x1:x2], source.style))
        if not patch.any():
            raise SystemExit(f'数字模板没有白色前景：{source.style}/{source.digit}')
        suffix = f'-{source.variant}' if source.variant else ''
        name = f'{source.style}-{source.digit}{suffix}.png'
        ok, buffer = cv2.imencode('.png', patch)
        if not ok:
            raise SystemExit(f'无法编码数字模板：{source.style}/{source.digit}')
        (directory / name).write_bytes(buffer.tobytes())
        digits[f'{source.style}/{source.digit}{suffix}'] = {
            'file': f'digits/{name}', 'source': source.source, 'box': list(source.box),
        }
    return digits


def build() -> dict:
    cn.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    templates = {}
    for anchor in SOURCES:
        image = read_image(ROOT / anchor.source)
        x1, y1, x2, y2 = anchor.box
        patch = cv2.cvtColor(
            image[max(0, y1 - anchor.pad):y2 + anchor.pad, max(0, x1 - anchor.pad):x2 + anchor.pad],
            cv2.COLOR_BGR2GRAY,
        )
        ok, buffer = cv2.imencode('.png', patch)
        if not ok:
            raise SystemExit(f'无法编码模板：{anchor.name}')
        (cn.TEMPLATE_DIR / f'{anchor.name}.png').write_bytes(buffer.tobytes())
        if isinstance(anchor.margin, int):
            margin = (anchor.margin,) * 4
        elif len(anchor.margin) == 2:
            margin = (anchor.margin[0], anchor.margin[1], anchor.margin[0], anchor.margin[1])
        else:
            margin = anchor.margin
        left, top, right, bottom = margin
        templates[anchor.name] = {
            'file': f'{anchor.name}.png',
            'source': anchor.source,
            'roi': [max(0, x1 - left), max(0, y1 - top),
                    min(cn.BASE_SIZE[0], x2 + right), min(cn.BASE_SIZE[1], y2 + bottom)],
            'threshold': anchor.threshold,
        }
    manifest = {'region': cn.REGION, 'baseSize': list(cn.BASE_SIZE), 'templates': templates,
                'emblems': build_emblems(), 'digits': build_digits()}
    (cn.TEMPLATE_DIR / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main() -> None:
    manifest = build()
    for name, spec in manifest['templates'].items():
        print(f"{name:26} {spec['file']:28} roi={spec['roi']} thr={spec['threshold']}")


if __name__ == '__main__':
    main()
