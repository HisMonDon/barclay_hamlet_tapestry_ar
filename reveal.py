import cv2

from config import resolve


def load_art(cfg, key="corrupt"):
    rel = cfg.get("assets", {}).get(key)
    if not rel:
        return None
    p = resolve(cfg, rel)
    if not p.exists():
        return None
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read reveal art: {p}. Rerun python -m tools.align_layers.")
    return img
