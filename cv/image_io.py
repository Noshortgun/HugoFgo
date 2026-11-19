"""兼容中文路径的 OpenCV 读写。"""
from pathlib import Path

import cv2
import numpy as np


def read_image(path: str | Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def write_image(path: str | Path, image: np.ndarray) -> bool:
    ok, buffer = cv2.imencode(Path(path).suffix or ".png", image)
    if not ok:
        return False
    buffer.tofile(str(path))
    return True
