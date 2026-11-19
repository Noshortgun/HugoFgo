"""回放适配器：帧从内存或目录按序取出，动作只记录不执行，不调用任何外部进程。"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from executor.device.base import DeviceError, DeviceMetrics, Frame, Region, to_device_point
from executor.device.config import DeviceConfig
from executor.device.png import png_size


class FakeDevice:
    def __init__(self, frames: Sequence[Frame] | None = None, *, frame_dir: str | Path | None = None,
                 config: DeviceConfig | None = None):
        if frames is not None and frame_dir is not None:
            raise ValueError('FakeDevice 的 frames 与 frame_dir 只能给出一个')
        if frame_dir is not None:
            frames = load_frames(frame_dir)
        self.frames = tuple(frames or ())
        self.config = config
        self.actions: list[dict] = []
        self._next = 0
        self._metrics: DeviceMetrics | None = None

        if self.frames:
            width, height = self.frames[0].width, self.frames[0].height
            for index, frame in enumerate(self.frames):
                if (frame.width, frame.height) != (width, height):
                    raise ValueError(f'FakeDevice 第 {index + 1} 帧分辨率 {frame.width}×{frame.height} '
                                     f'与首帧 {width}×{height} 不一致')
        elif config is not None and config.width is not None:
            width, height = config.width, config.height
        else:
            raise DeviceError('FakeDevice 需要至少一帧截图，或在配置中给出 width/height')

        self.width = width
        self.height = height

    def metrics(self) -> DeviceMetrics:
        if self._metrics is None:
            config = self.config
            self._metrics = DeviceMetrics(
                serial=config.serial if config else 'fake',
                width=self.width,
                height=self.height,
                density=None,
                region=config.region if config else Region.CN,
            )
        return self._metrics

    def frame(self) -> Frame:
        if self._next >= len(self.frames):
            raise DeviceError(f'FakeDevice 的帧已用尽：共 {len(self.frames)} 帧，第 {self._next + 1} 次取帧；'
                              '请补充回放截图')
        frame = self.frames[self._next]
        self._next += 1
        return frame

    def tap(self, x: int, y: int) -> None:
        px, py = to_device_point(x, y, self.width, self.height)
        self.actions.append({'action': 'tap', 'x': px, 'y': py})

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None:
        start = to_device_point(x1, y1, self.width, self.height)
        end = to_device_point(x2, y2, self.width, self.height)
        self.actions.append({'action': 'swipe', 'x1': start[0], 'y1': start[1], 'x2': end[0], 'y2': end[1],
                             'duration_ms': int(duration_ms)})


def load_frames(directory: str | Path) -> list[Frame]:
    """按文件名顺序读取目录中的 PNG 截图。"""
    paths = sorted(Path(directory).glob('*.png'))
    if not paths:
        raise DeviceError(f'帧目录没有 PNG 截图：{directory}')
    frames = []
    for path in paths:
        data = path.read_bytes()
        size = png_size(data)
        if size is None:
            raise DeviceError(f'帧目录中的截图不是有效的 PNG：{path}')
        frames.append(Frame(image=data, width=size[0], height=size[1], captured_at=path.stat().st_mtime))
    return frames
