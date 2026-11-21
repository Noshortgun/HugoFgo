"""离线回放关键帧与刷本动作；不会调用 ADB 或点击真实设备。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "cv"))

import farm_ember as farm
from image_io import read_image, write_image
from state_classifier import load_templates, result_for_image
from executor.device.base import Frame
from executor.perception import Perception

PERCEPTION_FIXTURES = ROOT / "executor/perception/fixtures/screens-cn.json"

SUPPORT_IMAGE = ROOT / "captures/2026-11-19-ember/key/02-support-select.png"
GRAND_SUPPORT_IMAGE = ROOT / "captures/2026-11-19-ember/34-grand-support-confirm.png"
TEAM_IMAGE = ROOT / "captures/2026-11-19-ember/key/03-team-confirm.png"
DROPS_IMAGE = ROOT / "captures/2026-11-19-ember/key/14-drops.png"
LOADING_IMAGE = ROOT / "captures/2026-11-19/07-battle-loading.png"

SUPPORT_ROWS = [
    ("助战选择", 120, 45),
    ("娄希", 300, 300),
    ("助战编队确认", 1130, 320),
]
REPORTS: list[dict] = []


def add_report(name, image, expected, actual, action, reason, ok, simulated_ocr=False):
    REPORTS.append({
        "name": name,
        "image": str(image.relative_to(ROOT)),
        "expected": expected,
        "actual": actual,
        "action": action,
        "reason": reason,
        "simulatedOcr": simulated_ocr,
        "ok": ok,
    })


def check_classifications():
    templates = load_templates()
    labels = json.loads((ROOT / "cv/labels.json").read_text(encoding="utf-8"))
    cases = [(ROOT / "captures/2026-11-19-ember/key" / name, "unknown" if state == "other" else state)
             for name, state in labels.items()]
    cases.extend([
        (ROOT / "captures/2026-11-19-ember/05-quest-start.png", "unknown"),
        (ROOT / "captures/2026-11-19-ember/01-gate.png", "unknown"),
        (LOADING_IMAGE, "battle"),
    ])
    for path, expected in cases:
        output = result_for_image(path, templates)
        actual = output["state"] if output else "unreadable"
        ok = actual == expected
        add_report(
            f"classify:{path.name}", path, expected, actual, "observe",
            f"confidence={output['confidence']:.3f}" if output else "截图读取失败", ok,
        )


def check_perception():
    """双速感知的逐屏回放：28 屏开发/回归集 + 跨会话关键帧，覆盖覆盖层与第三张卡之后的动作安全。"""
    perception = Perception()
    try:
        fixtures = json.loads(PERCEPTION_FIXTURES.read_text(encoding="utf-8"))
        for group in ("regression", "crossSession"):
            for case in fixtures[group]:
                path = ROOT / case["file"]
                frame = Frame(image=path.read_bytes(), width=1280, height=720, captured_at=0.0)
                observation = perception.read(frame)
                overlays = sorted(item.value for item in observation.overlays)
                variants = sorted(item.value for item in observation.variants)
                blocked = [name for name in case.get("absentControls", ())
                           if (control := observation.control(name)) is not None and control.actionable]
                missing = [name for name in case.get("actionableControls", ())
                           if (control := observation.control(name)) is None or not control.actionable]
                expected = f"{case['base']}/{sorted(case['overlays'])}/actionable={case['actionable']}"
                actual = f"{observation.base_page.value}/{overlays}/actionable={observation.actionable}"
                ok = (actual == expected and variants == sorted(case["variants"])
                      and not blocked and not missing)
                name = case["file"].replace("captures/", "").replace("/", "-")
                add_report(f"perception:{group}:{name}", path, expected, actual, "observe",
                           f"variants={variants} 越权可点={blocked} 缺失可点={missing}", ok)
    finally:
        perception.close()


def recorder():
    taps = []

    def record(x, y, reason=""):
        taps.append({"x": int(x), "y": int(y), "reason": reason})

    return taps, record


def check_result_action(name, rows, expected_action, expected_tap=None):
    taps, tap = recorder()
    with mock.patch.object(farm, "tap", tap):
        actual_action = farm.result_action(rows)
        if actual_action == "next":
            farm.click_row(rows, "下一步", "结算下一步", (1130, 660))
        elif actual_action == "repeat":
            farm.click_row(rows, "连续出击", "连续出击", (870, 577))
        elif actual_action == "result":
            farm.tap(640, 360, "战斗结果")
    ok = actual_action == expected_action
    if expected_tap is not None:
        ok = ok and len(taps) == 1 and (taps[0]["x"], taps[0]["y"]) == expected_tap
    else:
        ok = ok and not taps
    add_report(name, DROPS_IMAGE, expected_action, actual_action, "tap" if taps else "wait",
               f"taps={taps}", ok, simulated_ocr=True)


def check_support_sequence(name, following_rows, expected, expected_reasons, expected_error=None, image_path=SUPPORT_IMAGE):
    image = read_image(image_path)
    taps, tap = recorder()
    sequences = [SUPPORT_ROWS, SUPPORT_ROWS, *following_rows]
    actual = None
    error = None
    with (
        mock.patch.object(farm, "shot", return_value=image),
        mock.patch.object(farm, "ocr", side_effect=sequences),
        mock.patch.object(farm, "tap", tap),
        mock.patch.object(farm, "swipe") as swipe,
        mock.patch.object(farm.time, "sleep"),
    ):
        try:
            actual = farm.select_support()
        except farm.FarmError as exc:
            error = str(exc)
    reasons = [item["reason"] for item in taps]
    top_scrolls = [call.args for call in swipe.call_args_list]
    ok = all(args == (640, 260, 640, 620, 200) for args in top_scrolls)
    ok = ok and reasons == expected_reasons
    ok = ok and ((error == expected_error) if expected_error else (actual == expected and error is None))
    add_report(name, image_path, expected_error or expected, error or actual,
               "stop" if error else "tap", f"taps={taps}; swipes={top_scrolls}", ok, simulated_ocr=True)


def check_support_refresh():
    image = read_image(SUPPORT_IMAGE)
    taps, tap = recorder()
    no_match = [("助战选择", 1100, 40), ("列表更新", 641, 134)]
    confirm = [("列表更新", 640, 110), ("是否确认执行", 640, 320), ("是", 840, 565)]
    team = [("队伍确认", 1100, 40), ("开始任务", 1190, 655)]
    sequences = [no_match] * 10 + [confirm, SUPPORT_ROWS, SUPPORT_ROWS, team]
    actual = None
    with (
        mock.patch.object(farm, "shot", return_value=image),
        mock.patch.object(farm, "ocr", side_effect=sequences),
        mock.patch.object(farm, "tap", tap),
        mock.patch.object(farm, "swipe"),
        mock.patch.object(farm.time, "sleep"),
    ):
        actual = farm.select_support()
    reasons = [item["reason"] for item in taps]
    expected = ["切换娄希的狂战士职阶页", "列表已到底，更新助战列表（固定坐标回退）",
                "确认更新助战列表", "选择娄希助战条目（普通或冠位）"]
    refresh_at_safe_coordinate = len(taps) > 1 and (taps[1]["x"], taps[1]["y"]) == (930, 125)
    add_report("action:support-refresh-at-bottom", SUPPORT_IMAGE, True, actual,
               "refresh", f"taps={taps}", actual is True and reasons == expected and refresh_at_safe_coordinate,
               simulated_ocr=True)


def check_finish_safety(name, image_path, rows, allow_repeat, expected, expected_error=None):
    image = read_image(image_path)
    taps, tap = recorder()
    actual = None
    error = None
    with (
        mock.patch.object(farm, "shot", return_value=image),
        mock.patch.object(farm, "ocr", return_value=rows),
        mock.patch.object(farm, "tap", tap),
        mock.patch.object(farm.time, "sleep"),
    ):
        try:
            actual = farm.finish_results(allow_repeat=allow_repeat)
        except farm.FarmError as exc:
            error = str(exc)
    ok = not taps and ((error == expected_error) if expected_error else actual == expected)
    add_report(name, image_path, expected_error or expected, error or actual,
               "stop" if error else "wait", f"taps={taps}", ok, simulated_ocr=True)


def check_job_actions(wave, expected_points):
    taps, tap = recorder()
    image = read_image(ROOT / "captures/2026-11-19-ember/key/04-battle-wave1.png")
    with (
        mock.patch.object(farm, "shot", return_value=image),
        mock.patch.object(farm, "ocr", return_value=[("请选择对象", 640, 190)]),
        mock.patch.object(farm, "tap", tap),
        mock.patch.object(farm.time, "sleep"),
    ):
        farm.run_wave_actions(wave)
    points = [(item["x"], item["y"]) for item in taps]
    add_report(f"action:job-wave-{wave}", ROOT / "jobs/娄希、娄希、奥伯龙.json", expected_points,
               points, "tap", f"taps={taps}", points == expected_points, simulated_ocr=True)


def check_team_start():
    taps, tap = recorder()
    rows = [("队伍确认", 120, 45), ("开始任务", 1190, 655)]
    with mock.patch.object(farm, "tap", tap):
        allowed = farm.has(rows, "队伍确认") or farm.has(rows, "开始任务")
        if allowed:
            farm.click_row(rows, "开始任务", "开始任务", (1190, 655))
    ok = allowed and len(taps) == 1 and (taps[0]["x"], taps[0]["y"]) == (1190, 655)
    add_report("action:team-confirm", TEAM_IMAGE, "start", "start" if allowed else "wait",
               "tap" if taps else "wait", f"taps={taps}", ok, simulated_ocr=True)


def check_ocr_action(name, path, expected):
    image = read_image(path)
    rows = farm.ocr(image)
    state = farm.classify(image, farm.state_templates)["state"]
    cards = farm.command_cards(rows) if state == "card" else []
    attack = farm.is_attack_button(rows) if state == "battle" else False
    action = "cards" if cards else "attack" if attack else "wait"
    add_report(name, path, expected, action, "observe", f"ocr={rows}", action == expected)


def run(output: Path):
    REPORTS.clear()
    check_classifications()
    check_perception()
    check_result_action("action:drops-fallback", [("获得战利品", 640, 60)], "next", (1130, 660))
    check_result_action("action:repeat-only-after-dialog", [("连续进行同一关卡", 640, 300)], "repeat", (870, 577))
    check_result_action("action:unknown-waits", [("加载中", 640, 360)], "wait")
    check_result_action("action:detail-does-not-advance", [("从者详情", 640, 60)], "wait")
    check_finish_safety(
        "action:final-battle-does-not-repeat", DROPS_IMAGE,
        [("连续进行同一关卡", 640, 300), ("连续出击", 870, 577)], False, True,
    )
    check_finish_safety(
        "action:ap-recovery-never-uses-items", ROOT / "captures/2026-11-19-ember/37-ap-recovery.png",
        [("行动力回复", 640, 40), ("圣晶石", 500, 120), ("黄金果实", 500, 230)],
        True, None, "行动力不足；未使用苹果或圣晶石",
    )
    check_support_sequence(
        "action:friend-application",
        [[("好友申请", 640, 180), ("结束", 330, 618)], [("队伍确认", 120, 45), ("开始任务", 1190, 655)]],
        True, ["切换娄希的狂战士职阶页", "选择娄希助战条目（普通或冠位）", "结束好友申请"],
    )
    check_support_sequence(
        "action:servant-detail-stops", [[("从者详情", 640, 60)]], None,
        ["切换娄希的狂战士职阶页", "选择娄希助战条目（普通或冠位）"], "误入了从者详情，请重新选择助战",
    )
    check_support_sequence(
        "action:repeat-then-reread-team",
        [[("连续进行同一关卡", 640, 300), ("连续出击", 870, 577)],
         [("队伍确认", 120, 45), ("开始任务", 1190, 655)]],
        True, ["切换娄希的狂战士职阶页", "选择娄希助战条目（普通或冠位）", "连续出击"],
    )
    check_support_sequence(
        "action:unexpected-grand-page-stops",
        [[("助战编队确认", 1100, 40), ("冠位编队确认", 750, 45)]],
        None, ["切换娄希的狂战士职阶页", "选择娄希助战条目（普通或冠位）"],
        "普通助战流程误入冠位编队页，未选择任何冠位卡面",
        image_path=GRAND_SUPPORT_IMAGE,
    )
    check_support_refresh()
    check_job_actions(1, [(480, 120), (615, 575), (435, 575)])
    check_job_actions(2, [(265, 575), (95, 575), (710, 575)])
    check_job_actions(3, [(795, 575), (640, 350), (880, 575), (640, 350),
                          (1195, 310), (1000, 300), (640, 350)])
    check_team_start()
    check_ocr_action("action:loading-waits", ROOT / "captures/2026-11-19-ember/05-quest-start.png", "wait")
    check_ocr_action("action:attack-animation-waits", ROOT / "captures/2026-11-19-ember/36-attack-animation.png", "wait")
    check_ocr_action("action:attack-button-ready", LOADING_IMAGE, "attack")

    failed = [item for item in REPORTS if not item["ok"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    failure_dir = output.parent / f"failures-{datetime.now():%Y%m%d-%H%M%S}"
    if failed:
        failure_dir.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(failed, 1):
            source = ROOT / item["image"]
            target = failure_dir / f"{index:02d}-{item['name'].replace(':', '-')}.png"
            image = read_image(source)
            if image is not None and write_image(target, image):
                item["failureImage"] = str(target.relative_to(ROOT))
    payload = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "adbUsed": False,
        "total": len(REPORTS),
        "passed": len(REPORTS) - len(failed),
        "failed": len(failed),
        "cases": REPORTS,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in REPORTS:
        print(f"{'OK' if item['ok'] else 'FAIL':4} {item['name']:42} {item['actual']}")
    print(f"\n{payload['passed']}/{payload['total']} passed; report={output}")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "logs/replay/latest.json")
    args = parser.parse_args()
    return run(args.output.resolve())


if __name__ == "__main__":
    sys.exit(main())
