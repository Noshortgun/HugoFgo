"""画面感知适配器：设备帧进来，Observation 出去。"""
from __future__ import annotations

import re
from typing import Any

import cv2
import numpy as np

from executor.device.base import Frame
from executor.perception.observation import Anchor, CardCandidate, Observation, ScreenState
from executor.perception.regions import cn

TARGET_SIZE = (1280, 720)
_ocr_engine: Any | None = None
_templates: dict[str, list[tuple[np.ndarray, tuple[int, int, int, int]]]] | None = None


class PerceptionError(RuntimeError):
    """帧无法解码或识别器不可用。"""


def _ocr() -> Any:
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:
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


def _load_templates() -> dict[str, list[tuple[np.ndarray, tuple[int, int, int, int]]]]:
    global _templates
    if _templates is None:
        templates = {}
        for state, specs in cn.TEMPLATE_SPECS.items():
            loaded = []
            for filename, roi in specs:
                path = cn.TEMPLATE_DIR / filename
                data = np.fromfile(str(path), dtype=np.uint8)
                image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
                if image is None:
                    raise PerceptionError(f'缺少识别模板：{path}')
                loaded.append((image, roi))
            templates[state] = loaded
        _templates = templates
    return _templates


def _read_text(image: np.ndarray) -> list[Anchor]:
    result, _ = _ocr()(image)
    anchors = []
    for box, text, score in result or []:
        if score < cn.OCR_THRESHOLD:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        anchors.append(Anchor(
            text=text.strip(),
            x=round(sum(xs) / 4),
            y=round(sum(ys) / 4),
            confidence=round(float(score), 3),
        ))
    return anchors


def _match_templates(image: np.ndarray) -> dict[str, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scores = {}
    for state, specs in _load_templates().items():
        best = 0.0
        for template, (x1, y1, x2, y2) in specs:
            roi = gray[y1:y2, x1:x2]
            if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
                continue
            result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(result.max()))
        scores[state] = round(best, 4)
    return scores


def _contains(anchors: list[Anchor], needles: tuple[str, ...]) -> Anchor | None:
    for anchor in anchors:
        if any(needle in anchor.text for needle in needles):
            return anchor
    return None


def _in_region(anchor: Anchor, region: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = region
    return x1 <= anchor.x <= x2 and y1 <= anchor.y <= y2


def _parse_wave(anchors: list[Anchor]) -> int | None:
    for anchor in anchors:
        if not _in_region(anchor, cn.WAVE_REGION):
            continue
        match = re.search(r'(\d+)\s*/\s*(\d+)', anchor.text)
        if match:
            return int(match.group(1))
    return None


def _parse_turn(anchors: list[Anchor]) -> int | None:
    for anchor in anchors:
        if not _in_region(anchor, cn.TURN_REGION):
            continue
        match = re.search(r'(\d+)\s*回合', anchor.text)
        if match:
            return int(match.group(1))
    return None


def _read_cards(anchors: list[Anchor]) -> tuple[CardCandidate, ...]:
    cards = []
    for anchor in anchors:
        if not _in_region(anchor, cn.CARD_REGION):
            continue
        label = next((item for item in cn.CARD_LABELS if item in anchor.text), None)
        if label is not None:
            cards.append(CardCandidate(label=label, x=anchor.x, y=anchor.y, confidence=anchor.confidence))
    return tuple(sorted(cards, key=lambda card: card.x))


def _choose_state(anchors: list[Anchor], scores: dict[str, float]) -> tuple[ScreenState, float, bool]:
    conflict = _contains(anchors, cn.CONFLICT_ANCHORS)
    if conflict is not None:
        return ScreenState.UNKNOWN, conflict.confidence, True

    for state, needles in (
        ('loading', cn.LOADING_ANCHORS),
        ('support', cn.SUPPORT_ANCHORS),
        ('team', cn.TEAM_ANCHORS),
        ('result', cn.RESULT_ANCHORS),
        ('card', cn.CARD_ANCHORS),
        ('battle', cn.BATTLE_ANCHORS),
        ('menu', cn.MENU_ANCHORS),
    ):
        hit = _contains(anchors, needles)
        if hit is not None:
            return ScreenState(state), hit.confidence, False

    for state in ('menu', 'battle', 'card', 'result'):
        if scores.get(state, 0.0) >= cn.TEMPLATE_THRESHOLD:
            return ScreenState(state), scores[state], False

    return ScreenState.UNKNOWN, max(scores.values(), default=0.0), True


class Perception:
    """国服画面感知；只读取画面，不点击、不推进状态机。"""

    def read(self, frame: Frame) -> Observation:
        image = _decode(frame)
        anchors = _read_text(image)
        scores = _match_templates(image)
        state, confidence, uncertain = _choose_state(anchors, scores)
        cards = _read_cards(anchors) if state == 'card' else ()
        wave = _parse_wave(anchors) if state in ('battle', 'card', 'result') else None
        turn = _parse_turn(anchors) if state in ('battle', 'card') else None
        return Observation(
            state=state,
            anchors=tuple(anchors),
            cards=cards,
            wave=wave,
            turn=turn,
            uncertain=uncertain,
            confidence=round(confidence, 3),
        )
