"""设备配置：serial、区服、adb 路径与可选分辨率覆盖（用于模拟器上报尺寸不准时）。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from executor.device.base import DeviceError, validate_resolution


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
class DeviceConfig:
    serial: str
    region: Region | str | None = None
    adb_path: str | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        serial = str(self.serial).strip()
        if not serial:
            raise DeviceError('设备配置缺少 serial：请填写 adb devices 第一列的序列号（如 emulator-5554）')
        object.__setattr__(self, 'serial', serial)
        if self.region is None:
            options = '、'.join(f'{region.value}（{region.label}）' for region in Region)
            raise DeviceError(f'设备配置缺少区服：请选择 {options}')
        object.__setattr__(self, 'region', Region.parse(self.region))

        adb_path = str(self.adb_path).strip() if self.adb_path else None
        object.__setattr__(self, 'adb_path', adb_path or None)

        if (self.width is None) != (self.height is None):
            raise DeviceError('设备配置的 width 与 height 必须同时给出或同时省略')
        for name in ('width', 'height'):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise DeviceError(f'设备配置的 {name} 必须是正整数，收到 {value!r}') from None
            if number <= 0:
                raise DeviceError(f'设备配置的 {name} 必须是正整数，收到 {value!r}')
            object.__setattr__(self, name, number)
        if self.width is not None and self.height is not None:
            validate_resolution(self.width, self.height, subject=f'设备配置 {self.serial}')
