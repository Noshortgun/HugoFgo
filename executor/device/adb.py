"""实机适配器：通过 adb 截图与输入，坐标从 1280×720 基准换算到设备像素。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from executor.device.base import DeviceError, DeviceMetrics, Frame, to_device_point
from executor.device.config import DeviceConfig
from executor.device.png import png_size

Runner = Callable[..., subprocess.CompletedProcess]
_SIZE = re.compile(r'(\d+)x(\d+)')

ADB_TIMEOUT = 30.0


def _adb_missing(for_list: bool) -> DeviceError:
    if for_list:
        return DeviceError('未找到 adb：请安装 Android platform-tools，或在 --list 时用 --adb 指定 adb.exe 路径，'
                           '或设置环境变量 HUGOFGO_ADB 指向 adb.exe')
    return DeviceError('未找到 adb：请安装 Android platform-tools，或在 DeviceConfig 中设置 adb_path，'
                       '或设置环境变量 HUGOFGO_ADB 指向 adb.exe')


def _timeout_error(command: str, seconds: float, serial: str | None = None) -> DeviceError:
    who = f'设备 {serial} 的 adb 命令' if serial else 'adb 命令'
    return DeviceError(f'{who}超时（{seconds:g} 秒）：{command}')


def _command_failed(command: str, result: subprocess.CompletedProcess) -> DeviceError:
    stderr = _text(result.stderr).strip() or _text(result.stdout).strip() or '无输出'
    return DeviceError(f'adb 命令失败（退出码 {result.returncode}）：{command}；{stderr}')


def _common_candidates() -> list[str]:
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    candidates = [r'C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe',
                  r'C:\Program Files\Netease\MuMu Player 12\shell\adb.exe',
                  r'C:\LDPlayer\LDPlayer9\adb.exe',
                  r'C:\LDPlayer\LDPlayer14\adb.exe',
                  r'D:\leidian\LDPlayer14\adb.exe']
    if local_app_data:
        candidates.insert(0, str(Path(local_app_data) / 'Android/Sdk/platform-tools/adb.exe'))
    return candidates


def resolve_adb_path(explicit: str | None = None, *, env: Mapping[str, str] | None = None,
                     which: Callable[[str], str | None] = shutil.which,
                     candidates: Sequence[str] | None = None, for_list: bool = False) -> str:
    """按配置路径 → HUGOFGO_ADB → PATH → 常见模拟器路径的顺序定位 adb。"""
    if explicit and explicit.strip():
        path = Path(explicit.strip()).expanduser()
        if path.is_file():
            return str(path)
        raise DeviceError(f'配置的 adb 路径不存在：{explicit}；请检查 DeviceConfig 的 adb_path，'
                          '--list 场景可用 --adb 指定')

    env = os.environ if env is None else env
    from_env = env.get('HUGOFGO_ADB', '').strip()
    if from_env:
        if Path(from_env).expanduser().is_file():
            return from_env
        raise DeviceError(f'环境变量 HUGOFGO_ADB 指向的 adb 不存在：{from_env}；请修正或删除该变量')

    found = which('adb')
    if found:
        return found

    for candidate in _common_candidates() if candidates is None else candidates:
        if Path(candidate).is_file():
            return candidate

    raise _adb_missing(for_list)


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ''
    if isinstance(data, str):
        return data
    return data.decode('utf-8', errors='replace')


def parse_size(text: str) -> tuple[int, int] | None:
    """解析 wm size；模拟器常上报 Override size，优先使用它。"""
    physical = overridden = None
    for line in text.splitlines():
        match = _SIZE.search(line)
        if not match:
            continue
        size = (int(match.group(1)), int(match.group(2)))
        if 'Override' in line:
            overridden = size
        else:
            physical = size
    return overridden or physical


def parse_density(text: str) -> int | None:
    """解析 wm density；模拟器可能同时上报 Physical 与 Override，优先 Override。"""
    physical = overridden = fallback = None
    for line in text.splitlines():
        numbers = re.findall(r'\d+', line)
        if not numbers:
            continue
        value = int(numbers[-1])
        if 'Override density' in line:
            overridden = value
        elif 'Physical density' in line:
            physical = value
        elif fallback is None:
            fallback = value
    return overridden if overridden is not None else physical if physical is not None else fallback


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    state: str
    model: str | None


def devices(adb_path: str | None = None, *, runner: Runner = subprocess.run) -> list[DeviceInfo]:
    """列出 adb 设备；这是架构 2.2 的设备发现入口，需要 adb 可用。"""
    adb = resolve_adb_path(adb_path, for_list=True)
    argv = [adb, 'devices', '-l']
    command = ' '.join(argv)
    try:
        result = runner(argv, capture_output=True, check=False, timeout=ADB_TIMEOUT)
    except subprocess.TimeoutExpired as error:
        seconds = error.timeout if error.timeout is not None else ADB_TIMEOUT
        raise _timeout_error(command, seconds) from error
    except OSError as error:
        raise DeviceError(f'无法启动 adb（{adb}）：{error}；请用 --adb 指定 adb.exe 路径') from error
    if result.returncode != 0:
        raise _command_failed(command, result)

    found: list[DeviceInfo] = []
    for line in _text(result.stdout).splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        model = next((part[len('model:'):].replace('_', ' ') for part in parts[2:] if part.startswith('model:')), None)
        found.append(DeviceInfo(serial=parts[0], state=parts[1], model=model))
    return found


class AdbDevice:
    def __init__(self, config: DeviceConfig, *, runner: Runner = subprocess.run):
        self.config = config
        self.adb_path = resolve_adb_path(config.adb_path)
        self._runner = runner
        self._metrics: DeviceMetrics | None = None

    def metrics(self) -> DeviceMetrics:
        if self._metrics is None:
            self._metrics = self._read_metrics()
        return self._metrics

    def frame(self) -> Frame:
        data = self._run('exec-out', 'screencap', '-p').stdout or b''
        size = png_size(data)
        if size is None:
            raise DeviceError(f'设备 {self.config.serial} 截图不是有效的 PNG（{len(data)} 字节）；'
                              '请确认模拟器画面正常后重试')
        width, height = size
        metrics = self.metrics()
        if (width, height) != (metrics.width, metrics.height):
            raise DeviceError(f'设备 {self.config.serial} 截图分辨率 {width}×{height} 与设备分辨率 '
                              f'{metrics.width}×{metrics.height} 不一致；请重新读取设备信息，'
                              '或在 DeviceConfig 中设置 width/height 校正')
        return Frame(image=data, width=width, height=height, captured_at=time.time())

    def tap(self, x: int, y: int) -> None:
        metrics = self.metrics()
        px, py = to_device_point(x, y, metrics.width, metrics.height)
        self._run('shell', 'input', 'tap', str(px), str(py))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None:
        metrics = self.metrics()
        start = to_device_point(x1, y1, metrics.width, metrics.height)
        end = to_device_point(x2, y2, metrics.width, metrics.height)
        self._run('shell', 'input', 'swipe', str(start[0]), str(start[1]), str(end[0]), str(end[1]),
                  str(int(duration_ms)))

    def _read_metrics(self) -> DeviceMetrics:
        state = _text(self._run('get-state').stdout).strip()
        if state != 'device':
            raise DeviceError(f'设备 {self.config.serial} 当前状态为 {state!r}；'
                              '请启动模拟器或完成授权后重试（adb get-state）')

        width, height = self.config.width, self.config.height
        if width is None or height is None:
            output = _text(self._run('shell', 'wm', 'size').stdout)
            size = parse_size(output)
            if size is None:
                raise DeviceError(f'无法从设备 {self.config.serial} 读取分辨率（wm size 输出：'
                                  f'{output.strip() or "空"}）；请在 DeviceConfig 中设置 width/height，避免盲点')
            width, height = size

        density = parse_density(_text(self._run('shell', 'wm', 'density').stdout))

        return DeviceMetrics(serial=self.config.serial, width=width, height=height,
                             density=density, region=self.config.region)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        argv = [self.adb_path, '-s', self.config.serial, *args]
        command = ' '.join(argv)
        try:
            result = self._runner(argv, capture_output=True, check=False, timeout=ADB_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            seconds = error.timeout if error.timeout is not None else ADB_TIMEOUT
            raise _timeout_error(command, seconds, self.config.serial) from error
        except OSError as error:
            raise DeviceError(f'无法启动 adb（{self.adb_path}）：{error}；'
                              '请检查 DeviceConfig 的 adb_path 或环境变量 HUGOFGO_ADB') from error
        if result.returncode != 0:
            raise _command_failed(command, result)
        return result
