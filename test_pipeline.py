import sys
import time
from pathlib import Path

import cv2
import numpy as np

from app import Renderer
from config import load
from tracking import marker_layout

ROOT = Path(__file__).parent
OUT = ROOT / "test_out"
FRAME = (1920, 1080)
PAPER = (196, 223, 234)
DESK = (60, 80, 100)
FAILS = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        FAILS.append(name)


def scene(cfg):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    layout = marker_layout(cfg)
    pad = int(np.ceil(-np.concatenate(list(layout.values())).min())) + 60
    img = np.empty((th + 2 * pad, tw + 2 * pad, 3), np.uint8)
    img[:] = DESK
    img[pad:pad + th, pad:pad + tw] = PAPER
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for i, sq in layout.items():
        x0, y0 = sq[0] + pad
        side = int(round(sq[1][0] - sq[0][0]))
        q = side // 6
        cv2.rectangle(img, (int(x0) - q, int(y0) - q), (int(x0) + side + q, int(y0) + side + q), (255, 255, 255), -1)
        img[int(y0):int(y0) + side, int(x0):int(x0) + side] = cv2.cvtColor(
            cv2.aruco.generateImageMarker(d, i, side, borderBits=1), cv2.COLOR_GRAY2BGR)
    S = np.array([[1, 0, pad], [0, 1, pad], [0, 0, 1]], np.float64)
    return img, S


def truth(cfg):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    h = 620.0
    w = h * tw / th
    cx, cy = FRAME[0] / 2, FRAME[1] / 2
    quad = np.float32([(cx - w / 2 + 30, cy - h / 2), (cx + w / 2 - 10, cy - h / 2 + 15),
                       (cx + w / 2 + 25, cy + h / 2), (cx - w / 2 - 20, cy + h / 2 - 10)])
    rect = np.float32([(0, 0), (tw, 0), (tw, th), (0, th)])
    return cv2.getPerspectiveTransform(rect, quad)


def to_frame(H, cfg, x, y):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    p = cv2.perspectiveTransform(np.float32([[[x * tw, y * th]]]), H)[0, 0]
    return int(round(p[0])), int(round(p[1]))


def sample(img, xy, r=2):
    x, y = xy
    return img[y - r:y + r + 1, x - r:x + r + 1].reshape(-1, 3).mean(axis=0)


