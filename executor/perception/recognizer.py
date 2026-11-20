"""画面感知适配器：设备帧进来，Observation 出去。

只做「看懂画面」：判状态、给按钮坐标、读关键字段。
不点击、不读作业、不推进流程。
"""
from __future__ import annotations

import re
from typing import Any

import cv2
import numpy as np

from executor.device.base import Frame
from executor.perception.observation import Anchor, Button, CardSlot, Observation, ScreenState
from executor.perception.regions import load_config

TARGET_SIZE = (1280, 720)
OCR_THRESHOLD = 0.5

# 判定优先级：先判覆盖层与特殊层，再判底层，避免同一帧命中多个状态。
ORDER: tuple[ScreenState, ...] = (
    ScreenState.COMMAND_SPELL_CONFIRM,
    ScreenState.COMMAND_SPELL_TARGET,
    ScreenState.COMMAND_SPELL_MENU,
    ScreenState.CLASS_INFO,
    ScreenState.BATTLE_MENU,
    ScreenState.MASTER_SKILLS,
    ScreenState.ORDER_CHANGE,
    ScreenState.AP_RECOVERY,
    ScreenState.REPEAT_DIALOG,
    ScreenState.SUPPORT_CONFIRM,
    ScreenState.SUPPORT,
    ScreenState.TEAM,
    ScreenState.DROPS,
    ScreenState.RESULT,
    ScreenState.CARD,
    ScreenState.LOADING,
    ScreenState.BATTLE,
)

# 这些状态读不到关键字段时算「不确定」，而不是猜一个值。
REQUIRED_FIELDS: dict[ScreenState, tuple[str, ...]] = {
    ScreenState.BATTLE: ('wave', 'turn'),
    ScreenState.REPEAT_DIALOG: ('ap_current', 'ap_cost'),
}

MASTER_SKILL_SLOTS = ((868, 275, 945, 352), (957, 275, 1034, 352), (1046, 274, 1123, 352))
MASTER_SKILL_MIN_VALUE = 150.0

AP_ITEMS = ('圣晶石', '黄金果实', '白银果实', '青铜果实', '赤铜果实')

_ocr_engine: Any | None = None


class PerceptionError(RuntimeError):
    """帧无法解码或识别器不可用。"""


def _ocr() -> Any:
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:  # pragma: no cover - 环境缺失时给出可操作信息
            raise PerceptionError('缺少 rapidocr-onnxruntime，无法识别画面') from error
        _ocr_engine = RapidOCR()
    return _ocr_engine


def _decode(frame: Frame) -> np.ndarray:
    data = np.frombuffer(frame.image, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise PerceptionError('设备帧不是有效的图片')
    if (image.shape[1], image.shape[0]) != TARGET_SIZE:
        image = cv2.resize(image, TARGET_SIZE, interpolation=cv2.INTER_AREA)
    return image


def _read_anchors(image: np.ndarray) -> tuple[Anchor, ...]:
    result, _ = _ocr()(image)
    anchors = []
    for box, text, score in result or []:
        if float(score) < OCR_THRESHOLD:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        anchors.append(Anchor(
            text=text.strip(),
            box=(round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))),
            score=round(float(score), 3),
        ))
    return tuple(anchors)


def _center(anchor: Anchor) -> tuple[float, float]:
    x1, y1, x2, y2 = anchor.box
    return (x1 + x2) / 2, (y1 + y2) / 2


def _inside(anchor: Anchor, area: list[int] | None) -> bool:
    if not area:
        return True
    cx, cy = _center(anchor)
    x1, y1, x2, y2 = area
    return x1 <= cx <= x2 and y1 <= cy <= y2


def _find(anchors: tuple[Anchor, ...], needle: str, area: list[int] | None = None) -> Anchor | None:
    for anchor in anchors:
        if needle in anchor.text and _inside(anchor, area):
            return anchor
    return None


def _check(anchors: tuple[Anchor, ...], criteria: list[dict]) -> bool:
    for criterion in criteria:
        found = _find(anchors, criterion['expect'], criterion.get('area'))
        if criterion['kind'] == 'text-exists' and found is None:
            return False
        if criterion['kind'] == 'text-absent' and found is not None:
            return False
    return True


def _zoom_text(image: np.ndarray, area: tuple[int, int, int, int], scale: int = 8) -> str:
    """小号数字常常漏识别：放大 + 二值化后再读一次。"""
    x1, y1, x2, y2 = area
    crop = image[y1:y2, x1:x2]
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    result, _ = _ocr()(binary)
    return ' '.join(text for _, text, score in (result or []) if float(score) >= OCR_THRESHOLD)


def _master_skills_open(image: np.ndarray) -> bool:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    values = [float(hsv[y1:y2, x1:x2, 2].mean()) for x1, y1, x2, y2 in MASTER_SKILL_SLOTS]
    return sum(values) / len(values) >= MASTER_SKILL_MIN_VALUE


