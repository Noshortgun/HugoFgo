"""同一帧局部图像的数字模板识别；未知或低区分度结果保持不确定。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from executor.perception.errors import PerceptionError
from executor.perception.observation import Field, FieldStatus
from executor.perception.regions import cn

IMAGE_FIELDS = ('wave', 'turn', 'np', 'crit', 'ap', 'enemies')


@dataclass(frozen=True)
class DigitTemplate:
    digit: int
    patch: np.ndarray


def white_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return (((hsv[..., 1] < 90) & (hsv[..., 2] > 180)).astype(np.uint8)) * 255


def digit_mask(image: np.ndarray, style: str) -> np.ndarray:
    if style == 'np':
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return ((gray > 100).astype(np.uint8)) * 255
    return white_mask(image)


def normalize_glyph(patch: np.ndarray) -> np.ndarray:
    height, width = patch.shape
    scale = min(24 / width, 32 / height)
    resized = cv2.resize(patch, (max(1, round(width * scale)), max(1, round(height * scale))),
                         interpolation=cv2.INTER_NEAREST)
    normalized = np.zeros((32, 24), dtype=np.uint8)
    y = (32 - resized.shape[0]) // 2
    x = (24 - resized.shape[1]) // 2
    normalized[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return normalized


def _similarity(first: np.ndarray, second: np.ndarray) -> float:
    left = first.astype(np.float64).ravel() / 255.0
    right = second.astype(np.float64).ravel() / 255.0
    left -= left.mean()
    right -= right.mean()
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    return float(np.dot(left, right) / denominator) if denominator else -1.0


def _native_glyph(patch: np.ndarray, height: int = 16) -> np.ndarray:
    ys, xs = np.where(patch > 0)
    tight = patch[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    width = max(1, round(tight.shape[1] * height / tight.shape[0]))
    return cv2.resize(tight, (width, height), interpolation=cv2.INTER_NEAREST)


class DigitReader:
    def __init__(self, directory: Path = cn.TEMPLATE_DIR) -> None:
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        self._templates: dict[str, list[DigitTemplate]] = {}
        for name, spec in manifest.get('digits', {}).items():
            style, digit = name.split('/', 1)
            digit = digit.split('-', 1)[0]
            data = np.fromfile(str(directory / spec['file']), dtype=np.uint8)
            patch = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
            if patch is None:
                raise PerceptionError(f'缺少数字模板：{directory / spec["file"]}')
            self._templates.setdefault(style, []).append(DigitTemplate(int(digit), patch))
        if not self._templates:
            raise PerceptionError('数字模板清单为空；先运行 python -m executor.perception.build_templates')

    @staticmethod
    def _glyphs(image: np.ndarray, box: tuple[int, int, int, int], style: str) -> list[np.ndarray]:
        x1, y1, x2, y2 = box
        crop = image[y1:y2, x1:x2]
        mask = digit_mask(crop, style)
        _count, _labels, stats, _centers = cv2.connectedComponentsWithStats(mask)
        components = [tuple(map(int, stat)) for stat in stats[1:]]
        boxes = [stat for stat in components
                 if 13 <= stat[3] <= 20 and 5 <= stat[2] <= 25 and stat[4] >= 30]
        if style == 'crit':
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            boxes = [stat for stat in boxes
                     if float(hsv[stat[1]:stat[1] + stat[3], stat[0]:stat[0] + stat[2], 2].mean()) <= 160]
            left = min((stat[0] for stat in boxes), default=x2 - x1)
            markers = [stat for stat in components
                       if 6 <= stat[3] <= 12 and 3 <= stat[2] <= 12 and stat[4] >= 10
                       and stat[0] > left + 40]
            if markers:
                percent_start = min(stat[0] for stat in markers)
                boxes = [stat for stat in boxes if stat[0] < percent_start]
        boxes.sort(key=lambda stat: stat[0])
        return [mask[y:y + height, x:x + width] for x, y, width, height, _area in boxes]

    def _digit(self, patch: np.ndarray, style: str) -> tuple[int, float] | None:
        query = normalize_glyph(patch)
        ranked = sorted(((template.digit, _similarity(query, template.patch))
                         for template in self._templates.get(style, ())),
                        key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < 0.55:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.08:
            return None
        return ranked[0]

    def _number(self, image: np.ndarray, box: tuple[int, int, int, int], style: str,
                *, single: bool = False) -> tuple[int, float] | None:
        glyphs = self._glyphs(image, box, style)
        if not glyphs or single and len(glyphs) != 1 or len(glyphs) > 3:
            return None
        digits = [self._digit(glyph, style) for glyph in glyphs]
        if any(digit is None for digit in digits):
            return None
        recognized = [digit for digit in digits if digit is not None]
        value = 0
        for digit, _score in recognized:
            value = value * 10 + digit
        return value, min(score for _digit, score in recognized)

    @staticmethod
    def _field(reading: tuple[int, float] | None) -> Field:
        if reading is None:
            return Field(FieldStatus.UNCERTAIN)
        value, confidence = reading
        return Field(FieldStatus.CONFIRMED, value, round(confidence, 3))

    def _np_candidates(self, crop: np.ndarray) -> list[tuple[float, int, int, int, np.ndarray]]:
        mask = digit_mask(crop, 'np')
        _count, _labels, stats, _centers = cv2.connectedComponentsWithStats(mask)
        components = [tuple(map(int, stat)) for stat in stats[1:]
                      if stat[3] >= 13 and stat[2] >= 3 and stat[4] >= 20]
        candidates = []
        for template in self._templates.get('np', ()):
            native = _native_glyph(template.patch)
            result = cv2.matchTemplate(mask, native, cv2.TM_CCOEFF_NORMED)
            for x in range(result.shape[1]):
                y = int(result[:, x].argmax())
                score = float(result[y, x])
                if score < 0.55:
                    continue
                if not any(stat[0] - 3 <= x < stat[0] + stat[2] + 3
                           and abs(y - stat[1]) <= 4 for stat in components):
                    continue
                candidates.append((score, x, template.digit, y, native))
        candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        accepted = []
        for candidate in candidates:
            _score, x, _digit, _y, _native = candidate
            if any(abs(x - other[1]) < 4 for other in accepted):
                continue
            accepted.append(candidate)
        if not accepted:
            return []
        left = min(candidate[1] for candidate in accepted)
        markers = [stat for stat in stats[1:]
                   if 6 <= stat[3] <= 12 and 3 <= stat[2] <= 12 and stat[4] >= 10
                   and stat[0] > left + 20]
        if markers:
            percent_start = min(int(stat[0]) for stat in markers)
            accepted = [candidate for candidate in accepted if candidate[1] < percent_start]
        accepted.sort(key=lambda candidate: candidate[1])
        return accepted

    def _np_number(self, image: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, float] | None:
        x1, y1, x2, y2 = box
        accepted = self._np_candidates(image[y1:y2, x1:x2])
        if not 1 <= len(accepted) <= 3:
            return None
        value = 0
        for _score, _x, digit, _y, _native in accepted:
            value = value * 10 + digit
        return value, min(score for score, _x, _digit, _y, _native in accepted)

    def _percentages(self, image: np.ndarray, regions, style: str) -> Field:
        readings = [(self._np_number(image, box) if style == 'np' else self._number(image, box, style))
                    for box in regions]
        if any(reading is None for reading in readings):
            return Field(FieldStatus.UNCERTAIN)
        return Field(FieldStatus.CONFIRMED,
                     tuple(reading[0] for reading in readings if reading is not None),
                     round(min(reading[1] for reading in readings if reading is not None), 3))

    def read(self, image: np.ndarray, names) -> dict[str, Field]:
        wanted = frozenset(names)
        fields: dict[str, Field] = {}
        if 'wave' in wanted:
            fields['wave'] = self._field(self._number(image, cn.WAVE_VALUE_REGION, 'wave', single=True))
        if 'turn' in wanted:
            fields['turn'] = self._field(self._number(image, cn.TURN_VALUE_REGION, 'turn', single=True))
        if 'np' in wanted:
            fields['np'] = self._percentages(image, cn.NP_VALUE_REGIONS, 'np')
        if 'crit' in wanted:
            fields['crit'] = self._percentages(image, cn.CRIT_VALUE_REGIONS, 'crit')
        for name in frozenset(('ap', 'enemies')) & wanted:
            fields[name] = Field(FieldStatus.UNCERTAIN)
        return fields
