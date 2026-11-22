"""设备网关：执行器唯一设备入口；实机显式选择 adb、LDPlayer 或 scrcpy，回放用 fake。"""
from __future__ import annotations

import subprocess

from executor.device.adb import AdbDevice, devices
from executor.device.base import Device, DeviceError, Frame, FrameFormat
from executor.device.config import DeviceChannel, DeviceConfig, DeviceMetrics, Region
from executor.device.fake import FakeDevice
from executor.device.ldplayer import LdplayerWindowDevice
from executor.device.scrcpy import ScrcpyDevice

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
    'ScrcpyDevice',
    'devices',
    'open_device',
]


def open_device(config: DeviceConfig, *, runner=subprocess.run) -> Device:
    """按 config.channel 构造适配器；默认 adb，其他通道必须显式选择。"""
    if config.channel is DeviceChannel.LDPLAYER_WINDOW:
        return LdplayerWindowDevice(config)
    if config.channel is DeviceChannel.SCRCPY:
        return ScrcpyDevice(config)
    return AdbDevice(config, runner=runner)
