"""画面感知适配器：同步快路径读帧，后台 OCR 只按需补字段。

`read()` 只做模板、颜色与布局证据，禁止 OCR；`read_fields()` 才把字段请求交给后台线程，
并按帧哈希拒绝已经过期的结果。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Iterable, Mapping

import cv2

from executor.device.base import Frame
from executor.perception import evidence
from executor.perception.errors import StaleFrameError
from executor.perception.evidence import Evidence, Template
from executor.perception.observation import (
    BasePage,
    Control,
    Field,
    FieldStatus,
    Observation,
    Overlay,
    Variant,
)
from executor.perception.ocr import PARSEABLE_FIELDS, OcrWorker
from executor.perception.regions import cn

__all__ = ['Perception', 'StaleFrameError']


def _classify(evidence: Evidence):
    for rule in cn.PAGE_RULES:
        if all(evidence.hit(anchor) for anchor in rule.anchors):
            page = rule.page
            confidence = min(evidence.score(anchor) for anchor in rule.anchors)
            break
    else:
        page = BasePage.UNKNOWN
        confidence = max(evidence.scores.values(), default=0.0)

    variants = frozenset(
        rule.variant for rule in cn.VARIANT_RULES
        if rule.page is page and all(evidence.hit(anchor) for anchor in rule.anchors)
    )
    overlays = frozenset(
        rule.overlay for rule in cn.OVERLAY_RULES
        if all(evidence.hit(anchor) for anchor in rule.anchors)
        and not any(evidence.hit(anchor) for anchor in rule.absent)
    )
    return page, variants, overlays, confidence


def _controls(page: BasePage, variants: frozenset[Variant], overlays: frozenset[Overlay],
              evidence: Evidence) -> tuple[Control, ...]:
    if page in (BasePage.UNKNOWN, BasePage.LOADING):
        return ()

    variant = next((item for item in variants if item in cn.VARIANT_CONTROLS), None)
    specs = cn.VARIANT_CONTROLS[variant] if variant is not None else cn.PAGE_CONTROLS[page]
    blocked = bool(overlays & cn.BLOCKING_OVERLAYS)
    controls = []
    for spec in specs:
        if blocked:
            actionable, reason = False, 'blocked_by_overlay'
        elif spec.anchor is not None and not evidence.hit(spec.anchor):
            actionable, reason = False, f'anchor_missing:{spec.anchor}'
        else:
            actionable, reason = True, 'visible'
        controls.append(Control(spec.name, spec.kind, spec.box, actionable, reason))

    for overlay in sorted(overlays, key=lambda item: item.value):
        for spec in cn.OVERLAY_CONTROLS.get(overlay, ()):
            if spec.anchor is not None and not evidence.hit(spec.anchor):
                actionable, reason = False, f'anchor_missing:{spec.anchor}'
            else:
                actionable, reason = True, f'overlay:{overlay.value}'
            controls.append(Control(spec.name, spec.kind, spec.box, actionable, reason))
    return tuple(controls)


def _fields(page: BasePage, cached: Mapping[str, Field] | None,
            pending: bool, requested: frozenset[str] = frozenset()) -> dict[str, Field]:
    fields: dict[str, Field] = {}
    for name, pages in cn.FIELD_PAGES.items():
        if page not in pages:
            fields[name] = Field(FieldStatus.NOT_APPLICABLE)
        elif name not in requested:
            fields[name] = Field(FieldStatus.NOT_READ)
        elif cached is not None and name in cached:
            fields[name] = cached[name]
        elif pending:
            fields[name] = Field(FieldStatus.PENDING)
        else:
            fields[name] = Field(FieldStatus.NOT_READ)
    return fields


class Perception:
    """国服画面感知；只读取画面，不点击、不推进状态机、不访问设备。"""

    def __init__(self, *, templates: Mapping[str, Template] | None = None,
                 ocr_reader=None, clock=time.perf_counter, cache_size: int = 64) -> None:
        self._templates = dict(templates) if templates is not None else evidence.load_templates()
        self._worker = OcrWorker(ocr_reader)
        self._clock = clock
        self._cache_size = cache_size
        self._cache: OrderedDict[str, Observation] = OrderedDict()
        self._requested: dict[str, frozenset[str]] = {}
        self._lock = threading.Lock()
        self._latest: Observation | None = None
        self._timings: deque[float] = deque(maxlen=200)
        self._reads = 0
        self._cache_hits = 0

    def read(self, frame: Frame) -> Observation:
        started = self._clock()
        digest = evidence.frame_hash(frame)
        with self._lock:
            cached = self._cache.get(digest)
            if cached is not None:
                self._cache_hits += 1
                self._latest = cached
                observation = cached
        if cached is None:
            observation = self._recognize(frame, digest)
            with self._lock:
                self._cache[digest] = observation
                while len(self._cache) > self._cache_size:
                    evicted, _ = self._cache.popitem(last=False)
                    self._requested.pop(evicted, None)
                self._latest = observation
                self._reads += 1
                self._timings.append((self._clock() - started) * 1000.0)
        return self._with_ocr(observation)

    def _recognize(self, frame: Frame, digest: str) -> Observation:
        image = evidence.decode_frame(frame)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        scores = evidence.match_templates(gray, self._templates)
        means = evidence.measure_probes(gray)
        frame_evidence = Evidence(scores=scores, templates=self._templates, means=means)
        page, variants, overlays, confidence = _classify(frame_evidence)
        return Observation(
            base_page=page,
            variants=variants,
            overlays=overlays,
            controls=_controls(page, variants, overlays, frame_evidence),
            fields=_fields(page, None, False),
            frame_hash=digest,
            confidence=round(min(max(confidence, 0.0), 1.0), 3),
            evidence={name: round(value, 4) for name, value in {**scores, **means}.items()},
        )

    def _with_ocr(self, observation: Observation) -> Observation:
        digest = observation.frame_hash
        cached = self._worker.cached(digest)
        pending = self._worker.pending(digest)
        with self._lock:
            requested = self._requested.get(digest, frozenset())
        fields = _fields(observation.base_page, cached, pending, requested)
        return observation.with_fields(fields)

    def read_fields(self, frame: Frame, names: Iterable[str], *,
                    timeout: float | None = None) -> Observation:
        """按需读取字段。timeout 为 None 时立即返回；只有必要字段才值得等待。"""
        wanted = tuple(dict.fromkeys(names))
        unknown = [name for name in wanted if name not in PARSEABLE_FIELDS]
        if unknown:
            raise ValueError(f'未知字段 {unknown}；可选 {list(PARSEABLE_FIELDS)}')

        digest = evidence.frame_hash(frame)
        with self._lock:
            latest = self._latest
        if latest is None or latest.frame_hash != digest:
            raise StaleFrameError('传入的帧不是最近一次 read() 的画面，结果只能进缓存')

        applicable = frozenset(name for name in wanted if latest.base_page in cn.FIELD_PAGES[name])
        if not applicable:
            return self._with_ocr(latest)
        with self._lock:
            self._requested[digest] = self._requested.get(digest, frozenset()) | applicable

        cached = self._worker.cached(digest)
        request_id = None
        if cached is None:
            request_id = self._worker.pending(digest)
            if request_id is None:
                request_id = self._worker.submit(digest, evidence.decode_frame(frame))

        if cached is None and timeout is not None:
            finished = self._worker.wait(digest, timeout)
            if finished is not None:
                finished_id, fields = finished
                if request_id is not None and finished_id != request_id:
                    raise StaleFrameError('OCR 结果不属于当前帧的请求，只能进缓存')
                with self._lock:
                    current = self._latest
                if current is None or current.frame_hash != digest:
                    raise StaleFrameError('等待 OCR 期间画面已变化，结果只能进缓存')
        return self._with_ocr(latest)

    def stats(self) -> dict:
        with self._lock:
            timings = list(self._timings)
            reads = self._reads
            hits = self._cache_hits
        if not timings:
            return {'reads': reads, 'cache_hits': hits, 'last_ms': None,
                    'mean_ms': None, 'p95_ms': None, 'max_ms': None}
        ordered = sorted(timings)
        p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
        return {
            'reads': reads,
            'cache_hits': hits,
            'last_ms': round(timings[-1], 2),
            'mean_ms': round(sum(timings) / len(timings), 2),
            'p95_ms': round(p95, 2),
            'max_ms': round(max(timings), 2),
        }

    def close(self) -> None:
        self._worker.close()
