"""设备网关：执行器唯一的设备访问入口，实机用 adb，回放用 fake。"""
from executor.device.adb import AdbDevice, devices
from executor.device.base import (
    BASE_HEIGHT,
    BASE_WIDTH,
    Device,
    DeviceError,
    DeviceMetrics,
    Frame,
    Region,
    to_device_point,
)
from executor.device.config import DeviceConfig
from executor.device.fake import FakeDevice

__all__ = [
    'AdbDevice',
    'BASE_HEIGHT',
    'BASE_WIDTH',
    'Device',
    'DeviceConfig',
    'DeviceError',
    'DeviceMetrics',
    'FakeDevice',
    'Frame',
    'Region',
    'devices',
    'to_device_point',
]
