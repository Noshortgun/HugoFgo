"""离线回放关键帧与刷本动作；不会调用 ADB 或点击真实设备。"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import replace
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "cv"))

import farm_ember as farm
from image_io import read_image, write_image
from state_classifier import load_templates, result_for_image
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
        expected = (3, ('缺少暴击率，按 0 处理', '缺少克制信息，按中立处理'))
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
                                     FieldStatus, Observation)
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
    observations = [battle, battle, card, card, card, card, card, card, unknown, unknown]
    times = [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8, 2.3, 2.4, 32.4]
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
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '卡1', '卡2', '卡3']
    ok = result.reason == 'unknown_timeout' and controls == expected and len(device.actions) == 4
    add_report('executor:third-card-stops-input', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}; taps={device.actions}', ok, simulated_ocr=True)


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
    expected = ['卡2', '卡4', '卡5']
    add_report('strategy:servant-priority', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', f'source={selection.source}; reason={selection.reason}',
               actual == expected and selection.source == 'global', simulated_ocr=True)


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
    expected = ['卡2', '卡3', '卡4']
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
    expected = ['卡1', '卡3', '卡5']
    add_report('strategy:balanced-cards', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', selection.reason, actual == expected, simulated_ocr=True)


def check_strategy_affinity_and_degraded_data():
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
    observation = Observation(base_page=BasePage.CARD, controls=tuple(
        Control(f'卡{index}', ControlKind.CARD, (index * 200, 415, index * 200 + 160, 655), True,
                card=CommandCardIdentity(0, 'buster', crit_rate=10, affinity=affinity))
        for index, affinity in enumerate(('resist', 'neutral', 'advantage', 'advantage', 'neutral'), 1)))

    selection = select_cards(observation, strategy, plan, 1)
    actual = [control.name for control in selection.controls]
    expected = ['卡2', '卡3', '卡4']
    add_report('strategy:affinity-priority', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', selection.reason, actual == expected, simulated_ocr=True)

    uncertain_card = replace(observation.controls[0], card=replace(observation.controls[0].card, crit_rate=None))
    degraded = select_cards(replace(observation, controls=(uncertain_card, *observation.controls[1:])),
                            strategy, plan, 1)
    actual = [control.name for control in degraded.controls]
    add_report('strategy:missing-crit-degrades', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', f'{degraded.reason}; warnings={degraded.warnings}',
               actual == expected and degraded.warnings == ('缺少暴击率，按 0 处理',), simulated_ocr=True)


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
    expected = ['卡1', '卡2', '宝具卡1']
    add_report('strategy:np-allowed', ROOT / 'captures/2026-11-20/key/card-select.png',
               expected, actual, 'select', enabled.reason, actual == expected, simulated_ocr=True)

    empty = select_cards(Observation(base_page=BasePage.CARD), strategy, plan, 1)
    add_report('strategy:empty-hand-stops', ROOT / 'captures/2026-11-20/key/card-select.png',
               'diagnostic', empty.diagnostic or 'selected', 'stop', empty.reason,
               not empty.controls and empty.diagnostic is not None, simulated_ocr=True)


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
    expected = (['卡2', '卡3', '卡5'], 'wave', ['卡2', '卡3', '卡4'], 'global')
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
        [battle(), battle(), isolated_middle, isolated_middle, obscured,
         isolated_middle, isolated_middle, isolated_middle, isolated_middle, unknown, unknown],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.3, 1.8, 1.9, 2.4, 2.5, 32.5], plan)
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['攻击', '宝具卡1', '卡1', '卡2']
    waited = any(event.type == 'warning' and event.data.get('reason') == 'card_confirmation_waiting'
                 for event in events.events)
    add_report('strategy:executor-card-confirmation-waits',
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
    expected = ['攻击', '卡1', '卡2', '卡3', '攻击', '卡1', '卡2', '卡3']
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
    expected = ['攻击', '卡1', '卡2', '卡3', '攻击', '卡1', '卡2', '卡3']
    progress = (result.progress.wave, result.progress.turn)
    add_report('strategy:executor-forward-wave-planned-turn', LOADING_IMAGE, expected, controls,
               'tap', f'reason={result.reason}; progress={progress}',
               result.reason == 'unknown_timeout' and progress == (2, 1) and controls == expected,
               simulated_ocr=True)

    short = Observation(base_page=BasePage.CARD, controls=(command(1, 0), command(2, 1)))
    result, device, events = run_core_scenario(
        [battle(), battle(), short], [0.0, 0.5, 0.6],
        make_plan(TurnPlan(attack=AttackPlan(mode='strategy', cards=()))))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    ok = (result.reason == 'fallback_cards_unavailable' and controls == ['攻击']
          and len(device.actions) == 1 and events.events[-1].data.get('message'))
    add_report('strategy:executor-short-hand-stops', ROOT / 'captures/2026-11-20/key/card-select.png',
               'fallback_cards_unavailable', result.reason, 'stop', f'controls={controls}',
               bool(ok), simulated_ocr=True)


def check_executor_self_target():
    from executor.core import RunOptions, run
    from executor.domain import (ActionPlan, AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan,
                                 TurnPlan, WavePlan)
    from executor.perception import BasePage, CommandCardIdentity, Control, ControlKind, Field, FieldStatus, Observation, Variant
    from executor.session import MemoryEventSink

    fields = {'wave': Field(FieldStatus.CONFIRMED, 1), 'turn': Field(FieldStatus.CONFIRMED, 1),
              'turn_boundary': Field(FieldStatus.CONFIRMED, True)}
    battle = Observation(base_page=BasePage.BATTLE, fields=fields, controls=(
        Control('攻击', ControlKind.BUTTON, (1069, 564, 1196, 637), True),
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
    expected = ['从者2技能3', '攻击']
    ok = result.reason == 'card_mismatch' and result.progress.action_index == 1 and controls == expected
    add_report('executor:self-target-direct-animation', LOADING_IMAGE, expected, controls,
               'stop', f'reason={result.reason}; progress={result.progress}', ok, simulated_ocr=True)


def check_executor_repeat_support():
    from executor.core import RunOptions, run
    from executor.domain import AttackCardPlan, AttackPlan, ExecutionPlan, FormationSlotPlan, TurnPlan, WavePlan
    from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Field,
                                     FieldStatus, Observation, Overlay, SupportCandidate)
    from executor.session import MemoryEventSink

    repeat = Observation(base_page=BasePage.DROPS, overlays=frozenset({Overlay.REPEAT_DIALOG}),
                         controls=(Control('连续出击', ControlKind.BUTTON, (709, 554, 970, 612), True),))
    candidate = SupportCandidate(name='娄希', svt_id=705000, role='Berserker')
    support = Observation(base_page=BasePage.SUPPORT, controls=(
        Control('助战卡片第1行', ControlKind.ROW, (40, 200, 1000, 400), True, support=candidate),))
    team = Observation(base_page=BasePage.TEAM, controls=(
        Control('开始任务', ControlKind.BUTTON, (1086, 635, 1248, 707), True),))
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
                                     support=True, support_required=True)
    turn = TurnPlan(attack=AttackPlan(mode='verify', cards=tuple(
        AttackCardPlan(slot=slot, type='arts') for slot in range(3))))
    plan = ExecutionPlan(mystic_id='default', enemy_count=3,
                         waves=(WavePlan(number=1, target=1, turns=(turn,)),), formation=tuple(formation))
    device = FakeDevice(frames)
    events = MemoryEventSink()
    with mock.patch('executor.core.runner.time.sleep', return_value=None):
        result = run(plan, device, ScriptedPerception(), events, options=RunOptions(run_count=2, write_evidence=False))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['连续出击', '助战卡片第1行', '开始任务', '攻击']
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
    expected = ['连续出击', '助战卡片第1行', '攻击']
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
        actual = f"complete={completed.returncode}:{finished_rows[-1]['type']}; stop={stopped.returncode}:{stopped_rows[-1]['type']}; history={[row['result'] for row in history]}"
        ok = (completed.returncode == 0 and finished_rows[-1]['type'] == 'finished'
              and stopped.returncode == 1 and stopped_rows[-1]['type'] == 'stopped'
              and not any(row['type'] == 'action' for row in stopped_rows)
              and [row['result'] for row in history] == ['completed', 'stopped'])
        add_report('executor:process-jsonl-and-stop', LOADING_IMAGE, 'complete=0:finished; stop=1:stopped',
                   actual, 'stop', 'UTF-8 JSON Lines；停止后无动作', ok, simulated_ocr=True)


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
    result, _, events = run_core_scenario(
        [battle(), battle(), battle(), animation, battle(), battle(), battle(), wrong],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.3, 1.8, 1.9], make_plan((skill,)))
    actions = [event for event in events.events if event.type == 'action']
    ok = result.reason == 'card_mismatch' and actions[-1].timestamp == 1.8
    add_report('executor:skill-needs-animation', LOADING_IMAGE, 1.8, actions[-1].timestamp,
               'stop', f'reason={result.reason}', ok, simulated_ocr=True)

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
        [wrong_support, wrong_support, wrong_support, wrong_support, wrong_support, wrong_support,
         cooldown, cooldown, wrong_support, support, support, Observation(base_page=BasePage.UNKNOWN),
         Observation(base_page=BasePage.UNKNOWN)],
        [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 9.0, 9.5, 10.0, 40.0],
        make_plan(support_required=True))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['助战列表回顶', '助战列表向下', '列表更新', '关闭', '助战卡片第1行']
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
        [target_battle, target_battle, detail, detail, target_battle, target_battle, wrong],
        [0.0, 0.5, 0.6, 1.1, 1.2, 1.7, 1.8], make_plan((target,)))
    controls = [event.data['control'] for event in events.events if event.type == 'action']
    expected = ['敌方2', '关闭', '攻击']
    add_report('executor:battle-status-change-detail', ROOT / 'captures/2026-11-22/key/battle-status-change-detail.png',
               expected, controls, 'tap', f'reason={result.reason}',
               controls == expected and (device.actions[1]['x'], device.actions[1]['y']) == (1000, 650),
               simulated_ocr=True)


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


def run(output: Path, name_filter: str | None = None):
    REPORTS.clear()
    check_classifications()
    check_perception()
    check_perception_identities()
    check_executor_core()
    check_strategy_servant_priority()
    check_strategy_color_priority()
    check_strategy_crit_priority()
    check_strategy_balanced_cards()
    check_strategy_affinity_and_degraded_data()
    check_strategy_np_and_short_hand()
    check_strategy_scope_slots_and_region()
    check_strategy_executor()
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
                          (1195, 310), (1000, 300), (640, 350)])
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
    parser.add_argument("--filter", dest='name_filter')
    args = parser.parse_args()
    return run(args.output.resolve(), args.name_filter)


if __name__ == "__main__":
    sys.exit(main())
