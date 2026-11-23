"""同步图像证据：帧解码、帧哈希与区域化模板匹配。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import cv2
import numpy as np

from executor.device.base import Frame, FrameFormat
from executor.perception.errors import PerceptionError
from executor.perception.regions import cn


@dataclass(frozen=True)
class Template:
    name: str
    patch: np.ndarray
    roi: tuple[int, int, int, int]
    threshold: float


@dataclass(frozen=True)
class Emblem:
    name: str
    patch: np.ndarray
    threshold: float


@dataclass(frozen=True)
class Evidence:
    """一帧画面的全部同步证据：模板得分与亮度探针共享同一套 hit 判据。"""

    scores: Mapping[str, float]
    templates: Mapping[str, Template]
    means: Mapping[str, float]
    edges: Mapping[str, float]
    slots: Mapping[str, float] = field(default_factory=dict)
    np_slots: Mapping[str, float] = field(default_factory=dict)
    np_blobs: Mapping[str, bool] = field(default_factory=dict)
    np_faces: Mapping[str, float] = field(default_factory=dict)
    locations: Mapping[str, tuple[int, int]] = field(default_factory=dict)

    def np_hit(self, slot: str) -> bool:
        return (bool(self.np_blobs.get(slot))
                or self.np_faces.get(slot, 0.0) >= cn.NP_FACE_MIN_FRACTION
                or self.np_slots.get(slot, 0.0) >= cn.NP_EMBLEM_THRESHOLD)

    def hit(self, name: str) -> bool:
        template = self.templates.get(name)
        if template is not None:
            return self.scores.get(name, 0.0) >= template.threshold
        frame = cn.FRAME_EDGE_PROBES.get(name)
        if frame is not None:
            return self.edges.get(name, 0.0) >= frame.threshold
        slot = cn.SLOT_PROBES.get(name)
        if slot is not None:
            return self.slots.get(name, 0.0) >= slot.threshold
        probe = cn.INTENSITY_PROBES.get(name)
        return probe is not None and self.means.get(name, 0.0) >= probe[1]

    def score(self, name: str) -> float:
        return self.scores.get(name, self.means.get(name, self.edges.get(name, self.slots.get(name, 0.0))))


def decode_frame(frame: Frame) -> np.ndarray:
    """把设备帧解成 BGR24，并统一到 1280×720 基准。"""
    if frame.format is FrameFormat.BGR24:
        image = np.frombuffer(frame.image, dtype=np.uint8).reshape(frame.height, frame.width, 3)
        image = np.ascontiguousarray(image)
    else:
        image = cv2.imdecode(np.frombuffer(frame.image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise PerceptionError('设备帧不是有效的 PNG 图片')
    if (image.shape[1], image.shape[0]) != cn.BASE_SIZE:
        image = cv2.resize(image, cn.BASE_SIZE, interpolation=cv2.INTER_AREA)
    return image


def frame_hash(frame: Frame) -> str:
    """帧内容的稳定哈希；字段结果与缓存都用它绑定具体画面。"""
    digest = hashlib.blake2b(frame.image, digest_size=8)
    digest.update(b'|')
    digest.update(f'{frame.width}x{frame.height}|{frame.format.value}'.encode('ascii'))
    return digest.hexdigest()


def load_templates(directory: Path = cn.TEMPLATE_DIR) -> dict[str, Template]:
    manifest_path = directory / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise PerceptionError(f'缺少识别模板清单：{manifest_path}；先运行 python -m executor.perception.build_templates') from None
    except json.JSONDecodeError as error:
        raise PerceptionError(f'识别模板清单无法解析：{manifest_path}：{error}') from None

    templates: dict[str, Template] = {}
    for name, spec in manifest.get('templates', {}).items():
        path = directory / spec['file']
        data = np.fromfile(str(path), dtype=np.uint8)
        patch = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
        if patch is None:
            raise PerceptionError(f'缺少识别模板：{path}')
        templates[name] = Template(
            name=name,
            patch=patch,
            roi=tuple(int(value) for value in spec['roi']),
            threshold=float(spec['threshold']),
        )
    if not templates:
        raise PerceptionError(f'识别模板清单为空：{manifest_path}')
    return templates


def _read_manifest(directory: Path) -> dict:
    manifest_path = directory / 'manifest.json'
    try:
        return json.loads(manifest_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise PerceptionError(f'缺少识别模板清单：{manifest_path}；先运行 python -m executor.perception.build_templates') from None
    except json.JSONDecodeError as error:
        raise PerceptionError(f'识别模板清单无法解析：{manifest_path}：{error}') from None


def load_emblems(directory: Path = cn.TEMPLATE_DIR) -> dict[str, Emblem]:
    manifest = _read_manifest(directory)
    emblems: dict[str, Emblem] = {}
    for name, spec in manifest.get('emblems', {}).items():
        path = directory / spec['file']
        data = np.fromfile(str(path), dtype=np.uint8)
        patch = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
        if patch is None:
            raise PerceptionError(f'缺少宝具纹章模板：{path}')
        emblems[name] = Emblem(name=name, patch=patch, threshold=float(spec['threshold']))
    return emblems


def find_enemy_bars(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """逐条找敌人血条（红/紫），按上→下、左→右排序；宽血条才算，排除职阶图标。"""
    x1, y1, x2, y2 = cn.ENEMY_BAR_REGION
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = ((hue < 10) | (hue > 170)) & (sat > 110) & (val > 100)
    purple = (hue > 120) & (hue < 165) & (sat > 90) & (val > 90)
    mask = ((red | purple).astype(np.uint8)) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bars = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (cn.ENEMY_BAR_MIN_WIDTH <= w <= cn.ENEMY_BAR_MAX_WIDTH):
            continue
        if not (cn.ENEMY_BAR_MIN_HEIGHT <= h <= cn.ENEMY_BAR_MAX_HEIGHT):
            continue
        bars.append((x + x1, y + y1, w, h))
    bars.sort(key=lambda item: (item[1] // 40, item[0]))
    return bars


def find_dialog_buttons(image: np.ndarray, region=cn.DIALOG_BUTTON_REGION) -> list[tuple[int, int, int, int]]:
    """在对话框里找白色胶囊按钮，按从左到右返回。"""
    x1, y1, x2, y2 = region
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    bright = ((hsv[..., 2] > 150) & (hsv[..., 1] < 80)).astype(np.uint8) * 255
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if 180 <= w <= 380 and 40 <= h <= 90 and cv2.contourArea(contour) > 0.6 * w * h:
            boxes.append((x + x1, y + y1, x + x1 + w, y + y1 + h))
    return sorted(boxes)


def _has_card_emblem(image: np.ndarray, box: tuple[int, int, int, int],
                     card_box: tuple[int, int, int, int]) -> bool:
    """卡种纹章：卡面下部一块足够宽、尺寸在合理范围的纯色图形（绿/蓝/红）。

    只取每种颜色的最大连通域：技能动画的巨型效果环会明显超出宝具卡纹章尺寸。
    """
    x1, y1, x2, y2 = box
    height = y2 - y1
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    low_ratio = 0.30
    card_top, card_height = card_box[1], card_box[3] - card_box[1]
    for index, hues in enumerate(cn.NP_EMBLEM_HUES):
        mask = np.zeros_like(hue, dtype=np.uint8)
        for low, high in hues:
            mask |= ((hue >= low) & (hue < high)).astype(np.uint8)
        mask = (mask & (sat > 110) & (val > 70)).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        bounds = cn.NP_EMBLEM_BOUNDS[index]
        if not (bounds['min_area'] <= area <= bounds['max_area']):
            continue
        _x, y, width, h = cv2.boundingRect(largest)
        if not (bounds['min_width'] <= width <= bounds['max_width']):
            continue
        # 蓝星的低面积候选要求更宽，避免把蓝色光斑当成 Arts 纹章。
        if index == 1 and area < 1500 and width < 90:
            continue
        if h and not (bounds['min_aspect'] <= width / h <= bounds['max_aspect']):
            continue
        fill = area / (width * h) if h else 0.0
        if fill < bounds['min_fill']:
            continue
        top = y + y1
        center_y = top + h / 2
        if top + h > bounds['max_bottom']:
            continue
        ratio = (center_y - card_top) / card_height if card_height else 0
        high_fill = fill >= bounds.get('high_fill_min', 1.0)
        max_ratio = bounds.get('high_fill_max_ratio', bounds['max_center_ratio']) if high_fill             else bounds['max_center_ratio']
        if low_ratio <= ratio <= max_ratio:
            return True
    return False


def measure_np_slots(image: np.ndarray, gray: np.ndarray,
                     emblems: Mapping[str, Emblem]) -> tuple[dict[str, float], dict[str, bool], dict[str, float]]:
    """逐槽判断宝具卡：卡面亮度为主，Quick 绿环 / Arts 蓝星模板为辅。"""
    scores: dict[str, float] = {}
    blobs: dict[str, bool] = {}
    faces: dict[str, float] = {}
    full_hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    for slot, box in cn.NP_SEARCH.items():
        x1, y1, x2, y2 = box
        roi = gray[y1:y2, x1:x2]
        best = 0.0
        for emblem in emblems.values():
            if roi.shape[0] < emblem.patch.shape[0] or roi.shape[1] < emblem.patch.shape[1]:
                continue
            result = cv2.matchTemplate(roi, emblem.patch, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(np.nan_to_num(result.max(), nan=0.0)))
        scores[slot] = best
        blobs[slot] = _has_card_emblem(image, box, cn.NP_SLOTS[slot])
        fx1, fy1, fx2, fy2 = cn.NP_SLOTS[slot]
        face = full_hsv[fy1:fy2, fx1:fx2]
        faces[slot] = float(((face[..., 2] > 165) & (face[..., 1] < 110)).mean())
    return scores, blobs, faces


def match_templates(gray: np.ndarray,
                    templates: Mapping[str, Template]) -> tuple[dict[str, float], dict[str, tuple[int, int]]]:
    """在每个锚点自己的 ROI 内做一次模板匹配，返回归一化得分与最佳位置。"""
    height, width = gray.shape[:2]
    scores: dict[str, float] = {}
    locations: dict[str, tuple[int, int]] = {}
    for name, template in templates.items():
        x1, y1, x2, y2 = template.roi
        roi = gray[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
        patch_height, patch_width = template.patch.shape[:2]
        if roi.shape[0] < patch_height or roi.shape[1] < patch_width:
            scores[name] = 0.0
            continue
        result = cv2.matchTemplate(roi, template.patch, cv2.TM_CCOEFF_NORMED)
        clean = np.nan_to_num(result, nan=0.0)
        _min, maximum, _min_loc, max_loc = cv2.minMaxLoc(clean)
        scores[name] = float(maximum)
        locations[name] = (max(0, x1) + int(max_loc[0]), max(0, y1) + int(max_loc[1]))
    return scores, locations


def measure_slots(image: np.ndarray, gray: np.ndarray, probes=cn.SLOT_PROBES) -> dict[str, float]:
    """槽位存在性：技能看边缘像素数，敌人看红色血条像素数。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    scores: dict[str, float] = {}
    for name, probe in probes.items():
        x1, y1, x2, y2 = probe.box
        if probe.mode == 'edge':
            edges = cv2.Canny(gray[y1:y2, x1:x2], 50, 150)
            scores[name] = float((edges > 0).sum())
        else:
            h, sat, val = hsv[y1:y2, x1:x2, 0], hsv[y1:y2, x1:x2, 1], hsv[y1:y2, x1:x2, 2]
            red = ((h < 10) | (h > 170)) & (sat > 110) & (val > 100)
            purple = (h > 120) & (h < 165) & (sat > 90) & (val > 90)
            scores[name] = float((red | purple).sum())
    return scores


def measure_frame_edges(gray: np.ndarray, probes=cn.FRAME_EDGE_PROBES) -> dict[str, float]:
    """数每个卡框里的长水平/垂直边缘像素；取所有卡的最小值，缺一张框就不算展开。"""
    scores: dict[str, float] = {}
    for name, probe in probes.items():
        per_card = []
        for x1, y1, x2, y2 in probe.cards:
            roi = gray[y1 - 3:y2 + 3, x1 - 3:x2 + 3]
            edges = cv2.Canny(roi, 50, 150)
            horizontal = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((1, probe.line_length), np.uint8))
            vertical = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((probe.line_length, 1), np.uint8))
            per_card.append(float((horizontal > 0).sum() + (vertical > 0).sum()))
        scores[name] = min(per_card) if per_card else 0.0
    return scores


def measure_probes(gray: np.ndarray, probes=cn.INTENSITY_PROBES) -> dict[str, float]:
    """颜色/亮度证据：例如换人按钮点亮前后的平均灰度差异。"""
    means: dict[str, float] = {}
    for name, (box, _minimum) in probes.items():
        x1, y1, x2, y2 = box
        region = gray[y1:y2, x1:x2]
        means[name] = float(region.mean()) if region.size else 0.0
    return means
