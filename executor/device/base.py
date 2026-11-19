"""设备协议：执行器只通过这四个操作访问设备，不感知 adb 或截图文件。

坐标一律使用 1280×720 基准，由设备适配器换算到实际分辨率。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

BASE_WIDTH = 1280
BASE_HEIGHT = 720


class DeviceError(RuntimeError):
    """设备访问失败。消息必须带定位信息：serial、命令、stderr 或截图路径。"""


class Region(str, Enum):
    """区服属于设备（ADR-0003）：决定后续加载哪套识别配置与图库。"""

    CN = 'CN'
    JP = 'JP'
    TW = 'TW'

    @property
    def label(self) -> str:
        return {'CN': '国服', 'JP': '日服', 'TW': '台服'}[self.value]

    @classmethod
    def parse(cls, value: 'Region | str') -> 'Region':
        if isinstance(value, cls):
            return value
        text = str(value).strip()
        for region in cls:
            if text.upper() == region.value or text == region.label:
                return region
        options = '、'.join(f'{region.value}（{region.label}）' for region in cls)
        raise DeviceError(f'未知区服 {value!r}；可选 {options}')


@dataclass(frozen=True)
class DeviceMetrics:
    serial: str
    width: int
    height: int
    density: int | None
    region: Region


@dataclass(frozen=True)
class Frame:
    """一帧截图。image 是原始 PNG 字节，由画面感知解码；captured_at 是 Unix 秒。"""

    image: bytes
    width: int
    height: int
    captured_at: float


@runtime_checkable
class Device(Protocol):
    def metrics(self) -> DeviceMetrics: ...

    def frame(self) -> Frame: ...

    def tap(self, x: int, y: int) -> None: ...

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None: ...


def to_device_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把 1280×720 基准坐标换算成设备像素，并把结果限制在屏幕内。"""
    if width <= 0 or height <= 0:
        raise DeviceError(f'设备分辨率无效：{width}×{height}，无法换算 1280×720 基准坐标')
    return _scale(x, BASE_WIDTH, width), _scale(y, BASE_HEIGHT, height)


def _scale(value: int, base: int, actual: int) -> int:
    pixel = round(value * actual / base)
    return min(max(pixel, 0), actual - 1)
