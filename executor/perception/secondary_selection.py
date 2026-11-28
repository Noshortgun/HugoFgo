from __future__ import annotations

import cv2
import numpy as np

from executor.perception.observation import Control, ControlKind


def _candidates(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not 120 <= width <= 500 or not 35 <= height <= 300:
            continue
        if not 0.3 <= width / height <= 5.0:
            continue
        if cv2.contourArea(contour) < 0.55 * width * height:
            continue
        candidates.append((x + 150, y + 100, x + 150 + width - 1, y + 100 + height - 1))
    return sorted(candidates)


def _aligned_group(candidates: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]] | None:
    groups: list[list[tuple[int, int, int, int]]] = []
    for box in candidates:
        center_y = (box[1] + box[3]) / 2
        if groups:
            previous = groups[-1][-1]
            previous_y = (previous[1] + previous[3]) / 2
            previous_height = previous[3] - previous[1] + 1
            if abs(center_y - previous_y) <= max(30, previous_height * 0.35):
                groups[-1].append(box)
                continue
        groups.append([box])

    for group in groups:
        if len(group) not in (2, 3, 4):
            continue
        widths = [box[2] - box[0] + 1 for box in group]
        heights = [box[3] - box[1] + 1 for box in group]
        if max(widths) > min(widths) * 1.8 or max(heights) > min(heights) * 1.8:
            continue
        gaps = [right[0] - left[2] for left, right in zip(group, group[1:])]
        if max(gaps) > max(widths) * 1.2:
            continue
        center_x = sum((box[0] + box[2]) / 2 for box in group) / len(group)
        if abs(center_x - 640) <= 220:
            return group
    return None


def _has_menu_title(gray: np.ndarray, group: list[tuple[int, int, int, int]]) -> bool:
    top = max(100, min(box[1] for box in group) - 140)
    bottom = max(top + 20, min(box[1] for box in group) - 10)
    left = max(150, min(box[0] for box in group) - 40)
    right = min(1130, max(box[2] for box in group) + 40)
    title = gray[top:bottom, left:right]
    return bool(title.size and title.mean() < 120 and (title > 170).mean() > 0.002)


def _controls(group: list[tuple[int, int, int, int]], gray: np.ndarray) -> tuple[Control, ...]:
    brightness = []
    for box in group:
        x1, y1, x2, y2 = box
        inset = 6
        crop = gray[y1 + inset:y2 - inset, x1 + inset:x2 - inset]
        brightness.append(float(crop.mean()) if crop.size else 0.0)
    brightest = max(brightness)
    return tuple(
        Control(
            f'追加按钮{index}', ControlKind.BUTTON, box, not disabled,
            'secondary_option_disabled' if disabled else 'secondary_option_geometry',
        )
        for index, (box, value) in enumerate(zip(group, brightness), start=1)
        for disabled in [brightest >= 120 and value < brightest * 0.6]
    )


def detect_secondary_options(image: np.ndarray) -> tuple[Control, ...]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    region = gray[100:560, 150:1110]
    masks = (
        cv2.Canny(region, 40, 120),
        cv2.morphologyEx((region > 90).astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)),
        cv2.morphologyEx((region > 100).astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)),
        cv2.morphologyEx((region > 110).astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)),
    )
    groups = []
    for mask in masks:
        group = _aligned_group(_candidates(mask))
        if group is not None and _has_menu_title(gray, group):
            groups.append(group)
    if not groups:
        return ()
    return _controls(max(groups, key=len), gray)
