import json
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.json"
MM_PER_IN = 25.4


def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def board(path=CONFIG, paper=None):
    c = read(path)
    if paper:
        c["paper"] = paper
    w, h = sorted(round(v * MM_PER_IN, 2) for v in c["papers_in"][c["paper"]])
    tw = int(c["tapestry_width"])
    return {
        "board": {"paper": c["paper"], "drawing_mm": [w, h], "marker_mm": c["marker_mm"], "gap_mm": c["gap_mm"]},
        "tapestry": {"width": tw, "height": int(round(tw * h / w))},
        "overlay": {"margin": c.get("overlay_margin", 0.5)},
    }


def load(beats_path, config_path=CONFIG, paper=None):
    cfg = read(beats_path)
    cfg.update(board(config_path, paper))
    return cfg
