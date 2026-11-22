"""scrcpy 固定服务端的 ADB 生命周期、视频 relay 与控制 socket。"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from executor.device.adb import ADB_TIMEOUT, AdbDevice, resolve_adb_path
from executor.device.base import DeviceError
from executor.device.config import DeviceConfig, DeviceMetrics
from executor.device.scrcpy_resources import verify_scrcpy_server

REMOTE_SERVER = '/data/local/tmp/hugofgo-scrcpy-server-3.3.3.jar'
SCRCPY_VERSION = '3.3.3'


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ''
    return data.decode('utf-8', errors='replace') if isinstance(data, bytes) else data


class _VideoRelay:
    """移除 scrcpy 握手字节后，把原始 H.264 转发给本机 OpenCV/FFmpeg。"""

    def __init__(self, video: socket.socket):
        self.video = video
        self.client: socket.socket | None = None
        self.closed = False
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(1.0)
        self.video_url = f'tcp://127.0.0.1:{self.listener.getsockname()[1]}'
        self.thread = threading.Thread(target=self._pump, name='scrcpy-video-relay', daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        try:
            self.client, _ = self.listener.accept()
            self.client.settimeout(1.0)
            while not self.closed:
                try:
                    data = self.video.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    return
                self.client.sendall(data)
        except (OSError, ValueError):
            return

    def close(self) -> None:
        self.closed = True
        for stream in (self.client, self.video, self.listener):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self.thread.join(timeout=1.0)


class ScrcpySession:
    def __init__(self, config: DeviceConfig, *, runner=subprocess.run, popen=subprocess.Popen,
                 socket_factory=socket.create_connection, relay_factory=_VideoRelay):
        self.config = config
        self.scid = f'{secrets.randbelow(0x100000000) & 0x7fffffff:08x}'
        self.socket_name = f'scrcpy_{self.scid}'
        self._runner = runner
        self._popen = popen
        self._socket_factory = socket_factory
        self._relay_factory = relay_factory
        self.adb_path = resolve_adb_path(config.adb_path)
        self.process = None
        self.video = None
        self.control = None
        self.relay = None
        self.ports: list[int] = []
        self.closed = False
        self._logs = deque(maxlen=30)
        self._log_thread = None
        try:
            self._start()
        except DeviceError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise DeviceError(f'无法建立 scrcpy 会话：设备 {config.serial}：{error}') from error

    def _start(self) -> None:
        configured = getattr(self.config, 'scrcpy_server_path', None) or os.environ.get('HUGOFGO_SCRCPY_SERVER')
        resource_dir = Path(configured).expanduser().resolve().parent if configured else None
        server = verify_scrcpy_server(resource_dir)
        if configured and server.resolve() != Path(configured).expanduser().resolve():
            raise DeviceError(f'配置的 scrcpy 服务端 {configured} 与固定清单文件名不一致；应使用 {server}')
        adb = AdbDevice(self.config, runner=self._runner)
        self.metrics = adb.metrics()
        self._run('push', str(server), REMOTE_SERVER)
        argv = [self.adb_path, '-s', self.config.serial, 'shell', f'CLASSPATH={REMOTE_SERVER}',
                'app_process', '/', 'com.genymobile.scrcpy.Server', SCRCPY_VERSION, f'scid={self.scid}',
                'log_level=debug', 'video=true', 'audio=false', 'control=true', 'tunnel_forward=true',
                'send_device_meta=false', 'send_codec_meta=false', 'send_frame_meta=false',
                'max_size=0', 'video_bit_rate=8000000', 'max_fps=30', 'cleanup=true',
                'power_on=false', 'power_off_on_close=false', 'clipboard_autosync=false']
        try:
            self.process = self._popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
        except OSError as error:
            raise DeviceError(f'无法启动 scrcpy 服务端（{self.adb_path}）：{error}') from error
        if self.process.stdout is not None:
            self._log_thread = threading.Thread(target=self._read_logs, name='scrcpy-server-log', daemon=True)
            self._log_thread.start()
        video_port = self._forward()
        control_port = self._forward()
        self.video = self._connect_video(video_port)
        self.video.settimeout(1.0)
        self.control = self._connect(control_port)
        self.control.settimeout(10.0)
        self.relay = self._relay_factory(self.video)
        self.video_url = self.relay.video_url
        self.ensure_alive()

    def _read_logs(self) -> None:
        try:
            for line in iter(self.process.stdout.readline, b''):
                self._logs.append(_text(line).strip())
        except (OSError, ValueError):
            pass

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        argv = [self.adb_path, '-s', self.config.serial, *args]
        try:
            result = self._runner(argv, capture_output=True, check=False, timeout=ADB_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            raise DeviceError(f'scrcpy 的 adb 命令超时：设备 {self.config.serial}，{args}') from error
        if result.returncode != 0:
            detail = _text(result.stderr).strip() or _text(result.stdout).strip() or '无输出'
            raise DeviceError(f'scrcpy 的 adb 命令失败：设备 {self.config.serial}，{args}；{detail}')
        return result

    def _forward(self) -> int:
        result = self._run('forward', 'tcp:0', f'localabstract:{self.socket_name}')
        text = _text(result.stdout).strip()
        try:
            port = int(text)
        except ValueError:
            raise DeviceError(f'无法解析 scrcpy adb forward 端口：设备 {self.config.serial}，输出 {text!r}') from None
        self.ports.append(port)
        return port

    def _connect(self, port: int) -> socket.socket:
        deadline = time.monotonic() + 10.0
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise DeviceError(self._exit_message())
            try:
                return self._socket_factory(('127.0.0.1', port), 1.0)
            except OSError as error:
                last_error = error
                time.sleep(0.1)
        raise DeviceError(f'连接 scrcpy 本地端口 {port} 超时：设备 {self.config.serial}：{last_error}')

    def _connect_video(self, port: int) -> socket.socket:
        deadline = time.monotonic() + 10.0
        last = b''
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise DeviceError(self._exit_message())
            try:
                video = self._socket_factory(('127.0.0.1', port), 1.0)
                last = video.recv(1)
                if last == b'\x00':
                    return video
                video.close()
            except OSError:
                pass
            time.sleep(0.1)
        detail = '；'.join(self._logs) or '服务端无日志'
        raise DeviceError(f'scrcpy 视频握手超时：设备 {self.config.serial}，最近响应 {last!r}，'
                          f'服务端退出码 {self.process.poll()}；{detail}')

    def _exit_message(self) -> str:
        detail = '\n'.join(self._logs) or '服务端无日志'
        return (f'scrcpy 服务端已退出：设备 {self.config.serial}，退出码 {self.process.poll()}，'
                f'会话资源 {REMOTE_SERVER}；{detail}')

    def ensure_alive(self) -> None:
        if self.closed:
            raise DeviceError(f'scrcpy 会话已关闭：设备 {self.config.serial}')
        if self.process is None or self.process.poll() is not None:
            raise DeviceError(self._exit_message())

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.relay is not None:
            self.relay.close()
            self.relay = None
        elif self.video is not None:
            try:
                self.video.close()
            except OSError:
                pass
        if self.control is not None:
            try:
                self.control.close()
            except OSError:
                pass
        for port in self.ports:
            try:
                self._run('forward', '--remove', f'tcp:{port}')
            except DeviceError:
                pass
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=3)
                except Exception:
                    pass
