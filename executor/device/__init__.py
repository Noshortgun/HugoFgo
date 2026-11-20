"""设备网关：执行器唯一的设备访问入口，实机用 adb（或可选的 LDPlayer 窗口通道），回放用 fake。"""
from __future__ import annotations

import subprocess

from executor.device.adb import AdbDevice, devices
from executor.device.base import Device, DeviceError, Frame, FrameFormat
from executor.device.config import DeviceChannel, DeviceConfig, DeviceMetrics, Region
from executor.device.fake import FakeDevice
from executor.device.ldplayer import LdplayerWindowDevice

__all__ = [
    'AdbDevice',
    'Device',
    'DeviceChannel',
    'DeviceConfig',
    'DeviceError',
    'DeviceMetrics',
    'FakeDevice',
    'Frame',
    'FrameFormat',
    'LdplayerWindowDevice',
    'Region',
    'devices',
    'open_device',
]


def open_device(config: DeviceConfig, *, runner=subprocess.run) -> Device:
    """按 config.channel 构造适配器：默认 adb；只有显式选择 ldplayer-window 才走窗口原始帧通道。"""
    if config.channel is DeviceChannel.LDPLAYER_WINDOW:
        return LdplayerWindowDevice(config)
    return AdbDevice(config, runner=runner)
