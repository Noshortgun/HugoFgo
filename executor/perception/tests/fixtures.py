"""感知测试共用工具：按仓库相对路径构造一帧。"""
from __future__ import annotations

from pathlib import Path

from executor.device.base import Frame

ROOT = Path(__file__).resolve().parents[3]


def load_frame(relative: str) -> Frame:
    return Frame(image=(ROOT / relative).read_bytes(), width=1280, height=720, captured_at=0.0)
