"""设备协议、错误、帧与坐标换算：上层只通过四个操作访问设备，不感知 adb 或截图文件。

坐标一律使用 1280×720 基准，由设备适配器换算到实际分辨率。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from executor.device.config import DeviceMetrics

BASE_WIDTH = 1280
BASE_HEIGHT = 720
MIN_WIDTH = BASE_WIDTH
MIN_HEIGHT = BASE_HEIGHT


class DeviceError(RuntimeError):
    """设备访问失败。消息必须带定位信息：serial、命令、stderr 或截图路径。"""


class FrameFormat(str, Enum):
    """帧字节的语义：PNG 压缩图，或自上而下的 BGR24 原始像素（每像素 3 字节，行间无填充）。"""

    PNG = 'png'
    BGR24 = 'bgr24'


@dataclass(frozen=True)
class Frame:
    """一帧截图。image 是 PNG 字节或 BGR24 原始像素（见 format），由画面感知解码；captured_at 是 Unix 秒。

    capture_ms 是适配器记录的采集耗时（毫秒），None 表示该通道不记录；失败原因不走 Frame，而是 DeviceError。
    """

    image: bytes
    width: int
    height: int
    captured_at: float
    format: FrameFormat = FrameFormat.PNG
    capture_ms: float | None = None
    frame_id: int | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        try:
            frame_format = FrameFormat(self.format)
        except ValueError:
            raise DeviceError(f'未知帧格式 {self.format!r}；可选 PNG、BGR24') from None
        object.__setattr__(self, 'format', frame_format)
        if frame_format is FrameFormat.BGR24 and len(self.image) != self.width * self.height * 3:
            raise DeviceError(f'{self.width}×{self.height} 的 BGR24 帧应为 {self.width * self.height * 3} 字节，'
                              f'实际 {len(self.image)} 字节；原始像素帧不做补齐或截断')


@runtime_checkable
class Device(Protocol):
    def metrics(self) -> DeviceMetrics: ...

    def frame(self) -> Frame: ...

    def tap(self, x: int, y: int) -> None: ...

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None: ...


def validate_resolution(width: int, height: int, *, subject: str | None = None) -> None:
    """只接受不低于 1280×720 的精确 16:9 横屏；其他分辨率必须拒绝而不是盲点。"""
    who = f'{subject} ' if subject else ''
    if width <= 0 or height <= 0:
        raise DeviceError(f'{who}分辨率无效：{width}×{height}，无法换算 {BASE_WIDTH}×{BASE_HEIGHT} 基准坐标')
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise DeviceError(f'{who}分辨率 {width}×{height} 低于最低支持的 {BASE_WIDTH}×{BASE_HEIGHT}；'
                          f'请把模拟器改成 16:9 横屏且不低于 {BASE_WIDTH}×{BASE_HEIGHT}')
    if width * 9 != height * 16:
        raise DeviceError(f'{who}不支持的分辨率：{width}×{height}（不是精确 16:9）；'
                          f'请把模拟器改成 16:9 横屏且不低于 {BASE_WIDTH}×{BASE_HEIGHT}'
                          f'（如 {BASE_WIDTH}×{BASE_HEIGHT}、1920×1080）')


def to_device_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把 1280×720 基准坐标换算成设备像素，并把结果限制在屏幕内。"""
    validate_resolution(width, height)
    return _scale(x, BASE_WIDTH, width), _scale(y, BASE_HEIGHT, height)


def _scale(value: int, base: int, actual: int) -> int:
    pixel = round(value * actual / base)
    return min(max(pixel, 0), actual - 1)
