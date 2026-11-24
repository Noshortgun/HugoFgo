"""从者与概念礼装的区域图库匹配；不访问网络。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from executor.perception.observation import CommandCardIdentity, Field, FieldStatus, SupportCandidate
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


def default_identity_root() -> Path:
    configured = os.environ.get('HUGOFGO_DATA_ROOT', '').strip()
    root = Path(configured) if configured else cn.PROJECT_ROOT / 'data'
    return root / os.environ.get('HUGOFGO_REGION', 'CN')


class IdentityMatcher:
    _catalog_cache: dict[tuple[str, str, frozenset[int] | None], list[IdentityTemplate]] = {}
    _command_cache: dict[tuple[str, frozenset[int] | None], list[CommandTemplate]] = {}
    _team_cache: dict[tuple[str, str, frozenset[int] | None], list[CommandTemplate]] = {}

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or default_identity_root()
        self._servants = self._metadata('servants-by-id.json')
        self._equips = self._metadata('equips-by-id.json')
        self._face_templates: list[IdentityTemplate] | None = None
        self._ce_templates: list[IdentityTemplate] | None = None
        self._command_templates: list[CommandTemplate] | None = None
        self._support_tag: np.ndarray | None = None
        self._formation_slots: dict[tuple[int, bool], int] = {}
        self._specified_slots: frozenset[int] | None = None
        self._required_face_ids: frozenset[int] | None = None
        self._required_ce_ids: frozenset[int] | None = None
        self._required_command_ids: frozenset[int] | None = None
        self._required_mystic_id: str | None = None
        self._sift = cv2.SIFT_create(nfeatures=cn.COMMAND_SIFT_FEATURES)
        self._team_sift = cv2.SIFT_create(nfeatures=1000)
        self._mystic_sift = cv2.SIFT_create(nfeatures=1000, contrastThreshold=0.01)
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
        self._specified_slots = frozenset(member.slot for member in formation if member.name.strip())
        self._required_face_ids = frozenset(member.svt_id for member in formation
                                            if member.svt_id is not None)
        self._required_ce_ids = frozenset(member.ce_id for member in support
                                          if member.ce_id is not None)
        self._required_command_ids = frozenset(member.svt_id for member in formation
                                               if member.svt_id is not None)
        self._face_templates = None
        self._ce_templates = None
        self._command_templates = None

    def bind_mystic(self, mystic_id: str) -> None:
        self._required_mystic_id = mystic_id

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
            good = [pair[0] for pair in matches if len(pair) == 2
                    and pair[0].distance < cn.COMMAND_SIFT_RATIO * pair[1].distance]
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

    @staticmethod
    def _command_affinity(image: np.ndarray, box: tuple[int, int, int, int]) -> str | None:
        _x1, y1, x2, _y2 = box
        left, right = cn.COMMAND_AFFINITY_X_OFFSETS
        top, bottom = cn.COMMAND_AFFINITY_Y_OFFSETS
        region = image[y1 + top:y1 + bottom, x2 + left:x2 + right]
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        colored = ((saturation > cn.COMMAND_AFFINITY_MIN_SATURATION)
                   & (value > cn.COMMAND_AFFINITY_MIN_VALUE))
        red = int((colored & ((hue < 12) | (hue > 168))).sum())
        blue = int((colored & (hue > 90) & (hue < 125)).sum())
        if red < cn.COMMAND_AFFINITY_MIN_PIXELS and blue < cn.COMMAND_AFFINITY_MIN_PIXELS:
            return 'neutral'
        if red >= cn.COMMAND_AFFINITY_MIN_PIXELS and blue >= cn.COMMAND_AFFINITY_MIN_PIXELS:
            return None
        return 'advantage' if red > blue else 'resist'

    def command(self, image: np.ndarray, box: tuple[int, int, int, int]) -> CommandCardIdentity | None:
        if self._command_templates is None:
            self._command_templates = self._load_commands()
            self._support_tag = self._read(cn.TEMPLATE_DIR / 'identities' / 'support-tag.png')
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        x1, y1, x2, _y2 = box
        support_score = 0.0
        if self._support_tag is not None:
            top, left, bottom, right = cn.COMMAND_SUPPORT_QUERY_OFFSETS
            tag_region = gray[y1 + top:y1 + bottom, x1 + left:x1 + right]
            result = cv2.matchTemplate(tag_region, self._support_tag, cv2.TM_CCOEFF_NORMED)
            support_score = float(np.nan_to_num(result.max(), nan=0.0))
        support = support_score >= cn.COMMAND_SUPPORT_THRESHOLD

        top, left, bottom, right = cn.COMMAND_SIFT_QUERY_OFFSETS
        query = gray[y1 + top:y1 + bottom, x1 + left:x1 + right]
        servant_id = self._command_servant(query)
        fallback_slot = None
        if support_score >= cn.COMMAND_SUPPORT_FALLBACK_THRESHOLD:
            support_slots = [(entity_id, slot) for (entity_id, is_support), slot
                             in self._formation_slots.items() if is_support and slot >= 0]
            if len(support_slots) == 1 and (servant_id is None or servant_id == support_slots[0][0]):
                servant_id, fallback_slot = support_slots[0]
                support = True
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

        slot = (-1 if servant_id is None else
                fallback_slot if fallback_slot is not None else self._formation_slots.get((servant_id, support), -1))
        return CommandCardIdentity(servant_slot=slot,
                                   card_type=card_type, svt_id=servant_id, support=support,
                                   affinity=self._command_affinity(image, box))

    def _load_team(self, kind: str) -> list[CommandTemplate]:
        if kind == 'mystic':
            numeric = {'default': 1, 'order-change': 20}.get(self._required_mystic_id or '')
            if numeric is None and (self._required_mystic_id or '').startswith('atlas-'):
                numeric = int((self._required_mystic_id or '').removeprefix('atlas-'))
            required = frozenset({numeric}) if numeric is not None else frozenset()
            directories = ('mystic/items',)
        else:
            required = self._required_face_ids if kind == 'face' else self._required_ce_ids
            directories = ('faces',) if kind == 'face' else ('ce',)
        cache_key = (str(self._root), kind, required)
        if cache_key in self._team_cache:
            return self._team_cache[cache_key]
        paths: dict[str, tuple[int, Path]] = {}
        for directory in directories:
            sources = (cn.TEMPLATE_DIR / 'identities' / directory,
                       self._root / 'assets/atlas' / directory)
            for source in sources:
                if not source.exists():
                    continue
                for path in source.glob('*.png'):
                    try:
                        entity_id = int(path.stem.split('-', 1)[0] if kind != 'mystic'
                                        else path.stem.split('-', 1)[1])
                    except (ValueError, IndexError):
                        continue
                    if required is not None and entity_id not in required:
                        continue
                    paths[str(path)] = (entity_id, path)
        templates = []
        for entity_id, path in paths.values():
            data = np.fromfile(str(path), dtype=np.uint8)
            asset = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
            if asset is None:
                continue
            sift = self._mystic_sift if kind == 'mystic' else self._team_sift
            variants = (0.6, 0.7) if kind == 'mystic' else (None,)
            for scale in variants:
                scaled = asset if scale is None else cv2.resize(asset, None, fx=scale, fy=scale,
                                                               interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
                keypoints, descriptors = sift.detectAndCompute(gray, None)
                if descriptors is not None:
                    templates.append(CommandTemplate(entity_id, tuple(keypoints), descriptors))
        self._team_cache[cache_key] = templates
        return templates

    def _team_match(self, query: np.ndarray, templates: list[CommandTemplate], kind: str) -> int | None:
        sift = self._mystic_sift if kind == 'mystic' else self._team_sift
        if kind == 'mystic':
            query = cv2.resize(query, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        query_keypoints, query_descriptors = sift.detectAndCompute(query, None)
        if query_descriptors is None:
            return None
        best: dict[int, int] = {}
        ratio = 0.8 if kind == 'mystic' else 0.75
        for template in templates:
            matches = self._feature_matcher.knnMatch(template.descriptors, query_descriptors, k=2)
            good = [pair[0] for pair in matches if len(pair) == 2
                    and pair[0].distance < ratio * pair[1].distance]
            if len(good) < 4:
                continue
            source = np.float32([template.keypoints[match.queryIdx].pt for match in good]).reshape(-1, 1, 2)
            target = np.float32([query_keypoints[match.trainIdx].pt for match in good]).reshape(-1, 1, 2)
            _homography, mask = cv2.findHomography(source, target, cv2.RANSAC, 5)
            if mask is not None:
                inliers = int(mask.sum())
                best[template.entity_id] = max(inliers, best.get(template.entity_id, 0))
        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
        minimum, margin = (4, 1) if kind == 'mystic' else (10, 5)
        if not ranked or ranked[0][1] < minimum:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < margin:
            return None
        return ranked[0][0]

    def team(self, image: np.ndarray) -> dict[str, Field]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self._load_team('face')
        regions = ((20, 240), (190, 390), (390, 590), (580, 780), (770, 970), (960, 1180))
        servants = [self._team_match(gray[120:350, x1:x2], faces, 'face')
                    if self._specified_slots is None or slot in self._specified_slots else None
                    for slot, (x1, x2) in enumerate(regions)]

        def servant_field(slots: tuple[int, ...]) -> Field:
            required = [slot for slot in slots
                        if self._specified_slots is None or slot in self._specified_slots]
            values = tuple(servants[slot] for slot in slots)
            return (Field(FieldStatus.CONFIRMED, values)
                    if all(servants[slot] is not None for slot in required) else Field(FieldStatus.UNCERTAIN))

        ce = self._team_match(gray[440:520, 20:240], self._load_team('ce'), 'ce')
        mystic = self._team_match(gray[630:710, 10:120], self._load_team('mystic'), 'mystic')
        return {
            'team_servants': servant_field((0, 1, 2)),
            'team_backline_servants': servant_field((3, 4, 5)),
            'team_support_ce': (Field(FieldStatus.CONFIRMED, ce)
                                if ce is not None else Field(FieldStatus.UNCERTAIN)),
            'team_mystic': (Field(FieldStatus.CONFIRMED, self._required_mystic_id)
                            if mystic is not None else Field(FieldStatus.UNCERTAIN)),
        }
