"""scrcpy 持续 H.264 帧与同一服务会话的控制输入通道。"""
from __future__ import annotations

import struct
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from executor.device.base import DeviceError, Frame, FrameFormat, to_device_point, validate_resolution
from executor.device.config import DeviceConfig, DeviceMetrics


@dataclass(frozen=True)
class _LatestFrame:
    sequence: int
    pixels: bytes
    captured_at: float
    received_at: float
    capture_ms: float


class ScrcpyDevice:
    def __init__(self, config: DeviceConfig, *, session_factory: Callable | None = None,
                 capture_factory: Callable | None = None, frame_timeout: float = 3.0,
                 max_frame_age: float = 1.0):
        self.config = config
        self.session_id = uuid.uuid4().hex
        self.frame_timeout = frame_timeout
        self.max_frame_age = max_frame_age
        self._condition = threading.Condition()
        self._latest: _LatestFrame | None = None
        self._delivered = 0
        self._failure: DeviceError | None = None
        self._closed = False
        self._input_lock = threading.Lock()
        if session_factory is None:
            from executor.device.scrcpy_runtime import ScrcpySession
            session_factory = ScrcpySession
        try:
            self._session = session_factory(config)
            factory = capture_factory or self._open_capture
            self._capture = factory(self._session.video_url)
        except Exception as error:
            session = getattr(self, '_session', None)
            if session is not None:
                session.close()
                session.control.close()
            raise DeviceError(f'scrcpy 启动失败：设备 {config.serial}：{error}') from error
        self._metrics = getattr(self._session, 'metrics', None) or DeviceMetrics(
            serial=config.serial, width=config.width or 0, height=config.height or 0,
            density=None, region=config.region)
        validate_resolution(self._metrics.width, self._metrics.height, subject=f'scrcpy 设备 {config.serial}')
        self._reader = threading.Thread(target=self._read_frames, name=f'scrcpy-{config.serial}', daemon=True)
        self._reader.start()

    @staticmethod
    def _open_capture(url: str):
        capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not capture.isOpened():
            capture.release()
            raise DeviceError(f'scrcpy 启动失败：无法打开持续视频流 {url}')
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return capture

    def metrics(self) -> DeviceMetrics:
        return self._metrics

    def _read_frames(self) -> None:
        while True:
            started = time.perf_counter()
            try:
                ok, image = self._capture.read()
            except Exception as error:
                self._fail(f'scrcpy 解码中断：设备 {self.config.serial}：{error}')
                return
            if self._closed:
                return
            if not ok or image is None:
                self._fail(f'scrcpy 视频流中断或解码失败：设备 {self.config.serial}；运行不会在旧帧上继续')
                return
            if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
                self._fail(f'scrcpy 解码帧格式无效：设备 {self.config.serial}，shape={image.shape}，dtype={image.dtype}')
                return
            height, width = image.shape[:2]
            if (width, height) != (self._metrics.width, self._metrics.height):
                self._fail(f'scrcpy 帧尺寸从 {self._metrics.width}×{self._metrics.height} 变为 {width}×{height}；'
                           '为避免坐标错位已停止')
                return
            received = time.monotonic()
            captured_at = time.time()
            capture_ms = (time.perf_counter() - started) * 1000.0
            pixels = np.ascontiguousarray(image).tobytes()
            with self._condition:
                sequence = 1 if self._latest is None else self._latest.sequence + 1
                self._latest = _LatestFrame(sequence, pixels, captured_at, received, capture_ms)
                self._condition.notify_all()

    def _fail(self, message: str) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = DeviceError(message)
            self._condition.notify_all()

    def _ensure_healthy(self) -> None:
        if self._closed:
            raise DeviceError(f'scrcpy 会话已关闭：设备 {self.config.serial}')
        if self._failure is not None:
            raise self._failure
        try:
            self._session.ensure_alive()
        except DeviceError as error:
            self._fail(str(error))
            raise self._failure from error

    def frame(self) -> Frame:
        deadline = time.monotonic() + self.frame_timeout
        with self._condition:
            while True:
                if self._failure is not None:
                    raise self._failure
                if self._closed:
                    raise DeviceError(f'scrcpy 会话已关闭：设备 {self.config.serial}')
                latest = self._latest
                if latest is not None and latest.sequence > self._delivered:
                    age = time.monotonic() - latest.received_at
                    if age > self.max_frame_age:
                        raise DeviceError(f'scrcpy 帧已过期：设备 {self.config.serial}，帧 {latest.sequence} '
                                          f'距今 {age * 1000:.0f} ms；不会用于新动作确认')
                    self._delivered = latest.sequence
                    return Frame(image=latest.pixels, width=self._metrics.width, height=self._metrics.height,
                                 captured_at=latest.captured_at, format=FrameFormat.BGR24,
                                 capture_ms=latest.capture_ms, frame_id=latest.sequence, session_id=self.session_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeviceError(f'scrcpy 等待新帧超时（{self.frame_timeout:g} 秒）：设备 {self.config.serial}，'
                                      f'会话 {self.session_id}，上一帧 {self._delivered}')
                self._condition.wait(remaining)

    @staticmethod
    def _touch(action: int, x: int, y: int, width: int, height: int) -> bytes:
        return struct.pack('>BBqIIHHHII', 0, action, 0, x, y, width, height,
                           0xFFFF if action != 1 else 0, 1, 0 if action == 1 else 1)

    def _send(self, packet: bytes) -> None:
        self._ensure_healthy()
        try:
            self._session.control.sendall(packet)
        except OSError as error:
            self._fail(f'scrcpy 控制通道中断：设备 {self.config.serial}，会话 {self.session_id}：{error}')
            raise self._failure from error

    def tap(self, x: int, y: int) -> None:
        metrics = self.metrics()
        px, py = to_device_point(x, y, metrics.width, metrics.height)
        with self._input_lock:
            self._send(self._touch(0, px, py, metrics.width, metrics.height))
            self._send(self._touch(1, px, py, metrics.width, metrics.height))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None:
        metrics = self.metrics()
        start = to_device_point(x1, y1, metrics.width, metrics.height)
        end = to_device_point(x2, y2, metrics.width, metrics.height)
        with self._input_lock:
            self._send(self._touch(0, *start, metrics.width, metrics.height))
            if duration_ms > 0:
                time.sleep(duration_ms / 1000.0)
            self._send(self._touch(2, *end, metrics.width, metrics.height))
            self._send(self._touch(1, *end, metrics.width, metrics.height))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._capture.release()
        finally:
            self._session.close()
            self._session.control.close()
            with self._condition:
                self._condition.notify_all()
