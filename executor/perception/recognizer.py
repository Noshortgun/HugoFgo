"""画面感知适配器：设备帧进来，Observation 出去。

性能策略（按实测数据定的）：
1. 状态判定用**模板匹配**（毫秒级），不做全图 OCR；
2. 只有需要读字段的状态才 OCR，而且只把**需要的区域拼成一张图**做一次识别；
3. 同一帧（字节完全一致）直接复用上一次结果，不重复识别。

只做「看懂画面」：判状态、给按钮坐标、读关键字段。不点击、不读作业、不推进流程。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import cv2
import numpy as np

from executor.device.base import Frame
from executor.perception.observation import Anchor, Button, CardSlot, Observation, ScreenState
from executor.perception.regions import load_config

TARGET_SIZE = (1280, 720)
OCR_THRESHOLD = 0.5
TEMPLATE_THRESHOLD = 0.72

TEMPLATE_DIR = __import__('pathlib').Path(__file__).resolve().parent / 'templates'

# 判定优先级：先判覆盖层与特殊层，再判底层。
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

REQUIRED_FIELDS: dict[ScreenState, tuple[str, ...]] = {
    ScreenState.BATTLE: ('wave', 'turn', 'enemies_left'),
    ScreenState.CARD: ('cards',),
    ScreenState.REPEAT_DIALOG: ('ap_current', 'ap_cost'),
}

# 御主技能展开靠三个图标槽的亮度判断（文字无法区分）
MASTER_SKILL_SLOTS = ((868, 275, 945, 352), (957, 275, 1034, 352), (1046, 274, 1123, 352))
# 用「技能槽 / 左侧背景」的亮度比判断是否展开：绝对值会被整体亮度带偏
MASTER_SKILL_REFERENCE = (760, 275, 860, 352)
MASTER_SKILL_MIN_RATIO = 1.10

AP_ITEMS = ('圣晶石', '黄金果实', '白银果实', '青铜果实', '赤铜果实')

# 字段读取区域统一在 cn.json 的 field_areas，不在代码里另写一套

_ocr_engine: Any | None = None
SKILL_BAND = (0, 530, 940, 650)
SKILL_ZOOM = 6
SKILL_OTSU = 140
_templates: dict[ScreenState, tuple[np.ndarray, tuple[int, int, int, int]]] | None = None
_observation_cache: dict[str, Observation] = {}


class PerceptionError(RuntimeError):
    """帧无法解码或识别器不可用。"""


def _ocr() -> Any:
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:  # pragma: no cover
            raise PerceptionError('缺少 rapidocr-onnxruntime，无法识别画面') from error
        try:
            _ocr_engine = RapidOCR(use_dml=True)
        except Exception:  # DirectML 不可用时退回 CPU
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


def _load_templates() -> dict[ScreenState, tuple[np.ndarray, tuple[int, int, int, int]]]:
    global _templates
    if _templates is None:
        index_path = TEMPLATE_DIR / 'index.json'
        if not index_path.exists():
            raise PerceptionError(f'缺少状态模板索引：{index_path}（先运行 build_templates.py）')
        import json
        index = json.loads(index_path.read_text(encoding='utf-8'))
        loaded: dict[ScreenState, tuple[np.ndarray, tuple[int, int, int, int]]] = {}
        for name, spec in index.items():
            data = np.fromfile(str(TEMPLATE_DIR / spec['template']), dtype=np.uint8)
            template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
            if template is None:
                raise PerceptionError(f'缺少状态模板：{spec["template"]}')
            loaded[ScreenState(name)] = (template, tuple(spec['box']))
        _templates = loaded
    return _templates


def _read_full(image: np.ndarray) -> tuple[Anchor, ...]:
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


def _match_criteria(anchors: tuple[Anchor, ...], states: dict) -> ScreenState:
    """模板低分时的回退：按标注的文字判据重新判一次。"""
    for state in ORDER:
        spec = states.get(state.value)
        if spec is None or state is ScreenState.MASTER_SKILLS:
            continue
        ok = True
        for criterion in spec['criteria']:
            found = _find(anchors, criterion['expect'], criterion.get('area'))
            if criterion['kind'] == 'text-exists' and found is None:
                ok = False
                break
            if criterion['kind'] == 'text-absent' and found is not None:
                ok = False
                break
        if ok:
            return state
    return ScreenState.UNKNOWN


def _template_score(image: np.ndarray, state: ScreenState) -> float:
    template, box = _load_templates()[state]
    x1, y1, x2, y2 = box
    pad = 18
    sx1, sy1 = max(0, x1 - pad), max(0, y1 - pad)
    sx2, sy2 = min(image.shape[1], x2 + pad), min(image.shape[0], y2 + pad)
    window = image[sy1:sy2, sx1:sx2]
    if window.shape[0] < template.shape[0] or window.shape[1] < template.shape[1]:
        return 0.0
    return float(cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED).max())


def _detect_state(image: np.ndarray) -> tuple[ScreenState, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    best_state = ScreenState.UNKNOWN
    best_score = 0.0
    for state in ORDER:
        if state not in _load_templates():
            continue
        score = _template_score(gray, state)
        if state is ScreenState.MASTER_SKILLS:
            # 御主技能展开没有专属文字，用图标槽亮度补充判定
            if not _master_skills_open(image):
                continue
            score = max(score, TEMPLATE_THRESHOLD)
        if score >= TEMPLATE_THRESHOLD and score > best_score:
            best_state, best_score = state, score
    return best_state, best_score


def _master_skills_open(image: np.ndarray) -> bool:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    values = [float(hsv[y1:y2, x1:x2, 2].mean()) for x1, y1, x2, y2 in MASTER_SKILL_SLOTS]
    x1, y1, x2, y2 = MASTER_SKILL_REFERENCE
    reference = float(hsv[y1:y2, x1:x2, 2].mean())
    return (sum(values) / len(values)) / max(reference, 1.0) >= MASTER_SKILL_MIN_RATIO


def _int_in(text: str) -> int | None:
    match = re.search(r'(\d+)', text)
    return int(match.group(1)) if match else None


def _center(anchor: Anchor) -> tuple[float, float]:
    x1, y1, x2, y2 = anchor.box
    return (x1 + x2) / 2, (y1 + y2) / 2


def _inside(anchor: Anchor, area: tuple[int, int, int, int] | list[int] | None) -> bool:
    if not area:
        return True
    cx, cy = _center(anchor)
    x1, y1, x2, y2 = area
    return x1 <= cx <= x2 and y1 <= cy <= y2


def _find(anchors: tuple[Anchor, ...], needle: str,
          area: tuple[int, int, int, int] | list[int] | None = None) -> Anchor | None:
    for anchor in anchors:
        if needle in anchor.text and _inside(anchor, area):
            return anchor
    return None


_region_anchor_cache: dict[tuple[tuple[int, ...], str], tuple[Anchor, ...]] = {}


def _read_regions(image: np.ndarray,
                  areas: tuple[tuple[int, int, int, int], ...]) -> tuple[Anchor, ...]:
    """把需要识别的区域纵向拼成一张图，只做一次 OCR；没变过的区域直接复用。"""
    if not areas:
        return ()
    parts = []
    for area in areas:
        x1, y1, x2, y2 = area
        parts.append((area, image[y1:y2, x1:x2]))

    cached_anchors: list[Anchor] = []
    pending: list[tuple[tuple[int, int, int, int], np.ndarray]] = []
    for area, part in parts:
        key = (area, hashlib.sha1(part.tobytes()).hexdigest())
        cached = _region_anchor_cache.get(key)
        if cached is None:
            pending.append((area, part))
        else:
            cached_anchors.extend(cached)

    if not pending:
        return tuple(cached_anchors)

    gap = 8
    width = max(part.shape[1] for _, part in pending)
    height = sum(part.shape[0] + gap for _, part in pending)
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    offsets = []
    y = 0
    for area, part in pending:
        canvas[y:y + part.shape[0], :part.shape[1]] = part
        offsets.append((area, y, part.shape[0]))
        y += part.shape[0] + gap

    result, _ = _ocr()(canvas)
    per_area: dict[tuple[int, int, int, int], list[Anchor]] = {area: [] for area, _ in pending}
    for box, text, score in result or []:
        if float(score) < OCR_THRESHOLD:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        center_y = sum(ys) / 4
        for area, offset_y, part_height in offsets:
            if offset_y <= center_y <= offset_y + part_height + gap:
                x1, y1, _, _ = area
                per_area[area].append(Anchor(
                    text=text.strip(),
                    box=(round(min(xs) + x1), round(min(ys) - offset_y + y1),
                         round(max(xs) + x1), round(max(ys) - offset_y + y1)),
                    score=round(float(score), 3),
                ))
                break
    for area, part in pending:
        key = (area, hashlib.sha1(part.tobytes()).hexdigest())
        _region_anchor_cache[key] = tuple(per_area[area])
        cached_anchors.extend(per_area[area])
    return tuple(cached_anchors)


def _fields_for(state: ScreenState, anchors: tuple[Anchor, ...],
                buttons: dict[str, Button], image: np.ndarray) -> tuple[dict[str, Any], tuple[CardSlot, ...]]:
    fields: dict[str, Any] = {}
    cards: tuple[CardSlot, ...] = ()

    if state is ScreenState.BATTLE:
        wave = _find(anchors, '/3', [850, 0, 990, 50])
        if wave:
            match = re.search(r'(\d+)\s*/\s*(\d+)', wave.text)
            if match:
                fields['wave'] = int(match.group(1))
                fields['wave_total'] = int(match.group(2))
        if fields.get('wave') is None:
            # OCR 会把 “2/3” 读成 “213”，放大后再解析一次
            match = re.search(r'(\d+)\s*/\s*(\d+)', _zoom_text(image, (850, 0, 990, 50), 6))
            if match:
                fields['wave'] = int(match.group(1))
                fields['wave_total'] = int(match.group(2))
        turn = _find(anchors, '回合', [850, 75, 990, 120])
        if turn:
            fields['turn'] = _int_in(turn.text)
        if fields.get('turn') is None:
            for area in ((855, 78, 990, 118), (840, 75, 995, 125)):
                fields['turn'] = _int_in(_zoom_text(image, area))
                if fields['turn'] is not None:
                    break
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
            crit_area = (x1, 340, x1 + 140, 420)
            percent = next((a.text for a in anchors
                            if a.text.endswith('%') and _inside(a, crit_area)), None)
            crit = percent or next((a.text for a in anchors
                                    if '暴击星' in a.text and _inside(a, crit_area)), None)
            affinity_area = (x1 + 120, 360, x2 + 40, 420)
            affinity_anchor = next((a for a in anchors if _inside(a, affinity_area)), None)
            affinity = affinity_anchor.text if affinity_anchor else None
            if affinity and affinity.startswith('克'):
                affinity = '克制'
            elif affinity and affinity.startswith('抵'):
                affinity = '抵抗'
            slots.append(CardSlot(index=index, box=button.box,
                                  crit_star=crit, affinity=affinity))
        cards = tuple(slots)
        fields['cards'] = tuple(card.index for card in cards)

    elif state is ScreenState.REPEAT_DIALOG:
        current = _find(anchors, '行动力现在')
        if current:
            match = re.search(r'(\d+)\s*/\s*(\d+)', current.text)
            if match:
                fields['ap_current'] = int(match.group(1))
                fields['ap_max'] = int(match.group(2))
            else:
                # 数值常被拆成独立的一段，取同一行右侧的数字
                right = [a for a in anchors
                         if re.search(r'\d+\s*/\s*\d+', a.text)
                         and a.box[0] >= current.box[2] - 10
                         and abs((a.box[1] + a.box[3]) / 2 - (current.box[1] + current.box[3]) / 2) < 30]
                if right:
                    match = re.search(r'(\d+)\s*/\s*(\d+)', min(right, key=lambda a: a.box[0]).text)
                    if match:
                        fields['ap_current'] = int(match.group(1))
                        fields['ap_max'] = int(match.group(2))
        cost = _find(anchors, '行动力消耗')
        if cost:
            match = re.search(r'(\d+)', cost.text)
            if match:
                fields['ap_cost'] = int(match.group(1))
            else:
                right = [a for a in anchors
                         if a.text.strip().isdigit() and a.box[0] >= cost.box[2]
                         and abs((a.box[1] + a.box[3]) / 2 - (cost.box[1] + cost.box[3]) / 2) < 30]
                if right:
                    fields['ap_cost'] = int(min(right, key=lambda a: a.box[0]).text.strip())

    elif state is ScreenState.AP_RECOVERY:
        fields['items'] = [name for name in AP_ITEMS if _find(anchors, name)]

    return fields, cards


def _skill_cooldowns(image: np.ndarray, buttons: dict[str, Button]) -> dict[str, int]:
    """读取“剩余 N 回合”文字并映射到最近的技能格。

    只在准备点技能时才需要，所以单独成方法，不占每帧 read 的耗时。
    """
    skill_buttons = [b for b in buttons.values()
                     if '号位' in b.label and '技能' in b.label]
    if not skill_buttons:
        return {}
    x1, y1, x2, y2 = SKILL_BAND
    crop = image[y1:y2, x1:x2]
    big = cv2.resize(crop, None, fx=SKILL_ZOOM, fy=SKILL_ZOOM, interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    _, big = cv2.threshold(gray, SKILL_OTSU, 255, cv2.THRESH_BINARY)
    result, _ = _ocr()(big)
    tokens = []
    for box, text, score in result or []:
        if float(score) < OCR_THRESHOLD:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        tokens.append({
            'text': text.strip(),
            'x1': min(xs) / SKILL_ZOOM + x1,
            'x2': max(xs) / SKILL_ZOOM + x1,
            'y': (min(ys) + max(ys)) / 2 / SKILL_ZOOM + y1,
        })
    cooldowns: dict[str, int] = {}
    for token in tokens:
        if '剩余' not in token['text']:
            continue
        turns = _int_in(token['text'])
        if turns is None:
            right = [t for t in tokens
                     if t['text'].isdigit() and t['x1'] >= token['x2']
                     and abs(t['y'] - token['y']) < 20]
            if right:
                turns = int(min(right, key=lambda t: t['x1'])['text'])
        nearest = min(skill_buttons, key=lambda b: abs((b.box[0] + b.box[2]) / 2 - token['x1']))
        cooldowns[nearest.label] = turns if turns is not None else -1
    return cooldowns


def _zoom_text(image: np.ndarray, area: tuple[int, int, int, int], scale: int = 8) -> str:
    """小号数字常常漏识别：放大 + 二值化后再读一次。"""
    x1, y1, x2, y2 = area
    crop = image[y1:y2, x1:x2]
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    result, _ = _ocr()(binary)
    return ' '.join(text for _, text, score in (result or []) if float(score) >= OCR_THRESHOLD)


class Perception:
    """`read(frame)` 是唯一入口。区服必须显式指定，配置在构造时加载一次。"""

    def __init__(self, region: str, config: dict | None = None) -> None:
        self._config = config or load_config(region)

    def read(self, frame: Frame) -> Observation:
        key = hashlib.sha1(frame.image).hexdigest()
        cached = _observation_cache.get(key)
        if cached is not None:
            return cached

        image = _decode(frame)
        state, score = _detect_state(image)
        if state is ScreenState.UNKNOWN:
            # 模板没把握时按文字判据复核，不直接把画面丢掉
            state = _match_criteria(_read_full(image), self._config['states'])
        if state is ScreenState.BATTLE and _master_skills_open(image):
            state = ScreenState.MASTER_SKILLS
        if state is ScreenState.UNKNOWN:
            observation = Observation(state=ScreenState.UNKNOWN, uncertain=True)
            _observation_cache[key] = observation
            return observation

        spec = self._config['states'][state.value]
        buttons = {item['label']: Button(label=item['label'], box=tuple(item['box']))
                   for item in spec['buttons']}
        areas = tuple(tuple(area) for area in spec.get('field_areas', []))
        anchors = _read_regions(image, areas)
        fields, cards = _fields_for(state, anchors, buttons, image)

        missing = [name for name in REQUIRED_FIELDS.get(state, ())
                   if fields.get(name) is None or fields.get(name) == ()]
        if missing:
            fields['missing'] = missing

        observation = Observation(
            state=state,
            anchors=anchors,
            buttons=tuple(buttons.values()),
            cards=cards,
            fields=fields,
            uncertain=bool(missing),
        )
        _observation_cache[key] = observation
        return observation

    def read_cooldowns(self, frame: Frame) -> dict[str, int]:
        """按需读取技能冷却（剩余回合数）。每帧 read 不做这次 OCR。"""
        image = _decode(frame)
        spec = self._config['states'][ScreenState.BATTLE.value]
        buttons = {item['label']: Button(label=item['label'], box=tuple(item['box']))
                   for item in spec['buttons']}
        return _skill_cooldowns(image, buttons)
