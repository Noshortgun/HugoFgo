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


@dataclass(frozen=True)
class CommandTemplate:
    entity_id: int
    keypoints: tuple
    descriptors: np.ndarray


class IdentityMatcher:
    _catalog_cache: dict[tuple[str, str, frozenset[int] | None], list[IdentityTemplate]] = {}
    _command_cache: dict[tuple[str, frozenset[int] | None], list[CommandTemplate]] = {}

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or cn.PROJECT_ROOT / 'data' / 'CN'
        self._servants = self._metadata('servants-by-id.json')
        self._equips = self._metadata('equips-by-id.json')
        self._face_templates: list[IdentityTemplate] | None = None
        self._ce_templates: list[IdentityTemplate] | None = None
        self._command_templates: list[CommandTemplate] | None = None
        self._support_tag: np.ndarray | None = None
        self._formation_slots: dict[tuple[int, bool], int] = {}
        self._required_face_ids: frozenset[int] | None = None
        self._required_ce_ids: frozenset[int] | None = None
        self._required_command_ids: frozenset[int] | None = None
        self._sift = cv2.SIFT_create(nfeatures=cn.COMMAND_SIFT_FEATURES)
        self._feature_matcher = cv2.BFMatcher()

    def bind_formation(self, formation) -> None:
        slots: dict[tuple[int, bool], int] = {}
        support = [member for member in formation if member.support]
        for member in formation:
            if member.svt_id is None:
                continue
            key = (member.svt_id, member.support)
            slots[key] = member.slot if key not in slots else -1
        self._formation_slots = slots
        self._required_face_ids = frozenset(member.svt_id for member in support
                                            if member.svt_id is not None)
        self._required_ce_ids = frozenset(member.ce_id for member in support
                                          if member.ce_id is not None)
        self._required_command_ids = frozenset(member.svt_id for member in formation
                                               if member.svt_id is not None)
        self._face_templates = None
        self._ce_templates = None
        self._command_templates = None

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
        required = self._required_face_ids if kind == 'face' else self._required_ce_ids
        cache_key = (str(self._root), kind, required)
        if cache_key in self._catalog_cache:
            return self._catalog_cache[cache_key]
        directory = self._root / 'assets/atlas' / ('faces' if kind == 'face' else 'ce')
        builtin = cn.TEMPLATE_DIR / 'identities' / ('faces' if kind == 'face' else 'ce')
        paths: dict[str, tuple[int, Path]] = {}
        for source in (builtin, directory):
            if not source.exists():
                continue
            for path in source.glob('*.png'):
                try:
                    entity_id = int(path.stem.split('-', 1)[0])
                except ValueError:
                    continue
                if required is None or entity_id in required or source == builtin:
                    paths[path.stem] = (entity_id, path)
        templates = []
        for name, (entity_id, path) in paths.items():
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

    def _load_commands(self) -> list[CommandTemplate]:
        cache_key = (str(self._root), self._required_command_ids)
        if cache_key in self._command_cache:
            return self._command_cache[cache_key]
        directory = self._root / 'assets/atlas/commands'
        builtin = cn.TEMPLATE_DIR / 'identities' / 'commands'
        paths: dict[str, tuple[int, Path]] = {}
        for source in (builtin, directory):
            if not source.exists():
                continue
            for path in source.glob('*/*.png'):
                try:
                    entity_id = int(path.parent.name)
                except ValueError:
                    continue
                if ((self._required_command_ids is None and source == builtin)
                        or (self._required_command_ids is not None
                            and entity_id in self._required_command_ids)):
                    paths[str(path.relative_to(source))] = (entity_id, path)
        templates = []
        for entity_id, path in paths.values():
            data = np.fromfile(str(path), dtype=np.uint8)
            asset = cv2.imdecode(data, cv2.IMREAD_UNCHANGED) if data.size else None
            if asset is None or asset.ndim != 3 or asset.shape[2] != 4:
                continue
            gray = cv2.cvtColor(asset[..., :3], cv2.COLOR_BGR2GRAY)
            keypoints, descriptors = self._sift.detectAndCompute(gray, asset[..., 3])
            if descriptors is not None:
                templates.append(CommandTemplate(entity_id, tuple(keypoints), descriptors))
        self._command_cache[cache_key] = templates
        return templates

    def _command_servant(self, query: np.ndarray) -> int | None:
        query_keypoints, query_descriptors = self._sift.detectAndCompute(query, None)
        if query_descriptors is None or len(query_descriptors) < 2:
            return None
        best: dict[int, int] = {}
        for template in self._command_templates or ():
            matches = self._feature_matcher.knnMatch(template.descriptors, query_descriptors, k=2)
            good = [first for first, second in matches
                    if first.distance < cn.COMMAND_SIFT_RATIO * second.distance]
            if len(good) < 4:
                continue
            source = np.float32([template.keypoints[match.queryIdx].pt for match in good]).reshape(-1, 1, 2)
            target = np.float32([query_keypoints[match.trainIdx].pt for match in good]).reshape(-1, 1, 2)
            _homography, mask = cv2.findHomography(source, target, cv2.RANSAC,
                                                   cn.COMMAND_SIFT_RANSAC_THRESHOLD)
            if mask is None:
                continue
            inliers = int(mask.sum())
            best[template.entity_id] = max(inliers, best.get(template.entity_id, 0))
        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < cn.COMMAND_SIFT_MIN_INLIERS:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < cn.COMMAND_SIFT_MIN_MARGIN:
            return None
        return ranked[0][0]

    def command(self, image: np.ndarray, box: tuple[int, int, int, int]) -> CommandCardIdentity | None:
        if self._command_templates is None:
            self._command_templates = self._load_commands()
            self._support_tag = self._read(cn.TEMPLATE_DIR / 'identities' / 'support-tag.png')
        if not self._command_templates:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        x1, y1, x2, _y2 = box
        top, left, bottom, right = cn.COMMAND_SIFT_QUERY_OFFSETS
        query = gray[y1 + top:y1 + bottom, x1 + left:x1 + right]
        servant_id = self._command_servant(query)
        if servant_id is None:
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
        return CommandCardIdentity(servant_slot=self._formation_slots.get((servant_id, support), -1),
                                   card_type=card_type, svt_id=servant_id, support=support)
