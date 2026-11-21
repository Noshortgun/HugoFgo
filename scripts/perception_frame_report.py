"""把一批截图逐帧跑感知，生成可核对的可视化报告。

用法：
  cv/.venv/Scripts/python.exe scripts/perception_frame_report.py <目录或图片...> [--out 报告路径]

默认读取 logs/perception-monitor（监控按 s 保存的目录），输出到
.scratch/review/live-frames/index.html。报告里的框是“当前代码”的结果，
不是保存时的旧结果。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from executor.perception import Perception
from executor.perception.evidence import decode_frame
from executor.device.base import Frame

STYLE = """
body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0;margin:24px;max-width:1280px}
h1{font-size:22px}.hint{color:#94a3b8;font-size:13px;line-height:1.7}
.card{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px;margin:16px 0}
.name{font-size:15px;font-weight:600}.meta{font-size:13px;color:#cbd5e1;margin:6px 0}
.stage{position:relative;max-width:1000px;margin-top:10px}
.stage img{width:100%;display:block;border-radius:6px}
.box{position:absolute;border:2px solid #22c55e;box-sizing:border-box}
.box.off{border-color:#94a3b8;border-style:dashed;opacity:.8}
.box span{position:absolute;left:0;top:-18px;font-size:11px;background:#000a;padding:0 4px;white-space:nowrap;color:#bbf7d0}
.box.off span{color:#e2e8f0}.chip{display:inline-block;padding:1px 8px;border-radius:6px;background:#334155;margin-right:6px;font-size:12px}
"""


def collect(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for item in args:
        path = Path(item)
        if path.is_dir():
            paths.extend(sorted(path.glob('*.png')))
        elif path.exists():
            paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('targets', nargs='*', help='目录或图片；缺省用 logs/perception-monitor')
    parser.add_argument('--out', default=str(ROOT / '.scratch/review/live-frames/index.html'))
    args = parser.parse_args()

    targets = args.targets or [str(ROOT / 'logs/perception-monitor')]
    paths = collect(targets)
    if not paths:
        print(f'没有找到截图：{targets}；先用 scripts/perception_monitor.py 按 s 保存', file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    perception = Perception()
    cards = []
    try:
        for path in paths:
            frame = Frame(image=path.read_bytes(), width=1280, height=720, captured_at=0.0)
            image = decode_frame(frame)
            height, width = image.shape[:2]
            observation = perception.read(frame)
            controls = [
                {'name': c.name, 'box': list(c.box), 'actionable': c.actionable, 'reason': c.reason}
                for c in observation.controls
            ]
            cards.append({
                'file': str(path.relative_to(ROOT)).replace('\\', '/') if path.is_relative_to(ROOT) else str(path),
                'base': observation.base_page.value,
                'variants': sorted(v.value for v in observation.variants),
                'overlays': sorted(o.value for o in observation.overlays),
                'actionable': observation.actionable,
                'controls': controls,
                'width': width,
                'height': height,
            })
    finally:
        perception.close()

    sections = []
    for card in cards:
        boxes = ''.join(
            f'<div class="box{" off" if not control["actionable"] else ""}" '
            f'style="left:{control["box"][0] / card["width"] * 100:.3f}%;'
            f'top:{control["box"][1] / card["height"] * 100:.3f}%;'
            f'width:{(control["box"][2] - control["box"][0]) / card["width"] * 100:.3f}%;'
            f'height:{(control["box"][3] - control["box"][1]) / card["height"] * 100:.3f}%">'
            f'<span>{control["name"]}{"" if control["actionable"] else "（不可点）"}</span></div>'
            for control in card['controls']
        )
        source = Path(card['file'])
        absolute = ROOT / source if not source.is_absolute() else source
        src = Path(os.path.relpath(absolute, out.parent)).as_posix()
        sections.append(f'''
<div class="card">
  <div class="name">{card['file']}</div>
  <div class="meta"><span class="chip">基页 {card['base']}</span>
    <span class="chip">变体 {",".join(card['variants']) or "-"}</span>
    <span class="chip">覆盖层 {",".join(card['overlays']) or "-"}</span>
    <span class="chip">可行动 {card['actionable']}</span></div>
  <div class="stage"><img src="{src}">{boxes}</div>
</div>''')

    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>逐帧核对报告</title><style>{STYLE}</style></head><body>
<h1>逐帧核对报告（{len(cards)} 帧）</h1>
<p class="hint">绿框 = 可点，灰虚框 = 不可点；每帧都用当前代码重新识别。发现不对就截图编号告诉我。</p>
{''.join(sections)}
</body></html>'''
    out.write_text(html, encoding='utf-8')
    print(f'{len(cards)} 帧 -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
