"""国服识别配置：判据与按钮坐标来自实机逐屏标注（.scratch/annotated）。

数据文件 `cn.json` 是单源；本模块不做任何运行时改写。
"""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent / 'cn.json'

SUPPORTED_REGIONS = ('cn',)


def load_config(region: str = 'cn') -> dict:
    """只支持已采集并验收过的区服；未支持的区服直接报错，不静默默认国服。"""
    if region not in SUPPORTED_REGIONS:
        raise ValueError(f'区服 {region} 未支持：当前只有 {SUPPORTED_REGIONS} 有识别配置')
    return json.loads(_CONFIG_PATH.read_text(encoding='utf-8'))
