"""画面感知适配器：模板、颜色、布局与数字字段都只使用当前帧的图像证据。"""
from __future__ import annotations

import threading
import time
from dataclasses import replace
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
from executor.perception.digits import IMAGE_FIELDS, DigitReader
from executor.perception.regions import cn
from executor.perception.secondary_selection import detect_secondary_options

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


def _row_visual_hash(gray: np.ndarray, box: tuple[int, int, int, int]) -> str:
    x1, y1, x2, y2 = box
    crop = gray[y1:y2, x1:x2]
    small = cv2.resize(crop, (16, 16), interpolation=cv2.INTER_AREA)
    return ''.join('1' if value else '0' for value in (small > small.mean()).flatten())


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


def _support_at_bottom(image: np.ndarray) -> Field:
    x1, y1, x2, y2 = cn.SUPPORT_SCROLL_REGION
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    handle = (hsv[..., 1] < 80) & (hsv[..., 2] > 170)
    rows = np.flatnonzero((handle.sum(axis=1) >= cn.SUPPORT_SCROLL_HANDLE_MIN_WIDTH))
    if not len(rows):
        return Field(FieldStatus.UNCERTAIN)
    handle_bottom = int(rows[-1]) + y1
    return Field(FieldStatus.CONFIRMED, handle_bottom >= cn.SUPPORT_SCROLL_BOTTOM_Y)


def _support_class(image: np.ndarray) -> Field:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    scores = []
    for x in cn.SUPPORT_CLASS_CENTERS:
        yy, xx = np.mgrid[90:170, max(0, x - 42):x + 42]
        distance = np.abs(xx - x) + np.abs(yy - 129)
        region = hsv[90:170, max(0, x - 42):x + 42]
        selected = ((distance >= 31) & (distance <= 40)
                    & (region[..., 0] >= 85) & (region[..., 0] <= 115)
                    & (region[..., 1] > 100) & (region[..., 2] > 120))
        scores.append(int(selected.sum()))
    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    if ranked[0][1] < cn.SUPPORT_CLASS_MIN_SCORE or ranked[0][1] - ranked[1][1] < cn.SUPPORT_CLASS_MIN_MARGIN:
        return Field(FieldStatus.UNCERTAIN)
    return Field(FieldStatus.CONFIRMED, cn.SUPPORT_CLASS_NAMES[ranked[0][0]])


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
                                        support=identities.support(image, box),
                                        visual_hash=_row_visual_hash(gray, box)))

        if page is BasePage.BATTLE:
            for slot in (1, 2, 3):
                if not evidence.hit(f'servant_slot_{slot}'):
                    continue
                x1, _y1, _x2, _y2 = cn.SLOT_PROBES[f'servant_slot_{slot}'].box
                for skill, offset in enumerate(cn.SKILL_ICON_OFFSETS, start=1):
                    left = x1 + offset
                    box = (left, cn.SKILL_ICON_BOX[1], left + cn.SKILL_ICON_BOX[2] - cn.SKILL_ICON_BOX[0],
                           cn.SKILL_ICON_BOX[3])
                    cooling = evidence.skill_cooling(slot, skill)
                    controls.append(Control(f'从者{slot}技能{skill}', ControlKind.SKILL, box, not cooling,
                                            'skill_cooldown' if cooling else f'slot_visible:{slot}',
                                            on_cooldown=cooling))
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


