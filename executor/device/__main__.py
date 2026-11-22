"""设备网关自检与手动验收入口。

用法：
  python -m executor.device --self-test   # 不依赖真机
  python -m executor.device --list        # 需要 adb 与在线设备
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import zlib
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import executor.device as gateway
from executor.device import adb as adb_module
from executor.device import open_device
from executor.device.adb import ADB_TIMEOUT, AdbDevice, devices, parse_density, resolve_adb_path
from executor.device.base import Device, DeviceError, Frame, FrameFormat, to_device_point
from executor.device.config import DeviceChannel, DeviceConfig, DeviceMetrics, Region
from executor.device.fake import FakeDevice
from executor.device.ldplayer import LdplayerWindowDevice, find_ldplayer_window, stats_summary
from executor.device.png import png_size


class ScriptedRunner:
    """按顺序回放 adb 调用结果或异常，并记录每次的完整命令与参数。"""

    def __init__(self, *responses: tuple[int, bytes, bytes] | Exception):
        self.responses = list(responses)
        self.calls: list[list[str]] = []
        self.options: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.options.append(dict(kwargs))
        if not self.responses:
            raise AssertionError(f'意外的 adb 调用：{argv}')
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        code, stdout, stderr = outcome
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


def adb_ok(stdout: bytes = b'') -> tuple[int, bytes, bytes]:
    return (0, stdout, b'')


def adb_fail(stderr: str, code: int = 1) -> tuple[int, bytes, bytes]:
    return (code, b'', stderr.encode('utf-8'))


class FakeWin32:
    """假 Win32：内存窗口树 + 合成像素，用来验证 LDPlayer 适配器的资源释放与每个失败分支。

    `leaked()` 非空说明 DC 或位图没释放；DeleteObject 时位图仍被选中会直接断言失败。
    """

    PARENT = 1000
    RENDER = 1001
    STOCK_OBJECT = 1

    def __init__(self, *, width: int = 1280, height: int = 720, pixel: int = 0x11):
        self.sizes = {
            self.RENDER: (width, height),
            self.PARENT: (width + 60, height + 60),
        }
        self.origins = {self.RENDER: (0, 60)}
        self.parents: set[int] = {self.PARENT}
        self.classes = {self.PARENT: 'LDPlayerMainFrame', self.RENDER: 'RenderWindow'}
        self.titles = {self.PARENT: '雷电模拟器', self.RENDER: 'TheRender'}
        self.processes = {self.PARENT: r'D:\leidian\LDPlayer14\dnplayer.exe'}
        self.render_children = {self.PARENT: [self.RENDER]}
        self.iconic = False
        self.print_ok = True
        self.lines: int | None = None
        self.pixels = bytes([pixel]) * (width * height * 3)
        self.live_dcs: list[int] = []
        self.live_bitmaps: list[int] = []
        self.selected: dict[int, int] = {}
        self.calls: list[str] = []
        self._next_handle = 5000

    def _new_dc(self, kind: str) -> int:
        self._next_handle += 1
        self.live_dcs.append(self._next_handle)
        self.calls.append(kind)
        return self._next_handle

    def enum_windows(self) -> list[int]:
        return sorted(self.parents)

    def enum_children(self, parent: int) -> list[int]:
        return list(self.render_children.get(parent, []))

    def class_name(self, hwnd: int) -> str:
        return self.classes.get(hwnd, '')

    def window_title(self, hwnd: int) -> str:
        return self.titles.get(hwnd, '')

    def process_name(self, hwnd: int) -> str | None:
        return self.processes.get(hwnd)

    def is_window(self, hwnd: int) -> bool:
        return hwnd in self.classes

    def is_iconic(self, hwnd: int) -> bool:
        return self.iconic

    def client_size(self, hwnd: int) -> tuple[int, int]:
        return self.sizes.get(hwnd, (0, 0))

    def window_size(self, hwnd: int) -> tuple[int, int]:
        return self.sizes.get(hwnd, (0, 0))

    def map_window_points(self, from_hwnd: int, to_hwnd: int,
                          points: list[tuple[int, int]]) -> list[tuple[int, int]]:
        origin = self.origins.get(from_hwnd, (0, 0))
        if from_hwnd == self.RENDER and to_hwnd == self.PARENT:
            return [(x + origin[0], y + origin[1]) for x, y in points]
        if from_hwnd == self.PARENT and to_hwnd == self.RENDER:
            return [(x - origin[0], y - origin[1]) for x, y in points]
        return list(points)

    def set_window_size(self, hwnd: int, width: int, height: int) -> bool:
        self.calls.append('SetWindowPos')
        self.sizes[hwnd] = (width, height)
        return True

    def get_dc(self, hwnd: int) -> int:
        return self._new_dc('GetDC')

    def release_dc(self, hwnd: int, hdc: int) -> None:
        self.calls.append('ReleaseDC')
        assert hdc in self.live_dcs, f'ReleaseDC 了未创建的 DC {hdc}'
        self.live_dcs.remove(hdc)

    def create_memory_dc(self, hdc: int) -> int:
        return self._new_dc('CreateCompatibleDC')

    def delete_dc(self, hdc: int) -> None:
        self.calls.append('DeleteDC')
        assert hdc in self.live_dcs, f'DeleteDC 了未创建的 DC {hdc}'
        assert self.selected.get(hdc, self.STOCK_OBJECT) == self.STOCK_OBJECT, f'DC {hdc} 删除时仍选中位图'
        self.live_dcs.remove(hdc)

    def create_bitmap(self, hdc: int, width: int, height: int) -> int:
        self._next_handle += 1
        self.live_bitmaps.append(self._next_handle)
        self.calls.append('CreateCompatibleBitmap')
        return self._next_handle

    def select_object(self, hdc: int, obj: int) -> int:
        self.calls.append('SelectObject')
        previous = self.selected.get(hdc, self.STOCK_OBJECT)
        self.selected[hdc] = obj
        return previous

    def delete_object(self, obj: int) -> None:
        self.calls.append('DeleteObject')
        assert obj in self.live_bitmaps, f'DeleteObject 了未创建的位图 {obj}'
        for hdc, selected in self.selected.items():
            assert selected != obj, f'位图 {obj} 仍被 DC {hdc} 选中就 DeleteObject 了'
        self.live_bitmaps.remove(obj)

    def print_window(self, hwnd: int, hdc: int) -> bool:
        self.calls.append('PrintWindow')
        return self.print_ok

    def bitmap_bits(self, hdc: int, bitmap: int, width: int, height: int) -> tuple[bytes, int]:
        self.calls.append('GetDIBits')
        return self.pixels, height if self.lines is None else self.lines

    def leaked(self) -> list[str]:
        leaks = []
        if self.live_dcs:
            leaks.append(f'DC {len(self.live_dcs)} 个')
        if self.live_bitmaps:
            leaks.append(f'位图 {len(self.live_bitmaps)} 个')
        return leaks


def make_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', crc)

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    row = b'\x00' + b'\x00\x00\x00' * width
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(row * height)) + chunk(b'IEND', b''))


def expect_error(action, *needles: str, error_type=Exception):
    try:
        action()
    except error_type as error:
        for needle in needles:
            assert needle in str(error), f'错误消息缺少 {needle!r}：{error}'
        return error
    raise AssertionError(f'应当抛出错误，消息含 {needles!r}')


def make_config(serial: str, **kwargs) -> DeviceConfig:
    kwargs.setdefault('region', Region.CN)
    return DeviceConfig(serial=serial, **kwargs)


def check_region() -> None:
    assert Region.parse('CN') is Region.CN
    assert Region.parse('cn') is Region.CN
    assert Region.parse('国服') is Region.CN
    assert Region.parse(Region.JP) is Region.JP
    assert Region.CN.label == '国服'
    expect_error(lambda: Region.parse('EU'), 'EU', error_type=DeviceError)
    expect_error(lambda: Region.parse('EU'), 'CN', error_type=DeviceError)


def check_config() -> None:
    assert get_type_hints(DeviceMetrics)['region'] is Region
    config = make_config(serial='emulator-5554')
    assert config.region is Region.CN
    assert config.adb_path is None and config.width is None and config.height is None

    config = make_config(serial=' 127.0.0.1:16384 ', region='jp', adb_path='', width=1280, height=720)
    assert config.serial == '127.0.0.1:16384'
    assert config.region is Region.JP
    assert config.adb_path is None
    assert (config.width, config.height) == (1280, 720)

    expect_error(lambda: make_config(serial='  '), 'serial', error_type=DeviceError)
    expect_error(lambda: DeviceConfig(serial='x'), '缺少区服', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', width=1280), 'width', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', width=0, height=720), 'width', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', region='EU'), 'EU', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', width='abc', height=720), 'width', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', width=2340, height=1080), '16:9', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', width=960, height=540), '低于最低支持', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', width=1366, height=768), '不支持的分辨率', error_type=DeviceError)
    expect_error(lambda: make_config(serial='x', width=1920, height=1088), '不支持的分辨率', error_type=DeviceError)
    make_config(serial='x', width=1920, height=1080)

    assert not issubclass(DeviceError, ValueError), '调用方只需捕获 DeviceError，配置错误不应再走 ValueError'


def check_adb_resolution() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        missing = Path(directory) / 'missing-adb.exe'

        assert resolve_adb_path(str(adb)) == str(adb)
        expect_error(lambda: resolve_adb_path(str(missing)), 'missing-adb.exe', error_type=DeviceError)

        env = {'HUGOFGO_ADB': str(adb)}
        assert resolve_adb_path(None, env=env, which=lambda _: None, candidates=()) == str(adb)
        assert resolve_adb_path(None, env=env, which=lambda _: str(missing), candidates=()) == str(adb)
        expect_error(
            lambda: resolve_adb_path(None, env={'HUGOFGO_ADB': str(missing)}, which=lambda _: None, candidates=()),
            'HUGOFGO_ADB', error_type=DeviceError,
        )
        assert resolve_adb_path(None, env={}, which=lambda _: str(adb), candidates=()) == str(adb)
        missing_device = expect_error(
            lambda: resolve_adb_path(None, env={}, which=lambda _: None, candidates=()),
            'HUGOFGO_ADB', error_type=DeviceError,
        )
        assert 'adb_path' in str(missing_device) and '--list' not in str(missing_device)

        missing_list = expect_error(
            lambda: resolve_adb_path(None, env={}, which=lambda _: None, candidates=(), for_list=True),
            'HUGOFGO_ADB', '--adb', error_type=DeviceError,
        )
        assert '--list' in str(missing_list)

        assert resolve_adb_path(str(adb), env={'HUGOFGO_ADB': str(missing)}, which=lambda _: None) == str(adb)


def check_density() -> None:
    assert parse_density('Physical density: 320\nOverride density: 440\n') == 440
    assert parse_density('Override density: 440\nPhysical density: 320\n') == 440
    assert parse_density('Physical density: 320\n') == 320
    assert parse_density('') is None
    assert parse_density('garbage') is None
    assert not hasattr(adb_module, 'parse_last_number'), '应只保留诚实的 parse_density'

    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        runner = ScriptedRunner(
            adb_ok(b'device\n'),
            adb_ok(b'Physical size: 1920x1080\nOverride size: 1280x720\n'),
            adb_ok(b'Physical density: 320\nOverride density: 440\n'),
        )
        device = AdbDevice(make_config(serial='emu', adb_path=str(adb)), runner=runner)
        assert device.metrics().density == 440

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 1920x1080\n'),
                                adb_ok(b'Physical density: 320\n'))
        device = AdbDevice(make_config(serial='emu', adb_path=str(adb)), runner=runner)
        assert device.metrics().density == 320


def check_adb_timeouts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        timeout = subprocess.TimeoutExpired(['adb'], ADB_TIMEOUT)

        runner = ScriptedRunner(timeout)
        device = AdbDevice(make_config(serial='emulator-5554', adb_path=str(adb)), runner=runner)
        expect_error(device.metrics, 'emulator-5554', 'get-state', f'{ADB_TIMEOUT:g}', error_type=DeviceError)
        assert runner.options[0]['timeout'] == ADB_TIMEOUT

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 320\n'), timeout)
        device = AdbDevice(make_config(serial='dev-tap', adb_path=str(adb), width=1280, height=720), runner=runner)
        device.metrics()
        expect_error(lambda: device.tap(10, 10), 'dev-tap', 'input tap', f'{ADB_TIMEOUT:g}', error_type=DeviceError)
        assert runner.options[-1]['timeout'] == ADB_TIMEOUT

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 320\n'), timeout)
        device = AdbDevice(make_config(serial='dev-shot', adb_path=str(adb), width=1280, height=720), runner=runner)
        device.metrics()
        expect_error(device.frame, 'dev-shot', 'screencap', f'{ADB_TIMEOUT:g}', error_type=DeviceError)
        assert runner.options[-1]['timeout'] == ADB_TIMEOUT

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 1280x720\n'), timeout)
        device = AdbDevice(make_config(serial='dev-density', adb_path=str(adb)), runner=runner)
        expect_error(device.metrics, 'dev-density', 'wm density', f'{ADB_TIMEOUT:g}', error_type=DeviceError)

        runner = ScriptedRunner(timeout)
        expect_error(lambda: devices(str(adb), runner=runner), 'devices -l', f'{ADB_TIMEOUT:g}',
                     error_type=DeviceError)
        assert runner.options[0]['timeout'] == ADB_TIMEOUT

        runner = ScriptedRunner(adb_fail('error: cannot connect to daemon'))
        expect_error(lambda: devices(str(adb), runner=runner), 'devices -l', 'cannot connect',
                     error_type=DeviceError)


def check_discovery() -> None:
    assert gateway.devices is devices, '设备发现只保留一个实现，并从包入口导出'
    assert set(gateway.__all__) == {
        'AdbDevice', 'Device', 'DeviceChannel', 'DeviceConfig', 'DeviceError', 'DeviceMetrics',
        'FakeDevice', 'Frame', 'FrameFormat', 'LdplayerWindowDevice', 'Region', 'ScrcpyDevice', 'devices', 'open_device',
    }, '包入口只暴露设备网关的稳定接口'
    assert len(gateway.__all__) == len(set(gateway.__all__)), '__all__ 不应有重复项'
    assert not hasattr(gateway, 'to_device_point'), '坐标换算属于适配器内部实现，不应从包入口暴露'
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')

        runner = ScriptedRunner(adb_ok(b'* daemon not running; starting now at tcp:5037\n'
                                       b'List of devices attached\nemulator-5554\tdevice model:Pixel_5\n'
                                       b'* daemon started successfully\n127.0.0.1:16384\toffline\n'
                                       b"adb server version (41) doesn't match this client (39); killing...\n"
                                       b'????????????\tno permissions (user in plugdev group)\n'
                                       b'emulator-5556\tauthorizing\n192.168.0.2:5555\trescue\n'
                                       b'localhost:5555\thost\n'))
        found = devices(str(adb), runner=runner)
        assert [(item.serial, item.state, item.model) for item in found] == [
            ('emulator-5554', 'device', 'Pixel 5'),
            ('127.0.0.1:16384', 'offline', None),
            ('????????????', 'no permissions', None),
            ('emulator-5556', 'authorizing', None),
            ('192.168.0.2:5555', 'rescue', None),
            ('localhost:5555', 'host', None),
        ], 'daemon/server 提示行不能被当成设备，合法状态不能被丢弃'
        assert runner.calls == [[str(adb), 'devices', '-l']]

        runner = ScriptedRunner(adb_ok(b'* daemon started successfully\n'
                                       b"adb server version (41) doesn't match this client (39); killing...\n"))
        assert devices(str(adb), runner=runner) == [], '没有表头时也不能把提示行当成设备'


def check_scale() -> None:
    assert to_device_point(640, 360, 1280, 720) == (640, 360)
    assert to_device_point(0, 0, 1920, 1080) == (0, 0)
    assert to_device_point(640, 360, 1920, 1080) == (960, 540)
    assert to_device_point(640, 360, 1600, 900) == (800, 450)
    assert to_device_point(1279, 719, 2560, 1440) == (2558, 1438)
    assert to_device_point(-40, 5000, 1920, 1080) == (0, 1079)
    assert to_device_point(5000, 5000, 2560, 1440) == (2559, 1439)
    assert to_device_point(640, 360, 3840, 2160) == (1920, 1080)
    expect_error(lambda: to_device_point(1, 1, 0, 720), '分辨率', error_type=DeviceError)
    expect_error(lambda: to_device_point(1, 1, 2340, 1080), '不支持的分辨率', '16:9', error_type=DeviceError)
    expect_error(lambda: to_device_point(1, 1, 960, 540), '低于最低支持', error_type=DeviceError)
    expect_error(lambda: to_device_point(1, 1, 1920, 1088), '不支持的分辨率', error_type=DeviceError)
    expect_error(lambda: to_device_point(1, 1, 1366, 768), '不支持的分辨率', error_type=DeviceError)


def check_adb_metrics() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')

        runner = ScriptedRunner(
            adb_ok(b'device\n'),
            adb_ok(b'Physical size: 1920x1080\nOverride size: 1280x720\n'),
            adb_ok(b'Physical density: 320\n'),
        )
        device = AdbDevice(make_config(serial='emulator-5554', adb_path=str(adb)), runner=runner)
        assert isinstance(device, Device)
        metrics = device.metrics()
        assert (metrics.serial, metrics.width, metrics.height, metrics.density) == ('emulator-5554', 1280, 720, 320)
        assert metrics.region is Region.CN
        assert runner.calls == [
            [str(adb), '-s', 'emulator-5554', 'get-state'],
            [str(adb), '-s', 'emulator-5554', 'shell', 'wm', 'size'],
            [str(adb), '-s', 'emulator-5554', 'shell', 'wm', 'density'],
        ]

        calls = len(runner.calls)
        assert device.metrics() is metrics
        assert len(runner.calls) == calls

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 240\n'))
        override = AdbDevice(make_config(serial='s', adb_path=str(adb), width=1600, height=900), runner=runner)
        metrics = override.metrics()
        assert (metrics.width, metrics.height, metrics.density) == (1600, 900, 240)
        assert all('size' not in call for call in runner.calls)


def check_adb_errors() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        config = make_config(serial='emulator-5554', adb_path=str(adb))

        runner = ScriptedRunner(adb_fail('error: device offline'))
        device = AdbDevice(config, runner=runner)
        expect_error(device.metrics, 'emulator-5554', 'device offline', error_type=DeviceError)

        runner = ScriptedRunner(adb_ok(b'offline\n'))
        device = AdbDevice(config, runner=runner)
        expect_error(device.metrics, 'offline', 'emulator-5554', error_type=DeviceError)

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_fail('error: no devices/emulators found'))
        device = AdbDevice(config, runner=runner)
        expect_error(device.metrics, 'shell wm size', 'no devices/emulators found', error_type=DeviceError)

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'garbage\n'))
        device = AdbDevice(config, runner=runner)
        error = expect_error(device.metrics, '分辨率', 'DeviceConfig', 'width', error_type=DeviceError)
        assert '--width' not in str(error)

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 2340x1080\n'))
        device = AdbDevice(config, runner=runner)
        expect_error(device.metrics, 'emulator-5554', '2340×1080', '不支持的分辨率', '16:9',
                     error_type=DeviceError)


def check_adb_actions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        config = make_config(serial='emulator-5554', adb_path=str(adb), width=1920, height=1080)

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 320\n'),
                                adb_ok(), adb_ok(), adb_ok(), adb_ok())
        device = AdbDevice(config, runner=runner)
        device.tap(640, 360)
        assert runner.calls[-1] == [str(adb), '-s', 'emulator-5554', 'shell', 'input', 'tap', '960', '540']

        device.tap(-10, 5000)
        assert runner.calls[-1][-2:] == ['0', '1079']

        device.swipe(100, 200, 300, 400)
        assert runner.calls[-1][-8:] == ['shell', 'input', 'swipe', '150', '300', '450', '600', '350']

        device.swipe(10, 20, 1278, 718, duration_ms=900)
        assert runner.calls[-1][-8:] == ['shell', 'input', 'swipe', '15', '30', '1917', '1077', '900']

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 320\n'), adb_fail('error: closed'))
        device = AdbDevice(config, runner=runner)
        device.metrics()
        expect_error(lambda: device.tap(1, 1), 'input tap', 'error: closed', error_type=DeviceError)


def check_adb_frame() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        config = make_config(serial='emulator-5554', adb_path=str(adb))

        png = make_png(1280, 720)
        runner = ScriptedRunner(
            adb_ok(b'device\n'), adb_ok(b'Physical size: 1280x720\n'), adb_ok(b'Physical density: 320\n'), adb_ok(png),
        )
        device = AdbDevice(config, runner=runner)
        device.metrics()
        frame = device.frame()
        assert (frame.width, frame.height) == (1280, 720)
        assert frame.image == png
        assert frame.captured_at > 0
        assert runner.calls[-1] == [str(adb), '-s', 'emulator-5554', 'exec-out', 'screencap', '-p']

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 1280x720\n'),
                                adb_ok(b'Physical density: 320\n'), adb_ok(b'not a png'))
        device = AdbDevice(config, runner=runner)
        device.metrics()
        expect_error(device.frame, 'PNG', 'emulator-5554', error_type=DeviceError)

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 320\n'), adb_ok(make_png(1280, 720)))
        device = AdbDevice(
            make_config(serial='emulator-5554', adb_path=str(adb), width=1600, height=900), runner=runner,
        )
        device.metrics()
        error = expect_error(device.frame, '1280×720', '1600×900', 'DeviceConfig', error_type=DeviceError)
        assert '--width' not in str(error)

        png = make_png(1280, 720)
        assert png_size(png) == (1280, 720)
        assert png_size(b'') is None
        assert png_size(png[:12]) is None
        assert png_size(png[:4] + b'xxxx' + png[8:]) is None


def check_fake_sequence() -> None:
    first = Frame(image=make_png(1280, 720), width=1280, height=720, captured_at=1.0)
    second = Frame(image=make_png(1280, 720) + b'2', width=1280, height=720, captured_at=2.0)
    device = FakeDevice([first, second])
    assert isinstance(device, Device)
    metrics = device.metrics()
    assert (metrics.width, metrics.height, metrics.region) == (1280, 720, Region.CN)
    assert device.frame() is first
    assert device.frame() is second
    expect_error(device.frame, '帧', '2', error_type=DeviceError)

    small = Frame(image=make_png(640, 360), width=640, height=360, captured_at=3.0)
    expect_error(lambda: FakeDevice([first, small]), '分辨率', error_type=DeviceError)
    expect_error(lambda: FakeDevice([first], frame_dir='.'), '只能给出一个', error_type=DeviceError)
    FakeDevice([first], config=make_config(serial='match', width=1280, height=720))
    expect_error(
        lambda: FakeDevice([first], config=make_config(serial='mismatch', width=1920, height=1080)),
        '配置分辨率', '1280×720', error_type=DeviceError,
    )
    square = Frame(image=make_png(1600, 1000), width=1600, height=1000, captured_at=4.0)
    expect_error(lambda: FakeDevice([square]), '回放帧', '不支持的分辨率', '16:9', error_type=DeviceError)

    empty = FakeDevice(config=make_config(serial='tap-only', width=1280, height=720))
    assert empty.metrics().serial == 'tap-only'
    expect_error(empty.frame, '帧', error_type=DeviceError)


def check_fake_actions() -> None:
    device = FakeDevice(
        [Frame(image=make_png(1920, 1080), width=1920, height=1080, captured_at=1.0)],
        config=make_config(serial='replay', region='JP'),
    )
    with mock.patch('subprocess.run', side_effect=AssertionError('FakeDevice 不应调用外部进程')):
        device.tap(640, 360)
        device.tap(-10, 5000)
        device.swipe(100, 200, 300, 400)
        device.swipe(10, 20, 1278, 718, duration_ms=900)
    assert device.actions == [
        {'action': 'tap', 'x': 640, 'y': 360, 'device_x': 960, 'device_y': 540},
        {'action': 'tap', 'x': -10, 'y': 5000, 'device_x': 0, 'device_y': 1079},
        {'action': 'swipe', 'x1': 100, 'y1': 200, 'x2': 300, 'y2': 400,
         'device_x1': 150, 'device_y1': 300, 'device_x2': 450, 'device_y2': 600, 'duration_ms': 350},
        {'action': 'swipe', 'x1': 10, 'y1': 20, 'x2': 1278, 'y2': 718,
         'device_x1': 15, 'device_y1': 30, 'device_x2': 1917, 'device_y2': 1077, 'duration_ms': 900},
    ]
    assert device.metrics().region is Region.JP


def check_fake_dir() -> None:
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, 'b.png').write_bytes(make_png(1280, 720) + b'B')
        Path(directory, 'a.png').write_bytes(make_png(1280, 720) + b'A')
        device = FakeDevice(frame_dir=directory)
        assert device.frame().image.endswith(b'A')
        assert device.frame().image.endswith(b'B')
        assert (device.metrics().width, device.metrics().height) == (1280, 720)

    with tempfile.TemporaryDirectory() as directory:
        expect_error(lambda: FakeDevice(frame_dir=directory), 'PNG', error_type=DeviceError)
        Path(directory, 'broken.png').write_bytes(b'not a png')
        expect_error(lambda: FakeDevice(frame_dir=directory), 'broken.png', error_type=DeviceError)

    with tempfile.TemporaryDirectory() as directory:
        Path(directory, 'nonsquare.png').write_bytes(make_png(1600, 1000))
        expect_error(lambda: FakeDevice(frame_dir=directory), '帧目录', '不支持的分辨率', '16:9',
                     error_type=DeviceError)


def check_channel() -> None:
    assert DeviceChannel.parse('adb') is DeviceChannel.ADB
    assert DeviceChannel.parse(' LDPLAYER-WINDOW ') is DeviceChannel.LDPLAYER_WINDOW
    assert DeviceChannel.parse(DeviceChannel.LDPLAYER_WINDOW) is DeviceChannel.LDPLAYER_WINDOW
    assert DeviceChannel.parse('LDPlayer 窗口原始帧') is DeviceChannel.LDPLAYER_WINDOW
    assert 'adb' in DeviceChannel.options() and 'ldplayer-window' in DeviceChannel.options()
    error = expect_error(lambda: DeviceChannel.parse('bluestacks'), 'bluestacks', error_type=DeviceError)
    assert 'adb' in str(error) and 'ldplayer-window' in str(error)

    assert make_config(serial='x').channel is DeviceChannel.ADB, '默认通道必须仍是 adb'
    assert make_config(serial='x', channel='ldplayer-window').channel is DeviceChannel.LDPLAYER_WINDOW
    assert make_config(serial='x', channel='scrcpy').channel is DeviceChannel.SCRCPY
    assert make_config(serial='x', channel='SCRCPY').channel is DeviceChannel.SCRCPY
    assert make_config(serial='x', channel='ADB').channel is DeviceChannel.ADB
    expect_error(lambda: make_config(serial='x', channel='mumu'), 'mumu', error_type=DeviceError)


def check_scrcpy_frames_and_input() -> None:
    import queue
    import struct
    import numpy as np
    from executor.device.scrcpy import ScrcpyDevice

    class Capture:
        def __init__(self):
            import threading
            self.frames = queue.Queue()
            self.released = False
            self.read_count = 0
            self.second_read = threading.Event()

        def read(self):
            try:
                frame = self.frames.get(timeout=0.1)
                self.read_count += 1
                if self.read_count == 2:
                    self.second_read.set()
                return True, frame
            except queue.Empty:
                return (False, None) if self.released else self.read()

        def release(self):
            self.released = True

    class Control:
        def __init__(self):
            self.packets = []
            self.closed = False

        def sendall(self, data):
            self.packets.append(bytes(data))

        def close(self):
            self.closed = True

    class Session:
        def __init__(self):
            self.control = Control()
            self.video_url = 'tcp://127.0.0.1:1'
            self.closed = False

        def ensure_alive(self):
            return None

        def close(self):
            self.closed = True

    capture = Capture()
    session = Session()
    config = make_config(serial='emulator-5554', channel='scrcpy', width=1920, height=1080)
    device = ScrcpyDevice(config, session_factory=lambda _: session, capture_factory=lambda _: capture,
                          frame_timeout=0.2, max_frame_age=1.0)
    try:
        assert isinstance(device, Device)
        capture.frames.put(np.zeros((1080, 1920, 3), dtype=np.uint8))
        capture.frames.put(np.full((1080, 1920, 3), 17, dtype=np.uint8))
        assert capture.second_read.wait(1), '视频源应先连续解码两帧'
        frame = device.frame()
        assert frame.format is FrameFormat.BGR24 and frame.frame_id == 2, '只向感知交付持续流中的最新帧'
        assert frame.session_id == device.session_id and frame.captured_at > 0
        assert (frame.width, frame.height) == (1920, 1080)
        expect_error(device.frame, '新帧', '超时', error_type=DeviceError)

        device.tap(640, 360)
        assert len(session.control.packets) == 2
        down = struct.unpack('>BBqIIHHHII', session.control.packets[0])
        up = struct.unpack('>BBqIIHHHII', session.control.packets[1])
        assert down[:5] == (0, 0, 0, 960, 540) and down[5:7] == (1920, 1080)
        assert up[:5] == (0, 1, 0, 960, 540) and up[5:7] == (1920, 1080)

        before = len(session.control.packets)
        device.swipe(100, 200, 300, 400, duration_ms=32)
        swipe = [struct.unpack('>BBqIIHHHII', packet) for packet in session.control.packets[before:]]
        assert [packet[1] for packet in swipe] == [0, 2, 1]
        assert (swipe[0][3], swipe[0][4]) == (150, 300)
        assert (swipe[-1][3], swipe[-1][4]) == (450, 600)
        assert all(packet[5:7] == (1920, 1080) for packet in swipe)
    finally:
        device.close()
    assert capture.released and session.closed and session.control.closed


def check_scrcpy_failures() -> None:
    from executor.device.scrcpy import ScrcpyDevice

    config = make_config(serial='emu', channel='scrcpy', width=1280, height=720)
    expect_error(lambda: ScrcpyDevice(config, session_factory=lambda _: (_ for _ in ()).throw(DeviceError('adb boom'))),
                 'scrcpy 启动失败', 'adb boom', error_type=DeviceError)

    class BrokenCapture:
        def read(self):
            return False, None

        def release(self):
            pass

    class BrokenControl:
        def __init__(self):
            self.calls = 0

        def sendall(self, data):
            self.calls += 1
            raise OSError('socket closed')

        def close(self):
            pass

    class Session:
        video_url = 'fake:video'

        def __init__(self, control=None):
            self.control = control or BrokenControl()
            self.closed = False

        def ensure_alive(self):
            return None

        def close(self):
            self.closed = True

    session = Session()
    expect_error(lambda: ScrcpyDevice(config, session_factory=lambda _: session,
                                      capture_factory=lambda _: (_ for _ in ()).throw(DeviceError('decoder missing'))),
                 'scrcpy 启动失败', 'decoder missing', error_type=DeviceError)
    assert session.closed, '解码器启动失败必须关闭已建立的服务端与控制会话'

    control = BrokenControl()
    device = ScrcpyDevice(config, session_factory=lambda _: Session(control),
                          capture_factory=lambda _: BrokenCapture(), frame_timeout=0.1)
    try:
        expect_error(device.frame, '视频流中断', '旧帧', error_type=DeviceError)
        calls = control.calls
        expect_error(lambda: device.tap(1, 1), '视频流中断', error_type=DeviceError)
        assert control.calls == calls, '视频中断后禁止继续输入'
    finally:
        device.close()

    class WaitingCapture:
        def read(self):
            time.sleep(1)
            return False, None

        def release(self):
            pass

    device = ScrcpyDevice(config, session_factory=lambda _: Session(),
                          capture_factory=lambda _: WaitingCapture(), frame_timeout=0.1)
    try:
        expect_error(lambda: device.tap(1, 1), '控制通道中断', 'socket closed', error_type=DeviceError)
        expect_error(device.frame, '控制通道中断', error_type=DeviceError)
    finally:
        device.close()


def check_scrcpy_resources() -> None:
    from executor.device.scrcpy_resources import verify_scrcpy_server

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'version': '3.3.3', 'server_file': 'scrcpy-server.jar',
                                        'sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}),
                            encoding='utf-8')
        expect_error(lambda: verify_scrcpy_server(root), 'scrcpy 服务端缺失', 'scrcpy-server.jar',
                     error_type=DeviceError)
        server = root / 'scrcpy-server.jar'
        server.write_bytes(b'abc')
        assert verify_scrcpy_server(root) == server
        manifest.write_text(json.dumps({'version': '3.3.2', 'server_file': 'scrcpy-server.jar',
                                        'sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}),
                            encoding='utf-8')
        expect_error(lambda: verify_scrcpy_server(root), '固定版本 3.3.3', '3.3.2', error_type=DeviceError)
        manifest.write_text(json.dumps({'version': '3.3.3', 'server_file': 'scrcpy-server.jar',
                                        'sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}),
                            encoding='utf-8')
        server.write_bytes(b'tampered')
        expect_error(lambda: verify_scrcpy_server(root), 'SHA-256', '不匹配', error_type=DeviceError)
        manifest.write_text('{broken', encoding='utf-8')
        expect_error(lambda: verify_scrcpy_server(root), '清单', error_type=DeviceError)

    bundled = verify_scrcpy_server()
    assert bundled.name == 'scrcpy-server.jar' and bundled.stat().st_size == 90164


def check_scrcpy_session() -> None:
    from executor.device.scrcpy_runtime import ScrcpySession

    class Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, argv, **kwargs):
            self.calls.append(list(argv))
            if 'get-state' in argv:
                return subprocess.CompletedProcess(argv, 0, b'device\n', b'')
            if 'density' in argv:
                return subprocess.CompletedProcess(argv, 0, b'Physical density: 320\n', b'')
            if 'push' in argv:
                return subprocess.CompletedProcess(argv, 0, b'1 file pushed\n', b'')
            if 'forward' in argv and '--remove' not in argv:
                port = b'31001\n' if sum('forward' in call for call in self.calls) == 1 else b'31002\n'
                return subprocess.CompletedProcess(argv, 0, port, b'')
            return subprocess.CompletedProcess(argv, 0, b'', b'')

    class Socket:
        def __init__(self, dummy=b'\x00'):
            self.closed = False
            self.reads = 0
            self.dummy = dummy

        def recv(self, size):
            self.reads += 1
            return self.dummy if self.reads == 1 else b''

        def settimeout(self, value):
            pass

        def close(self):
            self.closed = True

        def sendall(self, data):
            pass

    class Process:
        def __init__(self):
            self.args = None
            self.returncode = None
            self.stdout = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -1

    class Relay:
        video_url = 'tcp://127.0.0.1:32000'

        def __init__(self, video):
            self.video = video

        def close(self):
            self.video.close()

    runner = Runner()
    process = Process()
    sockets = []

    def popen(argv, **kwargs):
        process.args = list(argv)
        return process

    def connect(address, timeout):
        sock = Socket(b'' if not sockets else b'\x00')
        sockets.append(sock)
        return sock

    config = make_config(serial='emulator-5554', channel='scrcpy', width=1280, height=720)
    with mock.patch('executor.device.scrcpy_runtime.secrets.randbelow', return_value=0xffffffff):
        session = ScrcpySession(config, runner=runner, popen=popen, socket_factory=connect,
                                relay_factory=Relay)
    try:
        assert 0 <= int(session.scid, 16) <= 0x7fffffff, 'scid 必须能被 scrcpy 的有符号 Integer.parseInt 解析'
        assert (session.metrics.width, session.metrics.height, session.metrics.density) == (1280, 720, 320)
        assert session.video_url == 'tcp://127.0.0.1:32000'
        assert process.args[1:3] == ['-s', 'emulator-5554']
        command = ' '.join(process.args)
        assert 'com.genymobile.scrcpy.Server 3.3.3' in command
        assert 'control=true' in command and 'send_frame_meta=false' in command and 'audio=false' in command
        assert f'scid={session.scid}' in command, '每个会话使用独立 socket，避免残留服务端占用 scrcpy 名称'
        assert len(sockets) == 3 and sockets[0].dummy == b'' and sockets[1].dummy == b'\x00', \
            'ADB 提前接受但服务端未就绪时，EOF 视频连接必须在启动期限内重建'
        assert sockets[1].reads == 1, '视频 socket 的首个 dummy 字节不得进入 H.264 流'
        assert any('push' in call and 'hugofgo-scrcpy-server-3.3.3.jar' in call[-1] for call in runner.calls)
        forwards = [call for call in runner.calls if 'forward' in call and '--remove' not in call]
        assert len(forwards) == 2 and all(call[-1] == f'localabstract:scrcpy_{session.scid}' for call in forwards)
        session.ensure_alive()
    finally:
        session.close()
    assert process.returncode == 0 and all(sock.closed for sock in sockets)
    assert sum('--remove' in call for call in runner.calls) == 2


def check_frame_format() -> None:
    raw = Frame(image=b'\x00' * 12, width=2, height=2, captured_at=0.0, format='bgr24')
    assert raw.format is FrameFormat.BGR24 and raw.capture_ms is None
    expect_error(lambda: Frame(image=b'\x00' * 8, width=2, height=2, captured_at=0.0, format=FrameFormat.BGR24),
                 '12 字节', error_type=DeviceError)
    expect_error(lambda: Frame(image=b'', width=1, height=1, captured_at=0.0, format='jpeg'),
                 'PNG', error_type=DeviceError)
    png_frame = Frame(image=make_png(1280, 720), width=1280, height=720, captured_at=1.0)
    assert png_frame.format is FrameFormat.PNG, 'adb 与回放帧的默认格式必须仍是 PNG'
    streamed = Frame(image=make_png(1280, 720), width=1280, height=720, captured_at=1.0,
                     frame_id=7, session_id='session-a')
    assert (streamed.frame_id, streamed.session_id) == (7, 'session-a')
    assert png_frame.frame_id is None and png_frame.session_id is None, '现有通道不需要伪造流身份'


def check_ldplayer_window() -> None:
    fake = FakeWin32()
    window = find_ldplayer_window(fake)
    assert (window.parent, window.render, window.title) == (FakeWin32.PARENT, FakeWin32.RENDER, '雷电模拟器')

    fake = FakeWin32()
    fake.parents = set()
    error = expect_error(lambda: find_ldplayer_window(fake), 'LDPlayer', 'adb', error_type=DeviceError)
    assert 'no-window' in str(error)

    fake = FakeWin32()
    fake.render_children = {}
    error = expect_error(lambda: find_ldplayer_window(fake), 'RenderWindow', error_type=DeviceError)
    assert 'no-render-window' in str(error) and 'LDPlayer 9/14' in str(error)

    fake = FakeWin32()
    fake.parents.add(2000)
    fake.classes[2000] = 'LDPlayerMainFrame'
    fake.titles[2000] = '雷电模拟器-2'
    fake.render_children[2000] = [2001]
    fake.classes[2001] = 'RenderWindow'
    fake.sizes[2001] = (1280, 720)
    error = expect_error(lambda: find_ldplayer_window(fake), '多开', '2 个', error_type=DeviceError)
    assert 'multi-instance' in str(error)

    fake = FakeWin32()
    fake.parents.add(2000)
    fake.classes[2000] = 'LDPlayerMainFrame'
    fake.titles[2000] = '雷电模拟器-2'
    assert find_ldplayer_window(fake).parent == FakeWin32.PARENT, '没有 RenderWindow 的辅助窗口不算实例'

    fake = FakeWin32()
    fake.classes[FakeWin32.PARENT] = 'UnknownMainFrame'
    assert find_ldplayer_window(fake).parent == FakeWin32.PARENT, '类名不认识时按进程名 dnplayer.exe 识别'

    if sys.platform != 'win32':
        expect_error(lambda: gateway.LdplayerWindowDevice(make_config(serial='x')), 'Windows',
                     error_type=DeviceError)


def check_ldplayer_frames() -> None:
    fake = FakeWin32()
    device = LdplayerWindowDevice(make_config(serial='emulator-5554', channel='ldplayer-window'), api=fake)
    assert isinstance(device, Device)
    metrics = device.metrics()
    assert (metrics.serial, metrics.width, metrics.height, metrics.density) == ('emulator-5554', 1280, 720, None)
    assert metrics.region is Region.CN

    frame = device.frame()
    assert (frame.width, frame.height) == (1280, 720)
    assert frame.format is FrameFormat.BGR24
    assert frame.image == fake.pixels and len(frame.image) == 1280 * 720 * 3
    assert frame.captured_at > 0
    assert frame.capture_ms is not None and frame.capture_ms >= 0, '每帧必须记录采集耗时'
    assert fake.leaked() == [], '每帧都必须释放 DC、位图并还原 SelectObject'
    assert fake.calls[:6] == ['GetDC', 'CreateCompatibleDC', 'CreateCompatibleBitmap', 'SelectObject',
                              'PrintWindow', 'GetDIBits'], f'采集调用顺序固定：{fake.calls}'
    assert fake.calls[6:] == ['SelectObject', 'DeleteObject', 'DeleteDC', 'ReleaseDC'], \
        f'释放顺序必须是还原 SelectObject → DeleteObject → DeleteDC → ReleaseDC：{fake.calls}'

    stats = device.stats()
    assert (stats.attempts, stats.frames, stats.failures) == (1, 1, 0)
    assert stats.last_reason is None and stats.last_ms is not None
    assert '失败原因 无' in stats_summary(stats)

    device.frame()
    assert device.stats().frames == 2, '连续取帧必须累计统计'
    assert fake.leaked() == []

    window_moved = FakeWin32()
    device = LdplayerWindowDevice(make_config(serial='s'), api=window_moved)
    device.metrics()
    replacement = 3001
    window_moved.classes.pop(FakeWin32.RENDER)
    window_moved.sizes.pop(FakeWin32.RENDER)
    window_moved.render_children[FakeWin32.PARENT] = [replacement]
    window_moved.classes[replacement] = 'RenderWindow'
    window_moved.sizes[replacement] = (1280, 720)
    assert device.frame().width == 1280, '窗口被重建后必须重新定位，而不是一直报旧句柄'

    clipped = FakeWin32()
    clipped.sizes[FakeWin32.PARENT] = (1340, 779)
    device = LdplayerWindowDevice(make_config(serial='s'), api=clipped)
    frame = device.frame()
    assert (frame.width, frame.height) == (1280, 720)
    assert clipped.sizes[FakeWin32.PARENT] == (1340, 780), '父窗口必须加高被裁的 1 px'
    assert clipped.calls.count('SetWindowPos') == 1
    assert device.stats().window_adjustments == 1
    assert '窗口修正 1 次' in stats_summary(device.stats())
    device.frame()
    assert device.stats().window_adjustments == 1, '已经放得下就不应每帧重复加高'
    assert clipped.leaked() == []


def check_ldplayer_errors() -> None:
    config = make_config(serial='emulator-5554', channel='ldplayer-window')
    fake = FakeWin32()
    device = LdplayerWindowDevice(config, api=fake)

    fake.iconic = True
    error = expect_error(device.frame, '最小化', 'minimized', error_type=DeviceError)
    assert fake.leaked() == [] and 'PrintWindow' in str(error), '最小化必须在采集前明确报错'
    stats = device.stats()
    assert (stats.attempts, stats.frames, stats.failures, stats.last_reason) == (1, 0, 1, 'minimized')
    assert stats.reasons == {'minimized': 1}
    fake.iconic = False

    fake.print_ok = False
    expect_error(device.frame, 'PrintWindow', 'print-window', error_type=DeviceError)
    assert fake.leaked() == [], 'PrintWindow 失败路径也要释放资源'
    fake.print_ok = True

    fake.lines = 719
    expect_error(device.frame, '719/720', 'copy', error_type=DeviceError)
    assert fake.leaked() == []
    fake.lines = 0
    expect_error(device.frame, '截图为空', 'empty', error_type=DeviceError)
    assert fake.leaked() == []
    fake.lines = None

    clipped = FakeWin32()
    clipped.sizes[FakeWin32.PARENT] = (1340, 700)
    clipped_device = LdplayerWindowDevice(config, api=clipped)
    error = expect_error(clipped_device.frame, 'clipped', '上限', error_type=DeviceError)
    assert '16' in str(error)
    assert clipped.leaked() == [], '裁切检查失败时也不能泄漏 GDI 资源'

    good = fake.pixels
    fake.pixels = bytes(1280 * 720 * 3)
    expect_error(device.frame, '全黑', 'black', error_type=DeviceError)
    assert fake.leaked() == []
    fake.pixels = good

    fake.sizes[FakeWin32.RENDER] = (1600, 900)
    error = expect_error(device.frame, '1600×900', 'size', error_type=DeviceError)
    assert '1280×720' in str(error)
    fake.sizes[FakeWin32.RENDER] = (1280, 720)

    assert device.stats().reasons == {'minimized': 1, 'print-window': 1, 'copy': 1, 'empty': 1, 'black': 1,
                                      'size': 1}
    assert 'black×1' in stats_summary(device.stats())

    expect_error(lambda: device.tap(1, 2), '只提供截图', 'adb', error_type=DeviceError)
    expect_error(lambda: device.swipe(1, 2, 3, 4), '只提供截图', error_type=DeviceError)

    fake = FakeWin32()
    device = LdplayerWindowDevice(config, api=fake)
    device.metrics()
    fake.parents = set()
    fake.classes = {}
    expect_error(device.frame, '未找到 LDPlayer', 'no-window', error_type=DeviceError)

    error = expect_error(lambda: LdplayerWindowDevice(make_config(serial='x', width=1920, height=1080),
                                                      api=FakeWin32()).metrics(),
                         '1920×1080', '1280×720', error_type=DeviceError)
    assert 'DeviceConfig' in str(error)

    error = expect_error(lambda: LdplayerWindowDevice(make_config(serial='x'),
                                                      api=FakeWin32(width=1366, height=768)).metrics(),
                         '不支持的分辨率', error_type=DeviceError)
    assert 'DPI' in str(error), '非 16:9 时要提示 DPI 感知，方便排查系统缩放'


def check_open_device() -> None:
    assert gateway.open_device is open_device
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 1280x720\n'),
                                adb_ok(b'Physical density: 320\n'))
        device = open_device(make_config(serial='emulator-5554', adb_path=str(adb)), runner=runner)
        assert isinstance(device, AdbDevice), '默认通道必须构造 adb 适配器'
        assert device.metrics().width == 1280

    if sys.platform == 'win32':
        device = open_device(make_config(serial='emulator-5554', channel='ldplayer-window'))
        assert isinstance(device, LdplayerWindowDevice)
        assert isinstance(device, Device)

    marker = object()
    with mock.patch.object(gateway, 'ScrcpyDevice', return_value=marker) as constructor:
        assert open_device(make_config(serial='emulator-5554', channel='scrcpy')) is marker
        assert constructor.call_args.args[0].channel is DeviceChannel.SCRCPY


def require_unoptimized() -> None:
    if sys.flags.optimize:
        raise RuntimeError('设备网关自检不能在 python -O 下运行：断言会被剥离，请使用普通 python 运行')


def check_optimized_guard() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, '-O', '-m', 'executor.device', '--self-test'],
        cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
        env={**os.environ, 'HUGOFGO_SELF_TEST_CHILD': '1'},
    )
    assert result.returncode == 1, 'python -O 下完整自检必须显式失败，不能空跑通过'
    assert 'python -O' in result.stderr, f'守卫失败必须给出 -O 原因：{result.stderr}'


CHECKS = [
    ('区服解析', check_region),
    ('设备配置校验', check_config),
    ('设备通道配置', check_channel),
    ('基准坐标换算与钳制', check_scale),
    ('adb 定位优先级', check_adb_resolution),
    ('adb 分辨率读取', check_adb_metrics),
    ('adb 密度解析', check_density),
    ('adb 调用超时', check_adb_timeouts),
    ('设备发现入口', check_discovery),
    ('adb 离线与命令失败上下文', check_adb_errors),
    ('adb 点击与滑动换算', check_adb_actions),
    ('adb 截图校验', check_adb_frame),
    ('fake 帧序', check_fake_sequence),
    ('fake 动作记录', check_fake_actions),
    ('fake 帧目录', check_fake_dir),
    ('帧格式与原始像素校验', check_frame_format),
    ('scrcpy 连续最新帧与输入', check_scrcpy_frames_and_input),
    ('scrcpy 启动、取帧、输入与中断失败', check_scrcpy_failures),
    ('scrcpy 固定服务端资源校验', check_scrcpy_resources),
    ('scrcpy 同设备双通道会话与清理', check_scrcpy_session),
    ('LDPlayer 窗口定位', check_ldplayer_window),
    ('LDPlayer 原始帧与资源释放', check_ldplayer_frames),
    ('LDPlayer 采集失败与原因', check_ldplayer_errors),
    ('设备工厂按通道构造', check_open_device),
]
if not os.environ.get('HUGOFGO_SELF_TEST_CHILD'):
    CHECKS.append(('python -O 防护', check_optimized_guard))


def self_test() -> int:
    try:
        require_unoptimized()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    failures = 0
    for name, check in CHECKS:
        try:
            check()
        except Exception as error:  # 自检要跑完全部用例，一次报告所有失败
            failures += 1
            print(f'失败 {name}：{type(error).__name__}: {error}')
            traceback.print_exc()
        else:
            print(f'通过 {name}')
    if failures:
        print(f'设备网关自检失败：{failures}/{len(CHECKS)} 项')
        return 1
    print(f'设备网关自检通过：{len(CHECKS)} 项')
    return 0


def list_command(adb_path: str | None) -> int:
    try:
        found = devices(adb_path)
    except DeviceError as error:
        print(f'设备错误：{error}', file=sys.stderr)
        return 2
    if not found:
        print('没有找到 adb 设备；请启动模拟器，并确认 adb devices 能看到它')
        return 1
    for info in found:
        print('\t'.join([info.serial, info.state, info.model or '']))
    return 0


def _percentile(values: list[float], fraction: float) -> float:
    """最近秩百分位；不引第三方依赖，够用即可。"""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def capture_command(*, serial: str | None, channel: str, adb_path: str | None, region: str,
                    count: int) -> int:
    """按通道连续取帧并打印耗时统计：实机验收入口，不写任何截图文件。"""
    if not serial:
        print('--capture 需要 --serial（adb devices 第一列，如 emulator-5554）', file=sys.stderr)
        return 2
    if count <= 0:
        print('--capture 的帧数必须为正整数', file=sys.stderr)
        return 2
    try:
        config = DeviceConfig(serial=serial, region=region, adb_path=adb_path, channel=channel)
        device = open_device(config)
        metrics = device.metrics()
    except DeviceError as error:
        print(f'设备错误：{error}', file=sys.stderr)
        return 2

    print(f'通道 {config.channel.value}（{config.channel.label}），设备 {metrics.serial}，'
          f'metrics {metrics.width}×{metrics.height}，连续 {count} 帧')
    times: list[float] = []
    failures = 0
    for index in range(1, count + 1):
        started = time.perf_counter()
        try:
            frame = device.frame()
        except DeviceError as error:
            failures += 1
            print(f'第 {index} 帧失败：{error}', file=sys.stderr)
            continue
        wall_ms = (time.perf_counter() - started) * 1000
        times.append(wall_ms)
        adapter = '—' if frame.capture_ms is None else f'{frame.capture_ms:.2f} ms'
        print(f'第 {index} 帧 {frame.width}×{frame.height} {frame.format.value} {len(frame.image)} 字节'
              f'｜墙钟 {wall_ms:.2f} ms｜通道 {adapter}')

    if times:
        mean = sum(times) / len(times)
        print(f'成功 {len(times)} 帧，失败 {failures} 帧；单帧墙钟 mean={mean:.2f} ms '
              f'P95={_percentile(times, 0.95):.2f} ms min={min(times):.2f} ms max={max(times):.2f} ms')
    else:
        print(f'全部 {count} 帧都失败', file=sys.stderr)
    stats = getattr(device, 'stats', None)
    if callable(stats):
        print(f'通道统计：{stats_summary(stats())}')
    close = getattr(device, 'close', None)
    if close is not None:
        close()
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m executor.device',
                                     description='设备网关自检、设备列表与通道取帧验收')
    parser.add_argument('--self-test', action='store_true', help='运行不依赖真机的自检')
    parser.add_argument('--list', action='store_true', help='列出 adb 设备（需要 adb 与在线模拟器）')
    parser.add_argument('--capture', metavar='帧数', type=int,
                        help='按 --channel 连续取帧并打印耗时统计（实机验收，不写文件）')
    parser.add_argument('--channel', choices=[channel.value for channel in DeviceChannel],
                        default=DeviceChannel.ADB.value,
                        help='帧来源通道；默认 adb，ldplayer-window 只支持 Windows + LDPlayer')
    parser.add_argument('--serial', help='设备序列号，如 emulator-5554（--capture 必填）')
    parser.add_argument('--region', default='CN', help='区服：CN、JP、TW 或中文名（默认 CN）')
    parser.add_argument('--adb', help='adb 路径；缺省按设备配置 → HUGOFGO_ADB → PATH → 常见模拟器路径查找')
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.list:
        return list_command(args.adb)
    if args.capture is not None:
        return capture_command(serial=args.serial, channel=args.channel, adb_path=args.adb,
                               region=args.region, count=args.capture)
    parser.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
