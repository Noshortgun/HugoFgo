"""从者与概念礼装的区域图库匹配；不调用 OCR 或网络。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from executor.perception.observation import CommandCardIdentity, SupportCandidate
from executor.perception.regions import cn


@dataclass(frozen=True)
class IdentityTemplate:
    entity_id: int
    patch: np.ndarray


class IdentityMatcher:
    _catalog_cache: dict[tuple[str, str], list[IdentityTemplate]] = {}

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or cn.PROJECT_ROOT / 'data'
        self._servants = self._metadata('servants-by-id.json')
        self._equips = self._metadata('equips-by-id.json')
        self._face_templates: list[IdentityTemplate] | None = None
        self._ce_templates: list[IdentityTemplate] | None = None
        self._command_templates: list[IdentityTemplate] | None = None
        self._support_tag: np.ndarray | None = None
        self._formation_slots: dict[tuple[int, bool], int] = {}

    def bind_formation(self, formation) -> None:
        slots: dict[tuple[int, bool], int] = {}
        for member in formation:
            if member.svt_id is None:
                continue
            key = (member.svt_id, member.support)
            slots[key] = member.slot if key not in slots else -1
        self._formation_slots = slots

    def _metadata(self, name: str) -> dict[int, dict]:
        path = self._root / name
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding='utf-8'))
        return {int(key): value for key, value in data.items()}

    @staticmethod
    def _read(path: Path) -> np.ndarray | None:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None

    def _load(self, kind: str) -> list[IdentityTemplate]:
        cache_key = (str(self._root), kind)
        if cache_key in self._catalog_cache:
            return self._catalog_cache[cache_key]
        directory = self._root / 'assets/atlas' / ('faces' if kind == 'face' else 'ce')
        builtin = cn.TEMPLATE_DIR / 'identities' / ('faces' if kind == 'face' else 'ce')
        paths = {path.stem: path for path in builtin.glob('*.png')}
        if directory.exists():
            paths.update({path.stem: path for path in directory.glob('*.png')})
        templates = []
        for name, path in paths.items():
            try:
                entity_id = int(name.split('-', 1)[0])
            except ValueError:
                continue
            patch = self._read(path)
            if patch is None:
                continue
            if kind == 'face':
                patch = cv2.resize(patch, cn.SUPPORT_FACE_SIZE)
                x1, y1, x2, y2 = cn.SUPPORT_FACE_TEMPLATE_BOX
                patch = patch[y1:y2, x1:x2]
            else:
                patch = cv2.resize(patch, cn.SUPPORT_CE_SIZE)[:cn.SUPPORT_CE_CROP_HEIGHT]
            templates.append(IdentityTemplate(entity_id, patch))
        self._catalog_cache[cache_key] = templates
        return templates

    @staticmethod
    def _match(query: np.ndarray, templates: list[IdentityTemplate], minimum: float,
               margin: float) -> tuple[int, float] | None:
        best: dict[int, float] = {}
        for template in templates:
            if (query.shape[0] < template.patch.shape[0]
                    or query.shape[1] < template.patch.shape[1]):
                continue
            result = cv2.matchTemplate(query, template.patch, cv2.TM_CCOEFF_NORMED)
            score = float(np.nan_to_num(result.max(), nan=0.0))
            best[template.entity_id] = max(score, best.get(template.entity_id, -1.0))
        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
        if len(ranked) < 2 or ranked[0][1] < minimum:
            return None
        if ranked[0][1] - ranked[1][1] < margin:
            return None
        return ranked[0]

    def support(self, image: np.ndarray, row: tuple[int, int, int, int]) -> SupportCandidate | None:
        if self._face_templates is None:
            self._face_templates = self._load('face')
        if self._ce_templates is None:
            self._ce_templates = self._load('ce')
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        face_y = row[3] - cn.SUPPORT_FACE_BOTTOM_OFFSET
        ce_y = row[3] - cn.SUPPORT_CE_BOTTOM_OFFSET
        if row[3] >= cn.BASE_SIZE[1]:
            face = gray[face_y + cn.SUPPORT_FACE_SEARCH_Y_OFFSET:
                        face_y + cn.SUPPORT_FACE_SEARCH_Y_OFFSET + cn.SUPPORT_FACE_SEARCH_HEIGHT,
                        cn.SUPPORT_FACE_SEARCH_X:
                        cn.SUPPORT_FACE_SEARCH_X + cn.SUPPORT_FACE_TEMPLATE_BOX[2]
                        - cn.SUPPORT_FACE_TEMPLATE_BOX[0]]
        else:
            face = gray[face_y + cn.SUPPORT_FACE_TEMPLATE_BOX[1]:
                        face_y + cn.SUPPORT_FACE_TEMPLATE_BOX[3],
                        cn.SUPPORT_FACE_SEARCH_X:
                        cn.SUPPORT_FACE_SEARCH_X + cn.SUPPORT_FACE_TEMPLATE_BOX[2]
                        - cn.SUPPORT_FACE_TEMPLATE_BOX[0]]
        ce = gray[ce_y:ce_y + cn.SUPPORT_CE_CROP_HEIGHT,
                  cn.SUPPORT_CE_X:cn.SUPPORT_CE_X + cn.SUPPORT_CE_SIZE[0]]
        servant = self._match(face, self._face_templates, cn.SUPPORT_FACE_THRESHOLD,
                              cn.SUPPORT_FACE_MARGIN)
        equip = self._match(ce, self._ce_templates, cn.SUPPORT_CE_THRESHOLD, cn.SUPPORT_CE_MARGIN)
        if servant is None and equip is None:
            return None
        servant_info = self._servants.get(servant[0], {}) if servant is not None else {}
        equip_info = self._equips.get(equip[0], {}) if equip is not None else {}
        return SupportCandidate(
            name=str(servant_info.get('name', '')),
            svt_id=servant[0] if servant is not None else None,
            role=servant_info.get('classLabel'),
            ce=equip_info.get('name'),
            ce_id=equip[0] if equip is not None else None,
        )

    def _load_commands(self) -> list[IdentityTemplate]:
        directory = cn.TEMPLATE_DIR / 'identities' / 'commands'
        templates = []
        for path in directory.glob('*.png'):
            patch = self._read(path)
            if patch is not None:
                templates.append(IdentityTemplate(int(path.stem), patch))
        return templates

    def command(self, image: np.ndarray, box: tuple[int, int, int, int]) -> CommandCardIdentity | None:
        if self._command_templates is None:
            self._command_templates = self._load_commands()
            self._support_tag = self._read(cn.TEMPLATE_DIR / 'identities' / 'support-tag.png')
        if not self._command_templates:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        x1, y1, x2, _y2 = box
        top, left, bottom, right = cn.COMMAND_FACE_QUERY_OFFSETS
        query = gray[y1 + top:y1 + bottom, x1 + left:x1 + right]
        scores = []
        for template in self._command_templates:
            result = cv2.matchTemplate(query, template.patch, cv2.TM_CCOEFF_NORMED)
            scores.append((template.entity_id, float(np.nan_to_num(result.max(), nan=0.0))))
        scores.sort(key=lambda item: item[1], reverse=True)
        if (len(scores) < 2 or scores[0][1] < cn.COMMAND_FACE_THRESHOLD
                or scores[0][1] - scores[1][1] < cn.COMMAND_FACE_MARGIN):
            return None

        type_top, type_bottom = cn.COMMAND_TYPE_Y_OFFSETS
        hsv = cv2.cvtColor(image[y1 + type_top:y1 + type_bottom, x1:x2], cv2.COLOR_BGR2HSV)
        hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        colored = ((saturation > cn.COMMAND_TYPE_MIN_SATURATION)
                   & (value > cn.COMMAND_TYPE_MIN_VALUE))
        counts = {
            card_type: sum(int((colored & (hue >= low) & (hue < high)).sum())
                           for low, high in ranges)
            for card_type, ranges in cn.COMMAND_CARD_HUES.items()
        }
        card_type = max(counts, key=counts.get)
        if counts[card_type] < cn.COMMAND_TYPE_MIN_PIXELS:
            return None

        support = False
        if self._support_tag is not None:
            top, left, bottom, right = cn.COMMAND_SUPPORT_QUERY_OFFSETS
            tag_region = gray[y1 + top:y1 + bottom, x1 + left:x1 + right]
            result = cv2.matchTemplate(tag_region, self._support_tag, cv2.TM_CCOEFF_NORMED)
            support = float(np.nan_to_num(result.max(), nan=0.0)) >= cn.COMMAND_SUPPORT_THRESHOLD
        return CommandCardIdentity(servant_slot=self._formation_slots.get((scores[0][0], support), -1),
                                   card_type=card_type, svt_id=scores[0][0], support=support)
