"""同步快路径的图像证据：帧解码、帧哈希与区域化模板匹配，不调用 OCR。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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
    """帧内容的稳定哈希；后台 OCR 的请求与结果用它绑定具体画面。"""
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


def match_templates(gray: np.ndarray, templates: Mapping[str, Template]) -> dict[str, float]:
    """在每个锚点自己的 ROI 内做一次模板匹配，返回归一化最佳得分。"""
    height, width = gray.shape[:2]
    scores: dict[str, float] = {}
    for name, template in templates.items():
        x1, y1, x2, y2 = template.roi
        roi = gray[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
        patch_height, patch_width = template.patch.shape[:2]
        if roi.shape[0] < patch_height or roi.shape[1] < patch_width:
            scores[name] = 0.0
            continue
        result = cv2.matchTemplate(roi, template.patch, cv2.TM_CCOEFF_NORMED)
        scores[name] = float(np.nan_to_num(result.max(), nan=0.0))
    return scores
