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
MIN_WIDTH = 1280
MIN_HEIGHT = 720
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


def validate_resolution(width: int, height: int, *, serial: str | None = None) -> None:
    """只接受不低于 1280×720、宽高比接近 16:9 的横屏；其他分辨率必须拒绝而不是盲点。"""
    who = f'设备 {serial} ' if serial else ''
    if width <= 0 or height <= 0:
        raise DeviceError(f'{who}分辨率无效：{width}×{height}，无法换算 1280×720 基准坐标')
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise DeviceError(f'{who}分辨率 {width}×{height} 低于最低支持的 1280×720；'
                          '请把模拟器改成 16:9 横屏且不低于 1280×720')
    ratio = width / height
    if abs(ratio - SUPPORTED_ASPECT) / SUPPORTED_ASPECT > ASPECT_TOLERANCE:
        raise DeviceError(f'{who}不支持的分辨率：{width}×{height}（宽高比 {ratio:.3f}:1）；'
                          '请把模拟器改成 16:9 横屏且不低于 1280×720（如 1280×720、1920×1080）')


def to_device_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把 1280×720 基准坐标换算成设备像素，并把结果限制在屏幕内。"""
    validate_resolution(width, height)
    return _scale(x, BASE_WIDTH, width), _scale(y, BASE_HEIGHT, height)


def _scale(value: int, base: int, actual: int) -> int:
    pixel = round(value * actual / base)
    return min(max(pixel, 0), actual - 1)
