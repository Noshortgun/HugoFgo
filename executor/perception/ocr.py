"""后台 OCR：只按需补字段，结果绑定帧哈希；过期结果只进缓存，不驱动当前动作。"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from executor.perception.errors import PerceptionError
from executor.perception.observation import Field, FieldStatus
from executor.perception.regions import cn


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: int
    y: int
    confidence: float


Reader = Callable[[np.ndarray], list[OcrLine]]

PARSEABLE_FIELDS = ('wave', 'turn', 'ap', 'np', 'enemies', 'crit')


def rapidocr_reader() -> Reader:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as error:
        raise PerceptionError('缺少 rapidocr-onnxruntime，后台 OCR 不可用') from error

    engine = RapidOCR()

    def read_image(image: np.ndarray, offset=(0, 0), scale=1.0) -> list[OcrLine]:
        result, _ = engine(image)
        lines = []
        for box, text, score in result or []:
            xs = [point[0] / scale + offset[0] for point in box]
            ys = [point[1] / scale + offset[1] for point in box]
            lines.append(OcrLine(
                text=str(text).strip(),
                x=round(sum(xs) / 4),
                y=round(sum(ys) / 4),
                confidence=float(score),
            ))
        return lines

    def read(image: np.ndarray) -> list[OcrLine]:
        lines = read_image(image)
        x1, y1, x2, y2 = cn.TURN_FALLBACK_REGION
        turn = image[y1:y2, x1:x2]
        turn = cv2.cvtColor(turn, cv2.COLOR_BGR2GRAY)
        turn = cv2.threshold(turn, 150, 255, cv2.THRESH_BINARY)[1]
        turn = cv2.resize(turn, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        lines.extend(read_image(turn, (x1, y1), 3.0))
        x1, y1, x2, y2 = cn.CRIT_REGION
        crit = image[y1:y2, x1:x2]
        crit = cv2.resize(crit, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        lines.extend(read_image(crit, (x1, y1), 2.0))
        return lines

    return read


def _inside(line: OcrLine, box: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= line.x <= x2 and y1 <= line.y <= y2


def _best_match(lines: list[OcrLine], region: tuple[int, int, int, int] | None,
                pattern: str, extract):
    best: tuple[float, object] | None = None
    for line in lines:
        if line.confidence < cn.OCR_MIN_CONFIDENCE:
            continue
        if region is not None and not _inside(line, region):
            continue
        match = re.search(pattern, line.text)
        if match is None:
            continue
        value = extract(match)
        if best is None or line.confidence > best[0]:
            best = (line.confidence, value)
    return best


def _field(best: tuple[float, object] | None) -> Field:
    if best is None:
        return Field(FieldStatus.UNCERTAIN)
    confidence, value = best
    return Field(FieldStatus.CONFIRMED, value, round(confidence, 3))


def _parse_number(lines: list[OcrLine], region: tuple[int, int, int, int],
                  pattern: str, group: int = 1) -> Field:
    return _field(_best_match(lines, region, pattern, lambda match: int(match.group(group))))


def _parse_ap(lines: list[OcrLine]) -> Field:
    return _field(_best_match(lines, None, cn.AP_PATTERN,
                              lambda match: (int(match.group(1)), int(match.group(2)))))


def _parse_percentages(lines: list[OcrLine], region, expected_count: int | None = None) -> Field:
    readings = []
    for line in lines:
        if line.confidence < cn.OCR_MIN_CONFIDENCE or not _inside(line, region):
            continue
        text = re.sub(r'(\d{1,2})\.0\s*%', r'\g<1>0%', line.text)
        match = re.search(cn.PERCENT_PATTERN, text)
        if match is None:
            continue
        readings.append((line.x, int(match.group(1)), line.confidence))
    readings.sort()
    deduplicated = []
    for reading in readings:
        if deduplicated and abs(reading[0] - deduplicated[-1][0]) < 20:
            if reading[2] > deduplicated[-1][2]:
                deduplicated[-1] = reading
        else:
            deduplicated.append(reading)
    if not deduplicated or expected_count is not None and len(deduplicated) != expected_count:
        return Field(FieldStatus.UNCERTAIN)
    return Field(FieldStatus.CONFIRMED, tuple(value for _, value, _ in deduplicated),
                 round(min(score for _, _, score in deduplicated), 3))


def _parse_turn(lines: list[OcrLine]) -> Field:
    field = _parse_number(lines, cn.TURN_REGION, cn.TURN_PATTERN)
    if field.status is FieldStatus.CONFIRMED:
        return field
    return _parse_number(lines, cn.TURN_FALLBACK_REGION, r'^(\d+)$')


def parse_fields(lines: list[OcrLine]) -> dict[str, Field]:
    return {
        'wave': _parse_number(lines, cn.WAVE_REGION, cn.WAVE_PATTERN),
        'turn': _parse_turn(lines),
        'ap': _parse_ap(lines),
        'np': _parse_percentages(lines, cn.NP_REGION),
        'enemies': _parse_number(lines, cn.ENEMY_COUNT_REGION, cn.ENEMY_COUNT_PATTERN),
        'crit': _parse_percentages(lines, cn.CRIT_REGION, expected_count=5),
    }


class OcrWorker:
    """单线程后台 OCR；同一帧只提交一次，结果按帧哈希缓存。"""

    def __init__(self, reader: Reader | None = None, *, cache_size: int = 8) -> None:
        self._reader = reader
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='perception-ocr')
        self._lock = threading.Lock()
        self._sequence = 0
        self._pending: dict[str, tuple[str, Future]] = {}
        self._results: OrderedDict[str, tuple[str, dict[str, Field]]] = OrderedDict()
        self._cache_size = cache_size
        self._closed = False

    def submit(self, digest: str, image: np.ndarray) -> str:
        with self._lock:
            if self._closed:
                raise PerceptionError('感知器已关闭，不能再提交 OCR 请求')
            if digest in self._results:
                return self._results[digest][0]
            if digest in self._pending:
                return self._pending[digest][0]
            self._sequence += 1
            request_id = f'{digest}:{self._sequence}'
            future = self._pool.submit(self._run, request_id, digest, image.copy())
            self._pending[digest] = (request_id, future)
            return request_id

    def _run(self, request_id: str, digest: str, image: np.ndarray) -> None:
        try:
            reader = self._reader or rapidocr_reader()
            fields = parse_fields(reader(image))
        except Exception:
            fields = {name: Field(FieldStatus.UNCERTAIN) for name in PARSEABLE_FIELDS}
        with self._lock:
            self._pending.pop(digest, None)
            self._results[digest] = (request_id, fields)
            while len(self._results) > self._cache_size:
                self._results.popitem(last=False)

    def pending(self, digest: str) -> str | None:
        with self._lock:
            entry = self._pending.get(digest)
            return entry[0] if entry is not None else None

    def cached(self, digest: str) -> dict[str, Field] | None:
        with self._lock:
            entry = self._results.get(digest)
            return dict(entry[1]) if entry is not None else None

    def request_id(self, digest: str) -> str | None:
        with self._lock:
            entry = self._results.get(digest)
            return entry[0] if entry is not None else None

    def wait(self, digest: str, timeout: float) -> tuple[str, dict[str, Field]] | None:
        with self._lock:
            entry = self._pending.get(digest)
        if entry is None:
            cached = self.request_id(digest)
            return (cached, self.cached(digest)) if cached is not None else None
        request_id, future = entry
        try:
            future.result(timeout=timeout)
        except FutureTimeout:
            return None
        with self._lock:
            done = self._results.get(digest)
        return (done[0], dict(done[1])) if done is not None else None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
