"""国服识别配置：判据与按钮坐标来自实机逐屏标注（.scratch/annotated）。"""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent / 'cn.json'


def load_config(path: Path | None = None) -> dict:
    data = json.loads((path or _CONFIG_PATH).read_text(encoding='utf-8'))
    states = data['states']
    # AP 恢复有两种列表（未滚动含圣晶石/黄金，滚动后含青铜），
    # 判据只保留两屏共有的部分，具体有哪些道具交给字段读取。
    states['ap_recovery']['criteria'] = [
        {'kind': 'text-exists', 'expect': '行动力回复', 'area': [550, 25, 730, 80]},
        {'kind': 'text-exists', 'expect': '消耗道具回复行动力', 'area': None},
        {'kind': 'text-absent', 'expect': '连续出击', 'area': None},
    ]
    # 职阶相性面板与战斗菜单都含“职阶相性/特殊职阶”，靠有没有“撤退”区分。
    states['class_info']['criteria'].append(
        {'kind': 'text-absent', 'expect': '撤退', 'area': None})
    return data
