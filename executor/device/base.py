"""设备协议、错误、帧与坐标换算：上层只通过四个操作访问设备，不感知 adb 或截图文件。

坐标一律使用 1280×720 基准，由设备适配器换算到实际分辨率。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from executor.device.config import DeviceMetrics

BASE_WIDTH = 1280
BASE_HEIGHT = 720
SUPPORTED_ASPECT = 16 / 9
ASPECT_TOLERANCE = 0.01


class DeviceError(RuntimeError):
    """设备访问失败。消息必须带定位信息：serial、命令、stderr 或截图路径。"""


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


def validate_resolution(width: int, height: int) -> None:
    """只接受 16:9 附近的横屏分辨率；其他宽高比会破坏布局换算，必须拒绝而不是盲点。"""
    if width <= 0 or height <= 0:
        raise DeviceError(f'设备分辨率无效：{width}×{height}，无法换算 1280×720 基准坐标')
    ratio = width / height
    if abs(ratio - SUPPORTED_ASPECT) > ASPECT_TOLERANCE:
        raise DeviceError(f'不支持的分辨率：{width}×{height}（宽高比 {ratio:.3f}:1）；'
                          '仅支持 16:9 横屏，例如 1280×720、1920×1080')


def to_device_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把 1280×720 基准坐标换算成设备像素，并把结果限制在屏幕内。"""
    validate_resolution(width, height)
    return _scale(x, BASE_WIDTH, width), _scale(y, BASE_HEIGHT, height)


def _scale(value: int, base: int, actual: int) -> int:
    pixel = round(value * actual / base)
    return min(max(pixel, 0), actual - 1)
