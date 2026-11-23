"""实机验证监控：页面就绪后才由用户启动 scrcpy 执行器。

用法：cv/.venv/Scripts/python.exe scripts/live_monitor.py [--port 8766] [--no-demo]
打开 http://127.0.0.1:8766/ 。演示帧只用于预览，点击“开始实机验证”才连接设备并发送输入。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from executor.core import RunOptions, run
from executor.core.ap_policy import RecoveryPolicy
from executor.device import open_device
from executor.device.base import Frame
from executor.device.config import DeviceChannel, DeviceConfig
from executor.domain import compile_job
from executor.perception import Perception, evidence
from executor.perception.regions import cn
from executor.session import RunControl, RunEvent
from executor.session.evidence import observation_data

PAGE_NAMES = {
    'menu': '菜单', 'support': '助战选择', 'team': '队伍确认', 'battle': '战斗',
    'card': '指令卡', 'result': '战斗结算', 'drops': '掉落奖励', 'ap_recovery': 'AP 恢复',
    'story': '剧情', 'loading': '加载中', 'unknown': '未知画面',
}
FIELD_NAMES = {
    'wave': '面数', 'turn': '累计回合', 'np': '宝具充能', 'crit': '暴击率',
    'ap': 'AP', 'enemies': '敌人数量', 'turn_boundary': '回合边界',
    'support_class': '助战职阶', 'team_servants': '队伍从者',
    'team_support_ce': '助战礼装', 'team_mystic': '御主礼装',
}
STATUS_NAMES = {
    'not_applicable': '不适用', 'not_read': '未读取', 'uncertain': '不确定', 'confirmed': '已确认',
}
EVENT_NAMES = {
    'run_started': '运行开始', 'state_changed': '画面切换', 'wave_started': '新面开始',
    'turn_started': '新回合', 'action': '执行动作', 'card_batch_submitted': '三卡批次提交',
    'action_confirmed': '动作已确认', 'warning': '警告', 'recovery_used': 'AP 恢复消耗',
    'drain_started': '开始清 AP', 'finished': '完成', 'stopped': '停止', 'failed': '失败',
}
FONT = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 14)
FONT_BIG = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)


def page_name(observation) -> str:
    return PAGE_NAMES.get(observation.base_page.value, observation.base_page.value)


def field_lines(observation) -> dict[str, str]:
    lines = {}
    for name in ('wave', 'turn', 'np', 'crit'):
        field = observation.field(name)
        if field is None or field.value is None:
            continue
        value = field.value
        if name in {'np', 'crit'} and isinstance(value, tuple):
            value = '/'.join(str(item) for item in value)
        lines[name] = str(value)
    return lines


def _label(draw: ImageDraw.ImageDraw, xy, text: str, color, *, big: bool = False) -> None:
    x, y = xy
    font = FONT_BIG if big else FONT
    box = draw.textbbox((x, y), text, font=font)
    draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=(5, 12, 24, 215))
    draw.text((x, y), text, font=font, fill=color)


def annotate(frame: Frame, observation) -> bytes:
    bgr = evidence.decode_frame(frame)
    image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).convert('RGBA')
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _label(draw, (12, 10), f'{page_name(observation)}  |  帧 {observation.frame_hash[:8]}',
           (105, 230, 255, 255), big=True)
    values = field_lines(observation)
    special = [('wave', cn.WAVE_VALUE_REGION, (0, 220, 255, 255)),
               ('turn', cn.TURN_VALUE_REGION, (0, 220, 255, 255))]
    special.extend((f'np-{index}', box, (255, 200, 60, 255))
                   for index, box in enumerate(cn.NP_VALUE_REGIONS, start=1))
    special.extend((f'crit-{index}', box, (180, 110, 255, 255))
                   for index, box in enumerate(cn.CRIT_VALUE_REGIONS, start=1))
    for name, box, color in special:
        x1, y1, x2, y2 = box
        draw.rectangle((x1, y1, x2, y2), outline=color, width=2)
        key = name.split('-')[0]
        if key in values:
            _label(draw, (x1, max(0, y1 - 22)), f'{key.upper()} {values[key]}', color)
    for control in observation.controls:
        x1, y1, x2, y2 = control.box
        color = (70, 230, 120, 255) if control.actionable else (150, 155, 165, 255)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=2)
        if control.actionable:
            _label(draw, (x1 + 2, max(28, y1 - 22)), control.name, color)
    output = Image.alpha_composite(image, overlay).convert('RGB')
    ok, buffer = cv2.imencode('.jpg', cv2.cvtColor(np.array(output), cv2.COLOR_RGB2BGR),
                              [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError('无法编码监控帧')
    return buffer.tobytes()


class MonitorState:
    def __init__(self, plan) -> None:
        self.plan = plan
        self.lock = threading.Lock()
        self.status = 'ready'
        self.demo = True
        self.run_dir = None
        self.observation = None
        self.image = None
        self.revision = 0
        self.history = deque(maxlen=24)
        self.events = deque(maxlen=120)
        self.decision = None
        self.result = None
        self.error = None
        self.last_signature = None
        self.last_history_at = 0.0
        self.history_sequence = 0

    def reset_live(self, run_dir: Path) -> None:
        with self.lock:
            self.status = 'running'
            self.demo = False
            self.run_dir = str(run_dir)
            self.observation = None
            self.image = None
            self.history.clear()
            self.events.clear()
            self.decision = None
            self.result = None
            self.error = None
            self.last_signature = None
            self.last_history_at = 0.0
            self.revision += 1

    def update(self, frame: Frame, observation) -> None:
        image = annotate(frame, observation)
        data = observation_data(observation)
        signature = (observation.base_page.value, observation.frame_hash,
                     tuple(sorted((name, str(field.value)) for name, field in observation.fields.items())))
        now = time.time()
        with self.lock:
            self.observation = data
            self.image = image
            self.revision += 1
            if signature == self.last_signature and now - self.last_history_at < 1.0:
                return
            if signature != self.last_signature or now - self.last_history_at >= 1.0:
                self.history_sequence += 1
                self.history.append({
                    'id': self.history_sequence,
                    'timestamp': now,
                    'observation': data,
                    'image': image,
                    'page': page_name(observation),
                    'decision': self.decision,
                })
                self.last_signature = signature
                self.last_history_at = now

    def emit(self, event: RunEvent) -> None:
        row = {'type': event.type, 'label': EVENT_NAMES.get(event.type, event.type),
               'timestamp': event.timestamp, 'data': dict(event.data)}
        with self.lock:
            self.events.append(row)
            if event.type == 'action':
                self.decision = row
                if self.history:
                    self.history[-1]['decision'] = row
            if event.type in {'finished', 'stopped', 'failed'}:
                self.status = {'finished': 'completed', 'stopped': 'stopped', 'failed': 'failed'}[event.type]

    def finish(self, result) -> None:
        with self.lock:
            self.result = {
                'status': result.status.value,
                'reason': result.reason,
                'perception': result.perception_stats,
            }
            self.status = result.status.value

    def fail(self, message: str) -> None:
        with self.lock:
            self.status = 'failed'
            self.error = message

    def snapshot(self) -> dict:
        with self.lock:
            history = []
            for item in self.history:
                history.append({key: value for key, value in item.items() if key != 'image'})
            return {
                'status': self.status,
                'demo': self.demo,
                'run_dir': self.run_dir,
                'observation': self.observation,
                'revision': self.revision,
                'decision': self.decision,
                'events': list(self.events)[-30:],
                'history': history,
                'result': self.result,
                'error': self.error,
                'serial': getattr(self, 'serial', None),
                'job_name': getattr(self, 'job_name', None),
                'server_time': time.time(),
            }

    def history_image(self, identifier: int) -> bytes | None:
        with self.lock:
            item = next((item for item in self.history if item['id'] == identifier), None)
            return item['image'] if item is not None else None


class MonitorPerception:
    def __init__(self, perception: Perception, state: MonitorState) -> None:
        self.perception = perception
        self.state = state

    def bind_formation(self, formation) -> None:
        self.perception.bind_formation(formation)

    def bind_mystic(self, mystic_id: str) -> None:
        self.perception.bind_mystic(mystic_id)

    def read(self, frame: Frame):
        observation = self.perception.read(frame)
        self.state.update(frame, observation)
        return observation

    def read_fields(self, frame: Frame, names, *, timeout=None):
        observation = self.perception.read_fields(frame, names, timeout=timeout)
        self.state.update(frame, observation)
        return observation

    def stats(self):
        return self.perception.stats()

    def close(self) -> None:
        self.perception.close()


class MonitorEvents:
    def __init__(self, state: MonitorState) -> None:
        self.state = state

    def emit(self, event: RunEvent) -> None:
        self.state.emit(event)


class LiveRunner:
    def __init__(self, state: MonitorState, job: Path, serial: str, server: Path) -> None:
        self.state = state
        self.job = job
        self.serial = serial
        self.server = server
        self.control = RunControl()
        self.thread = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return False
        run_dir = ROOT / 'logs' / 'farm' / f"{datetime.now():%Y%m%d-%H%M%S}-live-monitor"
        self.control = RunControl()
        self.state.reset_live(run_dir)
        self.thread = threading.Thread(target=self._execute, args=(run_dir,), name='live-monitor-run', daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        self.control.stop()

    def _execute(self, run_dir: Path) -> None:
        device = None
        perception = None
        try:
            config = DeviceConfig(serial=self.serial, region='CN', channel=DeviceChannel.SCRCPY,
                                  scrcpy_server_path=str(self.server))
            device = open_device(config)
            perception = Perception()
            monitored = MonitorPerception(perception, self.state)
            options = RunOptions(run_count=1, recovery=RecoveryPolicy(), run_dir=str(run_dir))
            result = run(self.state.plan, device, monitored, MonitorEvents(self.state),
                         options=options, control=self.control)
            self.state.finish(result)
        except Exception as error:
            self.state.fail(str(error))
        finally:
            if perception is not None:
                perception.close()
            if device is not None:
                close = getattr(device, 'close', None)
                if close is not None:
                    close()


def load_demo(state: MonitorState, job: Path) -> None:
    perception = Perception()
    monitored = MonitorPerception(perception, state)
    monitored.bind_formation(state.plan.formation)
    monitored.bind_mystic(state.plan.mystic_id)
    try:
        for relative, fields in (
            ('captures/2026-11-23/key/manual-team-04.png', ()),
            ('captures/2026-11-23/key/manual-w1-t1-after-s3-07.png', ('wave', 'turn', 'np')),
            ('captures/2026-11-23/key/manual-w3-t1-cards-25.png', ('crit', 'np')),
            ('captures/2026-11-23/key/manual-repeat-dialog-29.png', ()),
            ('captures/2026-11-23/key/manual-w2-t1-cards-14.png', ('crit', 'np')),
        ):
            frame = Frame(image=(ROOT / relative).read_bytes(), width=1280, height=720,
                          captured_at=time.time())
            monitored.read(frame)
            if fields:
                monitored.read_fields(frame, fields, timeout=0)
        state.emit(RunEvent('action', time.time(), {
            'control': '演示：卡4 → 宝具 → 卡1', 'reason': '真实关键帧预览；尚未启动实机',
        }))
    finally:
        perception.close()


def make_handler(state: MonitorState, runner: LiveRunner):
    class Handler(BaseHTTPRequestHandler):
        def _bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)

        def _json(self, value, status: int = 200) -> None:
            self._bytes(json.dumps(value, ensure_ascii=False).encode('utf-8'),
                        'application/json; charset=utf-8', status)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == '/api/state':
                self._json(state.snapshot())
                return
            if parsed.path == '/frame.jpg':
                with state.lock:
                    image = state.image
                if image is None:
                    self._bytes(b'', 'image/jpeg', 404)
                else:
                    self._bytes(image, 'image/jpeg')
                return
            if parsed.path.startswith('/history/') and parsed.path.endswith('.jpg'):
                try:
                    identifier = int(parsed.path.removeprefix('/history/').removesuffix('.jpg'))
                except ValueError:
                    self._bytes(b'', 'text/plain', 400)
                    return
                image = state.history_image(identifier)
                self._bytes(image or b'', 'image/jpeg', 200 if image else 404)
                return
            name = parsed.path.lstrip('/') or 'live_monitor.html'
            if name not in {'live_monitor.html', 'live_monitor.css', 'live_monitor.js'}:
                self._bytes(b'not found', 'text/plain', 404)
                return
            content = (ROOT / 'scripts' / name).read_bytes()
            content_type = {'live_monitor.html': 'text/html; charset=utf-8',
                            'live_monitor.css': 'text/css; charset=utf-8',
                            'live_monitor.js': 'text/javascript; charset=utf-8'}[name]
            self._bytes(content, content_type)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == '/api/start':
                started = runner.start()
                self._json({'started': started, 'state': state.snapshot()})
                return
            if parsed.path == '/api/stop':
                runner.stop()
                self._json({'stopping': True})
                return
            self._json({'error': 'not found'}, 404)

        def log_message(self, _format, *args) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='HugoFgo scrcpy 实机验证监控')
    parser.add_argument('--job', default=str(ROOT / 'jobs' / '娄希、娄希、奥伯龙.json'))
    parser.add_argument('--serial', default='emulator-5554')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--scrcpy-server', default=str(ROOT / 'resources' / 'scrcpy' / 'scrcpy-server.jar'))
    parser.add_argument('--no-demo', action='store_true', help='不加载演示关键帧')
    args = parser.parse_args(argv)

    job = Path(args.job)
    compiled = compile_job(job.read_text(encoding='utf-8'))
    if compiled.plan is None:
        for diagnostic in compiled.diagnostics:
            print(f'{diagnostic.path}: {diagnostic.message}', file=sys.stderr)
        return 2
    state = MonitorState(compiled.plan)
    state.serial = args.serial
    state.job_name = job.stem
    runner = LiveRunner(state, job, args.serial, Path(args.scrcpy_server))
    if not args.no_demo:
        load_demo(state, job)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(state, runner))
    print(f'监控就绪：http://127.0.0.1:{args.port}/', flush=True)
    print('当前为演示帧；只有在页面点击“开始实机验证”后才连接 scrcpy 并发送输入。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
