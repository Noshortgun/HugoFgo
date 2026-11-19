"""设备网关自检与手动验收入口。

用法：
  python -m executor.device --self-test   # 不依赖真机
  python -m executor.device --list        # 需要 adb 与在线设备
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import executor.device as gateway
from executor.device import adb as adb_module
from executor.device.adb import ADB_TIMEOUT, AdbDevice, devices, parse_density, resolve_adb_path
from executor.device.base import Device, DeviceError, Frame, to_device_point
from executor.device.config import DeviceConfig, DeviceMetrics, Region
from executor.device.fake import FakeDevice
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


def check_region() -> None:
    assert get_type_hints(DeviceMetrics)['region'] is Region
    assert Region.parse('CN') is Region.CN
    assert Region.parse('cn') is Region.CN
    assert Region.parse('国服') is Region.CN
    assert Region.parse(Region.JP) is Region.JP
    assert Region.CN.label == '国服'
    expect_error(lambda: Region.parse('EU'), 'EU', error_type=DeviceError)
    expect_error(lambda: Region.parse('EU'), 'CN', error_type=DeviceError)


def check_config() -> None:
    config = DeviceConfig(serial='emulator-5554')
    assert config.region is Region.CN
    assert config.adb_path is None and config.width is None and config.height is None

    config = DeviceConfig(serial=' 127.0.0.1:16384 ', region='jp', adb_path='', width=1280, height=720)
    assert config.serial == '127.0.0.1:16384'
    assert config.region is Region.JP
    assert config.adb_path is None
    assert (config.width, config.height) == (1280, 720)

    expect_error(lambda: DeviceConfig(serial='  '), 'serial', error_type=DeviceError)
    expect_error(lambda: DeviceConfig(serial='x', width=1280), 'width', error_type=DeviceError)
    expect_error(lambda: DeviceConfig(serial='x', width=0, height=720), 'width', error_type=DeviceError)
    expect_error(lambda: DeviceConfig(serial='x', region='EU'), 'EU', error_type=DeviceError)
    expect_error(lambda: DeviceConfig(serial='x', width='abc', height=720), 'width', error_type=DeviceError)
    expect_error(lambda: DeviceConfig(serial='x', width=2340, height=1080), '16:9', error_type=DeviceError)

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
        device = AdbDevice(DeviceConfig(serial='emu', adb_path=str(adb)), runner=runner)
        assert device.metrics().density == 440

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 1920x1080\n'),
                                adb_ok(b'Physical density: 320\n'))
        device = AdbDevice(DeviceConfig(serial='emu', adb_path=str(adb)), runner=runner)
        assert device.metrics().density == 320


def check_adb_timeouts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        timeout = subprocess.TimeoutExpired(['adb'], ADB_TIMEOUT)

        runner = ScriptedRunner(timeout)
        device = AdbDevice(DeviceConfig(serial='emulator-5554', adb_path=str(adb)), runner=runner)
        expect_error(device.metrics, 'emulator-5554', 'get-state', f'{ADB_TIMEOUT:g}', error_type=DeviceError)
        assert runner.options[0]['timeout'] == ADB_TIMEOUT

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 320\n'), timeout)
        device = AdbDevice(DeviceConfig(serial='dev-tap', adb_path=str(adb), width=1280, height=720), runner=runner)
        device.metrics()
        expect_error(lambda: device.tap(10, 10), 'dev-tap', 'input tap', f'{ADB_TIMEOUT:g}', error_type=DeviceError)
        assert runner.options[-1]['timeout'] == ADB_TIMEOUT

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical density: 320\n'), timeout)
        device = AdbDevice(DeviceConfig(serial='dev-shot', adb_path=str(adb), width=1280, height=720), runner=runner)
        device.metrics()
        expect_error(device.frame, 'dev-shot', 'screencap', f'{ADB_TIMEOUT:g}', error_type=DeviceError)
        assert runner.options[-1]['timeout'] == ADB_TIMEOUT

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 1280x720\n'), timeout)
        device = AdbDevice(DeviceConfig(serial='dev-density', adb_path=str(adb)), runner=runner)
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
        'AdbDevice', 'Device', 'DeviceConfig', 'DeviceError', 'DeviceMetrics',
        'FakeDevice', 'Frame', 'Region', 'devices',
    }, '包入口只暴露设备网关的稳定接口'
    assert not hasattr(gateway, 'to_device_point'), '坐标换算属于适配器内部实现，不应从包入口暴露'
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')

        runner = ScriptedRunner(adb_ok(b'* daemon not running; starting now at tcp:5037\n'
                                       b'List of devices attached\nemulator-5554\tdevice model:Pixel_5\n'
                                       b'* daemon started successfully\n127.0.0.1:16384\toffline\n'
                                       b"adb server version (41) doesn't match this client (39); killing...\n"
                                       b'????????????\tno permissions (user in plugdev group)\n'))
        found = devices(str(adb), runner=runner)
        assert [(item.serial, item.state, item.model) for item in found] == [
            ('emulator-5554', 'device', 'Pixel 5'),
            ('127.0.0.1:16384', 'offline', None),
            ('????????????', 'no permissions', None),
        ], 'daemon/server 提示行不能被当成设备'
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
    expect_error(lambda: to_device_point(1, 1, 1080, 2400), '不支持的分辨率', error_type=DeviceError)


def check_adb_metrics() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')

        runner = ScriptedRunner(
            adb_ok(b'device\n'),
            adb_ok(b'Physical size: 1920x1080\nOverride size: 1280x720\n'),
            adb_ok(b'Physical density: 320\n'),
        )
        device = AdbDevice(DeviceConfig(serial='emulator-5554', adb_path=str(adb)), runner=runner)
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
        override = AdbDevice(DeviceConfig(serial='s', adb_path=str(adb), width=1600, height=900), runner=runner)
        metrics = override.metrics()
        assert (metrics.width, metrics.height, metrics.density) == (1600, 900, 240)
        assert all('size' not in call for call in runner.calls)


def check_adb_errors() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        config = DeviceConfig(serial='emulator-5554', adb_path=str(adb))

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

        runner = ScriptedRunner(adb_ok(b'device\n'), adb_ok(b'Physical size: 1080x2400\n'))
        device = AdbDevice(config, runner=runner)
        expect_error(device.metrics, '1080×2400', '不支持的分辨率', '16:9', error_type=DeviceError)


def check_adb_actions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adb = Path(directory) / 'adb.exe'
        adb.write_bytes(b'')
        config = DeviceConfig(serial='emulator-5554', adb_path=str(adb), width=1920, height=1080)

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
        config = DeviceConfig(serial='emulator-5554', adb_path=str(adb))

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
            DeviceConfig(serial='emulator-5554', adb_path=str(adb), width=1600, height=900), runner=runner,
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
    FakeDevice([first], config=DeviceConfig(serial='match', width=1280, height=720))
    expect_error(
        lambda: FakeDevice([first], config=DeviceConfig(serial='mismatch', width=640, height=360)),
        '配置分辨率', '1280×720', error_type=DeviceError,
    )
    square = Frame(image=make_png(1000, 1000), width=1000, height=1000, captured_at=4.0)
    expect_error(lambda: FakeDevice([square]), '不支持的分辨率', '16:9', error_type=DeviceError)

    empty = FakeDevice(config=DeviceConfig(serial='tap-only', width=1280, height=720))
    assert empty.metrics().serial == 'tap-only'
    expect_error(empty.frame, '帧', error_type=DeviceError)


def check_fake_actions() -> None:
    device = FakeDevice(
        [Frame(image=make_png(1920, 1080), width=1920, height=1080, captured_at=1.0)],
        config=DeviceConfig(serial='replay', region='JP'),
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


def check_optimized_guard() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, '-O', '-m', 'executor.device', '--self-test'],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 1, 'python -O 下自检必须显式失败，不能空跑通过'
    assert 'python -O' in result.stderr, f'应说明 -O 不可用：{result.stderr}'


CHECKS = [
    ('区服解析', check_region),
    ('设备配置校验', check_config),
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
    ('python -O 防护', check_optimized_guard),
]


def self_test() -> int:
    if sys.flags.optimize:
        print('设备网关自检不能在 python -O 下运行：断言会被剥离，请使用普通 python 运行', file=sys.stderr)
        return 1
    failures = 0
    for name, check in CHECKS:
        try:
            check()
        except Exception as error:  # 自检要跑完全部用例，一次报告所有失败
            failures += 1
            print(f'失败 {name}：{type(error).__name__}: {error}')
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m executor.device', description='设备网关自检与设备列表')
    parser.add_argument('--self-test', action='store_true', help='运行不依赖真机的自检')
    parser.add_argument('--list', action='store_true', help='列出 adb 设备（需要 adb 与在线模拟器）')
    parser.add_argument('--adb', help='adb 路径；缺省按设备配置 → HUGOFGO_ADB → PATH → 常见模拟器路径查找')
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.list:
        return list_command(args.adb)
    parser.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
