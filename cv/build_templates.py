"""从真实截图裁剪四类状态的模板。

模板只取稳定的 UI 区域，不依赖从者立绘或敌人素材：
- menu：主界面的「迦勒底之门」按钮；
- battle：右侧「战斗菜单 / 御主技能」菱形按钮，以及各面的敌方血条区域；
- card：指令卡选择界面的「战斗速度」按钮；
- result：战斗结果标题，以及战利品页标题。
"""
from pathlib import Path

import cv2

from image_io import read_image, write_image

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

CROPS = {
    "menu": (ROOT / "captures/2026-11-19/01-main.png", (680, 485, 1095, 575)),
    "battle_diamond": (ROOT / "captures/2026-11-19-ember/06b-battle-wave1.png", (1120, 150, 1250, 340)),
    "battle_enemy1": (ROOT / "captures/2026-11-19-ember/key/05-skill.png", (0, 0, 540, 60)),
    "battle_enemy2": (ROOT / "captures/2026-11-19-ember/13b-wave2-start.png", (0, 0, 540, 60)),
    "battle_enemy3": (ROOT / "captures/2026-11-19-ember/17b-wave3-start.png", (0, 0, 540, 60)),
    "card": (ROOT / "captures/2026-11-19-ember/08-card-select.png", (1060, 25, 1225, 100)),
    "result_title": (ROOT / "captures/2026-11-19-ember/30-victory.png", (480, 25, 800, 100)),
    "result_drops": (ROOT / "captures/2026-11-19-ember/32-drops.png", (100, 25, 650, 80)),
}


def main() -> None:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    for name, (source, (x1, y1, x2, y2)) in CROPS.items():
        image = read_image(source)
        if image is None:
            raise SystemExit(f"cannot read capture: {source}")
        if image.shape[1] != 1280 or image.shape[0] != 720:
            image = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_AREA)
        crop = image[y1:y2, x1:x2]
        target = TEMPLATE_DIR / f"{name}.png"
        if not write_image(target, crop):
            raise SystemExit(f"cannot write template: {target}")
        print(f"{name}: {crop.shape[1]}x{crop.shape[0]} -> {target}")


if __name__ == "__main__":
    main()
