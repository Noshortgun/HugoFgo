from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from executor.core import RunOptions, RunStatus, run
from executor.core.ap_policy import RecoveryItem, RecoveryPolicy
from executor.device import DeviceConfig, DeviceError, FakeDevice, open_device
from executor.domain import compile_job
from executor.perception import Perception
from executor.session import JsonlEventSink, RunControl, RunEvent
from executor.session.history import RunHistoryRecorder


class StdoutEventSink:
    def emit(self, event: RunEvent) -> None:
        row = {'type': event.type, 'timestamp': event.timestamp, 'data': dict(event.data)}
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + '\n')
        sys.stdout.flush()


class TeeEventSink:
    def __init__(self, *sinks) -> None:
        self.sinks = sinks

    def emit(self, event: RunEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)


def listen_for_stop(control: RunControl) -> None:
    for line in sys.stdin:
        if line.strip() == 'stop':
            control.stop()
            return


def emit(sink, event_type: str, **data: object) -> None:
    sink.emit(RunEvent(event_type, time.time(), data))


def fake_sleep(control: RunControl, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if control.stop_requested:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 0.05))


def run_fake(plan, settings, run_dir: Path, interval: float,
             control: RunControl, events: TeeEventSink) -> int:
    device = FakeDevice(config=DeviceConfig(serial='fake', region='CN', width=1280, height=720))
    jsonl = JsonlEventSink(run_dir / 'events.jsonl')
    events.sinks = (*events.sinks, jsonl)
    status = 'completed'
    reason = 'completed'
    completed_runs = 0
    try:
        for _ in range(settings.run_count):
            for wave in plan.waves:
                emit(events, 'state_changed', state='battle', wave=wave.number, turn=1, action_index=0)
                for turn_number, turn in enumerate(wave.turns, start=1):
                    emit(events, 'turn_started', wave=wave.number, turn=turn_number)
                    actions = [*turn.actions, None]
                    for action_index, action in enumerate(actions):
                        if control.stop_requested or not fake_sleep(control, interval):
                            raise StopIteration
                        device.tap(640, 360)
                        emit(events, 'action', control=action.title if action is not None else '攻击',
                             detail=action.detail if action is not None else '假运行选卡',
                             wave=wave.number, turn=turn_number, action_index=action_index,
                             x=640, y=360, reason='fake_run')
            completed_runs += 1
            emit(events, 'battle_completed', completed_runs=completed_runs,
                 wave=len(plan.waves), turn=max((len(wave.turns) for wave in plan.waves), default=1))
        emit(events, 'finished', completed_runs=completed_runs,
             recovery_used={}, evidence_dir=str(run_dir))
        return 0
    except StopIteration:
        status = 'stopped'
        reason = 'manual_stop'
        emit(events, 'stopped', reason=reason, evidence_dir=str(run_dir))
        return 1
    finally:
        (run_dir / 'fake-actions.json').write_text(
            json.dumps(device.actions, ensure_ascii=False, indent=2), encoding='utf-8')
        (run_dir / 'result.json').write_text(json.dumps({
            'status': status, 'reason': reason, 'completed_runs': completed_runs,
            'evidence_dir': str(run_dir),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        jsonl.close()


def executable_region(value: str) -> str:
    if value != 'CN':
        raise argparse.ArgumentTypeError(f'区服 {value} 暂不支持执行；当前仅支持 CN，请在“设置与资源”中选择国服')
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog='python -m executor', description='HugoFgo 执行器进程')
    result.add_argument('job', help='作业 JSON 路径')
    result.add_argument('--serial', required=True, help='设备 serial；假运行使用 fake')
    result.add_argument('--region', required=True, type=executable_region,
                        help='设备区服；当前仅支持 CN，必须显式指定')
    result.add_argument('--run-dir', help='运行记录目录')
    result.add_argument('--battles', type=int, default=1, help='连续出击场数')
    result.add_argument('--recovery', nargs='*', choices=[item.value for item in RecoveryItem],
                        help='AP 恢复白名单与优先级；按参数顺序尝试，显式给出空列表表示禁止使用')
    result.add_argument('--drain-after-turns', action='store_true', help='场数完成后清空剩余 AP')
    result.add_argument('--adb', help='adb 路径')
    result.add_argument('--channel', default='scrcpy', help='设备通道，默认 scrcpy；ADB 作为启动失败保底')
    result.add_argument('--allow-adb-fallback', action='store_true',
                        help='用户已预先确认：仅 scrcpy 启动失败时可见回退 ADB；运行中断绝不回退')
    result.add_argument('--scrcpy-server', help='固定 scrcpy 服务端路径；其同目录 manifest.json 会校验版本与 SHA-256')
    result.add_argument('--fake', action='store_true', help='假运行：只记录动作，不接触设备')
    result.add_argument('--fake-interval', type=float, default=0.15, help=argparse.SUPPRESS)
    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = parser().parse_args(argv)
    run_dir = Path(args.run_dir) if args.run_dir else Path('logs/farm') / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    run_dir.mkdir(parents=True, exist_ok=True)
    history = RunHistoryRecorder(run_dir, job_name=Path(args.job).stem, job_file=args.job,
                                 serial=args.serial, region=args.region, channel=args.channel)
    events = TeeEventSink(StdoutEventSink(), history)
    control = RunControl()
    threading.Thread(target=listen_for_stop, args=(control,), daemon=True).start()
    emit(events, 'run_started', serial=args.serial, region=args.region, channel=args.channel,
         job=str(Path(args.job).resolve()), run_dir=str(run_dir.resolve()), fake=args.fake)

    try:
        job_text = Path(args.job).read_text(encoding='utf-8')
        job_data = json.loads(job_text)
        if isinstance(job_data.get('name'), str) and job_data['name'].strip():
            history.job_name = job_data['name'].strip()
        compiled = compile_job(job_text)
        if compiled.plan is None:
            diagnostics = [{'path': item.path, 'message': item.message,
                            'wave': item.wave, 'turn': item.turn} for item in compiled.diagnostics]
            emit(events, 'failed', reason='invalid_job', diagnostics=diagnostics, evidence_dir=str(run_dir))
            return 2
        priority = tuple(RecoveryItem(item) for item in
                         (args.recovery if args.recovery is not None else [RecoveryItem.BRONZE.value]))
        settings = RunOptions(
            run_count=args.battles,
            recovery=RecoveryPolicy(allowed=frozenset(priority), priority=priority,
                                    drain_after_turns=args.drain_after_turns),
            run_dir=str(run_dir),
        )
        if args.fake_interval < 0:
            raise ValueError('假运行间隔不能为负数')
        if args.fake:
            return run_fake(compiled.plan, settings, run_dir, args.fake_interval, control, events)
        device_config = DeviceConfig(serial=args.serial, region=args.region, adb_path=args.adb,
                                     channel=args.channel, scrcpy_server_path=args.scrcpy_server)
        try:
            device = open_device(device_config)
        except DeviceError as error:
            if device_config.channel.value != 'scrcpy' or not args.allow_adb_fallback:
                raise
            emit(events, 'warning', reason='scrcpy_start_failed', message=str(error), channel='scrcpy')
            emit(events, 'channel_changed', **{'from': 'scrcpy'}, to='adb',
                 message=f'scrcpy 启动失败，按用户已确认的回退策略改用 ADB：{error}')
            device = open_device(DeviceConfig(serial=args.serial, region=args.region, adb_path=args.adb,
                                              channel='adb'))
        perception = Perception()
        try:
            result = run(compiled.plan, device, perception, events, options=settings, control=control)
        finally:
            perception.close()
            close = getattr(device, 'close', None)
            if close is not None:
                close()
        return 0 if result.status is RunStatus.COMPLETED else 1
    except Exception as error:
        emit(events, 'failed', reason='process_error', message=str(error), evidence_dir=str(run_dir))
        print(f'执行器异常：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