def _int_in(text: str) -> int | None:
    match = re.search(r'(\d+)', text)
    return int(match.group(1)) if match else None


def _fields_for(state: ScreenState, anchors: tuple[Anchor, ...],
                buttons: dict[str, Button],
                image: np.ndarray) -> tuple[dict[str, Any], tuple[CardSlot, ...]]:
    fields: dict[str, Any] = {}
    cards: tuple[CardSlot, ...] = ()

    if state is ScreenState.BATTLE:
        wave = _find(anchors, '/3', [850, 0, 990, 50])
        if wave:
            fields['wave'] = _int_in(wave.text.split('/')[0] or '')
            fields['wave_total'] = _int_in(wave.text.split('/')[-1] or '')
        turn = _find(anchors, '回合', [850, 75, 990, 120])
        if turn:
            fields['turn'] = _int_in(turn.text)
        if fields.get('turn') is None:
            fields['turn'] = _int_in(_zoom_text(image, (855, 78, 990, 118)))
        left = _find(anchors, '还剩')
        if left:
            fields['enemies_left'] = _int_in(left.text)
        target = _find(anchors, '目标')
        if target:
            fields['target_x'] = round(_center(target)[0])

    elif state is ScreenState.CARD:
        slots = []
        for index in range(1, 6):
            button = buttons.get(f'卡{index}') or buttons.get(f'卡{index}（指令卡）')
            if button is None:
                continue
            x1, y1, x2, y2 = button.box
            # 暴击星率：优先取百分比；暴击率为 0 时这整块不显示，返回 None。
            crit_area = [x1, 340, x1 + 140, 420]
            percent = next((a.text for a in anchors
                            if a.text.endswith('%') and _inside(a, crit_area)), None)
            crit = percent or next((a.text for a in anchors
                                    if '暴击星' in a.text and _inside(a, crit_area)), None)
            # 克制/抵抗：与暴击分开显示，识别不清时保留原始文字交给上层判断。
            affinity_area = [x1 + 120, 360, x2 + 40, 420]
            affinity_anchor = next((a for a in anchors if _inside(a, affinity_area)), None)
            affinity = affinity_anchor.text if affinity_anchor else None
            # OCR 常把“克制”读成“克利/就利”：只按首字归一，保留“抵抗”分支。
            if affinity and affinity.startswith('克'):
                affinity = '克制'
            elif affinity and affinity.startswith('抵'):
                affinity = '抵抗'
            slots.append(CardSlot(
                index=index,
                box=button.box,
                crit_star=crit,
                affinity=affinity,
            ))
        cards = tuple(slots)

    elif state is ScreenState.REPEAT_DIALOG:
        current = _find(anchors, '行动力现在')
        if current:
            match = re.search(r'(\d+)\s*/\s*(\d+)', current.text)
            if match:
                fields['ap_current'] = int(match.group(1))
                fields['ap_max'] = int(match.group(2))
        cost = _find(anchors, '行动力消耗')
        if cost:
            match = re.search(r'(\d+)', cost.text)
            if match:
                fields['ap_cost'] = int(match.group(1))
            else:
                # 数值常被 OCR 拆成独立的一段，取标签右侧最近的数字
                right = [a for a in anchors
                         if a.text.strip().isdigit() and a.box[0] >= cost.box[2]
                         and abs((a.box[1] + a.box[3]) / 2 - (cost.box[1] + cost.box[3]) / 2) < 30]
                if right:
                    fields['ap_cost'] = int(min(right, key=lambda a: a.box[0]).text.strip())

    elif state is ScreenState.AP_RECOVERY:
        fields['items'] = [name for name in AP_ITEMS if _find(anchors, name)]

    return fields, cards


class Perception:
    """`read(frame)` 是唯一入口。配置在构造时加载一次。"""

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or load_config()

    def read(self, frame: Frame) -> Observation:
        image = _decode(frame)
        anchors = _read_anchors(image)
        states = self._config['states']

        matched = ScreenState.UNKNOWN
        for state in ORDER:
            spec = states.get(state.value)
            if spec is None:
                continue
            if not _check(anchors, spec['criteria']):
                continue
            if state is ScreenState.MASTER_SKILLS and not _master_skills_open(image):
                continue
            matched = state
            break

        if matched is ScreenState.UNKNOWN:
            return Observation(state=ScreenState.UNKNOWN, anchors=anchors, uncertain=True)

        spec = states[matched.value]
        buttons = {item['label']: Button(label=item['label'], box=tuple(item['box']))
                   for item in spec['buttons']}
        fields, cards = _fields_for(matched, anchors, buttons, image)

        missing = [name for name in REQUIRED_FIELDS.get(matched, ())
                   if fields.get(name) is None]
        uncertain = bool(missing)
        if missing:
            fields['missing'] = missing

        return Observation(
            state=matched,
            anchors=anchors,
            buttons=tuple(buttons.values()),
            cards=cards,
            fields=fields,
            uncertain=uncertain,
        )
