"""LDPlayer 窗口原始帧通道（可选适配器）：跳过 adb 子进程与 PNG 编解码，直接取游戏画面原始像素。

适用范围：Windows（10/11）+ LDPlayer 9/14（进程 dnplayer.exe）。其他模拟器不因此获得支持，
仍走 adb 通道；本模块不发送任何输入，也不推断游戏状态。

通道选择：`DeviceConfig(channel='ldplayer-window')` + `open_device(config)`；默认仍是 adb。
选择本通道后不会再退回 adb——找不到窗口、窗口最小化、截图或拷贝失败都会抛 `DeviceError`
（错误消息带原因代码，`CaptureStats.reasons` 同时累计），由调用方决定是否改用 adb。

采集方式：枚举 LDPlayer 顶层窗口，取类名 `RenderWindow` 的子窗口，用
`PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT)` 把客户区画进内存 DC，再用 24 位 DIB
取出自上而下的 BGR24 原始像素。1280×720 基准坐标不变，`metrics()` 上报窗口客户区尺寸。

实机已知限制（2026-11-20，LDPlayer 14，1280×720，窗口客户区 1340×779）：
- 父窗口客户区比「工具栏 60 px + 画面 720 px」少 1 px，`RenderWindow` 的最后一行被父窗口裁掉。
  首次采集会自动把 LDPlayer 窗口加高被裁的像素（最多 16 px），并记录修正次数；修正后末行与 adb 一致。
  窗口最大化或被系统限制导致加高失败时抛 `clipped`，不把黑帧当有效画面。
- 最小化时 `PrintWindow` 返回 0 且像素全黑：明确报错，不承诺后台截图。
- 窗口被其他窗口遮挡（但未最小化）时截图仍可用，已实机验证。
- 多开（多个 LDPlayer 窗口）不受支持：会明确报错而不是猜哪个实例。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from typing import Mapping

from executor.device.base import DeviceError, Frame, FrameFormat, validate_resolution
from executor.device.config import DeviceConfig, DeviceMetrics

LDPLAYER_PROCESS = 'dnplayer.exe'
LDPLAYER_MAIN_CLASS = 'LDPlayerMainFrame'
RENDER_WINDOW_CLASS = 'RenderWindow'

PW_CLIENTONLY = 0x0001
PW_RENDERFULLCONTENT = 0x0002
PRINT_WINDOW_FLAGS = PW_CLIENTONLY | PW_RENDERFULLCONTENT
BI_RGB = 0
DIB_RGB_COLORS = 0
PER_MONITOR_AWARE_V2 = -4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH_LONG = 1024
MAX_WINDOW_ADJUST = 16
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

REASON_NO_WINDOW = 'no-window'
REASON_NO_RENDER_WINDOW = 'no-render-window'
REASON_MULTI_INSTANCE = 'multi-instance'
REASON_WIN32 = 'win32'
REASON_MINIMIZED = 'minimized'
REASON_PRINT_WINDOW = 'print-window'
REASON_COPY = 'copy'
REASON_SIZE = 'size'
REASON_EMPTY = 'empty'
REASON_BLACK = 'black'
REASON_CLIPPED = 'clipped'


class CaptureFailure(DeviceError):
    """带原因代码的采集失败；reason 供运行日志按类统计（见模块顶部的 REASON_* 常量）。"""

    def __init__(self, reason: str, message: str):
        super().__init__(f'{message}（原因 {reason}）')
        self.reason = reason


@dataclass
class CaptureStats:
    """采集统计：尝试、成功、失败次数，最近一次耗时与最近一次失败原因，以及按原因累计的失败次数。

    last_reason 是最近一次采集失败的原因（之后成功也不会清空），按次日志请直接看调用方拿到的
    DeviceError；reasons 是按原因代码累计的失败次数，便于运行结束后判断故障类型。
    """

    attempts: int = 0
    frames: int = 0
    failures: int = 0
    window_adjustments: int = 0
    last_ms: float | None = None
    last_reason: str | None = None
    reasons: dict[str, int] = field(default_factory=dict)

    def record(self, capture_ms: float) -> None:
        self.attempts += 1
        self.frames += 1
        self.last_ms = capture_ms

    def fail(self, reason: str, capture_ms: float) -> None:
        self.attempts += 1
        self.failures += 1
        self.last_ms = capture_ms
        self.last_reason = reason
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG), ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [('bmiHeader', _BitmapInfoHeader), ('bmiColors', wintypes.DWORD * 3)]


class Win32Api:
    """user32/gdi32 的最小绑定。每个函数都声明 argtypes/restype，句柄按 8 字节指针收发。"""

    def __init__(self) -> None:
        if sys.platform != 'win32':
            raise DeviceError('ldplayer-window 通道仅支持 Windows + LDPlayer；'
                              '当前系统不是 Windows，请改用 adb 通道')
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
        self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._bind()
        self._set_dpi_awareness()

    def _bind(self) -> None:
        user32, gdi32, kernel32 = self._user32, self._gdi32, self._kernel32
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
        user32.EnumChildWindows.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND,
                                           ctypes.POINTER(wintypes.POINT), wintypes.UINT]
        user32.MapWindowPoints.restype = ctypes.c_int
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL

        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                    ctypes.c_void_p, ctypes.POINTER(_BitmapInfo), wintypes.UINT]
        gdi32.GetDIBits.restype = ctypes.c_int

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

    def _set_dpi_awareness(self) -> None:
        """按物理像素读窗口尺寸；进程已设置过其他感知级别时这里会失败，尺寸校验会随后报错。"""
        try:
            self._user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(PER_MONITOR_AWARE_V2))
        except (AttributeError, OSError):
            pass

    def enum_windows(self) -> list[int]:
        found: list[int] = []

        def visit(hwnd, _):
            found.append(int(hwnd))
            return True

        self._user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
        return found

    def enum_children(self, parent: int) -> list[int]:
        found: list[int] = []

        def visit(hwnd, _):
            found.append(int(hwnd))
            return True

        self._user32.EnumChildWindows(parent,
                                      ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
        return found

    def class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self._user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def window_title(self, hwnd: int) -> str:
        length = self._user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 2)
        self._user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def process_name(self, hwnd: int) -> str | None:
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(MAX_PATH_LONG)
            size = wintypes.DWORD(len(buffer))
            if not self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            self._kernel32.CloseHandle(handle)

    def is_window(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindow(hwnd))

    def is_iconic(self, hwnd: int) -> bool:
        return bool(self._user32.IsIconic(hwnd))

    def client_size(self, hwnd: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise DeviceError(f'窗口 {hwnd} 已不可用：GetClientRect 失败；请确认 LDPlayer 仍在运行')
        return int(rect.right - rect.left), int(rect.bottom - rect.top)

    def get_dc(self, hwnd: int) -> int:
        return int(self._user32.GetDC(hwnd) or 0)

    def release_dc(self, hwnd: int, hdc: int) -> None:
        self._user32.ReleaseDC(hwnd, hdc)

    def window_size(self, hwnd: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise DeviceError(f'窗口 {hwnd} 已不可用：GetWindowRect 失败；请确认 LDPlayer 仍在运行')
        return int(rect.right - rect.left), int(rect.bottom - rect.top)

    def map_window_points(self, from_hwnd: int, to_hwnd: int,
                          points: list[tuple[int, int]]) -> list[tuple[int, int]]:
        mapped = (wintypes.POINT * len(points))(*[wintypes.POINT(x, y) for x, y in points])
        self._user32.MapWindowPoints(from_hwnd, to_hwnd, mapped, len(points))
        return [(int(point.x), int(point.y)) for point in mapped]

    def set_window_size(self, hwnd: int, width: int, height: int) -> bool:
        return bool(self._user32.SetWindowPos(
            hwnd, wintypes.HWND(0), 0, 0, width, height,
            SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
        ))

    def create_memory_dc(self, hdc: int) -> int:
        return int(self._gdi32.CreateCompatibleDC(hdc) or 0)

    def delete_dc(self, hdc: int) -> None:
        self._gdi32.DeleteDC(hdc)

    def create_bitmap(self, hdc: int, width: int, height: int) -> int:
        return int(self._gdi32.CreateCompatibleBitmap(hdc, width, height) or 0)

    def select_object(self, hdc: int, obj: int) -> int:
        return int(self._gdi32.SelectObject(hdc, obj) or 0)

    def delete_object(self, obj: int) -> None:
        self._gdi32.DeleteObject(obj)

    def print_window(self, hwnd: int, hdc: int) -> bool:
        return bool(self._user32.PrintWindow(hwnd, hdc, PRINT_WINDOW_FLAGS))

    def bitmap_bits(self, hdc: int, bitmap: int, width: int, height: int) -> tuple[bytes, int]:
        """取 24 位 DIB 像素（自上而下、BGR 顺序）与 GetDIBits 实际写入的行数。"""
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 24
        info.bmiHeader.biCompression = BI_RGB
        stride = (width * 3 + 3) // 4 * 4
        buffer = ctypes.create_string_buffer(stride * height)
        lines = int(self._gdi32.GetDIBits(hdc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS))
        if lines <= 0:
            return b'', lines
        raw = ctypes.string_at(buffer, stride * height)
        if stride == width * 3:
            return raw, lines
        return b''.join(raw[row * stride:row * stride + width * 3] for row in range(lines)), lines


@dataclass(frozen=True)
class LdplayerWindow:
    """LDPlayer 主窗口与它的 RenderWindow 子窗口句柄。"""

    parent: int
    render: int
    title: str


def _is_ldplayer_frame(api: Win32Api, hwnd: int) -> bool:
    if api.class_name(hwnd) == LDPLAYER_MAIN_CLASS:
        return True
    process = api.process_name(hwnd) or ''
    return process.replace('\\', '/').rsplit('/', 1)[-1].lower() == LDPLAYER_PROCESS


def _render_child(api: Win32Api, parent: int) -> int | None:
    for child in api.enum_children(parent):
        if api.class_name(child) == RENDER_WINDOW_CLASS:
            return child
    return None


def find_ldplayer_window(api: Win32Api) -> LdplayerWindow:
    """定位唯一的 LDPlayer 主窗口及其 RenderWindow 子窗口；找不到或有多个时明确报错。

    dnplayer.exe 还会创建通知、输入法、GDI+ 等辅助顶层窗口，所以只把「含 RenderWindow 子窗口」
    的窗口当成游戏主窗口；这样多开计数也不会被辅助窗口污染。
    """
    candidates = [hwnd for hwnd in api.enum_windows() if _is_ldplayer_frame(api, hwnd)]
    if not candidates:
        raise CaptureFailure(
            REASON_NO_WINDOW,
            '未找到 LDPlayer 窗口：请启动 LDPlayer 并保持主窗口存在（ldplayer-window 只支持 Windows + LDPlayer；'
            '其他模拟器请用 adb 通道）',
        )
    windows = [(parent, _render_child(api, parent)) for parent in candidates]
    found = [(parent, render) for parent, render in windows if render is not None]
    if not found:
        titled = next((hwnd for hwnd in candidates if api.window_title(hwnd)), candidates[0])
        raise CaptureFailure(REASON_NO_RENDER_WINDOW,
                             f'LDPlayer 窗口 {api.window_title(titled)!r} 没有 RenderWindow 子窗口；'
                             '请确认是 LDPlayer 9/14，且模拟器已完成启动')
    if len(found) > 1:
        titles = '、'.join(repr(api.window_title(parent)) for parent, _ in found)
        raise CaptureFailure(REASON_MULTI_INSTANCE,
                             f'检测到 {len(found)} 个 LDPlayer 窗口（{titles}）；多开不受支持，请只保留一个实例')
    parent, render = found[0]
    return LdplayerWindow(parent=parent, render=render, title=api.window_title(parent))


class LdplayerWindowDevice:
    """LDPlayer RenderWindow 原始帧适配器：只产出 Frame，不发送输入。

    由 `open_device()` 依据 `DeviceConfig.channel` 构造；坐标仍是 1280×720 基准，
    `metrics()` 上报窗口客户区尺寸（实机为 1280×720）。
    """

    def __init__(self, config: DeviceConfig, *, api: Win32Api | None = None):
        self.config = config
        self._api = api if api is not None else Win32Api()
        self._window: LdplayerWindow | None = None
        self._metrics: DeviceMetrics | None = None
        self._stats = CaptureStats()

    def metrics(self) -> DeviceMetrics:
        if self._metrics is None:
            window = self._window_handle()
            width, height = self._api.client_size(window.render)
            self._metrics = self._make_metrics(width, height)
        return self._metrics

    def stats(self) -> CaptureStats:
        """采集统计快照（含每帧耗时与失败原因），供运行日志写入。"""
        return replace(self._stats, reasons=dict(self._stats.reasons))

    def frame(self) -> Frame:
        started = time.perf_counter()
        try:
            frame = self._capture_frame(started)
        except DeviceError as error:
            reason = getattr(error, 'reason', REASON_WIN32)
            self._stats.fail(reason, _elapsed_ms(started))
            raise
        self._stats.record(frame.capture_ms or 0.0)
        return frame

    def tap(self, x: int, y: int) -> None:
        raise DeviceError(f'ldplayer-window 通道只提供截图，不支持输入（tap {x},{y}）；'
                          '输入请使用同一台模拟器的 adb 通道')

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None:
        raise DeviceError(f'ldplayer-window 通道只提供截图，不支持输入（swipe {x1},{y1}→{x2},{y2}）；'
                          '输入请使用同一台模拟器的 adb 通道')

    def _capture_frame(self, started: float) -> Frame:
        window = self._window_handle()
        if self._api.is_iconic(window.parent):
            raise CaptureFailure(REASON_MINIMIZED,
                                 f'LDPlayer 窗口 {window.title!r} 已最小化：最小化时 PrintWindow 返回空帧，'
                                 '请还原窗口后重试（本通道不承诺后台截图）')
        metrics = self.metrics()
        width, height = self._api.client_size(window.render)
        if (width, height) != (metrics.width, metrics.height):
            raise CaptureFailure(REASON_SIZE,
                                 f'RenderWindow 客户区已从 {metrics.width}×{metrics.height} 变为 {width}×{height}；'
                                 '窗口被缩放或模拟器分辨率变了，请重新读取设备信息')
        window = self._ensure_visible(window)
        pixels = self._capture_pixels(window.render, width, height)
        self._check_pixels(pixels, width, height)
        return Frame(image=pixels, width=width, height=height, captured_at=time.time(),
                     format=FrameFormat.BGR24, capture_ms=_elapsed_ms(started))

    def _capture_pixels(self, hwnd: int, width: int, height: int) -> bytes:
        api = self._api
        window_dc = api.get_dc(hwnd)
        if not window_dc:
            raise CaptureFailure(REASON_WIN32, f'GetDC 失败：无法取得窗口 {hwnd} 的设备上下文')
        memory_dc = 0
        bitmap = 0
        previous = 0
        try:
            memory_dc = api.create_memory_dc(window_dc)
            if not memory_dc:
                raise CaptureFailure(REASON_WIN32, f'CreateCompatibleDC 失败：无法为窗口 {hwnd} 创建内存 DC')
            bitmap = api.create_bitmap(window_dc, width, height)
            if not bitmap:
                raise CaptureFailure(REASON_WIN32,
                                     f'CreateCompatibleBitmap 失败：无法创建 {width}×{height} 位图')
            previous = api.select_object(memory_dc, bitmap)
            if not api.print_window(hwnd, memory_dc):
                raise CaptureFailure(REASON_PRINT_WINDOW,
                                     f'PrintWindow 失败：窗口 {hwnd} 拒绝渲染（常见于最小化、渲染进程退出或'
                                     '显卡驱动异常）')
            pixels, lines = api.bitmap_bits(memory_dc, bitmap, width, height)
        finally:
            if memory_dc and previous:
                api.select_object(memory_dc, previous)
            if bitmap:
                api.delete_object(bitmap)
            if memory_dc:
                api.delete_dc(memory_dc)
            api.release_dc(hwnd, window_dc)
        if lines <= 0 or not pixels:
            raise CaptureFailure(REASON_EMPTY,
                                 f'截图为空：GetDIBits 没有取到像素（{lines}/{height} 行）；请重试')
        if lines != height:
            raise CaptureFailure(REASON_COPY,
                                 f'GetDIBits 只取到 {lines}/{height} 行：像素拷贝不完整，请重试；'
                                 '持续失败说明 LDPlayer 渲染异常')
        return pixels

    def _check_pixels(self, pixels: bytes, width: int, height: int) -> None:
        expected = width * height * 3
        if len(pixels) != expected:
            raise CaptureFailure(REASON_COPY, f'截图数据 {len(pixels)} 字节与 {width}×{height}×3={expected} 不符')
        if pixels.count(0) == expected:
            raise CaptureFailure(REASON_BLACK,
                                 '截图为全黑：窗口可能被隐藏/最小化，或渲染进程暂停；'
                                 '本通道不把黑帧当有效画面')

    def _window_handle(self) -> LdplayerWindow:
        window = self._window
        if window is not None and self._api.is_window(window.parent) and self._api.is_window(window.render):
            return window
        self._window = find_ldplayer_window(self._api)
        self._metrics = None
        return self._window

    def _ensure_visible(self, window: LdplayerWindow) -> LdplayerWindow:
        """父窗口比「工具栏 + 720」矮时，把窗口加高被裁的像素；只调整一次，之后每帧检查为空操作。"""
        parent_width, parent_height = self._api.client_size(window.parent)
        render_width, render_height = self._api.client_size(window.render)
        _, corner = self._api.map_window_points(
            window.render, window.parent, [(0, 0), (render_width, render_height)],
        )
        overflow_x = max(0, corner[0] - parent_width)
        overflow_y = max(0, corner[1] - parent_height)
        if not overflow_x and not overflow_y:
            return window
        if overflow_x > MAX_WINDOW_ADJUST or overflow_y > MAX_WINDOW_ADJUST:
            raise CaptureFailure(
                REASON_CLIPPED,
                f'窗口客户区裁掉了画面 {overflow_x}×{overflow_y} px（自动修正上限 {MAX_WINDOW_ADJUST}）：'
                '请把 LDPlayer 窗口调大或取消最大化',
            )
        window_width, window_height = self._api.window_size(window.parent)
        if not self._api.set_window_size(window.parent, window_width + overflow_x, window_height + overflow_y):
            raise CaptureFailure(
                REASON_CLIPPED,
                f'LDPlayer 窗口无法加高 {overflow_x}×{overflow_y} px：请手动调大窗口或取消最大化',
            )
        time.sleep(0.2)
        parent_width, parent_height = self._api.client_size(window.parent)
        _, corner = self._api.map_window_points(
            window.render, window.parent, [(0, 0), (render_width, render_height)],
        )
        if corner[0] > parent_width or corner[1] > parent_height:
            raise CaptureFailure(
                REASON_CLIPPED,
                f'加高窗口后画面仍被裁掉 {corner[0] - parent_width}×{corner[1] - parent_height} px：'
                '请手动调大窗口或取消最大化',
            )
        self._stats.window_adjustments += 1
        return window

    def _make_metrics(self, width: int, height: int) -> DeviceMetrics:
        subject = f'设备 {self.config.serial} 的 LDPlayer RenderWindow'
        try:
            validate_resolution(width, height, subject=subject)
        except DeviceError as error:
            raise DeviceError(f'{error}；另请确认执行器进程创建窗口前已设置 per-monitor DPI 感知，'
                              '否则窗口尺寸会被系统缩放虚报') from error
        configured = (self.config.width, self.config.height)
        if configured[0] is not None and configured != (width, height):
            raise DeviceError(f'{subject} 客户区 {width}×{height} 与 DeviceConfig 配置的 '
                              f'{configured[0]}×{configured[1]} 不一致；请修正配置或模拟器分辨率')
        return DeviceMetrics(serial=self.config.serial, width=width, height=height,
                             density=None, region=self.config.region)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def stats_summary(stats: CaptureStats) -> str:
    """把统计压成一行，供命令行与运行日志使用。"""
    reasons: Mapping[str, int] = stats.reasons
    detail = '、'.join(f'{reason}×{count}' for reason, count in sorted(reasons.items())) or '无'
    last = '—' if stats.last_ms is None else f'{stats.last_ms:.2f} ms'
    adjusted = f'；窗口修正 {stats.window_adjustments} 次' if stats.window_adjustments else ''
    return (f'尝试 {stats.attempts} 帧，成功 {stats.frames}，失败 {stats.failures}；'
            f'最近一帧 {last}；失败原因 {detail}{adjusted}')
