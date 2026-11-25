"""离线回放关键帧与刷本动作；不会调用 ADB 或点击真实设备。"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import replace
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from rapidocr_onnxruntime import RapidOCR

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cv"))

from image_io import read_image, write_image
from state_classifier import classify, load_templates, result_for_image
from executor.device.base import Frame
from executor.device.fake import FakeDevice
from executor.perception import Perception

PERCEPTION_FIXTURES = ROOT / "executor/perception/fixtures/screens-cn.json"

SUPPORT_IMAGE = ROOT / "captures/2026-11-19-ember/key/02-support-select.png"
GRAND_SUPPORT_IMAGE = ROOT / "captures/2026-11-19-ember/34-grand-support-confirm.png"
TEAM_IMAGE = ROOT / "captures/2026-11-19-ember/key/03-team-confirm.png"
DROPS_IMAGE = ROOT / "captures/2026-11-19-ember/key/14-drops.png"
AP_RECOVERY_IMAGE = ROOT / "captures/2026-11-20/key/ap-recovery-bronze.png"
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
    temporary = TemporaryDirectory()
    perception = Perception(identity_root=Path(temporary.name))
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
        temporary.cleanup()


def check_perception_identities():
    from executor.domain import FormationSlotPlan, compile_job

    temporary = TemporaryDirectory()
    perception = Perception(identity_root=Path(temporary.name))
    perception.bind_formation((
        FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True,
                          ce='迦勒底午茶时光', ce_id=9403520),
        FormationSlotPlan(slot=1, name='娄希', svt_id=705000),
        FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100),
    ))
    try:
        support_path = ROOT / 'captures/2026-11-20/key/support-select-correct-ce.png'
        support = perception.read(Frame(image=support_path.read_bytes(), width=1280, height=720,
                                        captured_at=0.0))
        candidate = support.control('助战卡片第2行').support
        actual = (candidate.svt_id, candidate.ce_id) if candidate is not None else None
        add_report('perception:identity:support', support_path, (705000, 9403520), actual, 'observe',
                   '从者与概念礼装身份', actual == (705000, 9403520))

        wrong_support_path = ROOT / 'captures/2026-11-20/key/support-select-wrong-ce.png'
        wrong_support = perception.read(Frame(image=wrong_support_path.read_bytes(), width=1280,
                                              height=720, captured_at=0.0))
        wrong_candidate = wrong_support.control('助战卡片第2行').support
        other_candidate = wrong_support.control('助战卡片第1行').support
        actual = ((wrong_candidate.svt_id, wrong_candidate.ce_id)
                  if wrong_candidate is not None else None)
        ok = (actual == (705000, 9400480)
              and (other_candidate is None or other_candidate.svt_id is None))
        add_report('perception:identity:support-wrong-ce', wrong_support_path, (705000, 9400480), actual,
                   'observe', '同从者不同概念礼装不得混淆；无关从者不加载', ok)

        card_path = ROOT / 'captures/2026-11-20/key/card-select.png'
        card = perception.read(Frame(image=card_path.read_bytes(), width=1280, height=720, captured_at=0.0))
        expected = [(2, 2800100, 'quick', False), (0, 705000, 'quick', True),
                    (0, 705000, 'quick', True), (1, 705000, 'quick', False),
                    (1, 705000, 'arts', False)]
        actual = [(control.card.servant_slot, control.card.svt_id, control.card.card_type,
                   control.card.support) if (control := card.control(f'卡{index}')).card else None
                  for index in range(1, 6)]
        add_report('perception:identity:command-cards', card_path, expected, actual, 'observe',
                   '编队槽位、从者、卡色与助战标记', actual == expected)

        from executor.core.strategy import select_cards

        live_path = ROOT / 'captures/2026-11-22/key/card-select-missing-rank-data.png'
        live = perception.read(Frame(image=live_path.read_bytes(), width=1280, height=720, captured_at=0.0))
        compiled = compile_job((ROOT / 'jobs/娄希、娄希、奥伯龙.json').read_text(encoding='utf-8'))
        if compiled.plan is None:
            raise ValueError('实机选卡回放作业无法编译')
        selection = select_cards(live, compiled.plan.strategy, compiled.plan, 1)
        expected = (3, ('缺少暴击率，按 0 处理',))
        actual = (len(selection.controls), selection.warnings)
        add_report('strategy:live-missing-rank-data', live_path, expected, actual, 'select',
                   f'{selection.reason}; controls={[control.name for control in selection.controls]}',
                   actual == expected and selection.diagnostic is None)

        old_path = ROOT / 'captures/2026-11-19-ember/key/06-card-select.png'
        perception.bind_formation((
            FormationSlotPlan(slot=0, name='梅林', svt_id=500800, support=True),
            FormationSlotPlan(slot=1, name='娄希', svt_id=705000),
            FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100),
        ))
        old = perception.read(Frame(image=old_path.read_bytes(), width=1280, height=720, captured_at=0.0))
        actual = [(old.control(f'卡{index}').card.svt_id if old.control(f'卡{index}').card else None)
                  for index in range(1, 6)]
        expected = [2800100, 500800, 2800100, 500800, 705000]
        add_report('perception:identity:cross-session', old_path, expected, actual, 'observe',
                   'Atlas 指令卡原图覆盖梅林等从者', actual == expected)

        ciel_path = ROOT / 'captures/2026-11-25/key/card-ciel-type-text.png'
        perception.bind_formation((
            FormationSlotPlan(slot=0, name='谜之代行者C.I.E.L', svt_id=2300900),
            FormationSlotPlan(slot=1, name='梅林', svt_id=500800),
            FormationSlotPlan(slot=2, name='光之高扬斯卡娅', svt_id=604200, support=True),
        ))
        ciel = perception.read(Frame(image=ciel_path.read_bytes(), width=1280, height=720, captured_at=0.0))
        actual = [(ciel.control(f'卡{index}').card.svt_id,
                   ciel.control(f'卡{index}').card.servant_slot,
                   ciel.control(f'卡{index}').card.card_type) for index in range(1, 6)]
        expected = [(2300900, 0, 'quick'), (2300900, 0, 'buster'), (2300900, 0, 'buster'),
                    (2300900, 0, 'arts'), (2300900, 0, 'arts')]
        add_report('perception:identity:ciel-type-text', ciel_path, expected, actual, 'observe',
                   '上方头像区优先，卡色特效文字不得阻断从者识别', actual == expected)

        affinity = [old.control(f'卡{index}').card.affinity for index in range(1, 6)]
        resisted_path = ROOT / 'captures/2026-11-19-ember/key/10-np-card-select.png'
        resisted = perception.read(Frame(image=resisted_path.read_bytes(), width=1280,
                                         height=720, captured_at=0.0))
        resisted_affinity = [resisted.control(f'卡{index}').card.affinity for index in range(1, 6)]
        expected = (['advantage', 'neutral', 'advantage', 'neutral', 'advantage'], ['resist'] * 5)
        actual = (affinity, resisted_affinity)
        add_report('perception:identity:affinity', resisted_path, expected, actual, 'observe',
                   '卡面克制、抵抗与无标识中立三态', actual == expected)
    finally:
        perception.close()
        temporary.cleanup()


def recorder():
    taps = []

    def record(x, y, reason=""):
        taps.append({"x": int(x), "y": int(y), "reason": reason})

    return taps, record


def run_core_scenario(observations, times, plan, *, options=None):
    from executor.testing import run_scenario

    return run_scenario(observations, times, plan, options=options)


def check_executor_core():
    from executor.core import RunOptions, run
    from executor.domain import AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Overlay, Variant)
    from executor.session import MemoryEventSink

    fields = {
        'wave': Field(FieldStatus.CONFIRMED, 1),
        'turn': Field(FieldStatus.CONFIRMED, 1),
        'turn_boundary': Field(FieldStatus.CONFIRMED, True),
    }
    battle = Observation(base_page=BasePage.BATTLE, fields=fields, controls=(
        Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),))
    boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
             (811, 415, 976, 655), (1066, 415, 1231, 655))
    card = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                card=CommandCardIdentity(index - 1, 'arts'))
        for index in range(1, 6)))
    unknown = Observation(base_page=BasePage.UNKNOWN)
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    observations = [battle, card, card, card, card, card, card,
                    animation, animation, animation, unknown, unknown]
    times = [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.0, 2.9, 3.0, 33.0]
    frames = [Frame(image=str(index).encode(), width=1280, height=720, captured_at=at)
              for index, at in enumerate(times)]

    class ScriptedPerception:
        def read(self, frame):
            return replace(observations[int(frame.image)], frame_hash=frame.image.decode())

        def read_fields(self, frame, names, *, timeout=None):
            return replace(observations[int(frame.image)], frame_hash=frame.image.decode())

    cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=cards))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),),
                         formation=tuple(FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)))
    device = FakeDevice(frames)
    events = MemoryEventSink()
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        result = run(plan, device, ScriptedPerception(), events, options=RunOptions(write_evidence=False))
    actions = [event for event in events.events if event.type == 'action']
    controls = [event.data['control'] for event in actions]
    expected = ['攻击', '卡1', '卡2', '卡3', '战斗跳过', '战斗跳过']
    skip_times = [event.timestamp for event in actions if event.data['reason'] == 'attack_animation_skip']
    skip_points = [(event.data['x'], event.data['y']) for event in actions
                   if event.data['reason'] == 'attack_animation_skip']
    ok = (result.reason == 'unknown_timeout' and controls == expected and len(device.actions) == 6
          and skip_times == [1.9, 2.9] and skip_points == [(1240, 420), (1240, 420)])
    add_report('executor:attack-animation-skip-rate-limit', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}; skip_times={skip_times}', ok, simulated_ocr=True)

    ready_animation = replace(
        battle, variants=frozenset({Variant.BATTLE_ANIMATION}),
        controls=(*battle.controls, Control('战斗菜单', ControlKind.BUTTON, (1120, 140, 1230, 220), True)))
    result, device, events = run_core_scenario(
        [battle, card, card, card, card, card, card, ready_animation, unknown, unknown],
        [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.0, 32.0], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3']
    ok = result.reason == 'unknown_timeout' and controls == expected
    add_report('executor:attack-skip-stops-when-battle-ready', LOADING_IMAGE, expected, controls,
               'stop', '实机故障回归：攻击和菜单已恢复时不得再点跳过区，避免误开御主技能',
               ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [battle, card, card, card, card, card, card, card, card, card],
        [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.9, 31.9], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3', '卡1', '卡2', '卡3']
    ok = (result.reason == 'action_confirmation_timeout' and controls == expected
          and len(device.actions) == 7 and events.events[-1].data.get('recovery_attempts') == 1)
    add_report('executor:attack-card-residual-never-skips', LOADING_IMAGE,
               'action_confirmation_timeout; no skip', f'{result.reason}; {controls}',
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    popup = Observation(base_page=BasePage.BATTLE, overlays=frozenset({Overlay.STORY_SKIP_CONFIRM}))
    result, device, events = run_core_scenario(
        [battle, card, card, card, card, card, card, popup],
        [0.0, 0.1, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3']
    ok = (result.status.value == 'stopped' and result.reason == 'unexpected_overlay'
          and controls == expected and len(device.actions) == 4)
    add_report('executor:attack-unexpected-popup-never-skips', LOADING_IMAGE,
               'unexpected_overlay; no skip', f'{result.reason}; {controls}',
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)


def check_command_card_batch():
    from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan,
                                 TurnPlan, WavePlan, compile_job)
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Variant)

    battle = Observation(base_page=BasePage.BATTLE, fields={
        'wave': Field(FieldStatus.CONFIRMED, 1),
        'turn': Field(FieldStatus.CONFIRMED, 1),
        'turn_boundary': Field(FieldStatus.CONFIRMED, True),
    }, controls=(Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),))
    boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
             (811, 415, 976, 655), (1066, 415, 1231, 655))

    def hand(slots):
        return Observation(base_page=BasePage.CARD, controls=tuple(
            Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                    card=CommandCardIdentity(slot, 'arts'))
            for index, slot in enumerate(slots, start=1)))

    initial = hand((0, 1, 2, 0, 1))
    changed = hand((2, 0, 1, 0, 1))
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    unknown = Observation(base_page=BasePage.UNKNOWN)
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=tuple(
        AttackCardPlan(slot=slot, type='arts') for slot in range(3))))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),),
                         formation=tuple(FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)))

    result, device, events = run_core_scenario(
        [battle, initial, animation, unknown, unknown], [0.0, 0.1, 0.7, 0.8, 30.8], plan)
    actions = [event.data for event in events.events if event.type == 'action']
    cards = [item for item in actions if item.get('reason') == 'command_card_batch']
    submitted = [event.data for event in events.events if event.type == 'card_batch_submitted']
    expected = ['攻击', '卡1', '卡2', '卡3', '战斗跳过']
    ok = (result.reason == 'unknown_timeout' and [item['control'] for item in actions] == expected
          and [item['card_position'] for item in cards] == [1, 2, 3]
          and len({item['batch_id'] for item in cards}) == 1
          and all(item['batch_attempt'] == 0 for item in cards)
          and cards[0].get('attack_to_first_card_ms', -1) >= 0
          and all(item.get('interval_ms', -1) >= 0 for item in cards[1:])
          and len(submitted) == 1 and submitted[0]['configured_interval_ms'] == 350.0)
    add_report('executor:card-batch-one-read-and-timing', LOADING_IMAGE, expected,
               [item['control'] for item in actions], 'tap',
               f'batch={submitted}; cards={cards}', ok, simulated_ocr=True)

    np_card = Control('宝具卡A', ControlKind.CARD, (330, 100, 510, 330), True,
                      card=CommandCardIdentity(servant_slot=0, card_type='buster', noble_phantasm=True))
    with_np = replace(initial, controls=(np_card, *initial.controls))
    np_action = ActionPlan(kind='attack', title='宝具', detail='', icon='', member_slot=0)
    np_turn = replace(turn, actions=(np_action,), np_positions={0: 2})
    np_plan = replace(plan, waves=(replace(plan.waves[0], turns=(np_turn,)),))
    result, device, events = run_core_scenario(
        [battle, initial, with_np, animation, unknown, unknown],
        [0.0, 0.1, 0.2, 0.3, 0.4, 30.4], np_plan)
    actions = [event.data for event in events.events if event.type == 'action']
    waiting = [event for event in events.events if event.type == 'warning'
               and event.data.get('reason') == 'np_reservation_waiting']
    expected = ['攻击', '卡1', '宝具卡A', '卡2', '战斗跳过']
    ok = (result.reason == 'unknown_timeout' and [item['control'] for item in actions] == expected
          and len(waiting) == 1)
    add_report('executor:card-batch-required-np-evidence', LOADING_IMAGE, expected,
               [item['control'] for item in actions], 'tap',
               f'waiting={len(waiting)}; reason={result.reason}', ok, simulated_ocr=True)

    flying_path = ROOT / 'captures/2026-11-25/key/card-flying-servants-unidentified.png'
    temporary = TemporaryDirectory()
    perception = Perception(identity_root=Path(temporary.name))
    try:
        perception.bind_formation(np_plan.formation)
        flying = perception.read(Frame(image=flying_path.read_bytes(), width=1280, height=720,
                                       captured_at=0.1))
    finally:
        perception.close()
        temporary.cleanup()
    flying_turn = replace(np_turn, attack=AttackPlan(mode='strategy', cards=()))
    flying_plan = replace(plan, waves=(replace(plan.waves[0], turns=(flying_turn,)),))
    result, device, events = run_core_scenario(
        [battle, flying, with_np, with_np, animation, unknown, unknown],
        [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 30.5], flying_plan)
    actions = [event.data for event in events.events if event.type == 'action']
    waiting = [event for event in events.events
               if event.type == 'warning' and event.data.get('reason') == 'card_batch_waiting']
    unidentified = [control.card.servant_slot for control in flying.controls
                    if control.card is not None and not control.card.noble_phantasm]
    expected = ['攻击', '卡1', '宝具卡A', '卡4', '战斗跳过']
    ok = (result.reason == 'unknown_timeout' and [item['control'] for item in actions] == expected
          and unidentified == [-1] * 5 and len(waiting) == 1)
    add_report('executor:card-flying-identities-wait', flying_path, expected,
               [item['control'] for item in actions], 'tap',
               f'unidentified={unidentified}; waiting={len(waiting)}; reason={result.reason}', ok)

    live_job = ROOT / 'jobs/谜之代行者C.I.E.L、梅林、光之高扬斯卡娅.json'
    live_plan = compile_job(live_job.read_text(encoding='utf-8')).plan
    live_turn = TurnPlan(actions=(ActionPlan(kind='attack', title='宝具', detail='', icon='', member_slot=0),),
                         np_positions={0: 2}, attack=AttackPlan(mode='strategy', cards=()))
    live_plan = replace(live_plan, waves=(replace(live_plan.waves[0], turns=(live_turn,)),))
    live_np = Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A')

    def live_hand(types):
        return Observation(base_page=BasePage.CARD, controls=(
            *(replace(initial.controls[index], card=CommandCardIdentity(0, card_type))
              for index, card_type in enumerate(types)), live_np))

    live_flying = Observation(base_page=BasePage.CARD, controls=(
        *(replace(initial.controls[index], card=CommandCardIdentity(-1, 'unknown'))
          for index in range(5)), live_np))
    live_wrong = live_hand(('buster', 'arts', 'arts', 'arts', 'quick'))
    live_settled = live_hand(('buster', 'buster', 'arts', 'arts', 'quick'))
    result, _, events = run_core_scenario(
        [battle, battle, live_flying, live_wrong, live_settled, live_settled, animation, unknown, unknown],
        [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 30.6], live_plan)
    selected = [event.data['control'] for event in events.events if event.type == 'action']
    submitted = next((event.data for event in events.events if event.type == 'card_batch_submitted'), {})
    hand_types = [card['card_type'] for card in submitted.get('hand', [])]
    expected = ['攻击', '卡1', '宝具卡1', '卡2', '战斗跳过']
    ok = (result.reason == 'unknown_timeout' and selected == expected
          and hand_types == ['buster', 'buster', 'arts', 'arts', 'quick'])
    add_report('strategy:live-job-two-buster-around-np', live_job, expected, selected, 'tap',
               f'run-1795596280454-23360：五张同为希耶尔，等待两张红卡颜色稳定；hand={hand_types}', ok)

    empty_turn = TurnPlan(attack=AttackPlan(mode='strategy', cards=()))
    empty_plan = replace(plan, formation=(*plan.formation[:2], FormationSlotPlan(slot=2, name='')),
                         waves=(replace(plan.waves[0], turns=(empty_turn,)),))
    uncertain = Observation(base_page=BasePage.CARD, controls=tuple(
        replace(initial.controls[index], card=CommandCardIdentity(-1 if index == 1 else index % 2, 'buster'))
        for index in range(5)))
    identified = Observation(base_page=BasePage.CARD, controls=tuple(
        replace(initial.controls[index], card=CommandCardIdentity(index % 2, 'buster'))
        for index in range(5)))
    result, _, events = run_core_scenario(
        [battle, battle, uncertain, identified, identified, animation, unknown, unknown],
        [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 30.5], empty_plan)
    submitted_event = next(event for event in events.events if event.type == 'card_batch_submitted')
    empty_hand = submitted_event.data.get('hand', [])
    ok = submitted_event.timestamp == 1.0 and all(card['servant_slot'] != -1 for card in empty_hand)
    add_report('strategy:empty-slot-single-unknown-never-submits', LOADING_IMAGE,
               '单帧未知不提交；后续指定从者多数确认', empty_hand, 'tap',
               f'timestamp={submitted_event.timestamp}; hand={empty_hand}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [battle, initial, initial, changed, animation, unknown, unknown],
        [0.0, 0.1, 1.0, 2.9, 3.0, 3.1, 33.2], plan)
    actions = [event.data for event in events.events if event.type == 'action']
    recovered = [item for item in actions if item.get('batch_attempt') == 1]
    recoveries = [event for event in events.events
                  if event.type == 'warning' and event.data.get('reason') == 'action_recovery']
    expected = ['攻击', '卡1', '卡2', '卡3', '卡2', '卡3', '卡1', '战斗跳过']
    ok = (result.reason == 'unknown_timeout' and [item['control'] for item in actions] == expected
          and [item['control'] for item in recovered] == ['卡2', '卡3', '卡1']
          and len(recoveries) == 1 and recoveries[0].data.get('control') == 'command_card_batch')
    add_report('executor:card-batch-fresh-hand-recovery', LOADING_IMAGE, expected,
               [item['control'] for item in actions], 'tap',
               f'recoveries={len(recoveries)}; reason={result.reason}', ok, simulated_ocr=True)

    remaining = replace(initial, controls=initial.controls[1:])
    result, device, events = run_core_scenario(
        [battle, initial, initial, remaining, animation, unknown, unknown],
        [0.0, 0.1, 1.0, 2.9, 3.0, 3.1, 33.2], plan)
    actions = [event.data for event in events.events if event.type == 'action']
    recovered = [item for item in actions if item.get('batch_attempt') == 1]
    expected = ['攻击', '卡1', '卡2', '卡3', '卡2', '卡3', '战斗跳过']
    ok = (result.reason == 'unknown_timeout' and [item['control'] for item in actions] == expected
          and [item['card_position'] for item in recovered] == [2, 3]
          and [item['control'] for item in recovered] == ['卡2', '卡3'])
    add_report('executor:card-batch-partial-drop-recovers-remaining', LOADING_IMAGE, expected,
               [item['control'] for item in actions], 'tap',
               f'recovered={recovered}; reason={result.reason}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [battle, initial, initial, remaining, remaining, remaining],
        [0.0, 0.1, 2.2, 2.9, 3.3, 5.7], plan)
    actions = [event.data for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3', '卡2', '卡3']
    ok = (result.status.value == 'stopped' and result.reason == 'action_confirmation_timeout'
          and [item['control'] for item in actions] == expected and len(device.actions) == 6
          and events.events[-1].data.get('recovery_attempts') == 1)
    add_report('executor:card-batch-partial-recovery-failure-stops', LOADING_IMAGE,
               'action_confirmation_timeout; remaining cards submitted once',
               f'{result.reason}; {[item["control"] for item in actions]}', 'stop',
               f'taps={device.actions}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [battle, initial, initial, initial, initial], [0.0, 0.1, 2.2, 2.9, 5.7], plan)
    actions = [event.data for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3', '卡1', '卡2', '卡3']
    ok = (result.status.value == 'stopped' and result.reason == 'action_confirmation_timeout'
          and [item['control'] for item in actions] == expected and len(device.actions) == 7
          and events.events[-1].data.get('recovery_attempts') == 1)
    add_report('executor:card-batch-recovery-limit', LOADING_IMAGE,
               'action_confirmation_timeout; one recovery', result.reason, 'stop',
               f'controls={[item["control"] for item in actions]}', ok, simulated_ocr=True)

    obscured = replace(initial, controls=tuple(replace(item, card=None) for item in initial.controls))
    next_battle = battle.with_fields({'turn': Field(FieldStatus.CONFIRMED, 2)})
    wrong = hand((4, 4, 4, 4, 4))
    result, device, events = run_core_scenario(
        [battle, initial, obscured, next_battle, wrong, wrong, wrong],
        [0.0, 0.1, 2.6, 2.7, 2.8, 32.8, 62.8], plan)
    actions = [event.data for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3', '攻击']
    confirmed_index = next((index for index, event in enumerate(events.events)
                            if event.type == 'action_confirmed'
                            and event.data.get('stage') == 'attack_end'), -1)
    last_action_index = max((index for index, event in enumerate(events.events)
                             if event.type == 'action'), default=-1)
    ok = (result.reason == 'fallback_cards_unavailable'
          and [item['control'] for item in actions] == expected and result.progress.turn == 2
          and 0 <= confirmed_index < last_action_index)
    add_report('executor:card-batch-recovery-wait-confirms-exit', LOADING_IMAGE, expected,
               [item['control'] for item in actions], 'stop',
               f'reason={result.reason}; turn={result.progress.turn}', ok, simulated_ocr=True)


def check_strategy_servant_priority():
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    strategy = FallbackStrategy(
        allow_np=False,
        servant_order=('从者2', '从者1', '从者0'),
        servant_order_slots=(2, 1, 0),
        cards='buster',
        color_priority=('buster', 'arts', 'quick'),
        strategy_order=('servant', 'cards', 'crit'),
    )
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
        formation=tuple(FormationSlotPlan(slot=slot, name=f'从者{slot}') for slot in range(3)),
        strategy=strategy,
    )
    observation = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(slot, 'buster', crit_rate=10, affinity='neutral'))
        for index, slot in enumerate((0, 1, 0, 1, 2), 1)))

    selection = select_cards(observation, strategy, plan, 1)
    actual = [control.name for control in selection.controls]
    expected = ['卡5', '卡2', '卡4']
    add_report('strategy:servant-priority', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', f'source={selection.source}; reason={selection.reason}',
               actual == expected and selection.source == 'global', simulated_ocr=True)

    reversed_hand = replace(observation, controls=tuple(
        replace(control, card=replace(control.card, servant_slot=0))
        for control in reversed(observation.controls)))
    sequential = select_cards(reversed_hand, strategy, plan, 1)
    actual = [control.name for control in sequential.controls]
    expected = ['卡1', '卡2', '卡3']
    add_report('strategy:equal-cards-screen-x', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', '完全并列按屏幕横坐标，不按输入下标',
               actual == expected, simulated_ocr=True)


def check_strategy_color_priority():
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    strategy = FallbackStrategy(
        allow_np=False, servant_order=('从者0',), servant_order_slots=(0,), cards='arts',
        color_priority=('arts', 'quick', 'buster'), strategy_order=('cards', 'servant', 'crit'),
    )
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
        formation=(FormationSlotPlan(slot=0, name='从者0'),), strategy=strategy,
    )
    observation = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(0, color, crit_rate=10, affinity='neutral'))
        for index, color in enumerate(('buster', 'arts', 'quick', 'arts', 'quick'), 1)))

    selection = select_cards(observation, strategy, plan, 1)
    actual = [control.name for control in selection.controls]
    expected = ['卡2', '卡4', '卡3']
    add_report('strategy:color-priority', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', selection.reason, actual == expected, simulated_ocr=True)


def check_strategy_crit_priority():
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    strategy = FallbackStrategy(
        allow_np=False, servant_order=('从者0',), servant_order_slots=(0,), cards='buster',
        color_priority=('buster', 'arts', 'quick'), strategy_order=('crit', 'servant', 'cards'),
    )
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
        formation=(FormationSlotPlan(slot=0, name='从者0'),), strategy=strategy,
    )
    observation = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(0, 'buster', crit_rate=crit, affinity='neutral'))
        for index, crit in enumerate((10, 90, 20, 80, 30), 1)))

    selection = select_cards(observation, strategy, plan, 1)
    actual = [control.name for control in selection.controls]
    expected = ['卡2', '卡4', '卡5']
    add_report('strategy:crit-priority', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', selection.reason, actual == expected, simulated_ocr=True)


def check_strategy_balanced_cards():
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    strategy = FallbackStrategy(
        allow_np=False, servant_order=('从者0',), servant_order_slots=(0,), cards='balanced',
        color_priority=('quick', 'arts', 'buster'), strategy_order=('cards', 'servant', 'crit'),
    )
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
        formation=(FormationSlotPlan(slot=0, name='从者0'),), strategy=strategy,
    )
    observation = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(0, color, crit_rate=10, affinity='neutral'))
        for index, color in enumerate(('buster', 'buster', 'arts', 'arts', 'quick'), 1)))

    selection = select_cards(observation, strategy, plan, 1)
    actual = [control.name for control in selection.controls]
    expected = ['卡5', '卡3', '卡1']
    add_report('strategy:balanced-cards', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', selection.reason, actual == expected, simulated_ocr=True)


def check_strategy_affinity_and_degraded_data():
    from dataclasses import replace
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    strategy = FallbackStrategy(
        allow_np=False, servant_order=('从者0', '从者1', '从者2'), servant_order_slots=(0, 1, 2),
        cards='buster', color_priority=('buster', 'arts', 'quick'),
        strategy_order=('servant', 'cards', 'crit'),
    )
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
        formation=tuple(FormationSlotPlan(slot=slot, name=f'从者{slot}') for slot in range(3)),
        strategy=strategy,
    )
    observation = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(slot, 'buster', crit_rate=10, affinity=affinity))
        for index, (slot, affinity) in enumerate(((0, 'advantage'), (0, 'resist'), (1, 'advantage'),
                                                 (1, 'resist'), (2, 'advantage')), 1)))

    selection = select_cards(observation, strategy, plan, 1)
    actual = [control.name for control in selection.controls]
    expected = ['卡1', '卡2', '卡3']
    add_report('strategy:affinity-priority', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', selection.reason, actual == expected, simulated_ocr=True)

    affinity_first = select_cards(
        observation, replace(strategy, strategy_order=('affinity', 'servant', 'cards', 'crit')), plan, 1)
    actual = [control.name for control in affinity_first.controls]
    expected = ['卡1', '卡3', '卡5']
    add_report('strategy:affinity-configured-position', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', affinity_first.reason, actual == expected, simulated_ocr=True)

    expected = ['卡2', '卡1', '卡3']
    uncertain_card = replace(observation.controls[0], card=replace(observation.controls[0].card, crit_rate=None))
    degraded = select_cards(replace(observation, controls=(uncertain_card, *observation.controls[1:])),
                            strategy, plan, 1)
    actual = [control.name for control in degraded.controls]
    add_report('strategy:missing-crit-degrades', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', f'{degraded.reason}; warnings={degraded.warnings}',
               actual == expected and degraded.warnings == ('缺少暴击率，按 0 处理',), simulated_ocr=True)

    uncertain_card = replace(observation.controls[0], card=replace(observation.controls[0].card, affinity=None))
    degraded = select_cards(replace(observation, controls=(uncertain_card, *observation.controls[1:])),
                            strategy, plan, 1)
    actual = [control.name for control in degraded.controls]
    expected = ['卡1', '卡2', '卡3']
    add_report('strategy:missing-affinity-degrades', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', f'{degraded.reason}; warnings={degraded.warnings}',
               actual == expected and degraded.warnings == ('缺少克制信息，按中立处理',), simulated_ocr=True)

    default_strategy = FallbackStrategy(servant_order=('从者0',), servant_order_slots=(0,))
    default_plan = replace(plan, strategy=default_strategy)
    mixed = Observation(base_page=BasePage.CARD, controls=(
        Control('卡1', ControlKind.CARD, (46, 415, 211, 655), True,
                card=CommandCardIdentity(0, 'buster', crit_rate=50, affinity='neutral')),
        Control('卡2', ControlKind.CARD, (301, 415, 466, 655), True,
                card=CommandCardIdentity(0, 'buster', crit_rate=10, affinity='advantage')),
        Control('卡3', ControlKind.CARD, (556, 415, 721, 655), True,
                card=CommandCardIdentity(0, 'buster', crit_rate=10, affinity='neutral')),
    ))
    default_selection = select_cards(mixed, default_strategy, default_plan, 1)
    actual = [control.name for control in default_selection.controls]
    expected = ['卡2', '卡1', '卡3']
    add_report('strategy:default-order-affinity-before-crit', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', f'{default_selection.reason}; order={default_strategy.strategy_order}',
               actual == expected, simulated_ocr=True)


def check_strategy_np_and_short_hand():
    from dataclasses import replace
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    strategy = FallbackStrategy(
        allow_np=False, servant_order=('从者0',), servant_order_slots=(0,), cards='buster',
        color_priority=('buster', 'arts', 'quick'), strategy_order=('servant', 'cards', 'crit'),
    )
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
        formation=(FormationSlotPlan(slot=0, name='从者0'),), strategy=strategy,
    )
    normal = tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(0, 'buster', crit_rate=10, affinity='neutral'))
        for index in range(1, 3))
    np_card = Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True,
                      card=CommandCardIdentity(0, 'buster', noble_phantasm=True,
                                               crit_rate=0, affinity='neutral'))
    observation = Observation(base_page=BasePage.CARD, controls=(*normal, np_card))

    disabled = select_cards(observation, strategy, plan, 1)
    add_report('strategy:np-disabled-short-hand', ROOT / 'captures/2026-11-20/key/card-select.png',
               'diagnostic', disabled.diagnostic or 'selected', 'stop', disabled.reason,
               not disabled.controls and disabled.diagnostic is not None, simulated_ocr=True)

    enabled_strategy = replace(strategy, allow_np=True)
    enabled = select_cards(observation, enabled_strategy, plan, 1)
    actual = [control.name for control in enabled.controls]
    expected = ['宝具卡1', '卡1', '卡2']
    add_report('strategy:np-allowed', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', enabled.reason, actual == expected, simulated_ocr=True)

    empty = select_cards(Observation(base_page=BasePage.CARD), strategy, plan, 1)
    add_report('strategy:empty-hand-stops', ROOT / 'captures/2026-11-20/key/card-select.png',
               'diagnostic', empty.diagnostic or 'selected', 'stop', empty.reason,
               not empty.controls and empty.diagnostic is not None, simulated_ocr=True)


def check_empty_slot_rules():
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    formation = (
        FormationSlotPlan(slot=0, name='指定0'),
        *(FormationSlotPlan(slot=slot, name='') for slot in range(1, 6)),
    )
    strategy = FallbackStrategy(
        allow_np=False, servant_order=('指定0',), servant_order_slots=(0,), cards='buster',
        color_priority=('buster', 'arts', 'quick'), strategy_order=('cards', 'crit', 'affinity'),
    )
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(),)),),
        formation=formation, strategy=strategy,
    )

    def normal(name, slot, color='buster', crit=10, affinity='neutral', x=0):
        return Control(name, ControlKind.CARD, (x, 415, x + 160, 655), True,
                       card=CommandCardIdentity(slot, color, crit_rate=crit, affinity=affinity))

    tier_hand = Observation(base_page=BasePage.CARD, controls=(
        normal('空槽优质卡', 1, crit=100, affinity='advantage', x=0),
        normal('指定普通1', 0, color='arts', crit=0, affinity='resist', x=200),
        normal('指定普通2', 0, color='quick', crit=0, affinity='resist', x=400),
        normal('空槽普通2', 1, crit=90, affinity='advantage', x=600),
        normal('空槽普通3', 1, crit=80, affinity='advantage', x=800),
    ))
    tiered = select_cards(tier_hand, strategy, plan, 1)
    actual = [control.name for control in tiered.controls]
    expected = ['指定普通1', '指定普通2', '空槽优质卡']
    add_report('strategy:empty-slots-hard-two-tiers', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', '卡色、暴击和克制不得跨层', actual == expected, simulated_ocr=True)

    unknown_hand = Observation(base_page=BasePage.CARD, controls=(
        normal('指定普通1', 0, color='arts', crit=0, affinity='resist', x=0),
        normal('指定普通2', 0, color='quick', crit=0, affinity='resist', x=200),
        normal('未知身份普通1', -1, crit=100, affinity='advantage', x=400),
        normal('未知身份普通2', -1, color='arts', x=600),
        normal('未知身份普通3', -1, color='quick', x=800),
    ))
    unknown = select_cards(unknown_hand, strategy, plan, 1)
    actual = [control.name for control in unknown.controls]
    expected = ['指定普通1', '指定普通2', '未知身份普通1']
    add_report('strategy:empty-slots-unknown-normal-color', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', '身份未知但卡色确认的普通卡只进入最低层',
               actual == expected, simulated_ocr=True)

    specified_plan = replace(plan, formation=tuple(
        FormationSlotPlan(slot=slot, name=f'指定{slot}') for slot in range(3)))
    specified_hand = Observation(base_page=BasePage.CARD, controls=(
        normal('未知身份普通', -1, crit=100, affinity='advantage', x=0),
        normal('指定普通1', 0, color='arts', crit=10, x=200),
        normal('指定普通2', 1, color='quick', crit=20, x=400),
    ))
    specified_unknown = select_cards(specified_hand, replace(strategy, strategy_order=('crit', 'cards')),
                                     specified_plan, 1)
    actual = [control.name for control in specified_unknown.controls]
    expected = '可用指令卡不足三张'
    add_report('strategy:specified-formation-excludes-unknown-normal',
               ROOT / 'captures/2026-11-20/key/card-select.png', expected,
               specified_unknown.diagnostic or actual, 'stop',
               '非空编队保持旧逻辑，已识别卡不足时不得用身份未知普通卡补齐',
               not actual and specified_unknown.diagnostic == expected, simulated_ocr=True)

    empty_np = Control('空槽宝具', ControlKind.CARD, (330, 100, 510, 330), True,
                       card=CommandCardIdentity(1, 'unknown', noble_phantasm=True, affinity='neutral'))
    np_hand = Observation(base_page=BasePage.CARD, controls=(
        normal('指定普通', 0, color='arts', x=0), empty_np,
        normal('空槽普通1', 1, x=200), normal('空槽普通2', 1, x=400), normal('空槽普通3', 1, x=600),
    ))
    np_strategy = replace(strategy, strategy_order=('servant', 'cards', 'crit'))
    disabled = select_cards(np_hand, np_strategy, plan, 1)
    actual = [control.name for control in disabled.controls]
    expected = ['指定普通', '空槽普通1', '空槽普通2']
    add_report('strategy:empty-slot-np-disabled', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', '空槽宝具同样受 allowNp 控制', actual == expected, simulated_ocr=True)
    enabled = select_cards(np_hand, replace(np_strategy, allow_np=True), plan, 1)
    actual = [control.name for control in enabled.controls]
    expected = ['指定普通', '空槽宝具', '空槽普通1']
    add_report('strategy:empty-slot-np-after-specified', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', '空槽宝具不越过指定从者，轮到空槽时先于其普通卡',
               actual == expected, simulated_ocr=True)

    without_servant = select_cards(
        np_hand, replace(np_strategy, allow_np=True, strategy_order=('crit', 'cards')), plan, 1)
    actual = [control.name for control in without_servant.controls]
    add_report('strategy:empty-slot-np-without-servant-criterion',
               ROOT / 'captures/2026-11-20/key/card-select.png', expected, actual, 'select',
               'strategyOrder 不含 servant 时，同从者宝具仍先于其普通卡',
               actual == expected, simulated_ocr=True)


def check_empty_slot_formation_once():
    from executor.core import RunOptions
    from executor.domain import (AttackPlan, ExecutionPlan, FallbackStrategy, FormationSlotPlan,
                                 TurnPlan, WavePlan)
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Overlay, Variant)

    team = Observation(base_page=BasePage.TEAM, fields={
        'team_servants': Field(FieldStatus.CONFIRMED, (705000, None, None)),
        'team_backline_servants': Field(FieldStatus.CONFIRMED, (4, None, None)),
        'team_mystic': Field(FieldStatus.CONFIRMED, 'default'),
    }, controls=(Control('开始任务', ControlKind.BUTTON, (1086, 635, 1248, 707), True),))
    changed_team = team.with_fields({
        'team_servants': Field(FieldStatus.CONFIRMED, (999, 999, 999)),
        'team_backline_servants': Field(FieldStatus.CONFIRMED, (999, 999, 999)),
    })
    battle = Observation(base_page=BasePage.BATTLE, fields={
        'wave': Field(FieldStatus.CONFIRMED, 1), 'turn': Field(FieldStatus.CONFIRMED, 1),
        'turn_boundary': Field(FieldStatus.CONFIRMED, True),
    }, controls=(Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),))
    boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
             (811, 415, 976, 655), (1066, 415, 1231, 655))
    card = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                card=CommandCardIdentity(slot, 'arts', crit_rate=10, affinity='neutral'))
        for index, slot in enumerate((0, 1, 2, 0, 1), start=1)))
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    result_page = Observation(base_page=BasePage.RESULT, controls=(
        Control('结算推进', ControlKind.BUTTON, (0, 0, 1280, 720), True),))
    drops = Observation(base_page=BasePage.DROPS, controls=(
        Control('下一步', ControlKind.BUTTON, (948, 606, 1272, 676), True),))
    repeat = Observation(base_page=BasePage.DROPS, overlays=frozenset({Overlay.REPEAT_DIALOG}),
                         controls=(Control('连续出击', ControlKind.BUTTON, (800, 540, 940, 610), True),))
    observations = [
        team, team, battle, battle, card, card, card, card, card, card, animation,
        result_page, result_page, drops, drops, repeat, repeat,
        changed_team, changed_team, battle, battle, card, card, card, card, card, card, animation,
        result_page, result_page, drops, drops, repeat,
    ]
    formation = (
        FormationSlotPlan(slot=0, name='指定0', svt_id=705000),
        FormationSlotPlan(slot=1, name=''), FormationSlotPlan(slot=2, name=''),
        FormationSlotPlan(slot=3, name='指定候补', svt_id=4),
        FormationSlotPlan(slot=4, name=''), FormationSlotPlan(slot=5, name=''),
    )
    strategy = FallbackStrategy(servant_order=('指定0',), servant_order_slots=(0,))
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1,
                        turns=(TurnPlan(attack=AttackPlan(mode='strategy', cards=())),)),),
        formation=formation, strategy=strategy,
    )
    result, _device, events = run_core_scenario(
        observations, [index * 0.5 for index in range(len(observations))], plan,
        options=RunOptions(run_count=2, write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['开始任务', '开始任务']
    actual = [control for control in controls if control == '开始任务']
    ok = result.status.value == 'completed' and result.progress.completed_runs == 2 and actual == expected
    add_report('executor:empty-slots-one-formation-check', TEAM_IMAGE, expected, actual, 'tap',
               f'reason={result.reason}; 指定候补已核对，后续队伍变化不再核对；controls={controls}',
               ok, simulated_ocr=True)


def check_strategy_scope_slots_and_region():
    from dataclasses import replace
    from executor.core.strategy import select_cards
    from executor.domain import ExecutionPlan, FallbackStrategy, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    global_strategy = FallbackStrategy(
        allow_np=False, servant_order=('从者0',), servant_order_slots=(0,), cards='arts',
        color_priority=('arts', 'quick', 'buster'), strategy_order=('cards', 'servant', 'crit'),
    )
    wave_strategy = replace(global_strategy, cards='quick', color_priority=('quick', 'arts', 'buster'))
    waves = tuple(WavePlan(number=wave, target=1, turns=(TurnPlan(),)) for wave in (1, 2))
    plan = ExecutionPlan(
        mystic_id='default', enemy_count=3, waves=waves,
        formation=(FormationSlotPlan(slot=0, name='从者0'),),
        strategy=global_strategy, strategy_by_wave={1: wave_strategy},
    )
    observation = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(0, color, crit_rate=10, affinity='neutral'))
        for index, color in enumerate(('buster', 'arts', 'quick', 'arts', 'quick'), 1)))
    first = select_cards(observation, global_strategy, plan, 1)
    second = select_cards(observation, global_strategy, plan, 2)
    actual = ([control.name for control in first.controls], first.source,
              [control.name for control in second.controls], second.source)
    expected = (['卡3', '卡5', '卡2'], 'wave', ['卡2', '卡4', '卡3'], 'global')
    add_report('strategy:wave-override-and-global', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', '按面覆盖与全局回退', actual == expected, simulated_ocr=True)

    same_name_strategy = FallbackStrategy(
        allow_np=False, servant_order=('娄希', '奥伯龙', '娄希'), servant_order_slots=(1, 2, 0),
        cards='buster', color_priority=('buster', 'arts', 'quick'),
        strategy_order=('servant', 'cards', 'crit'),
    )
    same_name_plan = replace(plan, formation=(
        FormationSlotPlan(slot=0, name='娄希'), FormationSlotPlan(slot=1, name='娄希'),
        FormationSlotPlan(slot=2, name='奥伯龙')), strategy=same_name_strategy, strategy_by_wave={})
    same_hand = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(slot, 'buster', crit_rate=10, affinity='neutral'))
        for index, slot in enumerate((0, 1, 1, 2, 2), 1)))
    selected = select_cards(same_hand, same_name_strategy, same_name_plan, 1)
    actual = [control.name for control in selected.controls]
    expected = ['卡2', '卡3', '卡4']
    add_report('strategy:same-name-slots', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', selected.reason, actual == expected, simulated_ocr=True)
    legacy = select_cards(same_hand, replace(same_name_strategy, servant_order_slots=None), same_name_plan, 1)
    add_report('strategy:legacy-name-warning', ROOT / 'captures/2026-11-20/key/card-select.png',
               True, bool(legacy.warnings), 'select', ';'.join(legacy.warnings),
               bool(legacy.warnings), simulated_ocr=True)

    animation = select_cards(replace(observation, base_page=BasePage.BATTLE), global_strategy, plan, 1)
    add_report('strategy:attack-animation-not-hand', ROOT / 'captures/2026-11-19-ember/36-attack-animation.png',
               'diagnostic', animation.diagnostic or 'selected', 'stop', animation.reason,
               not animation.controls and animation.diagnostic is not None, simulated_ocr=True)


def check_strategy_executor():
    from executor.domain import (AttackCardPlan, AttackPlan, ExecutionPlan, FallbackStrategy, FormationSlotPlan,
                                 TurnPlan, WavePlan)
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Variant)

    def battle(wave=1, turn=1):
        return Observation(base_page=BasePage.BATTLE, fields={
            'wave': Field(FieldStatus.CONFIRMED, wave),
            'turn': Field(FieldStatus.CONFIRMED, turn),
            'turn_boundary': Field(FieldStatus.CONFIRMED, True),
        }, controls=(Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),))

    def make_plan(turn, strategy=None):
        return ExecutionPlan(
            mystic_id='default', enemy_count=3,
            waves=(WavePlan(number=1, target=1, turns=(turn,)),),
            formation=tuple(FormationSlotPlan(slot=slot, name=f'从者{slot}') for slot in range(3)),
            strategy=strategy or FallbackStrategy(strategy_order=('cards', 'crit')),
        )

    def command(index, slot, card_type='arts'):
        boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
                 (811, 415, 976, 655), (1066, 415, 1231, 655))
        return Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                       card=CommandCardIdentity(slot, card_type, crit_rate=10, affinity='neutral'))

    np_cards = (
        Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A'),
        Control('宝具卡2', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:B'),
    )
    cards = Observation(base_page=BasePage.CARD, controls=(
        *np_cards, *(command(index, (index - 1) % 3) for index in range(1, 6))))
    unknown = Observation(base_page=BasePage.UNKNOWN)
    plan = make_plan(TurnPlan(np_positions={1: 'auto'}))
    result, device, events = run_core_scenario(
        [battle(), battle(), cards, cards, cards, cards, cards, cards, unknown, unknown],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '宝具卡2', '卡1', '卡2']
    add_report('strategy:executor-np-auto', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, controls, 'tap', f'reason={result.reason}',
               result.reason == 'unknown_timeout' and controls == expected, simulated_ocr=True)

    isolated_middle = Observation(base_page=BasePage.CARD, controls=(
        Control('宝具卡1', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:A'),
        *(command(index, (index - 1) % 3) for index in range(1, 6))))
    result, _, events = run_core_scenario(
        [battle(), battle(), isolated_middle, isolated_middle, isolated_middle, isolated_middle,
         isolated_middle, isolated_middle, unknown, unknown],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '宝具卡1', '卡1', '卡2']
    add_report('strategy:executor-np-fixed-middle', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, controls, 'tap', f'reason={result.reason}',
               result.reason == 'unknown_timeout' and controls == expected, simulated_ocr=True)

    effect_only = Observation(base_page=BasePage.CARD, controls=tuple(
        command(index, (index - 1) % 3) for index in range(1, 6)))
    result, _, events = run_core_scenario(
        [battle(), battle(), effect_only, isolated_middle, isolated_middle, isolated_middle,
         isolated_middle, isolated_middle, isolated_middle, unknown, unknown],
        [0.0, 0.5, 0.6, 0.7, 1.2, 1.3, 1.8, 1.9, 2.4, 2.5, 32.5], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '宝具卡1', '卡1', '卡2']
    waited = any(event.type == 'warning' and event.data.get('reason') == 'np_reservation_waiting'
                 for event in events.events)
    add_report('strategy:executor-np-waits-for-settled-frame',
               ROOT / 'captures/2026-11-22/key/card-np-charge-light-effect.png',
               expected, controls, 'tap', f'reason={result.reason}; waited={waited}',
               result.reason == 'unknown_timeout' and waited and controls == expected, simulated_ocr=True)

    obscured = Observation(base_page=BasePage.CARD, controls=(
        *(replace(item, card=None) for item in effect_only.controls), isolated_middle.controls[0]))
    result, _, events = run_core_scenario(
        [battle(), battle(), obscured, isolated_middle, unknown, unknown],
        [0.0, 0.5, 0.6, 0.7, 0.8, 30.8], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '宝具卡1', '卡1', '卡2']
    waited = any(event.type == 'warning' and event.data.get('reason') == 'card_batch_waiting'
                 for event in events.events)
    add_report('strategy:executor-card-batch-waits-for-complete-hand',
               ROOT / 'captures/2026-11-20/key/card-np-selected.png',
               expected, controls, 'tap', f'reason={result.reason}; waited={waited}',
               result.reason == 'unknown_timeout' and waited and controls == expected
               and controls.count('宝具卡1') == 1, simulated_ocr=True)

    next_battle = battle(turn=2)
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    normal_cards = Observation(base_page=BasePage.CARD, controls=tuple(
        command(index, (index - 1) % 3) for index in range(1, 6)))
    planned = TurnPlan(attack=AttackPlan(mode='verify', cards=tuple(
        AttackCardPlan(slot=slot, type='arts') for slot in range(3))))
    result, _, events = run_core_scenario(
        [battle(), battle(), normal_cards, normal_cards, normal_cards, normal_cards,
         normal_cards, normal_cards, animation, next_battle, next_battle,
         normal_cards, normal_cards, normal_cards, normal_cards, normal_cards, normal_cards,
         unknown, unknown],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0,
         3.1, 3.6, 3.7, 4.2, 4.3, 4.8, 4.9, 34.9], make_plan(planned))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3', '战斗跳过', '攻击', '卡1', '卡2', '卡3']
    add_report('strategy:executor-unplanned-turn', LOADING_IMAGE, expected, controls,
               'tap', f'reason={result.reason}; turn={result.progress.turn}',
               result.reason == 'unknown_timeout' and result.progress.turn == 2 and controls == expected,
               simulated_ocr=True)

    forward_plan = make_plan(planned)
    first_wave = forward_plan.waves[0]
    forward_plan = replace(forward_plan, waves=(
        first_wave, WavePlan(number=2, target=1, turns=first_wave.turns)))
    forward_battle = battle(wave=2, turn=2)
    result, _, events = run_core_scenario(
        [battle(), battle(), normal_cards, normal_cards, normal_cards, normal_cards,
         normal_cards, normal_cards, animation, forward_battle, forward_battle,
         normal_cards, normal_cards, normal_cards, normal_cards, normal_cards, normal_cards,
         unknown, unknown],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0,
         3.1, 3.6, 3.7, 4.2, 4.3, 4.8, 4.9, 34.9], forward_plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3', '战斗跳过', '攻击', '卡1', '卡2', '卡3']
    progress = (result.progress.wave, result.progress.turn)
    add_report('strategy:executor-forward-wave-planned-turn', LOADING_IMAGE, expected, controls,
               'tap', f'reason={result.reason}; progress={progress}',
               result.reason == 'unknown_timeout' and progress == (2, 1) and controls == expected,
               simulated_ocr=True)

    short = Observation(base_page=BasePage.CARD, controls=(command(1, 0), command(2, 1)))
    result, device, events = run_core_scenario(
        [battle(), battle(), short, short], [0.0, 0.5, 0.6, 30.6],
        make_plan(TurnPlan(attack=AttackPlan(mode='strategy', cards=()))))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = (result.reason == 'fallback_cards_unavailable' and controls == ['攻击']
          and len(device.actions) == 1 and events.events[-1].data.get('message'))
    add_report('strategy:executor-short-hand-stops', ROOT / 'captures/2026-11-20/key/card-select.png',
               'fallback_cards_unavailable', result.reason, 'stop', f'controls={controls}',
               bool(ok), simulated_ocr=True)


def check_order_change_auto_fill():
    from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FallbackStrategy,
                                 FormationSlotPlan, TurnPlan, WavePlan, compile_job)
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Overlay, Variant)

    job = json.loads((ROOT / 'executor/domain/fixtures/valid-multi-turn.json').read_text(encoding='utf-8'))
    second = dict(job['waves'][0]['turns'][0]['actions'][0])
    second['swapOutSlot'] = 1
    second['swapInSlot'] = 3
    job['waves'][1]['turns'][0]['actions'].insert(0, second)
    compiled = compile_job(json.dumps(job, ensure_ascii=False))
    actual = [(item.path, item.wave, item.turn) for item in compiled.diagnostics]
    expected = [('$.waves[1].turns[0].actions[0].kind', 2, 1)]
    add_report('executor:second-order-change-rejected', TEAM_IMAGE, expected, actual, 'stop',
               '同一场战斗最多一次主动换人', compiled.plan is None and actual == expected,
               simulated_ocr=True)

    formation = (
        FormationSlotPlan(slot=0, name='娄希', svt_id=705000, support=True),
        FormationSlotPlan(slot=1, name='娄希', svt_id=705000),
        FormationSlotPlan(slot=2, name='奥伯龙', svt_id=2800100),
        *(FormationSlotPlan(slot=slot, name='') for slot in range(3, 6)),
    )
    temporary = TemporaryDirectory()
    perception = Perception(identity_root=Path(temporary.name))
    try:
        perception.bind_formation(formation)
        ready = perception.read(Frame(
            image=(ROOT / 'captures/2026-11-20/key/battle-wave1.png').read_bytes(),
            width=1280, height=720, captured_at=0.0))
        process_path = ROOT / 'captures/2026-11-25/key/battle-faded-no-auto-fill.png'
        process = perception.read(Frame(image=process_path.read_bytes(), width=1280, height=720,
                                        captured_at=0.0))
    finally:
        perception.close()
        temporary.cleanup()
    progress = {'wave': Field(FieldStatus.CONFIRMED, 1), 'turn': Field(FieldStatus.CONFIRMED, 1),
                'turn_boundary': Field(FieldStatus.CONFIRMED, True)}
    ready = ready.with_fields(progress)
    process = process.with_fields(progress)
    used = replace(ready, controls=tuple(
        replace(control, actionable=False, reason='skill_cooldown', on_cooldown=True)
        if control.name == '从者2技能3' else control for control in ready.controls))
    actions = (
        ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2),
        ActionPlan(kind='skill', title='破灭的难题', detail='', icon='', member_slot=1, skill=2),
    )
    turn = TurnPlan(actions=actions, attack=AttackPlan(mode='verify', cards=tuple(
        AttackCardPlan(slot=slot, type='arts') for slot in range(3))))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=2, turns=(turn,)),), formation=formation)
    wrong = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(9, 'arts')) for index in range(1, 6)))
    result, _device, events = run_core_scenario(
        [ready, ready, process, used, used, wrong], [0.0, 0.1, 0.2, 0.3, 0.4, 0.5], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    fills = [event for event in events.events if event.type == 'auto_fill_confirmed']
    expected = ['敌方2', '从者2技能3', '战斗跳过', '攻击']
    ok = (not process.field('battle_servants').usable and result.reason == 'card_mismatch'
          and controls == expected and not fills)
    add_report('executor:faded-skill-frame-no-auto-fill', process_path, expected, controls, 'tap',
               f'field={process.field("battle_servants").status.value}; fills={len(fills)}', ok)

    formation = (*formation[:3], FormationSlotPlan(slot=3, name='候补', svt_id=500800))
    strategy = FallbackStrategy(
        servant_order=('娄希', '娄希', '奥伯龙', '候补'), servant_order_slots=(0, 1, 2, 3))
    turn = TurnPlan(attack=AttackPlan(mode='strategy', cards=()))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),),
                         formation=formation, strategy=strategy)
    battle = Observation(base_page=BasePage.BATTLE, fields=progress, controls=(
        Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),
        Control('战斗菜单', ControlKind.BUTTON, (1141, 162, 1245, 250), True),
    )).with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (3, 1, 2))})
    boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
             (811, 415, 976, 655), (1066, 415, 1231, 655))
    cards = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                card=CommandCardIdentity(slot, 'arts', crit_rate=10, affinity='neutral'))
        for index, slot in enumerate((3, 3, 1, 2, 2), start=1)))
    unknown = Observation(base_page=BasePage.UNKNOWN)
    result, _device, events = run_core_scenario(
        [battle, battle, cards, unknown, unknown], [0.0, 0.1, 0.2, 0.3, 30.3], plan)
    card_slots = [event.data['servant_slot'] for event in events.events
                  if event.type == 'action' and event.data.get('reason') == 'command_card_batch']
    fills = [event.data for event in events.events if event.type == 'auto_fill_confirmed']
    actual = (card_slots, [(item['out_slot'], item['in_slot']) for item in fills])
    expected = ([3, 3, 1], [(0, 3)])
    add_report('executor:auto-fill-inherits-priority', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'tap', f'reason={result.reason}; two stable frames required',
               result.reason == 'unknown_timeout' and actual == expected, simulated_ocr=True)

    order_action = ActionPlan(kind='orderChange', title='换人', detail='', icon='',
                              swap_out_slot=0, swap_in_slot=3)
    order_plan = ExecutionPlan(
        mystic_id='default', enemy_count=3,
        waves=(WavePlan(number=1, target=1, turns=(TurnPlan(actions=(order_action,)),)),),
        formation=formation)
    order_battle = ready.with_fields({'battle_servants': Field(FieldStatus.CONFIRMED, (0, 1, 2))})
    menu = Observation(base_page=BasePage.BATTLE, overlays=frozenset({Overlay.MASTER_SKILLS}), controls=tuple(
        Control(f'御主技能{index}', ControlKind.BUTTON, (850 + index * 90, 275, 920 + index * 90, 352), True)
        for index in range(1, 4)))
    order = Observation(base_page=BasePage.BATTLE, overlays=frozenset({Overlay.ORDER_CHANGE}), controls=(
        *(Control(f'先发第{index}位', ControlKind.TARGET, (index * 200, 250, index * 200 + 180, 565), True)
          for index in range(1, 4)),
        *(Control(f'候补第{index}位', ControlKind.TARGET, (600 + index * 200, 250, 780 + index * 200, 565), True)
          for index in range(1, 4)),
        Control('进行更替', ControlKind.BUTTON, (478, 591, 803, 661), True)))
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    result, _device, events = run_core_scenario(
        [order_battle, menu, order, order, order, animation, order_battle, order_battle],
        [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 30.6], order_plan)
    confirmed = [event for event in events.events if event.type == 'order_change_confirmed']
    add_report('executor:order-change-mismatch-keeps-pending', process_path,
               ('action_confirmation_timeout', 0), (result.reason, len(confirmed)), 'stop',
               '换后前排不符合预期时不得交换原始槽位映射',
               result.reason == 'action_confirmation_timeout' and not confirmed, simulated_ocr=True)


def check_executor_self_target():
    from executor.core import RunOptions, run
    from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan,
                                 TurnPlan, WavePlan)
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field, FieldStatus,
                                     Observation, Overlay, Variant)
    from executor.session import MemoryEventSink

    fields = {'wave': Field(FieldStatus.CONFIRMED, 1), 'turn': Field(FieldStatus.CONFIRMED, 1),
              'turn_boundary': Field(FieldStatus.CONFIRMED, True)}
    battle = Observation(base_page=BasePage.BATTLE, fields=fields, controls=(
        Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),
        Control('战斗菜单', ControlKind.BUTTON, (1141, 162, 1245, 250), True),
        Control('从者2技能3', ControlKind.SKILL, (539, 548, 600, 610), True),
    ))
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    wrong = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(4, 'arts')) for index in range(1, 6)))
    observations = [battle, battle, animation, battle, battle, wrong]
    times = [0.0, 0.5, 0.6, 1.0, 1.5, 1.6]
    frames = [Frame(image=str(index).encode(), width=1280, height=720, captured_at=at)
              for index, at in enumerate(times)]

    class ScriptedPerception:
        def read(self, frame):
            return replace(observations[int(frame.image)], frame_hash=frame.image.decode())

        def read_fields(self, frame, names, *, timeout=None):
            return replace(observations[int(frame.image)], frame_hash=frame.image.decode())

    action = ActionPlan(kind='skill', title='自身技能', detail='', icon='',
                        member_slot=1, skill=2, player_target_slot=1)
    cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
    turn = TurnPlan(actions=(action,), attack=AttackPlan(mode='verify', cards=cards))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),),
                         formation=tuple(FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)))
    device = FakeDevice(frames)
    events = MemoryEventSink()
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        result = run(plan, device, ScriptedPerception(), events, options=RunOptions(write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['从者2技能3', '战斗跳过', '攻击']
    ok = result.reason == 'card_mismatch' and result.progress.action_index == 1 and controls == expected
    add_report('executor:self-target-direct-animation', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}; progress={result.progress}', ok, simulated_ocr=True)

    delayed_target = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.SKILL_TARGET}),
                                 controls=tuple(Control(
                                     f'目标从者{index}', ControlKind.TARGET,
                                     (index * 300, 269, index * 300 + 200, 499), True)
                                     for index in range(1, 4)))
    result, _, events = run_core_scenario(
        [battle, animation, delayed_target, animation, battle, battle, wrong],
        [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6], plan)
    actions = [event for event in events.events if event.type == 'action']
    controls = [event.data['control'] for event in actions]
    expected = ['从者2技能3', '战斗跳过']
    ok = (result.status.value == 'stopped' and result.reason == 'skill_target_after_animation_skip'
          and controls == expected
          and sum(event.data.get('reason') == 'skill_animation_skip' for event in actions) == 1)
    add_report('executor:self-target-after-skip-stops', LOADING_IMAGE,
               'skill_target_after_animation_skip; no target selection', f'{result.reason}; {controls}',
               'stop', '目标晚于加速出现时安全停止，不执行乱序目标选择或第二次加速', ok, simulated_ocr=True)

    targeted = ActionPlan(kind='skill', title='单体技能', detail='', icon='',
                          member_slot=0, skill=0, player_target_slot=1)
    targeted_battle = replace(battle, controls=(
        battle.controls[0],
        Control('战斗菜单', ControlKind.BUTTON, (1141, 162, 1245, 250), True),
        Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True, 'slot_visible:1',
                on_cooldown=False),
    ))
    targeted_used = replace(targeted_battle, controls=(
        targeted_battle.controls[0], targeted_battle.controls[1],
        Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), False, 'skill_cooldown',
                on_cooldown=True),
    ))
    servant_target = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.SKILL_TARGET}),
                                 controls=tuple(Control(
                                     f'目标从者{index}', ControlKind.TARGET,
                                     (index * 300, 269, index * 300 + 200, 499), True)
                                     for index in range(1, 4)))
    targeted_turn = replace(turn, actions=(targeted,))
    targeted_plan = replace(plan, waves=(replace(plan.waves[0], turns=(targeted_turn,)),))
    result, _, events = run_core_scenario(
        [targeted_battle, servant_target, targeted_used, targeted_used, targeted_used, wrong],
        [0.0, 0.1, 0.2, 0.3, 0.4, 0.5], targeted_plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['从者1技能1', '目标从者2', '战斗跳过', '攻击']
    ok = result.reason == 'card_mismatch' and result.progress.action_index == 1 and controls == expected
    add_report('executor:targeted-skill-panel-open-close', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}', ok, simulated_ocr=True)

    result, _, events = run_core_scenario(
        [targeted_battle, targeted_battle, targeted_battle, servant_target,
         targeted_used, targeted_used, targeted_used, wrong],
        [0.0, 0.1, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6], targeted_plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['从者1技能1', '从者1技能1', '目标从者2', '战斗跳过', '攻击']
    recoveries = [event for event in events.events
                  if event.type == 'warning' and event.data.get('reason') == 'action_recovery']
    ok = (result.reason == 'card_mismatch' and controls == expected and len(recoveries) == 1
          and recoveries[0].data.get('attempt') == 1)
    add_report('executor:targeted-skill-one-new-frame-recovery', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}; recoveries={len(recoveries)}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [targeted_battle, targeted_battle, targeted_battle, targeted_battle, targeted_battle],
        [0.0, 0.1, 2.1, 2.2, 32.2], targeted_plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['从者1技能1', '从者1技能1']
    recoveries = [event for event in events.events
                  if event.type == 'warning' and event.data.get('reason') == 'action_recovery']
    ok = (result.status.value == 'stopped' and result.reason == 'target_selection_timeout'
          and controls == expected and len(device.actions) == 2 and len(recoveries) == 1
          and events.events[-1].data.get('stage') == 'skill_target'
          and events.events[-1].data.get('wait_ms', 0) >= 30000)
    add_report('executor:targeted-skill-recovery-failure-stops', LOADING_IMAGE,
               'target_selection_timeout; two skill taps', f'{result.reason}; {controls}',
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    master = ActionPlan(kind='master', title='御主二技能', detail='', icon='',
                        skill=1, player_target_slot=1)
    turn = replace(turn, actions=(master,))
    plan = replace(plan, waves=(replace(plan.waves[0], turns=(turn,)),))
    master_battle = replace(battle, controls=(
        Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),
        Control('战斗菜单', ControlKind.BUTTON, (1141, 162, 1245, 250), True),
        Control('御主技能', ControlKind.BUTTON, (1139, 271, 1248, 365), True),
    ))
    menu = Observation(base_page=BasePage.BATTLE, overlays=frozenset({Overlay.MASTER_SKILLS}), controls=tuple(
        Control(f'御主技能{index}', ControlKind.BUTTON, (850 + index * 90, 275, 920 + index * 90, 352), True)
        for index in range(1, 4)))
    target = Observation(
        base_page=BasePage.BATTLE, variants=frozenset({Variant.SKILL_TARGET}),
        overlays=frozenset({Overlay.MASTER_SKILLS}), controls=tuple(
            Control(f'目标从者{index}', ControlKind.TARGET, (index * 300, 269, index * 300 + 200, 499), True)
            for index in range(1, 4)))
    observations = [master_battle, master_battle, menu, menu, target, target,
                    animation, master_battle, master_battle, wrong]
    times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.0, 2.5, 2.6]
    frames = [Frame(image=str(index).encode(), width=1280, height=720, captured_at=at)
              for index, at in enumerate(times)]
    device = FakeDevice(frames)
    events = MemoryEventSink()
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        result = run(plan, device, ScriptedPerception(), events, options=RunOptions(write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['御主技能', '御主技能2', '目标从者2', '战斗跳过', '攻击']
    ok = result.reason == 'card_mismatch' and result.progress.action_index == 1 and controls == expected
    add_report('executor:master-target-with-overlay', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [master_battle, menu, menu, menu, menu, menu],
        [0.0, 0.1, 0.2, 2.2, 2.3, 32.3], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['御主技能', '御主技能2', '御主技能2']
    recoveries = [event for event in events.events
                  if event.type == 'warning' and event.data.get('reason') == 'action_recovery']
    ok = (result.status.value == 'stopped' and result.reason == 'target_selection_timeout'
          and controls == expected and len(device.actions) == 3 and len(recoveries) == 1)
    add_report('executor:master-target-one-recovery', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}; recoveries={len(recoveries)}', ok, simulated_ocr=True)


def check_executor_repeat_support():
    from executor.core import RunOptions, run
    from executor.domain import AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Overlay, SupportCandidate)
    from executor.session import MemoryEventSink

    repeat = Observation(base_page=BasePage.DROPS, overlays=frozenset({Overlay.REPEAT_DIALOG}),
                         controls=(Control('连续出击', ControlKind.BUTTON, (709, 554, 970, 612), True),))
    candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker', ce_id=9403520)
    support = Observation(base_page=BasePage.SUPPORT, controls=(
        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True, support=candidate),))
    team = Observation(base_page=BasePage.TEAM, fields={
        'team_servants': Field(FieldStatus.CONFIRMED, (705000, 2, 3)),
        'team_support_ce': Field(FieldStatus.CONFIRMED, 9403520),
        'team_mystic': Field(FieldStatus.CONFIRMED, 'default'),
    }, controls=(Control('开始任务', ControlKind.BUTTON, (1086, 635, 1248, 707), True),))
    fields = {'wave': Field(FieldStatus.CONFIRMED, 1), 'turn': Field(FieldStatus.CONFIRMED, 1),
              'turn_boundary': Field(FieldStatus.CONFIRMED, True)}
    battle = Observation(base_page=BasePage.BATTLE, fields=fields, controls=(
        Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),))
    wrong = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(4, 'arts')) for index in range(1, 6)))
    observations = [repeat, repeat, support, support, team, team, battle, battle, wrong]
    times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4]
    frames = [Frame(image=str(index).encode(), width=1280, height=720, captured_at=at)
              for index, at in enumerate(times)]

    class ScriptedPerception:
        def read(self, frame):
            return replace(observations[int(frame.image)], frame_hash=frame.image.decode())

        def read_fields(self, frame, names, *, timeout=None):
            return replace(observations[int(frame.image)], frame_hash=frame.image.decode())

    formation = [FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)]
    formation[0] = FormationSlotPlan(slot=0, name='娄希', svt_id=705000, role='Berserker',
                                     support=True, support_required=True, ce_id=9403520)
    formation[1] = FormationSlotPlan(slot=1, name='1', svt_id=2)
    formation[2] = FormationSlotPlan(slot=2, name='2', svt_id=3)
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=tuple(
        AttackCardPlan(slot=slot, type='arts') for slot in range(3))))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),), formation=tuple(formation))
    device = FakeDevice(frames)
    events = MemoryEventSink()
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        result = run(plan, device, ScriptedPerception(), events, options=RunOptions(run_count=2, write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['连续出击', '职阶筛选:berserker', '助战卡片第1行', '开始任务', '攻击']
    ok = result.reason == 'card_mismatch' and controls == expected
    add_report('executor:repeat-continues-through-support', SUPPORT_IMAGE, expected, controls,
               'stop', f'reason={result.reason}', ok, simulated_ocr=True)

    observations = [repeat, repeat, support, support, battle, battle, wrong, battle]
    times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 31.2]
    frames = [Frame(image=str(index).encode(), width=1280, height=720, captured_at=at)
              for index, at in enumerate(times)]
    device = FakeDevice(frames)
    events = MemoryEventSink()
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        result = run(plan, device, ScriptedPerception(), events,
                     options=RunOptions(run_count=2, write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['连续出击', '职阶筛选:berserker', '助战卡片第1行', '攻击']
    ok = result.reason == 'card_mismatch' and controls == expected
    add_report('executor:repeat-support-skips-team', SUPPORT_IMAGE, expected, controls,
               'stop', f'reason={result.reason}', ok, simulated_ocr=True)


def check_executor_recovery():
    from executor.core import RunOptions
    from executor.core.ap_policy import RecoveryItem, RecoveryPolicy
    from executor.domain import AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, Control, ControlKind, Observation, Overlay

    cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=cards))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),),
                         formation=tuple(FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)))
    bronze = Observation(base_page=BasePage.AP_RECOVERY, controls=(
        Control('青铜果实', ControlKind.ITEM, (315, 290, 981, 415), True),))
    team = Observation(base_page=BasePage.TEAM)
    support = Observation(base_page=BasePage.SUPPORT)
    result, device, events = run_core_scenario(
        [bronze, bronze, team, bronze, bronze, support], [0.0, 0.5, 0.6, 0.7, 1.2, 1.3], plan)
    used = [dict(event.data) for event in events.events if event.type == 'recovery_used']
    expected = [{'item': 'bronze', 'count': 1}, {'item': 'bronze', 'count': 2}]
    ok = len(device.actions) == 2 and used == expected and result.progress.recovery_used == {'bronze': 2}
    add_report('executor:recovery-whitelist-and-counts', AP_RECOVERY_IMAGE, expected, used,
               'tap', f'taps={device.actions}; reason={result.reason}', ok, simulated_ocr=True)

    confirm = Observation(base_page=BasePage.AP_RECOVERY,
                          overlays=frozenset({Overlay.AP_RECOVERY_CONFIRM}), controls=(
        Control('青铜果实', ControlKind.ITEM, (315, 100, 981, 240), True),
        Control('决定', ControlKind.BUTTON, (708, 550, 972, 614), True),
        Control('取消', ControlKind.BUTTON, (307, 550, 572, 614), True),
    ))
    result, _, events = run_core_scenario(
        [bronze, bronze, confirm, confirm, team], [0.0, 0.5, 0.6, 1.1, 1.2], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    used = [dict(event.data) for event in events.events if event.type == 'recovery_used']
    expected = ['青铜果实', '决定']
    ok = controls == expected and used == [{'item': 'bronze', 'count': 1}]
    add_report('executor:recovery-confirmation-bronze',
               ROOT / 'captures/2026-11-22/key/ap-recovery-confirm-bronze.png',
               expected, controls, 'tap', f'recovery={used}; reason={result.reason}', ok, simulated_ocr=True)

    top = Observation(base_page=BasePage.AP_RECOVERY, controls=(
        Control('圣晶石', ControlKind.ITEM, (315, 120, 981, 265), True),
        Control('黄金果实', ControlKind.ITEM, (315, 267, 981, 412), True),
        Control('白银果实', ControlKind.ITEM, (315, 414, 981, 559), True),
    ))
    result, device, events = run_core_scenario(
        [top, top, bronze, bronze, team], [0.0, 0.5, 0.6, 1.1, 1.2], plan)
    actions = [action['action'] for action in device.actions]
    used = [dict(event.data) for event in events.events if event.type == 'recovery_used']
    ok = actions == ['swipe', 'tap'] and used == [{'item': 'bronze', 'count': 1}]
    add_report('executor:recovery-scrolls-to-bronze', AP_RECOVERY_IMAGE, ['swipe', 'tap'], actions,
               'tap', f'recovery={used}', ok, simulated_ocr=True)

    valuable = Observation(base_page=BasePage.AP_RECOVERY, controls=(
        Control('白银果实', ControlKind.ITEM, (315, 143, 981, 267), True),
        Control('青铜果实', ControlKind.ITEM, (315, 290, 981, 415), True),
    ))
    result, device, events = run_core_scenario(
        [valuable], [0.0], plan,
        options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset({RecoveryItem.GOLD})), write_evidence=False))
    ok = result.reason == 'ap_recovery' and not device.actions
    add_report('executor:recovery-non-whitelist-stops', AP_RECOVERY_IMAGE, 'ap_recovery', result.reason,
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [bronze], [0.0], plan,
        options=RunOptions(recovery=RecoveryPolicy(allowed=frozenset()), write_evidence=False))
    ok = result.reason == 'ap_recovery' and not device.actions
    add_report('executor:recovery-empty-whitelist-stops', AP_RECOVERY_IMAGE, 'ap_recovery', result.reason,
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    repeat = Observation(base_page=BasePage.DROPS, overlays=frozenset({Overlay.REPEAT_DIALOG}),
                         controls=(Control('连续出击', ControlKind.BUTTON, (709, 554, 970, 612), True),))
    result, device, events = run_core_scenario(
        [repeat, repeat, bronze], [0.0, 0.5, 0.6], plan,
        options=RunOptions(recovery=RecoveryPolicy(drain_after_turns=True), write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = (result.status.value == 'completed' and result.reason == 'ap_drained'
          and controls == ['连续出击'] and not result.progress.recovery_used)
    add_report('executor:recovery-drain-stops-without-items', AP_RECOVERY_IMAGE, 'ap_drained', result.reason,
               'complete', f'controls={controls}; recovery={result.progress.recovery_used}', ok,
               simulated_ocr=True)

    result, device, events = run_core_scenario(
        [repeat, repeat, bronze, bronze, team], [0.0, 0.5, 0.6, 1.1, 1.2], plan,
        options=RunOptions(run_count=2, write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    used = [dict(event.data) for event in events.events if event.type == 'recovery_used']
    expected = ['连续出击', '青铜果实']
    ok = controls == expected and used == [{'item': 'bronze', 'count': 1}]
    add_report('executor:recovery-after-repeat', AP_RECOVERY_IMAGE, expected, controls,
               'tap', f'recovery={used}; reason={result.reason}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [repeat, repeat, bronze, bronze, repeat, repeat, team],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8], plan,
        options=RunOptions(run_count=2, write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    used = [dict(event.data) for event in events.events if event.type == 'recovery_used']
    expected = ['连续出击', '青铜果实', '连续出击']
    ok = controls == expected and used == [{'item': 'bronze', 'count': 1}]
    add_report('executor:recovery-back-to-repeat', AP_RECOVERY_IMAGE, expected, controls,
               'tap', f'recovery={used}; reason={result.reason}', ok, simulated_ocr=True)

    result, device, events = run_core_scenario(
        [bronze, bronze, bronze], [0.0, 0.5, 31.0], plan)
    used = [dict(event.data) for event in events.events if event.type == 'recovery_used']
    ok = (result.reason == 'action_confirmation_timeout' and len(device.actions) == 1 and not used
          and not result.progress.recovery_used)
    add_report('executor:recovery-unconfirmed-not-counted', AP_RECOVERY_IMAGE,
               'action_confirmation_timeout; no recovery_used', result.reason,
               'stop', f'taps={len(device.actions)}; recovery={used}', ok, simulated_ocr=True)


def check_executor_process():
    with TemporaryDirectory() as directory:
        base = [sys.executable, '-m', 'executor', str(ROOT / 'examples/sample-job.json'),
                '--serial', 'fake', '--region', 'CN', '--battles', '1',
                '--recovery', 'bronze', '--fake']
        completed = subprocess.run(
            [*base, '--run-dir', str(Path(directory) / 'logs/farm/complete'), '--fake-interval', '0'],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20)
        stopped = subprocess.Popen(
            [*base, '--run-dir', str(Path(directory) / 'logs/farm/stop'), '--fake-interval', '5'],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        stdout, _ = stopped.communicate('stop\n', timeout=20)
        finished_rows = [json.loads(line) for line in completed.stdout.splitlines()]
        stopped_rows = [json.loads(line) for line in stdout.splitlines()]
        history = [json.loads(line) for line in
                   (Path(directory) / 'logs/run-history.jsonl').read_text(encoding='utf-8').splitlines()]
        complete_summary = (Path(directory) / 'logs/farm/complete/summary.md').read_text(encoding='utf-8')
        stopped_summary = (Path(directory) / 'logs/farm/stop/summary.md').read_text(encoding='utf-8')
        summaries_ok = ('运行状态：正常完成' in complete_summary and '完成场数：1' in complete_summary
                        and '阿尔托莉雅' in complete_summary and '第 2 场' not in complete_summary
                        and '运行状态：手动停止' in stopped_summary and '完成场数：0' in stopped_summary)
        actual = f"complete={completed.returncode}:{finished_rows[-1]['type']}; stop={stopped.returncode}:{stopped_rows[-1]['type']}; history={[row['result'] for row in history]}; summaries={summaries_ok}"
        ok = (completed.returncode == 0 and finished_rows[-1]['type'] == 'finished'
              and stopped.returncode == 1 and stopped_rows[-1]['type'] == 'stopped'
              and not any(row['type'] == 'action' for row in stopped_rows)
              and [row['result'] for row in history] == ['completed', 'stopped'] and summaries_ok)
        add_report('executor:process-jsonl-and-stop', LOADING_IMAGE, 'complete=0:finished; stop=1:stopped',
                   actual, 'stop', 'UTF-8 JSON Lines；停止后无动作；自动生成中文摘要', ok, simulated_ocr=True)


def check_executor_safety():
    from executor.core import RunOptions
    from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan,
                                 TurnPlan, WavePlan)
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Overlay, SupportCandidate, Variant)

    def make_plan(actions=(), wave_count=1, support_required=False):
        cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
        turn = TurnPlan(actions=tuple(actions), attack=AttackPlan(mode='verify', cards=cards))
        waves = tuple(WavePlan(number=index, target=1, turns=(turn,))
                      for index in range(1, wave_count + 1))
        formation = [FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)]
        if support_required:
            formation[0] = FormationSlotPlan(slot=0, name='娄希', svt_id=705000, role='Berserker',
                                             support=True, support_required=True)
        return ExecutionPlan(mystic_id='default', enemy_count=3, waves=waves, formation=tuple(formation))

    def battle(wave=1, turn=1, overlays=()):
        fields = {'wave': Field(FieldStatus.CONFIRMED, wave), 'turn': Field(FieldStatus.CONFIRMED, turn),
                  'turn_boundary': Field(FieldStatus.CONFIRMED, True)}
        return Observation(base_page=BasePage.BATTLE, fields=fields, overlays=frozenset(overlays), controls=(
            Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),
            Control('从者1技能1', ControlKind.SKILL, (43, 548, 104, 610), True),
        ))

    boxes = ((46, 415, 211, 655), (301, 415, 466, 655), (556, 415, 721, 655),
             (811, 415, 976, 655), (1066, 415, 1231, 655))
    card = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                card=CommandCardIdentity(index - 1, 'arts')) for index in range(1, 6)))
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    wrong = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, boxes[index - 1], True,
                card=CommandCardIdentity(4, 'arts')) for index in range(1, 6)))
    skill = ActionPlan(kind='skill', title='一技能', detail='', icon='', member_slot=0, skill=0)
    ready = replace(battle(), controls=(*battle().controls, Control(
        '战斗菜单', ControlKind.BUTTON, (1141, 162, 1245, 250), True)))
    result, _, events = run_core_scenario(
        [ready, animation, ready, ready, wrong], [0.0, 0.1, 0.2, 0.3, 0.4], make_plan((skill,)))
    actions = [event for event in events.events if event.type == 'action']
    controls = [event.data['control'] for event in actions]
    expected = ['从者1技能1', '战斗跳过', '攻击']
    ok = (result.reason == 'card_mismatch' and controls == expected
          and [event.timestamp for event in actions] == [0.0, 0.1, 0.3]
          and all(event.data.get('channel_ms', -1) >= 0 and event.data.get('wait_ms', -1) >= 0
                  and event.data.get('input_finished_at', 0) > 0 for event in actions)
          and sum(event.data.get('reason') == 'skill_animation_skip' for event in actions) == 1)
    add_report('executor:untargeted-skill-battle-recovery', LOADING_IMAGE, expected, controls,
               'stop', '无目标技能也加速一次，并等待两个连续战斗可操作新帧', ok,
               simulated_ocr=True)

    result, _, events = run_core_scenario(
        [battle(), animation, battle(), ready, ready, wrong],
        [0.0, 0.1, 0.2, 0.3, 0.4, 0.5], make_plan((skill,)))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = result.reason == 'card_mismatch' and controls == expected
    add_report('executor:untargeted-skill-menu-recovery', LOADING_IMAGE, expected, controls,
               'stop', '攻击和战斗菜单均恢复后才推进；攻击仍必须进入选卡页', ok,
               simulated_ocr=True)

    first = ActionPlan(kind='skill', title='二技能', detail='', icon='', member_slot=0, skill=1)
    second = ActionPlan(kind='skill', title='三技能', detail='', icon='', member_slot=0, skill=2)
    consecutive = replace(ready, controls=(
        *ready.controls,
        Control('从者1技能2', ControlKind.SKILL, (130, 548, 190, 610), True),
        Control('从者1技能3', ControlKind.SKILL, (217, 548, 278, 610), True),
    ))
    input_times = [1795498380.6858945, 1795498380.7996883, 1795498380.9,
                   1795498381.6858945, 1795498381.8, 1795498381.9, 1795498382.6858945]
    with mock.patch('executor.core.runner.time.time', side_effect=input_times):
        result, _, events = run_core_scenario(
            [consecutive, animation, consecutive, consecutive, animation,
             consecutive, consecutive, wrong],
            [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], make_plan((first, second)))
    actions = [event for event in events.events if event.type == 'action']
    controls = [event.data['control'] for event in actions]
    expected = ['从者1技能2', '战斗跳过', '从者1技能3', '战斗跳过', '攻击']
    interval = (actions[2].data['input_finished_at'] - actions[0].data['input_finished_at']
                if len(actions) == 5 else -1)
    ok = (result.reason == 'card_mismatch' and controls == expected and interval > 0.3613193
          and actions[2].data['input_finished_at'] > 1795498381.0472138)
    add_report('executor:skill-live-361ms-early-input-regression', LOADING_IMAGE,
               '第二技能输入晚于实机 0.361 秒过早点击',
               f'{controls}; input_interval={interval:.6f}', 'stop',
               'run-1795498348917-18264 输入完成时间回归；不推断技能生效', ok,
               simulated_ocr=True)

    merlin = ActionPlan(kind='skill', title='梅林一技能', detail='', icon='', member_slot=1, skill=0)
    cross_servant = replace(consecutive, controls=(
        *consecutive.controls,
        Control('从者2技能1', ControlKind.SKILL, (365, 548, 426, 610), True),
    ))
    input_times = [1795592987.2630138, 1795592987.395141, 1795592987.5122046,
                   1795592988.2630138, 1795592988.3, 1795592988.4, 1795592989.2630138]
    with mock.patch('executor.core.runner.time.time', side_effect=input_times):
        result, _, events = run_core_scenario(
            [cross_servant, animation, cross_servant, cross_servant, cross_servant,
             animation, cross_servant, cross_servant, wrong],
            [0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2, 1.3, 1.4], make_plan((second, merlin)))
    actions = [event for event in events.events if event.type == 'action']
    controls = [event.data['control'] for event in actions]
    expected = ['从者1技能3', '战斗跳过', '从者2技能1', '战斗跳过', '攻击']
    interval = (actions[2].data['input_finished_at'] - actions[0].data['input_finished_at']
                if len(actions) == 5 else -1)
    ok = result.reason == 'card_mismatch' and controls == expected and interval >= 1.0
    add_report('executor:skill-live-368ms-early-input-regression', LOADING_IMAGE,
               '梅林一技能不早于前一技能 1 秒', f'{controls}; input_interval={interval:.6f}', 'stop',
               'run-1795592952313-23360 的 0.368 秒输入间隔回归；不推断技能生效', ok,
               simulated_ocr=True)

    merlin_second = ActionPlan(kind='skill', title='梅林二技能', detail='', icon='', member_slot=1, skill=1)
    merlin_battle = replace(ready, controls=(
        *ready.controls,
        Control('从者2技能1', ControlKind.SKILL, (365, 548, 426, 610), True,
                'slot_visible:2', on_cooldown=False),
        Control('从者2技能2', ControlKind.SKILL, (452, 548, 513, 610), True,
                'slot_visible:2', on_cooldown=False),
    ))
    result, _, events = run_core_scenario(
        [merlin_battle, merlin_battle, merlin_battle, merlin_battle],
        [0.0, 0.1, 2.2, 33.0], make_plan((merlin, merlin_second)))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['从者2技能1', '战斗跳过', '从者2技能1']
    ok = result.reason == 'action_confirmation_timeout' and controls == expected
    add_report('executor:merlin-second-requires-first-cooldown',
               ROOT / 'captures/2026-11-23/key/skill-cooldowns.png', expected, controls, 'stop',
               'run-1795596280454-23360：一技能未进冷却时重试并停止，不得使用二技能', ok,
               simulated_ocr=True)

    repeat = battle(overlays=(Overlay.REPEAT_DIALOG,))
    repeat = replace(repeat, controls=(*repeat.controls,
                                      Control('连续出击', ControlKind.BUTTON, (709, 554, 970, 612), True)))
    result, device, _ = run_core_scenario([repeat], [0.0], make_plan())
    ok = result.reason == 'unexpected_overlay' and not device.actions
    add_report('executor:unexpected-repeat-stops', LOADING_IMAGE, 'unexpected_overlay', result.reason,
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    friend = Observation(base_page=BasePage.SUPPORT, overlays=frozenset({Overlay.FRIEND_REQUEST}))
    result, device, _ = run_core_scenario([friend, friend], [0.0, 30.0], make_plan())
    ok = result.reason == 'overlay_recovery_unavailable' and not device.actions
    add_report('executor:overlay-recovery-timeout', SUPPORT_IMAGE, 'overlay_recovery_unavailable', result.reason,
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
    support = Observation(base_page=BasePage.SUPPORT, controls=(
        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True, support=candidate),))
    result_page = Observation(base_page=BasePage.RESULT, controls=(
        Control('结算推进', ControlKind.BUTTON, (0, 0, 1280, 720), True),))
    drops = Observation(base_page=BasePage.DROPS, controls=(
        Control('下一步', ControlKind.BUTTON, (948, 606, 1272, 676), True),))
    observations = [battle(), battle(), card, card, card, card, card, card, animation,
                    battle(2, 2), battle(2, 2), card, card, card, card, card, card, animation,
                    result_page, result_page, drops, drops, repeat, repeat, support, support]
    result, _, events = run_core_scenario(
        observations, [index * 0.5 for index in range(len(observations))],
        make_plan(wave_count=2, support_required=True), options=RunOptions(run_count=2, write_evidence=False))
    support_states = [event for event in events.events
                      if event.type == 'state_changed' and event.data['state'] == 'support']
    ok = bool(support_states) and support_states[0].data['wave'] == 2
    add_report('executor:repeat-progress-resets-after-next-page', SUPPORT_IMAGE, 2,
               support_states[0].data['wave'] if support_states else None,
               'stop', f'reason={result.reason}', ok, simulated_ocr=True)

    resumed = battle(2, 3).with_fields({'turn': Field(FieldStatus.UNCERTAIN, None)})
    observations = [resumed, resumed, card, animation,
                    battle(3, 4), battle(3, 4), wrong]
    result, _, events = run_core_scenario(
        observations, [index * 0.5 for index in range(len(observations))], make_plan(wave_count=3))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3', '战斗跳过', '攻击']
    progress = (result.progress.wave, result.progress.turn)
    ok = result.reason == 'card_mismatch' and controls == expected and progress == (3, 1)
    add_report('executor:resume-midbattle-fallback', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}; progress={progress}', ok, simulated_ocr=True)

    result, _, events = run_core_scenario(
        [drops, repeat, repeat, repeat, support, support], [0.0, 0.1, 0.6, 1.1, 1.2, 1.7],
        make_plan(support_required=True), options=RunOptions(run_count=2, write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = controls.count('连续出击') == 1
    add_report('executor:persistent-repeat-clicks-once', SUPPORT_IMAGE, 1, controls.count('连续出击'),
               'stop', f'controls={controls}', ok, simulated_ocr=True)

    support_repeat = Observation(base_page=BasePage.SUPPORT, overlays=frozenset({Overlay.REPEAT_DIALOG}),
                                 controls=(Control('连续出击', ControlKind.BUTTON,
                                                   (709, 554, 970, 612), True),))
    result, device, _ = run_core_scenario([drops, support_repeat], [0.0, 0.1], make_plan())
    ok = result.reason == 'unexpected_overlay' and not device.actions
    add_report('executor:repeat-on-support-stops', SUPPORT_IMAGE, 'unexpected_overlay', result.reason,
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
    wrong_support = Observation(base_page=BasePage.SUPPORT, controls=(
        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True, support=wrong_candidate),
        Control('列表更新', ControlKind.BUTTON, (888, 98, 972, 162), True),
    ))
    cooldown = Observation(base_page=BasePage.SUPPORT, overlays=frozenset({Overlay.SUPPORT_REFRESH_COOLDOWN}),
                           controls=(Control('关闭', ControlKind.BUTTON, (508, 531, 772, 599), True),))
    result, device, events = run_core_scenario(
        [wrong_support] * 7 + [cooldown, cooldown, wrong_support, support, support,
                              Observation(base_page=BasePage.UNKNOWN), Observation(base_page=BasePage.UNKNOWN)],
        [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 9.0, 9.5, 10.0, 40.0],
        make_plan(support_required=True))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['职阶筛选:berserker', '助战列表向下', '列表更新', '关闭', '助战卡片第1行']
    add_report('executor:support-refresh-cooldown', ROOT / 'captures/2026-11-22/key/support-refresh-cooldown.png',
               expected, controls, 'tap', f'reason={result.reason}',
               controls == expected and (device.actions[3]['x'], device.actions[3]['y']) == (640, 565),
               simulated_ocr=True)

    target = ActionPlan(kind='target', title='切换目标', detail='', icon='', to_target=2)
    target_battle = replace(battle(), controls=(*battle().controls, *(
        Control(f'敌方{index}', ControlKind.ENEMY, (index * 250, 20, index * 250 + 200, 100), True)
        for index in range(1, 4))))
    detail = Observation(base_page=BasePage.BATTLE, overlays=frozenset({Overlay.BATTLE_STATUS_CHANGE_DETAIL}),
                         controls=(Control('关闭', ControlKind.BUTTON, (990, 640, 1010, 660), True),))
    result, device, events = run_core_scenario(
        [target_battle, detail, detail, target_battle, target_battle, wrong],
        [0.0, 0.1, 0.6, 0.7, 1.2, 1.3], make_plan((target,)))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['敌方2', '关闭', '攻击']
    add_report('executor:battle-status-change-detail', ROOT / 'captures/2026-11-22/key/battle-status-change-detail.png',
               expected, controls, 'tap', f'reason={result.reason}',
               controls == expected and (device.actions[1]['x'], device.actions[1]['y']) == (1000, 650),
               simulated_ocr=True)

    plan = make_plan((target,))
    plan = replace(plan, waves=(replace(plan.waves[0], target=2),))
    result, _, events = run_core_scenario(
        [target_battle, target_battle, wrong], [0.0, 0.1, 0.2], plan)
    actions = [event for event in events.events if event.type == 'action']
    controls = [event.data['control'] for event in actions]
    expected = ['敌方2', '攻击']
    ok = (result.reason == 'card_mismatch' and controls == expected
          and [event.timestamp for event in actions] == [0.0, 0.1])
    add_report('executor:wave-target-action-confirmation', LOADING_IMAGE, expected, controls,
               'tap', f'reason={result.reason}; times={[event.timestamp for event in actions]}',
               ok, simulated_ocr=True)

    from executor.device import DeviceConfig, DeviceError

    class InterruptedScrcpyDevice(FakeDevice):
        def __init__(self, frames):
            super().__init__(frames, config=DeviceConfig(serial='replay-scrcpy', region='CN', channel='scrcpy'))

        def tap(self, x, y):
            raise DeviceError('scrcpy 控制通道中断：回放故障注入')

    with mock.patch.object(legacy, 'FakeDevice', InterruptedScrcpyDevice):
        result, _, events = run_core_scenario([battle(), battle()], [0.0, 0.5], make_plan())
    ok = (result.reason == 'device_error' and events.events[-1].data.get('reason') == 'device_error'
          and not any(event.type == 'channel_changed' for event in events.events))
    add_report('executor:scrcpy-interruption-no-fallback', LOADING_IMAGE, 'device_error/no-channel-change',
               f'{result.reason}/{[event.type for event in events.events]}', 'stop',
               '控制中断在动作边界停止，不跨通道续跑', ok, simulated_ocr=True)

    class UnresponsiveScrcpyDevice(FakeDevice):
        def __init__(self, frames):
            super().__init__(frames, config=DeviceConfig(serial='replay-scrcpy', region='CN', channel='scrcpy'))

    with mock.patch.object(legacy, 'FakeDevice', UnresponsiveScrcpyDevice):
        result, device, events = run_core_scenario(
            [ready, ready, ready, ready], [0.0, 0.5, 2.5, 32.5], make_plan())
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = (result.reason == 'action_confirmation_timeout' and controls == ['攻击', '攻击']
          and len(device.actions) == 2
          and not any(event.type == 'channel_changed' for event in events.events))
    add_report('executor:scrcpy-input-success-without-screen-response', LOADING_IMAGE,
               'action_confirmation_timeout; one recovery/no-channel-change',
               f'{result.reason}; {controls}', 'stop',
               '输入无画面响应时基于新帧最多恢复一次，仍失败则停止', ok, simulated_ocr=True)

    card_without_hand = Observation(base_page=BasePage.CARD)
    result, _, events = run_core_scenario(
        [ready, ready, ready, card_without_hand], [0.0, 0.5, 2.5, 2.6], make_plan())
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    recoveries = [event for event in events.events
                  if event.type == 'warning' and event.data.get('reason') == 'action_recovery']
    ok = (controls == ['攻击', '攻击'] and len(recoveries) == 1
          and recoveries[0].timestamp == 2.5 and recoveries[0].data.get('control') == '攻击')
    add_report('executor:attack-recovers-from-fresh-battle-frame', LOADING_IMAGE,
               'two attacks; one recovery at 2.5s', f'{controls}; {recoveries}', 'tap',
               '实机故障回归：攻击首次未生效时以新战斗帧有限恢复', ok, simulated_ocr=True)

    import struct
    from executor.device.scrcpy import ScrcpyDevice
    down = struct.unpack('>BBQiiHHHII', ScrcpyDevice._touch(0, 930, 130, 1280, 720))
    up = struct.unpack('>BBQiiHHHII', ScrcpyDevice._touch(1, 930, 130, 1280, 720))
    expected = ((2, 0, 0, 930, 130, 1280, 720, 0xFFFF, 0, 0),
                (2, 1, 0, 930, 130, 1280, 720, 0, 0, 0))
    ok = (down, up) == expected
    add_report('executor:scrcpy-touch-wire-protocol', LOADING_IMAGE, expected, (down, up), 'stop',
               '实机故障回归：类型0是按键，触摸必须使用类型2及完整32字节字段', ok,
               simulated_ocr=True)

    import threading
    from executor.device.config import DeviceMetrics
    sampled = object.__new__(ScrcpyDevice)
    sampled._metrics = DeviceMetrics(serial='replay', width=1280, height=720, density=240, region='CN')
    sampled._input_lock = threading.Lock()
    sampled._send = mock.Mock()
    with mock.patch('executor.device.scrcpy.time.sleep', return_value=None):
        sampled.tap(930, 130)
    packets = [struct.unpack('>BBQiiHHHII', call.args[0]) for call in sampled._send.call_args_list]
    actions = [packet[1] for packet in packets]
    points = {packet[3:7] for packet in packets}
    ok = actions == [0, 2, 2, 2, 1] and points == {(930, 130, 1280, 720)}
    add_report('executor:scrcpy-tap-sampled-contact', LOADING_IMAGE,
               'DOWN/MOVE/MOVE/MOVE/UP', actions, 'tap',
               '实机故障回归：40ms 点击期间保留静止采样，不只发送 DOWN/UP', ok,
               simulated_ocr=True)


# 旧原型的纯流程仅作为回归参照保留；所有设备输入都由用例 mock。
legacy = sys.modules[__name__]
state_templates = load_templates()
_ocr_engine = None


class FarmError(RuntimeError):
    pass


def tap(x, y, reason=''):
    raise AssertionError('旧原型回放的设备输入必须 mock')


def swipe(x1, y1, x2, y2, ms=350):
    raise AssertionError('旧原型回放的设备输入必须 mock')


def shot(name='legacy.png'):
    raise AssertionError('旧原型回放的设备截图必须 mock')


def trace(message):
    print(message, flush=True)


def ocr(image):
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    result, _ = _ocr_engine(image)
    rows = []
    for box, text, score in result or []:
        if score < 0.5:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        rows.append((text.strip(), sum(xs) / 4, sum(ys) / 4))
    return rows


def has(rows, needle):
    return any(needle in text for text, _, _ in rows)


def find(rows, needle):
    for row in rows:
        if needle in row[0]:
            return row
    return None


def click_row(rows, needle, reason='', fallback=None):
    hit = find(rows, needle)
    if hit:
        _, x, y = hit
        tap(x, y, reason or needle)
        return True
    if fallback:
        tap(fallback[0], fallback[1], f'{reason or needle}（固定坐标回退）')
        return True
    return False


def wait_state(pred, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        rows = ocr(shot())
        result = pred(rows)
        if result:
            return result
        time.sleep(0.8)
    return None


SKILL_X = [[95, 180, 265], [435, 525, 615], [710, 795, 880]]
ALLY_X = [400, 640, 880]
ENEMY_X = [180, 480, 780]
MASTER_SKILL_X = [910, 1000, 1090]
JOB = json.loads((ROOT / 'jobs/娄希、娄希、奥伯龙.json').read_text(encoding='utf-8'))


def is_attack_button(rows):
    return any('攻击' in text and x > 1000 and y > 500 for text, x, y in rows)


def command_cards(rows):
    cards = []
    for label in ('力击', '迅击', '技击'):
        for text, x, y in rows:
            if label in text and 450 < y < 650:
                cards.append((x, label))
    return sorted(cards)


def select_ally_target(action):
    target = action.get('playerTargetSlot')
    if target is None or action.get('detail') == '目标：自身':
        return
    rows = wait_state(lambda current: current if has(current, '请选择对象') else None, 6)
    if not rows:
        raise FarmError(f"{action['title']} 未出现对象选择框")
    tap(ALLY_X[target], 350, f"{action['title']} 目标：从者{target + 1}")
    time.sleep(1.5)


def run_wave_actions(wave):
    if not 1 <= wave <= len(JOB['waves']):
        raise FarmError(f'作业没有第 {wave} 面配置')
    actions = JOB['waves'][wave - 1]['turns'][0]['actions']
    for action in actions:
        if action['kind'] == 'target':
            target = action['toTarget']
            tap(ENEMY_X[target - 1], 120, f'切换目标：敌方{target}')
            time.sleep(0.5)
        elif action['kind'] == 'skill':
            member = action['memberSlot']
            skill = action['skill']
            tap(SKILL_X[member][skill], 575, action['title'])
            time.sleep(0.8)
            select_ally_target(action)
            time.sleep(1.2)
        elif action['kind'] == 'master':
            tap(1195, 310, '打开御主技能')
            time.sleep(0.8)
            tap(MASTER_SKILL_X[action['skill']], 300, action['title'])
            time.sleep(0.8)
            select_ally_target(action)
            time.sleep(1.2)


def support_state(rows):
    if has(rows, '从者详情'):
        return 'detail'
    if has(rows, '好友申请') or has(rows, '发送好友申请'):
        return 'friend'
    if has(rows, '连续进行同一关卡'):
        return 'repeat'
    if has(rows, '助战编队确认') and has(rows, '冠位编队'):
        return 'grand'
    if has(rows, '队伍确认') or has(rows, '开始任务'):
        return 'team'
    if has(rows, '加载中') or has(rows, '小贴士'):
        return 'loading'
    return 'unknown'


def wait_support_state(timeout=12):
    outcome = wait_state(lambda rows: (support_state(rows), rows) if support_state(rows) != 'unknown' else None, timeout)
    return outcome or ('unknown', [])


def select_support(servant='娄希', search_only=False):
    delay_scale = 0.45 if search_only else 1.0
    initial_rows = ocr(shot('support-entry.png'))
    if not has(initial_rows, '助战选择'):
        raise FarmError('当前不在助战选择页，已停止')
    tap(560, 125, '切换娄希的狂战士职阶页')
    time.sleep(1.2 * delay_scale)
    for refresh_index in range(3):
        for _ in range(8):
            swipe(640, 260, 640, 620, 200)
            time.sleep(0.15 * delay_scale)
        for _ in range(8):
            time.sleep(0.7 * delay_scale)
            rows = ocr(shot('support.png'))
            if not has(rows, '助战选择'):
                raise FarmError('搜索过程中离开了助战选择页，已停止')
            hit = find(rows, servant)
            if hit:
                if search_only:
                    trace(f'找到测试助战：{servant}；未点击条目')
                    return True
                tap(600, hit[2], f'选择{servant}助战条目（普通或冠位）')
                handled = set()
                started = time.time()
                last_state = 'unknown'
                while time.time() - started < 30:
                    state, rows = wait_support_state()
                    last_state = state
                    if state in ('team', 'loading'):
                        return True
                    if state == 'unknown':
                        return False
                    if state == 'detail':
                        raise FarmError('误入了从者详情，请重新选择助战')
                    if state == 'grand':
                        raise FarmError('普通助战流程误入冠位编队页，未选择任何冠位卡面')
                    if state in handled:
                        time.sleep(0.8)
                        continue
                    handled.add(state)
                    if state == 'friend':
                        if not click_row(rows, '结束', '结束好友申请', (330, 618)):
                            raise FarmError('好友申请弹窗未找到结束按钮')
                    elif state == 'repeat':
                        if not click_row(rows, '连续出击', '连续出击', (870, 577)):
                            raise FarmError('连续出击弹窗未找到按钮')
                raise FarmError(f'助战流程未离开状态：{last_state}')
            swipe(640, 550, 640, 260, 450)
        if refresh_index < 2:
            rows = ocr(shot('support-bottom.png'))
            update_area = [row for row in rows if row[1] > 850 and row[2] < 170]
            if not click_row(update_area, '列表更新', '列表已到底，更新助战列表', (930, 125)):
                raise FarmError('助战列表到底，但没有找到列表更新按钮')
            confirm_rows = wait_state(
                lambda current: current if has(current, '列表更新') and has(current, '是否确认执行') else None,
                5,
            )
            if not confirm_rows:
                raise FarmError('列表更新后没有出现确认弹窗')
            confirm_area = [row for row in confirm_rows if row[0] == '是' and row[1] > 640 and row[2] > 450]
            if not click_row(confirm_area, '是', '确认更新助战列表', (840, 565)):
                raise FarmError('列表更新确认弹窗未找到是按钮')
            if not wait_state(lambda current: current if has(current, '助战选择') else None, 8):
                raise FarmError('确认更新后没有返回助战选择页')
            time.sleep(1.0 * delay_scale)
    return False


def result_action(rows):
    if has(rows, '行动力回复') or has(rows, '消耗道具回复行动力'):
        return 'recovery'
    if has(rows, '连续进行同一关卡'):
        return 'repeat'
    if has(rows, '助战选择'):
        return 'support'
    if has(rows, '下一步') or has(rows, '战利品'):
        return 'next'
    if has(rows, '战斗结果') or has(rows, '与从者的牵绊'):
        return 'result'
    return 'wait'


def finish_results(allow_repeat=True):
    started = time.time()
    while time.time() - started < 35:
        rows = ocr(shot('result.png'))
        action = result_action(rows)
        if action == 'recovery':
            raise FarmError('行动力不足；未使用苹果或圣晶石')
        if action == 'repeat':
            if not allow_repeat:
                trace('最后一次战斗已完成；不点击连续出击')
                return True
            if not click_row(rows, '连续出击', '连续出击', (870, 577)):
                raise FarmError('连续出击弹窗未找到按钮')
            time.sleep(2.0)
            continue
        if action == 'support':
            return True
        if action == 'next':
            if not click_row(rows, '下一步', '结算下一步', (1130, 660)):
                raise FarmError('战利品页未找到下一步按钮')
            time.sleep(1.4)
            continue
        if action == 'result':
            tap(640, 360, '战斗结果')
            time.sleep(1.2)
            continue
        time.sleep(0.8)
    return has(ocr(shot()), '助战选择')


def check_result_action(name, rows, expected_action, expected_tap=None):
    taps, tap = recorder()
    with mock.patch.object(legacy, "tap", tap):
        actual_action = legacy.result_action(rows)
        if actual_action == "next":
            legacy.click_row(rows, "下一步", "结算下一步", (1130, 660))
        elif actual_action == "repeat":
            legacy.click_row(rows, "连续出击", "连续出击", (870, 577))
        elif actual_action == "result":
            legacy.tap(640, 360, "战斗结果")
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
        mock.patch.object(legacy, "shot", return_value=image),
        mock.patch.object(legacy, "ocr", side_effect=sequences),
        mock.patch.object(legacy, "tap", tap),
        mock.patch.object(legacy, "swipe") as swipe,
        mock.patch.object(legacy.time, "sleep"),
    ):
        try:
            actual = legacy.select_support()
        except legacy.FarmError as exc:
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
        mock.patch.object(legacy, "shot", return_value=image),
        mock.patch.object(legacy, "ocr", side_effect=sequences),
        mock.patch.object(legacy, "tap", tap),
        mock.patch.object(legacy, "swipe"),
        mock.patch.object(legacy.time, "sleep"),
    ):
        actual = legacy.select_support()
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
        mock.patch.object(legacy, "shot", return_value=image),
        mock.patch.object(legacy, "ocr", return_value=rows),
        mock.patch.object(legacy, "tap", tap),
        mock.patch.object(legacy.time, "sleep"),
    ):
        try:
            actual = legacy.finish_results(allow_repeat=allow_repeat)
        except legacy.FarmError as exc:
            error = str(exc)
    ok = not taps and ((error == expected_error) if expected_error else actual == expected)
    add_report(name, image_path, expected_error or expected, error or actual,
               "stop" if error else "wait", f"taps={taps}", ok, simulated_ocr=True)


def check_job_actions(wave, expected_points):
    taps, tap = recorder()
    image = read_image(ROOT / "captures/2026-11-19-ember/key/04-battle-wave1.png")
    with (
        mock.patch.object(legacy, "shot", return_value=image),
        mock.patch.object(legacy, "ocr", return_value=[("请选择对象", 640, 190)]),
        mock.patch.object(legacy, "tap", tap),
        mock.patch.object(legacy.time, "sleep"),
    ):
        legacy.run_wave_actions(wave)
    points = [(item["x"], item["y"]) for item in taps]
    add_report(f"action:job-wave-{wave}", ROOT / "jobs/娄希、娄希、奥伯龙.json", expected_points,
               points, "tap", f"taps={taps}", points == expected_points, simulated_ocr=True)


def check_team_start():
    taps, tap = recorder()
    rows = [("队伍确认", 120, 45), ("开始任务", 1190, 655)]
    with mock.patch.object(legacy, "tap", tap):
        allowed = legacy.has(rows, "队伍确认") or legacy.has(rows, "开始任务")
        if allowed:
            legacy.click_row(rows, "开始任务", "开始任务", (1190, 655))
    ok = allowed and len(taps) == 1 and (taps[0]["x"], taps[0]["y"]) == (1190, 655)
    add_report("action:team-confirm", TEAM_IMAGE, "start", "start" if allowed else "wait",
               "tap" if taps else "wait", f"taps={taps}", ok, simulated_ocr=True)


def check_ocr_action(name, path, expected):
    image = read_image(path)
    rows = legacy.ocr(image)
    state = legacy.classify(image, legacy.state_templates)["state"]
    cards = legacy.command_cards(rows) if state == "card" else []
    attack = legacy.is_attack_button(rows) if state == "battle" else False
    action = "cards" if cards else "attack" if attack else "wait"
    add_report(name, path, expected, action, "observe", f"ocr={rows}", action == expected)


def check_final_frame_evidence():
    from executor.core import RunOptions
    from executor.domain import AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Overlay, Variant)
    cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=cards))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),),
                         formation=tuple(FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)))
    battle = Observation(base_page=BasePage.BATTLE, fields={
        'wave': Field(FieldStatus.CONFIRMED, 1), 'turn': Field(FieldStatus.CONFIRMED, 1),
        'turn_boundary': Field(FieldStatus.CONFIRMED, True),
    }, controls=(Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),))
    card = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (46, 415, 211, 655), True,
                card=CommandCardIdentity(index - 1, 'arts')) for index in range(1, 6)))
    animation = Observation(base_page=BasePage.BATTLE, variants=frozenset({Variant.BATTLE_ANIMATION}))
    result_page = Observation(base_page=BasePage.RESULT, controls=(
        Control('结算推进', ControlKind.BUTTON, (0, 0, 1280, 720), True),))
    drops = Observation(base_page=BasePage.DROPS, controls=(
        Control('下一步', ControlKind.BUTTON, (948, 606, 1272, 676), True),))
    repeat = Observation(base_page=BasePage.BATTLE, overlays=frozenset({Overlay.REPEAT_DIALOG}),
                         controls=(Control('连续出击', ControlKind.BUTTON, (800, 540, 940, 610), True),))
    observations = [battle, battle, card, card, card, card, card, card, animation,
                    result_page, result_page, drops, drops, repeat]
    times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 2.5, 3.0, 3.1, 3.6, 3.7]
    with TemporaryDirectory() as directory:
        result, _, _ = run_core_scenario(
            observations, times, plan, options=RunOptions(run_dir=directory))
        final = Path(directory) / 'final.png'
        payload = json.loads((Path(directory) / 'result.json').read_text(encoding='utf-8'))
        final_exists = final.is_file()
        ok = result.status.value == 'completed' and final_exists and payload.get('final_frame') == str(final)
    add_report('executor:completed-final-frame', DROPS_IMAGE, 'completed; final.png saved',
               f'{result.reason}; final={final_exists}', 'observe', 'completed runs retain final visual evidence',
               ok, simulated_ocr=True)


def check_transient_second_np_rejected():
    from executor.domain import ExecutionPlan, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Observation

    turn = TurnPlan(np_positions={1: 2})
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),),
                         formation=tuple(FormationSlotPlan(slot=slot, name=str(slot)) for slot in range(3)))
    normal = tuple(Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                           card=CommandCardIdentity(index % 3, 'arts', crit_rate=10, affinity='neutral'))
                   for index in range(1, 6))
    first_np = Control('宝具卡1', ControlKind.CARD, (330, 100, 510, 330), True, 'np_slot:A')
    false_second_np = Control('宝具卡2', ControlKind.CARD, (545, 85, 725, 315), True, 'np_slot:B')
    effect = Observation(base_page=BasePage.CARD, controls=(*normal, first_np, false_second_np))
    settled = Observation(base_page=BasePage.CARD, controls=(*normal, first_np))
    result, device, events = run_core_scenario(
        [effect, settled, settled, settled], [0.0, 0.5, 1.0, 1.5], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    add_report('executor:transient-second-np-not-clicked', ROOT / 'captures/2026-11-23/key/live-card-wave3-effect.png',
               '不点击特效造成的宝具卡2', f'{result.reason}; actions={controls}', 'stop',
               'multi-NP regions require consecutive evidence',
               not any(control.startswith('宝具卡') for control in controls), simulated_ocr=True)


def check_support_refresh_confirmation_timeout():
    from executor.domain import (AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan,
                                 TurnPlan, WavePlan)
    from executor.perception import BasePage, Control, ControlKind, Observation, Overlay, SupportCandidate
    cards = tuple(AttackCardPlan(slot=slot, type='arts') for slot in range(3))
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=cards))
    formation = (
        FormationSlotPlan(slot=0, name='娄希', svt_id=705000, role='Berserker',
                          support=True, support_required=True),
        FormationSlotPlan(slot=1, name='1'), FormationSlotPlan(slot=2, name='2'),
    )
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),), formation=formation)
    wrong_candidate = SupportCandidate(name='其他', svt_id=1, role='Saber')
    wrong = Observation(base_page=BasePage.SUPPORT, controls=(
        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True, support=wrong_candidate),
        Control('列表更新', ControlKind.BUTTON, (888, 98, 972, 162), True),
    ))
    result, device, events = run_core_scenario(
        [wrong] * 8, [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 33.5], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['职阶筛选:berserker', '助战列表向下', '列表更新']
    ok = (result.status.value == 'stopped' and result.reason == 'action_confirmation_timeout'
          and controls == expected and events.events[-1].data.get('stage') == 'page')
    add_report('executor:support-refresh-confirmation-timeout', SUPPORT_IMAGE,
               'action_confirmation_timeout; no repeated refresh', f'{result.reason}; {controls}',
               'stop', f'taps={device.actions}', ok, simulated_ocr=True)

    confirm = Observation(base_page=BasePage.SUPPORT, overlays=frozenset({Overlay.SUPPORT_CONFIRM}),
                          controls=(Control('是', ControlKind.BUTTON, (708, 534, 969, 592), True),))
    blank = Observation(base_page=BasePage.SUPPORT)
    candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
    match = Observation(base_page=BasePage.SUPPORT, controls=(
        Control('助战卡片第2行', ControlKind.ROW, (40, 360, 1000, 560), True, support=candidate),))
    unknown = Observation(base_page=BasePage.UNKNOWN)
    observations = [wrong] * 7 + [confirm] * 2 + [wrong] * 6 + [confirm] * 2
    observations += [blank, blank, match, match, unknown, unknown]
    times = [index * 0.5 for index in range(len(observations) - 2)]
    times += [len(times) * 0.5, len(times) * 0.5 + 30.0]
    result, device, events = run_core_scenario(observations, times, plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = result.reason == 'unknown_timeout' and '助战卡片第2行' in controls
    add_report('executor:support-refresh-waits-for-candidates', SUPPORT_IMAGE,
               'transient empty rows wait; matching Louhi selected', f'{result.reason}; {controls}',
               'tap', 'second refresh returned candidates after a transient frame', ok, simulated_ocr=True)

    empty = Observation(base_page=BasePage.SUPPORT, controls=(
        Control('列表更新', ControlKind.BUTTON, (888, 98, 972, 162), True),))
    result, device, events = run_core_scenario(
        [wrong] * 4 + [empty] * 4 + [unknown] * 2,
        [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 34.0], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = result.reason == 'unknown_timeout' and '列表更新' not in controls
    add_report('executor:support-empty-scroll-is-not-bottom', SUPPORT_IMAGE,
               'empty scrolled list waits; no refresh', f'{result.reason}; {controls}',
               'wait', f'actions={device.actions}', ok, simulated_ocr=True)


def check_manual_key_frames():
    from executor.device import Frame
    from executor.perception import Perception
    from executor.replay import ReplayFailure, get_case, manual_job_plan, run_case

    with TemporaryDirectory() as directory:
        for name in ('manual-full-loop', 'manual-team-mystic-mismatch',
                     'manual-team-servant-mismatch', 'manual-team-support-ce-mismatch',
                     'support-unknown-rows-bottom', 'costume-team-start'):
            case = get_case(name)
            try:
                report = run_case(case, Path(directory))
                actual = f'{report.result.status.value}/{report.result.reason}'
                ok = True
            except ReplayFailure as error:
                actual = str(error)
                ok = False
            add_report(f'executor:{name}', ROOT / case.frames[0], f'{case.status.value}/{case.reason}',
                       actual, 'replay', '人工关键帧规则回放', ok)

    plan = manual_job_plan()
    perception = Perception()
    try:
        perception.bind_formation(plan.formation)
        perception.bind_mystic(plan.mystic_id)

        def observe(name, fields=(), root=ROOT / 'captures/2026-11-23/key'):
            path = root / name
            frame = Frame(image=path.read_bytes(), width=1280, height=720, captured_at=1.0)
            perception.read(frame)
            if not fields:
                return path, perception.read(frame), None
            observed = perception.read_fields(frame, fields, timeout=0)
            cached = perception.read_fields(frame, fields, timeout=0)
            return path, observed, cached

        def image_fields_ok(observation, cached):
            return (observation.evidence.get('field_ms', -1) >= 0
                    and observation.evidence.get('field_cache_hit') == 0.0
                    and cached is not None and cached.frame_hash == observation.frame_hash
                    and cached.fields == observation.fields
                    and cached.evidence.get('field_cache_hit') == 1.0)

        path, observation, _cached = observe('manual-team-04.png')
        actual = tuple(observation.field(name).value for name in ('team_servants', 'team_support_ce', 'team_mystic'))
        expected = ((705000, 705000, 2800100), 9403520, 'atlas-260')
        add_report('perception:manual-team-job-match', path, expected, actual, 'observe',
                   '从者、助战礼装与御主礼装', actual == expected)

        from executor.perception.identity import CommandTemplate
        import numpy as np
        perception.bind_formation(plan.formation)
        with mock.patch.object(perception._identities, '_load_team', return_value=[
                CommandTemplate(705000, (), np.ones((2, 128), dtype=np.uint8))]):
            with mock.patch.object(perception._identities, '_feature_matcher',
                                   mock.Mock(knnMatch=mock.Mock(return_value=[[]]))):
                path, observation, _cached = observe('manual-team-04.png')
        actual = (observation.field('team_servants').status.value,
                  observation.field('team_support_ce').status.value)
        expected = ('uncertain', 'uncertain')
        add_report('perception:team-empty-descriptor-match', path, expected, actual, 'observe',
                   '空匹配组返回不确定而不是异常', actual == expected)

        path, observation, _cached = observe('manual-support-berserker-02.png')
        actual = observation.field('support_class').value
        add_report('perception:manual-support-class', path, 'berserker', actual, 'observe',
                   '当前选中职阶', actual == 'berserker')

        path, observation, cached = observe('manual-w1-t1-after-s3-07.png', ('wave', 'turn', 'np'))
        actual = tuple(observation.field(name).value for name in ('wave', 'turn', 'np'))
        expected = (1, 1, (40, 100, 40))
        add_report('perception:manual-wave-turn-np', path, expected, actual, 'observe',
                   '同帧图像数字字段', actual == expected and image_fields_ok(observation, cached))

        path, observation, cached = observe('manual-w2-t1-cards-14.png', ('crit',))
        actual = observation.field('crit').value
        expected = (10, 100, 80, 30, 90)
        add_report('perception:manual-card-crit', path, expected, actual, 'observe',
                   '五张普通卡暴击率与同帧缓存耗时证据',
                   actual == expected and image_fields_ok(observation, cached))

        path, observation, _cached = observe('live-card-support-obscured.png')
        card = observation.control('卡4').card
        actual = None if card is None else (card.svt_id, card.servant_slot, card.support, card.card_type)
        expected = (705000, 0, True, 'quick')
        add_report('perception:live-obscured-support-card', path, expected, actual, 'observe',
                   '技能文字遮挡时用助战标签与唯一助战槽位确认身份', actual == expected)

        path, observation, cached = observe('10-np-card-select.png', ('crit',),
                                           root=ROOT / 'captures/2026-11-19-ember/key')
        actual = observation.field('crit').value
        expected = (20, 30, 50, 50, 30)
        add_report('perception:legacy-card-crit', path, expected, actual, 'observe',
                   '二十、三十与五十暴击率数字模板',
                   actual == expected and image_fields_ok(observation, cached))

        cases = [
            ('perception:crit-digits-4-6', 'card-crit-50-60-100-40-30.png', '2026-11-22',
             ('crit',), (50, 60, 100, 40, 30)),
            ('perception:crit-digit-7', 'card-crit-40-100-70-30-40.png', '2026-11-22',
             ('crit',), (40, 100, 70, 30, 40)),
            ('perception:crit-cross-validation', 'card-w3-t6.png', '2026-11-22',
             ('crit',), (10, 60, 100, 100, 40)),
            ('perception:np-digit-2', 'battle-wave1.png', '2026-11-20', ('np',), (20, 20, 20)),
            ('perception:np-digits-3-7', 'card-crit-40-100-70-30-40.png', '2026-11-22',
             ('np',), (107, 114, 43)),
            ('perception:np-digits-5-8-9', 'manual-w2-t1-cards-14.png', '2026-11-23',
             ('np',), (140, 59, 80)),
            ('perception:np-digit-6', 'card-np-12-94-60.png', '2026-11-23', ('np',), (12, 94, 60)),
            ('perception:np-cross-114-74-43', 'card-np-114-74-43.png', '2026-11-22',
             ('np',), (114, 74, 43)),
            ('perception:np-cross-154-89-43', 'card-np-154-89-43.png', '2026-11-22',
             ('np',), (154, 89, 43)),
            ('perception:np-cross-154-110-43', 'card-w3-t6.png', '2026-11-22',
             ('np',), (154, 110, 43)),
            ('perception:turn-digit-4', 'battle-w2-t4.png', '2026-11-22', ('wave', 'turn'), (2, 4)),
            ('perception:turn-digit-5', 'battle-w3-t5.png', '2026-11-22', ('wave', 'turn'), (3, 5)),
            ('perception:turn-digit-6', 'battle-w3-t6.png', '2026-11-22', ('wave', 'turn'), (3, 6)),
            ('perception:turn-digit-7', 'battle-w3-t7.png', '2026-11-22', ('wave', 'turn'), (3, 7)),
            ('perception:turn-live-variant', 'live-battle-w2-turn2.png', '2026-11-23', ('wave', 'turn'), (2, 2)),
            ('perception:crit-all-100', 'manual-w3-t1-cards-25.png', '2026-11-23',
             ('crit',), (100, 100, 100, 100, 100)),
            ('perception:crit-no-digits', 'manual-w1-t1-cards-09.png', '2026-11-23', ('crit',), None),
        ]
        for report_name, filename, date, fields, expected in cases:
            path, observation, cached = observe(filename, fields, root=ROOT / 'captures' / date / 'key')
            actual = tuple(observation.field(name).value for name in fields)
            expected_values = (expected,) if len(fields) == 1 else expected
            add_report(report_name, path, expected_values, actual, 'observe',
                       '真实帧数字模板与同帧缓存',
                       actual == expected_values and image_fields_ok(observation, cached))
    finally:
        perception.close()


def run(output: Path, name_filter: str | None = None):
    REPORTS.clear()
    check_classifications()
    check_perception()
    check_perception_identities()
    check_executor_core()
    check_manual_key_frames()
    check_command_card_batch()
    check_transient_second_np_rejected()
    check_support_refresh_confirmation_timeout()
    check_final_frame_evidence()
    check_strategy_servant_priority()
    check_strategy_color_priority()
    check_strategy_crit_priority()
    check_strategy_balanced_cards()
    check_strategy_affinity_and_degraded_data()
    check_strategy_np_and_short_hand()
    check_empty_slot_rules()
    check_empty_slot_formation_once()
    check_strategy_scope_slots_and_region()
    check_strategy_executor()
    check_order_change_auto_fill()
    check_executor_self_target()
    check_executor_repeat_support()
    check_executor_recovery()
    check_executor_process()
    check_executor_safety()
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
                          (1195, 310), (910, 300), (1195, 310), (1000, 300), (640, 350)])
    check_team_start()
    check_ocr_action("action:loading-waits", ROOT / "captures/2026-11-19-ember/05-quest-start.png", "wait")
    check_ocr_action("action:attack-animation-waits", ROOT / "captures/2026-11-19-ember/36-attack-animation.png", "wait")
    check_ocr_action("action:attack-button-ready", LOADING_IMAGE, "attack")

    if name_filter:
        REPORTS[:] = [item for item in REPORTS if name_filter in item['name']]
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
                try:
                    item["failureImage"] = str(target.relative_to(ROOT))
                except ValueError:
                    item["failureImage"] = str(target)
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
    parser.add_argument("--filter", dest='name_filter')
    args = parser.parse_args()
    return run(args.output.resolve(), args.name_filter)


if __name__ == "__main__":
    sys.exit(main())
