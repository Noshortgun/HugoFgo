"""设备配置：serial、区服、adb 路径与可选分辨率覆盖（用于模拟器上报尺寸不准时）。"""
from __future__ import annotations

from dataclasses import dataclass

from executor.device.base import Region


@dataclass(frozen=True)
class DeviceConfig:
    serial: str
    region: Region = Region.CN
    adb_path: str | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        serial = str(self.serial).strip()
        if not serial:
            raise ValueError('设备配置缺少 serial：请填写 adb devices 第一列的序列号（如 emulator-5554）')
        object.__setattr__(self, 'serial', serial)
        object.__setattr__(self, 'region', Region.parse(self.region))

        adb_path = str(self.adb_path).strip() if self.adb_path else None
        object.__setattr__(self, 'adb_path', adb_path or None)

        if (self.width is None) != (self.height is None):
            raise ValueError('设备配置的 width 与 height 必须同时给出或同时省略')
        for name in ('width', 'height'):
            value = getattr(self, name)
            if value is None:
                continue
            if int(value) <= 0:
                raise ValueError(f'设备配置的 {name} 必须是正整数，收到 {value!r}')
            object.__setattr__(self, name, int(value))