def _fields(page: BasePage) -> dict[str, Field]:
    return {name: Field(FieldStatus.NOT_APPLICABLE if page not in pages else FieldStatus.NOT_READ)
            for name, pages in cn.FIELD_PAGES.items()}


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
                 clock=time.perf_counter, cache_size: int = 64,
                 identity_root: Path | None = None) -> None:
        self._templates = dict(templates) if templates is not None else evidence.load_templates()
        self._emblems = dict(emblems) if emblems is not None else evidence.load_emblems()
        self._identities = IdentityMatcher(identity_root)
        self._digits = DigitReader()
        self._clock = clock
        self._cache_size = cache_size
        self._cache: OrderedDict[str, Observation] = OrderedDict()
        self._lock = threading.Lock()
        self._latest: Observation | None = None
        self._timings: deque[float] = deque(maxlen=200)
        self._field_timings: deque[float] = deque(maxlen=200)
        self._reads = 0
        self._cache_hits = 0
        self._field_reads = 0
        self._field_cache_hits = 0

    def bind_formation(self, formation) -> None:
        self._identities.bind_formation(formation)
        with self._lock:
            self._cache.clear()
            self._latest = None

    def bind_mystic(self, mystic_id: str) -> None:
        self._identities.bind_mystic(mystic_id)

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
                    self._cache.popitem(last=False)
                self._latest = observation
                self._reads += 1
                self._timings.append((self._clock() - started) * 1000.0)
        return _with_turn_boundary(observation)

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
        if page is BasePage.BATTLE and Variant.BATTLE_ANIMATION not in variants:
            cooldowns = evidence.measure_skill_cooldowns(gray, self._emblems.get('skill_cooldown'))
            frame_evidence = replace(frame_evidence, skill_cooldowns=cooldowns)
        secondary_controls: tuple[Control, ...] = ()
        if (page is BasePage.BATTLE and not overlays
                and not variants & {Variant.BATTLE_ANIMATION, Variant.SKILL_TARGET}):
            secondary_controls = detect_secondary_options(image)
            if secondary_controls:
                variants = variants | {Variant.SECONDARY_SELECTION}
        fields = _fields(page)
        if page is BasePage.SUPPORT:
            fields['support_class'] = _support_class(image)
            fields['support_at_bottom'] = _support_at_bottom(image)
        if page is BasePage.TEAM:
            fields.update(self._identities.team(image))
        if (page is BasePage.BATTLE and not overlays
                and not variants & {Variant.BATTLE_ANIMATION, Variant.SECONDARY_SELECTION}):
            fields['battle_servants'] = self._identities.battle(image)
        return Observation(
            base_page=page,
            variants=variants,
            overlays=overlays,
            controls=secondary_controls or _controls(page, variants, overlays, frame_evidence, gray, image, self._identities),
            fields=fields,
            frame_hash=digest,
            confidence=round(min(max(confidence, 0.0), 1.0), 3),
            evidence={name: round(value, 4) for name, value in {**scores, **means, **edges, **slots, **frame_evidence.np_slots}.items()},
        )

    @staticmethod
    def _with_crit(observation: Observation) -> Observation:
        crit = observation.field('crit')
        if crit is None or not crit.usable or not isinstance(crit.value, tuple):
            return observation
        controls = list(observation.controls)
        for index, control in enumerate(controls):
            if control.card is None or control.card.noble_phantasm:
                continue
            card_index = int(control.name.removeprefix('卡')) - 1
            if 0 <= card_index < len(crit.value):
                controls[index] = replace(control, card=replace(control.card, crit_rate=crit.value[card_index]))
        return replace(observation, controls=tuple(controls))

    def read_fields(self, frame: Frame, names: Iterable[str], *,
                    timeout: float | None = None) -> Observation:
        """同步读取当前帧字段；timeout 只为兼容调用方保留，不会排队或等待。"""
        started = self._clock()
        wanted = tuple(dict.fromkeys(names))
        unknown = [name for name in wanted if name not in IMAGE_FIELDS]
        if unknown:
            raise ValueError(f'未知字段 {unknown}；可选 {list(IMAGE_FIELDS)}')

        digest = evidence.frame_hash(frame)
        with self._lock:
            latest = self._latest
        if latest is None or latest.frame_hash != digest:
            raise StaleFrameError('传入的帧不是最近一次 read() 的画面')

        applicable = tuple(name for name in wanted if latest.base_page in cn.FIELD_PAGES[name])
        if not applicable:
            return _with_turn_boundary(latest)
        missing = tuple(name for name in applicable
                        if latest.field(name) is None or latest.field(name).status is FieldStatus.NOT_READ)
        if not missing:
            with self._lock:
                self._field_cache_hits += 1
            cached = replace(latest, evidence={**latest.evidence, 'field_cache_hit': 1.0})
            return _with_turn_boundary(cached)

        fields = self._digits.read(evidence.decode_frame(frame), missing)
        elapsed_ms = (self._clock() - started) * 1000.0
        observation = self._with_crit(latest.with_fields(fields))
        observation = replace(observation, evidence={**observation.evidence,
                                                     'field_ms': round(elapsed_ms, 3),
                                                     'field_cache_hit': 0.0})
        with self._lock:
            if self._latest is None or self._latest.frame_hash != digest:
                raise StaleFrameError('数字识别期间画面已变化，丢弃当前字段结果')
            self._cache[digest] = observation
            self._latest = observation
            self._field_reads += 1
            self._field_timings.append((self._clock() - started) * 1000.0)
        return _with_turn_boundary(observation)

    def stats(self) -> dict:
        with self._lock:
            timings = list(self._timings)
            field_timings = list(self._field_timings)
            reads = self._reads
            hits = self._cache_hits
            field_reads = self._field_reads
            field_hits = self._field_cache_hits

        def measured(values: list[float], prefix: str = '') -> dict:
            if not values:
                return {f'{prefix}last_ms': None, f'{prefix}mean_ms': None,
                        f'{prefix}p95_ms': None, f'{prefix}max_ms': None}
            ordered = sorted(values)
            p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
            return {f'{prefix}last_ms': round(values[-1], 2),
                    f'{prefix}mean_ms': round(sum(values) / len(values), 2),
                    f'{prefix}p95_ms': round(p95, 2), f'{prefix}max_ms': round(max(values), 2)}

        return {'reads': reads, 'cache_hits': hits, **measured(timings),
                'field_reads': field_reads, 'field_cache_hits': field_hits,
                **measured(field_timings, 'field_')}

    def close(self) -> None:
        pass
