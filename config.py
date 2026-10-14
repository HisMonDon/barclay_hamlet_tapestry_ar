import copy
import json
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.json"
MM_PER_IN = 25.4
SECTIONS = ("quality", "atmosphere", "sound", "camera_input", "backup_video", "cache_dir", "debug", "editor", "tracking", "performance")


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


def default_beats(path=CONFIG):
    value = Path(read(path).get("beats", "beats_v2.json"))
    candidate = Path(path).resolve().parent / value
    return str(candidate if candidate.exists() else ROOT / value)


def load(beats_path=None, config_path=CONFIG, paper=None, quality="live"):
    beats_path = Path(beats_path or default_beats(config_path)).resolve()
    cfg = read(beats_path)
    cfg.update(board(config_path, paper))
    c = read(config_path)
    for k in SECTIONS:
        if k in c:
            cfg[k] = copy.deepcopy(c[k])
    q = cfg.get("quality", {}).get(quality, {})
    cfg["quality_name"] = quality
    cfg["ink"] = dict(cfg.get("ink", {}))
    if "ink_resolution" in q:
        cfg["ink"]["resolution"] = q["ink_resolution"]
    cfg["overlay"]["scale"] = q.get("overlay_scale", 1.0)
    cfg["output_scale"] = q.get("output_scale", 1.0)
    cfg["beats_path"] = str(beats_path)
    cfg["root"] = str(ROOT)
    return cfg


def resolve(cfg, rel):
    if rel is None:
        return None
    p = Path(rel)
    if p.is_absolute():
        return p
    for base in (Path(cfg.get("beats_path", ROOT)).parent, ROOT):
        if (base / p).exists():
            return base / p
    return ROOT / p


def _inline(v):
    return isinstance(v, (int, float, str, bool)) or v is None


def _fmt(v, ind):
    pad = " " * ind
    if isinstance(v, dict):
        if not v:
            return "{}"
        items = [f'{pad} {json.dumps(k)}: {_fmt(x, ind + 1)}' for k, x in v.items()]
        if all(_inline(x) for x in v.values()) and sum(len(s) for s in items) < 150:
            return "{" + ", ".join(f"{json.dumps(k)}: {json.dumps(x)}" for k, x in v.items()) + "}"
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(v, list):
        if all(_inline(x) for x in v) or all(isinstance(x, list) and all(_inline(y) for y in x) for x in v):
            return json.dumps(v, separators=(", ", ": "))
        return "[\n" + ",\n".join(f"{pad} {_fmt(x, ind + 1)}" for x in v) + "\n" + pad + "]"
    return json.dumps(v)


def dump(obj, path):
    text = _fmt(obj, 0) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
