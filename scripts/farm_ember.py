#!/usr/bin/env python
"""种火刷本原型：OCR + 状态确认。失败时保存截图并停止，不继续盲点。"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'cv'))
from state_classifier import classify, load_templates

ADB = os.environ.get('HUGOFGO_ADB', r'D:\leidian\LDPlayer14\adb.exe')
SER = os.environ.get('HUGOFGO_SERIAL', 'emulator-5554')
TMP = os.environ.get('TEMP', '.')
engine = RapidOCR()
state_templates = load_templates()


class FarmError(RuntimeError):
    pass


run_dir = None
log_path = None


def trace(message):
    print(message, flush=True)
    if log_path:
        with log_path.open('a', encoding='utf-8') as f:
            f.write(f'{datetime.now():%H:%M:%S} {message}\n')


def prepare_log():
    global run_dir, log_path
    run_dir = Path('logs/farm') / datetime.now().strftime('%Y%m%d-%H%M%S')
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / 'actions.log'


def adb(*args):
    result = subprocess.run([ADB, '-s', SER, *args], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise FarmError(f'adb 失败：{result.stderr.decode("utf-8", errors="replace").strip()}')


def tap(x, y, reason=''):
    trace(f'点击 ({int(x)}, {int(y)}) {reason}')
    adb('shell', 'input', 'tap', str(int(x)), str(int(y)))


def swipe(x1, y1, x2, y2, ms=350):
    adb('shell', 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(ms))


def shot(name='farm.png'):
    path = os.path.join(TMP, name)
    result = subprocess.run([ADB, '-s', SER, 'exec-out', 'screencap', '-p'], capture_output=True, check=False)
    if result.returncode != 0:
        raise FarmError('截图失败：adb 未返回图像')
    buffer = np.frombuffer(result.stdout, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise FarmError('截图不是有效的 PNG')
    with open(path, 'wb') as f:
        f.write(result.stdout)
    return image


def save_failure(label):
    if not run_dir:
        prepare_log()
    image = shot('failure.png')
    path = run_dir / f'{label}.png'
    path.write_bytes(cv2.imencode('.png', image)[1].tobytes())
    trace(f'已保存失败画面：{path}')
    return path


def ocr(image):
    result, _ = engine(image)
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


def nearest(rows, needle, x, y, max_distance=65):
    candidates = [row for row in rows if needle in row[0] and abs(row[2] - y) <= max_distance]
    return min(candidates, key=lambda row: abs(row[1] - x) + abs(row[2] - y)) if candidates else None


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


X = {'attack': 1135, 'attack_y': 600}
SKILL_X = [[95, 180, 265], [435, 525, 615], [710, 795, 880]]
ALLY_X = [400, 640, 880]
ENEMY_X = [180, 480, 780]
MASTER_SKILL_X = [910, 1000, 1090]
CARD_Y = 540
JOB_PATH = Path(os.environ.get('HUGOFGO_JOB', str(Path(__file__).resolve().parent.parent / 'jobs/娄希、娄希、奥伯龙.json')))
JOB = json.loads(JOB_PATH.read_text(encoding='utf-8'))


def planned_attack(wave):
    if not 1 <= wave <= len(JOB['waves']):
        return None
    turn = JOB['waves'][wave - 1]['turns'][0]
    for action in turn['actions']:
        if action['kind'] == 'attack':
            position = int(turn.get('npPositions', {}).get(str(action['memberSlot']), 1))
            return action, position
    return None


def is_attack_button(rows):
    return any('攻击' in text and x > 1000 and y > 500 for text, x, y in rows)


def command_cards(rows):
    cards = []
    for label in ('力击', '迅击', '技击'):
        for text, x, y in rows:
            if label in text and 450 < y < 650:
                cards.append((x, label))
    return sorted(cards)


def choose_cards(wave):
    cards = command_cards(ocr(shot('cards.png')))
    if not cards:
        return False
    picked = []
    for wanted in ('力击', '迅击', '技击'):
        for x, label in cards:
            if label == wanted and x not in picked:
                picked.append(x)
    attack = planned_attack(wave)
    if attack and len(picked) >= 2:
        action, position = attack
        commands = [('card', picked[0]), ('card', picked[1])]
        commands.insert(position - 1, ('np', ALLY_X[action['memberSlot']]))
        for index, (kind, x) in enumerate(commands, 1):
            tap(x, 190 if kind == 'np' else CARD_Y, f'第{index}张：宝具卡' if kind == 'np' else f'第{index}张：指令卡')
            time.sleep(0.6)
    else:
        for x in picked[:3]:
            tap(x, CARD_Y, '指令卡')
            time.sleep(0.6)
    return True


def wave_number(rows):
    for text, _, _ in rows:
        match = re.search(r'([123])\s*/\s*3', text)
        if match:
            return int(match.group(1))
    return None


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


def battle_once():
    wave = None
    executed = set()
    started = time.time()
    while time.time() - started < 300:
        image = shot()
        state = classify(image, state_templates)['state']
        rows = ocr(image)
        if has(rows, '战斗结果') or has(rows, '获得战利品') or has(rows, '与从者的牵绊') or has(rows, '连续进行同一关卡'):
            return True
        if has(rows, '请选择对象'):
            raise FarmError('出现未预期的对象选择框，已停止而不是点击背后的攻击按钮')
        current_wave = wave_number(rows)
        if current_wave:
            wave = current_wave
        if state == 'card' and command_cards(rows):
            if choose_cards(wave or 1):
                time.sleep(3)
            else:
                time.sleep(0.8)
            continue
        if state == 'battle' and is_attack_button(rows):
            current_wave = wave or 1
            if current_wave not in executed:
                run_wave_actions(current_wave)
                executed.add(current_wave)
            time.sleep(0.5)
            tap(X['attack'], X['attack_y'], '攻击')
            time.sleep(1.2)
            continue
        time.sleep(0.8)
    return False


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


def fail(label):
    path = save_failure(label)
    trace(f'已停止：{label}；画面：{path}')


def main(loops=1):
    prepare_log()
    for index in range(loops):
        trace(f'--- loop {index + 1} ---')
        rows = ocr(shot())
        if not has(rows, '战斗场次') and not is_attack_button(rows):
            if not finish_results():
                fail('not at support screen')
                return 1
            if not select_support():
                fail('support not found')
                return 1
            time.sleep(1.0)
            rows = ocr(shot())
            if has(rows, '队伍确认') or has(rows, '开始任务'):
                if not click_row(rows, '开始任务', '开始任务', (1190, 655)):
                    fail('start quest button not found')
                    return 1
        if not wait_state(lambda r: has(r, '战斗场次') or is_attack_button(r), 25):
            fail('battle not detected')
            return 1
        if not battle_once():
            fail('battle did not finish')
            return 1
        trace('battle finished')
        if not finish_results(allow_repeat=index + 1 < loops):
            fail('could not finish result screens')
            return 1
    return 0


def self_test():
    assert is_attack_button([('攻击', 1135, 600)])
    assert not is_attack_button([('攻击力提升', 500, 100)])
    rows = [('娄希', 300, 300), ('助战编队确认', 1130, 330)]
    assert nearest(rows, '编队确认', 1130, 300) == rows[1]
    attacks = [(planned_attack(wave)[0]['memberSlot'], planned_attack(wave)[1]) for wave in (1, 2, 3)]
    assert attacks == [(1, 1), (0, 2), (1, 1)]
    print('farm_ember 自检通过')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        self_test()
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == '--support-test':
        prepare_log()
        try:
            found = select_support(sys.argv[2], search_only=True)
            trace(f'助战搜索测试结束：{sys.argv[2]}；found={found}；未选择助战')
            sys.exit(0)
        except FarmError as error:
            trace(str(error))
            fail('support-test-exception')
            sys.exit(2)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    try:
        sys.exit(main(count))
    except FarmError as error:
        trace(str(error))
        fail('exception')
        sys.exit(2)
