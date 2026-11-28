"""从数据目录读取 Atlas 技能效果派生出的锁卡技能表。

锁卡技能由 `donotSelectCommandcard` 状态与 `cardReset` 效果共同确定，
不按从者名称或技能编号硬编码。文件由 `scripts/download-servant-assets.mjs` 生成。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from executor.perception.identity import default_identity_root


@lru_cache(maxsize=4)
def _read(path: str) -> tuple[tuple[int, tuple[tuple[int, int], ...]], ...]:
    file = Path(path)
    if not file.is_file():
        return ()
    try:
        raw = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ()
    entries = []
    for svt_text, value in raw.items():
        try:
            svt_id = int(svt_text)
        except (TypeError, ValueError):
            continue
        skills = []
        for item in (value or {}).get('lockCards') or []:
            num, turns = item.get('num'), item.get('turns', 1)
            if not isinstance(num, int) or num < 1:
                continue
            skills.append((num - 1, turns if isinstance(turns, int) and turns > 0 else 1))
        if skills:
            entries.append((svt_id, tuple(skills)))
    return tuple(sorted(entries))


def lock_card_skills(root: Path | None = None) -> dict[int, dict[int, int]]:
    """从者 ID → {技能序号（0 起）: 锁卡持续手数}；缺少数据时返回空表。"""
    base = root or default_identity_root()
    return {svt_id: dict(skills) for svt_id, skills in _read(str(base / 'skill-effects-by-svt.json'))}