def run(cfg):
    renderer = Renderer(cfg)
    tl = renderer.timeline
    img, S = scene(cfg)
    Ht = truth(cfg)
    frame = cv2.warpPerspective(img, Ht @ np.linalg.inv(S), FRAME, flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=DESK)
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    print(f"-- {cfg['board']['paper']}: tapestry {tw}x{th}, drawing {cfg['board']['drawing_mm']} mm")
    dw, dh = cfg["board"]["drawing_mm"]
    check("tapestry aspect follows paper", abs(th / tw - dh / dw) < 1.0 / tw, f"{th / tw:.4f} vs {dh / dw:.4f}")

    out, locked = renderer.render(frame, 0.0)
    check("tracker locks", locked, f"seen {renderer.tracker.seen}")
    H = renderer.tracker.H
    rect = np.float32([[(0, 0), (tw, 0), (tw, th), (0, th)]])
    err = float(np.abs(cv2.perspectiveTransform(rect, H) - cv2.perspectiveTransform(rect, Ht)).max())
    check("homography matches truth", err < 1.5, f"max corner error {err:.2f}px")
    check("virtual camera output size", out.shape == (FRAME[1], FRAME[0], 3), str(out.shape))

    centre = to_frame(Ht, cfg, 0.5, 0.55)
    c0 = renderer.composite(frame, H, *renderer.ink.layers(0.0))
    c1 = renderer.composite(frame, H, *renderer.ink.layers(tl.end))
    v0, v1 = sample(c0, centre).mean(), sample(c1, centre).mean()
    check("paper untouched at t=0", v0 > 180, f"{v0:.0f}")
    check("paper black at end", v1 < 40, f"{v1:.0f}")
    desk = to_frame(Ht, cfg, -0.3, 0.5)
    dv = sample(c1, desk)
    check("desk unchanged by ink", np.abs(dv - np.array(DESK)).max() < 6, str(dv.round()))

    ghost = sample(c1, to_frame(Ht, cfg, 0.5125, 0.1278))
    paper = sample(c0, to_frame(Ht, cfg, 0.5125, 0.1278))
    check("ghost capped, not blocked", 90 < ghost.mean() < paper.mean() - 30, f"ghost {ghost.round()} paper {paper.round()}")
    check("ghost tint is cool", ghost[0] > ghost[2] + 12 and paper[0] < paper[2], f"B-R {ghost[0] - ghost[2]:.0f}")

    banner = next(w for w in tl.beats if w["name"] == "Banner")["seeds"][0]["bounds"]
    bx0, by0, bx1, by1 = banner
    corners = [to_frame(Ht, cfg, x, y) for x, y in [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)]]
    region = np.zeros(c1.shape[:2], np.uint8)
    cv2.fillConvexPoly(region, np.int32(corners), 1)
    region = cv2.erode(region, np.ones((9, 9), np.uint8))
    px = c1[region > 0].astype(np.float32)
    gold = (px[:, 2] > 110) & (px[:, 2] > px[:, 0] + 50)
    black = px.max(axis=1) < 45
    check("banner woven in gold", gold.mean() > 0.4, f"gold {gold.mean():.2f}")
    check("banner gaps show black ink", 0.05 < black.mean() < 0.45, f"black {black.mean():.2f}")
    start = tl.starts[[b["name"] for b in tl.beats].index("Banner")]
    under = renderer.ink.layers(tl.end)[0][renderer.ink.ink_canvas.span(bx0, by0, bx1, by1)].mean()
    check("banner does not erase ink", under < 40, f"ink under banner {under:.0f}")
    mid_over = renderer.ink.layers(start + 1.2)[1]
    a = mid_over[..., 3]
    rows = np.nonzero(a.max(axis=1) > 0)[0]
    oy0, oy1 = renderer.ink.over_canvas.span(bx0, by0, bx1, by1)[0].start, renderer.ink.over_canvas.span(bx0, by0, bx1, by1)[0].stop
    check("banner weaves row by row", len(rows) and rows.min() < oy0 + 20 and rows.max() < oy1 - 40,
          f"rows {rows.min() if len(rows) else None}-{rows.max() if len(rows) else None} of {oy0}-{oy1}")
    check("no overlay before banner", renderer.ink.layers(start - 0.5)[1] is None)

    margin = cfg["overlay"]["margin"]
    for name, cv in (("ink", renderer.ink.ink_canvas), ("over", renderer.ink.over_canvas)):
        check(f"{name} canvas extends past paper", cv.pad == round(margin * cv.w0) and cv.w == cv.w0 + 2 * cv.pad
              and cv.h == cv.h0 + 2 * cv.pad, f"{cv.w0}x{cv.h0} -> {cv.w}x{cv.h}")
    over = np.zeros_like(renderer.ink.over)
    spots = [(-0.25, 0.5), (1.3, 0.2), (0.5, -0.3), (0.5, 1.0 + 0.3 * tw / th)]
    for x, y in spots:
        over[renderer.ink.over_canvas.span(x - 0.03, y - 0.02, x + 0.03, y + 0.02)] = (0, 0, 255, 255)
    cd = renderer.composite(frame, H, renderer.ink.layers(0.0)[0], over)
    hits = [sample(cd, to_frame(Ht, cfg, x, y)) for x, y in spots]
    check("desk margin draws through homography", all(h[2] > 240 and h[1] < 15 for h in hits),
          " ".join(str(h.round()) for h in hits))
    cv2.imwrite(str(OUT / f"{cfg['board']['paper']}_desk.png"), cd)
    cv2.imwrite(str(OUT / f"{cfg['board']['paper']}_weaving.png"),
                renderer.composite(frame, H, *renderer.ink.layers(start + 1.2)))

    t0 = time.perf_counter()
    n = 0
    for k in np.arange(0.0, tl.end + 1e-6, 1.5):
        renderer.render(frame, float(k))
        n += 1
    check("full run renders", True, f"{1000 * (time.perf_counter() - t0) / n:.1f} ms/frame")

    OUT.mkdir(exist_ok=True)
    cv2.imwrite(str(OUT / f"{cfg['board']['paper']}_end.png"), c1)


def main():
    default = load(str(ROOT / "beats.json"))
    check("default paper is tabloid", default["board"]["paper"] == "tabloid", default["board"]["paper"])
    run(default)
    run(load(str(ROOT / "beats.json"), paper="letter"))
    if FAILS:
        print(f"{len(FAILS)} failed: {', '.join(FAILS)}")
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
