"""四类画面状态识别原型。

类别：
- menu：主界面（以「迦勒底之门」按钮为锚点）
- battle：战斗中（以「战斗菜单 / 御主技能」按钮为锚点）
- card：指令卡选择（以「战斗速度」按钮为锚点）
- result：战斗结果（以「战斗结果」标题为锚点）

只使用 OpenCV 模板匹配，不依赖从者立绘或 OCR。固定按 1280x720 处理；
其他分辨率会先缩放，后续可扩展到多分辨率模板。

用法：
  python cv/state_classifier.py --image captures/.../key/04-battle-wave1.png
  python cv/state_classifier.py --dir captures/2026-11-19-ember/key --labels cv/labels.json
  python cv/state_classifier.py --device
  python cv/state_classifier.py --device --serial emulator-5554 --save live.png --json
  python cv/state_classifier.py --watch
  python cv/state_classifier.py --watch --interval 0.5 --duration 30
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from image_io import read_image, write_image

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TARGET_SIZE = (1280, 720)
UNKNOWN = "unknown"

TEMPLATE_SPECS: dict[str, list[tuple[str, tuple[int, int, int, int]]]] = {
    "menu": [("menu.png", (580, 430, 1160, 610))],
    "battle": [
        ("battle_diamond.png", (1080, 120, 1260, 390)),
        ("battle_enemy1.png", (0, 0, 600, 90)),
        ("battle_enemy2.png", (0, 0, 600, 90)),
        ("battle_enemy3.png", (0, 0, 600, 90)),
    ],
    "card": [("card.png", (1020, 0, 1260, 130))],
    "result": [
        ("result_title.png", (420, 0, 860, 130)),
        ("result_drops.png", (60, 0, 700, 120)),
    ],
}
THRESHOLD = 0.55

TemplateMap = dict[str, list[tuple[np.ndarray, tuple[int, int, int, int]]]]


def load_templates() -> TemplateMap:
    templates: dict[str, list[tuple[np.ndarray, tuple[int, int, int, int]]]] = {}
    for name, specs in TEMPLATE_SPECS.items():
        loaded: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []
        for filename, roi in specs:
            image = read_image(TEMPLATE_DIR / filename)
            if image is None:
                raise SystemExit(f"missing template: {TEMPLATE_DIR / filename}")
            loaded.append((cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), roi))
        templates[name] = loaded
    return templates


def normalize(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    if (width, height) != TARGET_SIZE:
        image = cv2.resize(image, TARGET_SIZE, interpolation=cv2.INTER_AREA)
    return image


def classify(image: np.ndarray, templates: TemplateMap) -> dict:
    image = normalize(image)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scores: dict[str, float] = {}

    for name, specs in templates.items():
        best = 0.0
        for template, (x1, y1, x2, y2) in specs:
            roi = gray[y1:y2, x1:x2]
            if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
                continue
            result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(result.max()))
        scores[name] = round(best, 4)

    best = max(scores, key=scores.get)
    confidence = scores[best]
    state = best if confidence >= THRESHOLD else UNKNOWN
    return {"state": state, "confidence": confidence, "scores": scores}


def adb_path() -> str:
    configured = os.environ.get("HUGOFGO_ADB", "").strip()
    if configured:
        return configured

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        shutil.which("adb"),
        str(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe"),
        r"C:\LDPlayer\LDPlayer14\adb.exe",
        r"D:\leidian\LDPlayer14\adb.exe",
        r"C:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("adb not found; set HUGOFGO_ADB")


def adb_device_serial() -> str:
    output = subprocess.run(
        [adb_path(), "devices"], capture_output=True, text=True, check=True
    ).stdout
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    raise SystemExit("no adb device in 'device' state")


def capture_device(serial: str | None) -> tuple[np.ndarray, str]:
    serial = serial or adb_device_serial()
    result = subprocess.run(
        [adb_path(), "-s", serial, "exec-out", "screencap", "-p"],
        capture_output=True,
        check=True,
    )
    buffer = np.frombuffer(result.stdout, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit("device screenshot is not a valid image")
    return image, serial


def result_for_image(path: Path, templates: TemplateMap) -> dict | None:
    image = read_image(path)
    if image is None:
        return None
    output = classify(image, templates)
    output["image"] = str(path)
    return output


def evaluate(directory: Path, labels_path: Path, templates: TemplateMap) -> int:
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    total = 0
    correct = 0
    failures = []
    for name in sorted(labels):
        path = directory / name
        output = result_for_image(path, templates)
        if output is None:
            print(f"MISSING {name}")
            continue
        expected = labels[name]
        predicted = output["state"]
        ok = predicted == expected or (expected == "other" and predicted == UNKNOWN)
        total += 1
        correct += int(ok)
        marker = "OK" if ok else "MISMATCH"
        print(
            f"{marker:8} {name:34} expected={expected:7} predicted={predicted:7} "
            f"conf={output['confidence']:.3f} scores={output['scores']}"
        )
        if not ok:
            failures.append(name)
    accuracy = correct / total if total else 0.0
    print(f"\naccuracy: {correct}/{total} = {accuracy:.1%}")
    if failures:
        print("mismatches:", ", ".join(failures))
        return 1
    return 0


def watch(
    serial: str | None,
    interval: float,
    duration: float,
    save_dir: Path,
    templates: TemplateMap,
) -> None:
    """只观察，不发送点击；状态变化时保存关键帧。"""
    serial = serial or adb_device_serial()
    session = save_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
    session.mkdir(parents=True, exist_ok=True)
    interval = max(0.2, interval)
    print(
        f"watching {serial} every {interval:.1f}s, save={session} "
        f"(observe only, Ctrl+C to stop)",
        flush=True,
    )

    last_state: str | None = None
    frame_index = 0
    started = time.time()

    try:
        while True:
            if duration > 0 and time.time() - started >= duration:
                break
            try:
                image, _ = capture_device(serial)
            except Exception as error:  # adb 断开时继续重试，不中断观察
                print(f"[{datetime.now():%H:%M:%S}] capture failed: {error}", flush=True)
                time.sleep(max(0.5, interval))
                continue

            output = classify(image, templates)
            state = output["state"]
            if state != last_state:
                frame_index += 1
                path = session / f"{frame_index:03d}-{state}.png"
                write_image(path, image)
                print(
                    f"[{datetime.now():%H:%M:%S}] {last_state or '-'} -> {state} "
                    f"conf={output['confidence']:.3f} "
                    f"scores={output['scores']} saved={path}",
                    flush=True,
                )
                last_state = state

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nwatch stopped", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="HugoFgo four-state classifier")
    parser.add_argument("--image", type=Path, help="classify one image")
    parser.add_argument("--dir", type=Path, help="classify every PNG in a directory")
    parser.add_argument("--labels", type=Path, help="labels JSON for --dir evaluation")
    parser.add_argument("--device", action="store_true", help="capture from adb and classify")
    parser.add_argument("--watch", action="store_true", help="observe the device continuously")
    parser.add_argument("--interval", type=float, default=1.0, help="watch interval seconds")
    parser.add_argument("--duration", type=float, default=0.0, help="watch duration seconds (0 = forever)")
    parser.add_argument("--save-dir", type=Path, default=Path("captures/live"), help="watch key-frame directory")
    parser.add_argument("--serial", help="adb serial (default: first device)")
    parser.add_argument("--save", type=Path, help="save the captured frame")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    templates = load_templates()

    if args.watch:
        watch(args.serial, args.interval, args.duration, args.save_dir, templates)
        return 0

    if args.device:
        image, serial = capture_device(args.serial)
        output = classify(image, templates)
        output["serial"] = serial
        if args.save:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            write_image(args.save, image)
            output["saved"] = str(args.save)
        print(json.dumps(output, ensure_ascii=False, indent=2) if args.json else
              f"{output['state']} confidence={output['confidence']:.3f} scores={output['scores']}")
        return 0

    if args.dir:
        if args.labels:
            return evaluate(args.dir, args.labels, templates)
        for path in sorted(args.dir.glob("*.png")):
            output = result_for_image(path, templates)
            if output:
                print(
                    f"{path.name:36} {output['state']:7} "
                    f"conf={output['confidence']:.3f} scores={output['scores']}"
                )
        return 0

    if args.image:
        output = result_for_image(args.image, templates)
        if output is None:
            raise SystemExit(f"cannot read image: {args.image}")
        print(json.dumps(output, ensure_ascii=False, indent=2) if args.json else
              f"{output['state']} confidence={output['confidence']:.3f} scores={output['scores']}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
