"""国服识别配置：锚点文案、区域限制与模板位置。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = ROOT / 'cv' / 'templates'

OCR_THRESHOLD = 0.5
TEMPLATE_THRESHOLD = 0.55

TEMPLATE_SPECS: dict[str, list[tuple[str, tuple[int, int, int, int]]]] = {
    'menu': [('menu.png', (580, 430, 1160, 610))],
    'battle': [
        ('battle_diamond.png', (1080, 120, 1260, 390)),
        ('battle_enemy1.png', (0, 0, 600, 90)),
        ('battle_enemy2.png', (0, 0, 600, 90)),
        ('battle_enemy3.png', (0, 0, 600, 90)),
    ],
    'card': [('card.png', (1020, 0, 1260, 130))],
    'result': [
        ('result_title.png', (420, 0, 860, 130)),
        ('result_drops.png', (60, 0, 700, 120)),
    ],
}

CONFLICT_ANCHORS = ('从者详情', '冠位编队')
LOADING_ANCHORS = ('小贴士', '加载中')
SUPPORT_ANCHORS = ('助战选择', '列表更新', '助战编队确认')
TEAM_ANCHORS = ('队伍确认', '开始任务', '队伍编队')
RESULT_ANCHORS = ('战斗结果', '获得战利品', '与从者的牵绊', '请点击游戏界面')
CARD_ANCHORS = ('战斗速度',)
BATTLE_ANCHORS = ('战斗场次', '攻击')
MENU_ANCHORS = ('迦勒底之门',)

CARD_LABELS = ('力击', '迅击', '技击')
CARD_REGION = (0, 450, 1280, 650)
WAVE_REGION = (780, 0, 1000, 70)
TURN_REGION = (780, 70, 1000, 130)
