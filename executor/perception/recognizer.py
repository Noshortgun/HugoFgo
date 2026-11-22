"""画面感知适配器：同步快路径读帧，后台 OCR 只按需补字段。

`read()` 只做模板、颜色与布局证据，禁止 OCR；`read_fields()` 才把字段请求交给后台线程，
并按帧哈希拒绝已经过期的结果。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Iterable, Mapping

import cv2
import numpy as np

from executor.device.base import Frame
from executor.perception import evidence
from executor.perception.errors import StaleFrameError
from executor.perception.evidence import Evidence, Template, find_dialog_buttons, find_enemy_bars
from executor.perception.identity import IdentityMatcher
from executor.perception.observation import (
    BasePage,
    Control,
    ControlKind,
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
    overlays = frozenset(
        rule.overlay for rule in cn.OVERLAY_RULES
        if all(evidence.hit(anchor) for anchor in rule.anchors)
        and not any(evidence.hit(anchor) for anchor in rule.absent)
    )

    direct = True
    for rule in cn.PAGE_RULES:
        if all(evidence.hit(anchor) for anchor in rule.anchors):
            page = rule.page
            confidence = min(evidence.score(anchor) for anchor in rule.anchors)
            break
    else:
        page = BasePage.UNKNOWN
        direct = False
        confidence = max(evidence.scores.values(), default=0.0)

    if page is BasePage.UNKNOWN:
        for overlay, fallback in cn.OVERLAY_BASE_FALLBACK.items():
            if overlay in overlays:
                page = fallback
                break

    variants = set()
    for rule in cn.VARIANT_RULES if direct else ():
        if rule.page is not page:
            continue
        if rule.variant is Variant.BATTLE_ANIMATION and overlays:
            continue
        if not all(evidence.hit(anchor) for anchor in rule.anchors):
            continue
        if any(evidence.hit(anchor) for anchor in rule.absent):
            continue
        variants.add(rule.variant)
    return page, frozenset(variants), overlays, confidence


def _support_rows(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """助战列表可滚动：用 Sobel 水平边缘找每张卡片的上下边界，再切成行。"""
    x1, y1, x2, y2 = cn.SUPPORT_ROW_REGION
    region = gray[y1:y2, x1:x2]
    gradient = np.abs(cv2.Sobel(region, cv2.CV_32F, 0, 1, ksize=3)).mean(axis=1)
    gradient = np.convolve(gradient, np.ones(3) / 3, 'same')
    threshold = np.percentile(gradient, cn.SUPPORT_ROW_EDGE_PERCENTILE)
    groups: list[list[int]] = []
    for index, value in enumerate(gradient):
        if value < threshold:
            continue
        if groups and index - groups[-1][-1] <= cn.SUPPORT_ROW_EDGE_GROUP:
            groups[-1].append(index)
        else:
            groups.append([index])
    edges: list[int] = []
    for group in groups:
        edge = int(np.mean(group)) + y1
        if edges and edge - edges[-1] < cn.SUPPORT_ROW_MIN_GAP:
            continue
        edges.append(edge)

    boxes = []
    start = y1
    for edge in edges:
        if edge - start >= cn.SUPPORT_ROW_MIN_HEIGHT:
            boxes.append((x1, start, x2, edge))
        start = edge
    if y2 - start >= cn.SUPPORT_ROW_MIN_HEIGHT:
        boxes.append((x1, start, x2, y2))
    return boxes


def _make_control(spec, evidence: Evidence, blocked: bool, reason: str) -> Control | None:
    box = spec.box
    if spec.follow_anchor and spec.anchor is not None and spec.anchor in evidence.locations:
        template = evidence.templates.get(spec.anchor)
        if template is not None:
            x, y = evidence.locations[spec.anchor]
            patch_height, patch_width = template.patch.shape[:2]
            box = (x - 60, y - 15, x + patch_width + 20, y + patch_height + 55)
    if spec.anchor is not None and not evidence.hit(spec.anchor):
        if spec.hide_when_missing:
            return None
        return Control(spec.name, spec.kind, box, False, f'anchor_missing:{spec.anchor}')
    if spec.always_disabled:
        return Control(spec.name, spec.kind, box, False, 'not_for_automation')
    if blocked:
        return Control(spec.name, spec.kind, box, False, 'blocked_by_overlay')
    return Control(spec.name, spec.kind, box, True, reason)


def _controls(page: BasePage, variants: frozenset[Variant], overlays: frozenset[Overlay],
              evidence: Evidence, gray: np.ndarray, image: np.ndarray,
              identities: IdentityMatcher) -> tuple[Control, ...]:
    modal = next((item for item in cn.MODAL_OVERLAYS if item in overlays), None)

    if page in (BasePage.UNKNOWN, BasePage.LOADING) or Variant.BATTLE_ANIMATION in variants:
        # 未知页面上只放行已识别的模态弹窗按钮（按钮本身是确认过存在的）。
        if modal is None:
            return ()
        return _overlay_controls(modal, evidence, image)

    if modal is not None:
        return _overlay_controls(modal, evidence, image)

    blocked = bool(overlays & cn.BLOCKING_OVERLAYS) or bool(variants & cn.BLOCKING_VARIANTS)
    controls: list[Control] = []
    # 被模态覆盖时基页控件一律不输出，而不是列一堆“不可点”。
    if not blocked:
        variant = next((item for item in variants if item in cn.VARIANT_CONTROLS), None)
        specs = cn.VARIANT_CONTROLS[variant] if variant is not None else cn.PAGE_CONTROLS[page]
        controls = [control for spec in specs
                    if (control := _make_control(spec, evidence, False, 'visible')) is not None]

        if page is BasePage.SUPPORT:
            for index, box in enumerate(_support_rows(gray), start=1):
                controls.append(Control(f'助战卡片第{index}行', ControlKind.ROW, box, True, 'row_detected',
                                        support=identities.support(image, box)))

        if page is BasePage.BATTLE:
            for slot in (1, 2, 3):
                if not evidence.hit(f'servant_slot_{slot}'):
                    continue
                x1, _y1, _x2, _y2 = cn.SLOT_PROBES[f'servant_slot_{slot}'].box
                for skill, offset in enumerate(cn.SKILL_ICON_OFFSETS, start=1):
                    left = x1 + offset
                    box = (left, cn.SKILL_ICON_BOX[1], left + cn.SKILL_ICON_BOX[2] - cn.SKILL_ICON_BOX[0],
                           cn.SKILL_ICON_BOX[3])
                    controls.append(Control(f'从者{slot}技能{skill}', ControlKind.SKILL, box, True,
                                            f'slot_visible:{slot}'))
            for index, (x, y, w, h) in enumerate(find_enemy_bars(image), start=1):
                # 面板相对血条的实测几何：左 80、上 37、右 32、下 57，贴近标注区域。
                box = (max(0, x - 80), max(0, y - 37), min(1280, x + w + 32), min(720, y + h + 57))
                controls.append(Control(f'敌方{index}', ControlKind.ENEMY, box, True, 'enemy_hp_bar'))

        if page is BasePage.CARD:
            controls = [Control(control.name, control.kind, control.box, control.actionable, control.reason,
                                card=identities.command(image, control.box))
                        if control.name.startswith('卡') else control for control in controls]
            present = [(slot, box) for slot, box in cn.NP_SLOTS.items() if evidence.np_hit(slot)]
            for index, (slot, box) in enumerate(present, start=1):
                controls.append(Control(f'宝具卡{index}', ControlKind.CARD, box, True, f'np_slot:{slot}'))

        if page is BasePage.AP_RECOVERY:
            layout = 'ap_stone' if evidence.hit('ap_stone') else 'ap_bronze'
            for spec in cn.AP_ITEM_LAYOUTS[layout]:
                control = _make_control(spec, evidence, False, 'visible')
                if control is not None:
                    controls.append(control)

    for item in variants:
        for spec in cn.VARIANT_EXTRA_CONTROLS.get(item, ()):
            control = _make_control(spec, evidence, False, f'variant:{item.value}')
            if control is not None:
                controls.append(control)

    for overlay in sorted(overlays, key=lambda item: item.value):
        for spec in cn.OVERLAY_CONTROLS.get(overlay, ()):
            control = _make_control(spec, evidence, False, f'overlay:{overlay.value}')
            if control is not None:
                controls.append(control)
    return tuple(controls)


def _overlay_controls(overlay: Overlay, evidence: Evidence, image: np.ndarray) -> tuple[Control, ...]:
    if overlay is Overlay.RETREAT_CONFIRM:
        boxes = find_dialog_buttons(image)
        if len(boxes) >= 2:
            left, right = boxes[0], boxes[1]
            return (
                Control('决定', ControlKind.BUTTON, right, True, f'overlay:{overlay.value}'),
                Control('取消', ControlKind.BUTTON, left, True, f'overlay:{overlay.value}'),
            )
    return tuple(
        control for spec in cn.OVERLAY_CONTROLS.get(overlay, ())
        if (control := _make_control(spec, evidence, False, f'overlay:{overlay.value}')) is not None
    )


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


def _with_turn_boundary(observation: Observation) -> Observation:
    if observation.base_page is not BasePage.BATTLE:
        return observation
    attack = observation.control('攻击')
    valid = (attack is not None and attack.actionable
             and Variant.BATTLE_ANIMATION not in observation.variants)
    field = Field(FieldStatus.CONFIRMED, True) if valid else Field(FieldStatus.UNCERTAIN, False)
    return observation.with_fields({'turn_boundary': field})


class Perception:
    """国服画面感知；只读取画面，不点击、不推进状态机、不访问设备。"""

    def __init__(self, *, templates: Mapping[str, Template] | None = None,
                 emblems: Mapping[str, evidence.Emblem] | None = None,
                 ocr_reader=None, clock=time.perf_counter, cache_size: int = 64,
                 identity_root: Path | None = None) -> None:
        self._templates = dict(templates) if templates is not None else evidence.load_templates()
        self._emblems = dict(emblems) if emblems is not None else evidence.load_emblems()
        self._identities = IdentityMatcher(identity_root)
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

    def bind_formation(self, formation) -> None:
        self._identities.bind_formation(formation)
        with self._lock:
            self._cache.clear()
            self._requested.clear()
            self._latest = None

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
        scores, locations = evidence.match_templates(gray, self._templates)
        means = evidence.measure_probes(gray)
        edges = evidence.measure_frame_edges(gray)
        slots = evidence.measure_slots(image, gray)
        frame_evidence = Evidence(scores=scores, templates=self._templates, means=means,
                                  edges=edges, slots=slots, locations=locations)
        page, variants, overlays, confidence = _classify(frame_evidence)
        if page is BasePage.CARD:
            np_slots, np_blobs, np_faces = evidence.measure_np_slots(image, gray, self._emblems)
            frame_evidence = Evidence(scores=scores, templates=self._templates, means=means,
                                      edges=edges, slots=slots, np_slots=np_slots, np_blobs=np_blobs,
                                      np_faces=np_faces, locations=locations)
        return Observation(
            base_page=page,
            variants=variants,
            overlays=overlays,
            controls=_controls(page, variants, overlays, frame_evidence, gray, image, self._identities),
            fields=_fields(page, None, False),
            frame_hash=digest,
            confidence=round(min(max(confidence, 0.0), 1.0), 3),
            evidence={name: round(value, 4) for name, value in {**scores, **means, **edges, **slots, **frame_evidence.np_slots}.items()},
        )

    def _with_ocr(self, observation: Observation) -> Observation:
        digest = observation.frame_hash
        cached = self._worker.cached(digest)
        pending = self._worker.pending(digest)
        with self._lock:
            requested = self._requested.get(digest, frozenset())
        fields = _fields(observation.base_page, cached, pending, requested)
        return _with_turn_boundary(observation.with_fields(fields))

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
