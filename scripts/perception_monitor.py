"""实时感知监控：只读模拟器画面，不发送任何输入。

用法：cv/.venv/Scripts/python.exe scripts/perception_monitor.py [--adb]
按键：q/Esc 退出，s 保存当前帧与观察（用于留出集），空格暂停。
窗口左上角显示基页/变体/覆盖层/耗时；绿框=可点，灰框=不可点。
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from executor.device import open_device
from executor.device.base import DeviceError
from executor.device.config import DeviceChannel, DeviceConfig
from executor.perception import Perception, evidence
from executor.perception.__main__ import describe

OUT = ROOT / 'logs' / 'perception-monitor'
PERCEPTION_ROOT = ROOT / 'executor' / 'perception'
RELOAD_MODULES = (
    'executor.perception.observation',
    'executor.perception.regions.cn',
    'executor.perception.evidence',
    'executor.perception.ocr',
    'executor.perception.recognizer',
    'executor.perception',
)


def rules_mtime() -> float:
    latest = 0.0
    for path in PERCEPTION_ROOT.rglob('*'):
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
    return latest


def reload_perception(perception):
    for name in RELOAD_MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    perception.close()
    print('识别规则已热加载', flush=True)
    return sys.modules['executor.perception'].Perception()
WINDOW = 'HugoFgo perception monitor'
FONT = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 15)
FONT_BIG = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 19)


def make_device(use_adb: bool):
    config = DeviceConfig(
        serial='emulator-5554', region='CN',
        channel=DeviceChannel.ADB if use_adb else DeviceChannel.LDPLAYER_WINDOW,
    )
    return open_device(config)


def panel(image: Image.Image, observation, ms: float, paused: bool, saved: int,
          reload_count: int = 0) -> Image.Image:
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, 0, 470, 152], fill=(0, 0, 0, 175))
    title = f"基页 {observation.base_page.value}"
    draw.text((8, 6), title, font=FONT_BIG, fill=(120, 255, 120))
    variants = ','.join(sorted(v.value for v in observation.variants)) or '-'
    overlays = ','.join(sorted(o.value for o in observation.overlays)) or '-'
    draw.text((8, 34), f'变体 {variants}', font=FONT, fill=(220, 220, 220))
    draw.text((8, 56), f'覆盖层 {overlays}', font=FONT, fill=(255, 220, 120))
    draw.text((8, 78), f'可行动 {observation.actionable}   耗时 {ms:.1f} ms   已存 {saved} 帧',
              font=FONT, fill=(220, 220, 220))
    fields = '  '.join(f'{name}={field.status.value}' for name, field in observation.fields.items())
    draw.text((8, 100), f'字段 {fields}', font=FONT, fill=(180, 200, 255))
    hint = 'q 退出  s 保存  空格暂停' if not paused else '已暂停（空格继续）'
    draw.text((8, 124), hint, font=FONT, fill=(160, 160, 160))
    if reload_count:
        draw.text((340, 126), f'规则 v{reload_count}', font=FONT, fill=(120, 255, 120))

    for control in observation.controls:
        x1, y1, x2, y2 = control.box
        color = (60, 220, 60, 255) if control.actionable else (160, 160, 160, 255)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
        draw.text((x1 + 2, max(0, y1 - 20)), control.name, font=FONT, fill=color)
    return Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')


def main() -> int:
    use_adb = '--adb' in sys.argv
    perception = Perception()
    device = None
    paused = False
    saved = 0
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)
    last_log = 0.0
    last_frame = None
    last_observation = None
    print('监控启动：只读截图，不发送输入。窗口按 q 退出，s 保存。', flush=True)

    rules_stamp = rules_mtime()
    reload_count = 0
    last_reload = 0.0
    try:
        while True:
            now = time.time()
            if now - last_reload > 1.0:
                last_reload = now
                stamp = rules_mtime()
                if stamp != rules_stamp:
                    rules_stamp = stamp
                    try:
                        perception = reload_perception(perception)
                        reload_count += 1
                    except Exception as error:
                        print(f'热加载失败：{error}（继续用旧规则）', flush=True)
            key = cv2.waitKey(20) & 0xFF
            if key in (ord('q'), 27):
                break
            if key == ord(' '):
                paused = not paused
            if key == ord('s') and last_frame is not None and last_observation is not None:
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                ok, buffer = cv2.imencode('.png', last_frame)
                if ok:
                    (OUT / f'{stamp}.png').write_bytes(buffer.tobytes())
                (OUT / f'{stamp}.json').write_text(
                    json.dumps(describe(last_observation), ensure_ascii=False, indent=2),
                    encoding='utf-8')
                saved += 1
                print(f'已保存 {stamp}', flush=True)

            if paused and last_frame is not None and last_observation is not None:
                image = Image.fromarray(cv2.cvtColor(last_frame, cv2.COLOR_BGR2RGB))
                canvas = panel(image, last_observation, 0.0, paused, saved, reload_count)
                cv2.imshow(WINDOW, cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR))
                continue

            try:
                if device is None:
                    device = make_device(use_adb)
                frame = device.frame()
                started = time.perf_counter()
                bgr = evidence.decode_frame(frame)
                observation = perception.read(frame)
                ms = (time.perf_counter() - started) * 1000
                last_frame, last_observation = bgr, observation
                image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                canvas = panel(image, observation, ms, paused, saved, reload_count)
                cv2.imshow(WINDOW, cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR))

                now = time.time()
                if now - last_log > 1.0:
                    last_log = now
                    overlays = ','.join(sorted(o.value for o in observation.overlays)) or '-'
                    print(f'{datetime.now():%H:%M:%S} {observation.base_page.value:11} '
                          f'v={sorted(v.value for v in observation.variants)} o={overlays} '
                          f'act={observation.actionable} {ms:.1f}ms', flush=True)
            except (DeviceError, RuntimeError) as error:
                if device is None and not use_adb:
                    print(f'窗口通道不可用（{error}），改用 adb', flush=True)
                    use_adb = True
                    continue
                blank = np.zeros((720, 1280, 3), dtype=np.uint8)
                cv2.putText(blank, str(error)[:100], (20, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (0, 0, 255), 2)
                cv2.imshow(WINDOW, blank)
                time.sleep(0.3)
    finally:
        perception.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == '__main__':
    sys.exit(main())
