from __future__ import annotations

import argparse
import json
from pathlib import Path

from executor.core import RunOptions, RunStatus, run
from executor.core.runner import StopRequested
from executor.device import DeviceConfig, FakeDevice, open_device
from executor.device.base import Device, Frame
from executor.domain import compile_job
from executor.perception import BasePage, Field, FieldStatus, Observation, Perception
from executor.session import MemoryEventSink, RunControl


class FrameBudget:
    def __init__(self, device: Device, *, max_frames: int, control: RunControl) -> None:
        if max_frames < 1:
            raise ValueError('最大帧数必须至少为 1')
        self.device = device
        self.max_frames = max_frames
        self.control = control
        self.reads = 0

    def metrics(self):
        return self.device.metrics()

    def frame(self) -> Frame:
        if self.reads >= self.max_frames:
            self.control.stop()
            raise StopRequested
        self.reads += 1
        return self.device.frame()

    def tap(self, x: int, y: int) -> None:
        self.device.tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None:
        self.device.swipe(x1, y1, x2, y2, duration_ms)


class ConfirmedStartPerception:
    def __init__(self, perception: Perception, wave: int, turn: int) -> None:
        self.perception = perception
        self.wave = wave
        self.turn = turn
        self.pending = True

    def bind_formation(self, formation) -> None:
        self.perception.bind_formation(formation)

    def read(self, frame: Frame) -> Observation:
        return self.perception.read(frame)

    def read_fields(self, frame: Frame, names, *, timeout=None) -> Observation:
        observation = self.perception.read_fields(frame, names, timeout=timeout)
        if self.pending and observation.base_page is BasePage.BATTLE:
            self.pending = False
            return observation.with_fields({
                'wave': Field(FieldStatus.CONFIRMED, self.wave),
                'turn': Field(FieldStatus.CONFIRMED, self.turn),
                'turn_boundary': Field(FieldStatus.CONFIRMED, True),
            })
        return observation


def parse_start(value: str) -> tuple[int, int]:
    try:
        wave_text, turn_text = value.split(':', 1)
        wave, turn = int(wave_text), int(turn_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError('回合边界格式应为 面:回合，例如 1:1') from error
    if wave < 1 or turn < 1:
        raise argparse.ArgumentTypeError('面和回合必须至少为 1')
    return wave, turn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m executor.core', description='有边界的作业冒烟')
    parser.add_argument('job', help='作业 JSON 路径')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--serial', help='实机 adb serial')
    source.add_argument('--frame-dir', help='离线 PNG 帧目录')
    parser.add_argument('--adb', help='adb 路径')
    parser.add_argument('--channel', default='adb', help='设备通道，默认 adb；scrcpy 必须显式选择')
    parser.add_argument('--scrcpy-server', help='固定 scrcpy 服务端路径；由同目录清单校验')
    parser.add_argument('--region', default='CN', help='区服，默认 CN')
    parser.add_argument('--expected-stop', default='completed', help='预期停止原因，默认 completed')
    parser.add_argument('--max-frames', type=int, default=200, help='最大读取帧数，默认 200')
    parser.add_argument('--run-dir', help='运行证据目录；缺省写入 logs/farm/<时间戳>')
    parser.add_argument('--confirm-start', type=parse_start,
                        help='人工确认的起始回合边界，格式 面:回合；只用于该次冒烟')
    args = parser.parse_args(argv)
    if args.max_frames < 1:
        parser.error('--max-frames 必须至少为 1')

    compiled = compile_job(Path(args.job).read_text(encoding='utf-8'))
    if compiled.plan is None:
        for diagnostic in compiled.diagnostics:
            print(json.dumps({'path': diagnostic.path, 'message': diagnostic.message,
                              'wave': diagnostic.wave, 'turn': diagnostic.turn}, ensure_ascii=False))
        return 2

    control = RunControl()
    device = (FakeDevice(frame_dir=args.frame_dir) if args.frame_dir
              else open_device(DeviceConfig(serial=args.serial, adb_path=args.adb,
                                            channel=args.channel, region=args.region,
                                            scrcpy_server_path=args.scrcpy_server)))
    budget = FrameBudget(device, max_frames=args.max_frames, control=control)
    perception = Perception()
    events = MemoryEventSink()
    try:
        observed = (ConfirmedStartPerception(perception, *args.confirm_start)
                    if args.confirm_start else perception)
        result = run(compiled.plan, budget, observed, events,
                     options=RunOptions(run_dir=args.run_dir), control=control)
    finally:
        perception.close()
        close = getattr(device, 'close', None)
        if close is not None:
            close()
    print(json.dumps({'status': result.status.value, 'reason': result.reason,
                      'progress': result.progress.__dict__, 'evidence_dir': result.evidence_dir,
                      'frames_read': budget.reads}, ensure_ascii=False))
    return 0 if result.status is RunStatus.COMPLETED and result.reason == args.expected_stop else 1
