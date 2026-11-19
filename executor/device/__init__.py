"""设备网关：执行器唯一的设备访问入口，实机用 adb，回放用 fake。"""
from executor.device.adb import AdbDevice, devices
from executor.device.base import Device, DeviceError, DeviceMetrics, Frame
from executor.device.config import DeviceConfig, Region
from executor.device.fake import FakeDevice

__all__ = [
    'AdbDevice',
    'Device',
    'DeviceConfig',
    'DeviceError',
    'DeviceMetrics',
    'FakeDevice',
    'Frame',
    'Region',
    'devices',
]
