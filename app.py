import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from ink import InkEngine, hex_bgr
from config import CONFIG, load
from timeline import Timeline
from tracking import BoardTracker

KEYS_LEFT = {2424832, 65361, 63234, 81}
KEYS_RIGHT = {2555904, 65363, 63235, 83}
KEYS_QUIT = {27, ord("q"), ord("Q")}
WINDOW = "Tapestry"


class Renderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.timeline = Timeline(cfg)
        self.ink = InkEngine(cfg, self.timeline)
        self.tracker = BoardTracker(cfg)
        self.mult_to_tapestry = self.ink.mult_to_tapestry()
        self.over_to_tapestry = self.ink.over_to_tapestry()
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        self.ow, self.oh = cfg["output"]["width"], cfg["output"]["height"]
        self.bg = tuple(int(v) for v in hex_bgr(cfg["background"]))

    def composite(self, frame, H, mult, over):
        h, w = frame.shape[:2]
        warped = cv2.warpPerspective(mult, H @ self.mult_to_tapestry, (w, h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        out = cv2.multiply(frame, warped, scale=1.0 / 255.0)
        if over is None:
            return out
        top = cv2.warpPerspective(over, H @ self.over_to_tapestry, (w, h), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        clear = cv2.subtract(255, top[:, :, 3])
        out = cv2.multiply(out, cv2.merge([clear, clear, clear]), scale=1.0 / 255.0)
        return cv2.add(out, top[:, :, :3])

    def crop_matrix(self, t):
        x, y, w = self.timeline.camera(t)
        cw = w * self.tw
        ch = cw * self.oh / self.ow
        return np.array([[cw / self.ow, 0, x * self.tw - cw / 2],
                         [0, ch / self.oh, y * self.th - ch / 2],
                         [0, 0, 1]], np.float64)

    def virtual_camera(self, frame, H, t):
        M = H @ self.crop_matrix(t)
        return cv2.warpPerspective(frame, M, (self.ow, self.oh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=self.bg)

    def fit(self, frame):
        h, w = frame.shape[:2]
        s = min(self.ow / w, self.oh / h)
        nw, nh = int(round(w * s)), int(round(h * s))
        out = np.empty((self.oh, self.ow, 3), np.uint8)
        out[:] = self.bg
        x0, y0 = (self.ow - nw) // 2, (self.oh - nh) // 2
        out[y0:y0 + nh, x0:x0 + nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
        return out

    def render(self, frame, t, virtual=True):
        H = self.tracker.update(frame)
        if H is None:
            return self.fit(frame), False
        comp = self.composite(frame, H, *self.ink.layers(t))
        return (self.virtual_camera(comp, H, t) if virtual else self.fit(comp)), True


def hud(img, lines):
    y = img.shape[0] - 24 - 30 * (len(lines) - 1)
    for line in lines:
        cv2.putText(img, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(img, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 225, 200), 1, cv2.LINE_AA)
        y += 30


def open_camera(source, width, height):
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def main():
    ap = argparse.ArgumentParser(description="Live AR ink tapestry")
    ap.add_argument("--beats", default=str(Path(__file__).with_name("beats.json")))
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--camera", default="0", help="device index or stream URL")
    ap.add_argument("--capture-size", default="1920x1080")
    ap.add_argument("--tone", default=None)
    ap.add_argument("--windowed", action="store_true")
    args = ap.parse_args()

    cfg = load(args.beats, args.config)
    if args.tone:
        cfg["ink"]["tone"] = args.tone
    renderer = Renderer(cfg)
    timeline = renderer.timeline
    cw, ch = (int(v) for v in args.capture_size.lower().split("x"))
    cap = open_camera(args.camera, cw, ch)
    if not cap.isOpened():
        raise SystemExit(f"Could not open camera {args.camera!r}")

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    if not args.windowed:
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    frame = None
    t, playing, virtual = 0.0, True, True
    last = time.perf_counter()
    while True:
        ok, f = cap.read()
        if ok:
            frame = f
        now = time.perf_counter()
        if playing:
            t = min(timeline.end, t + now - last)
        last = now
        if frame is None:
            if cv2.waitKey(10) in KEYS_QUIT:
                break
            continue

        out, locked = renderer.render(frame, t, virtual)
        lines = []
        if not locked:
            lines.append("Looking for markers 0-3 ... seen: " + (", ".join(map(str, renderer.tracker.seen)) or "none"))
        if not playing:
            i = timeline.beat_at(t)
            lines.append(f"PAUSED  {i + 1:02d} {timeline.beats[i]['name']}  {t:5.2f}s")
        if lines:
            hud(out, lines)
        cv2.imshow(WINDOW, out)

        key = cv2.waitKeyEx(1)
        if key in KEYS_QUIT:
            break
        if key == 32:
            playing = not playing
        elif key in KEYS_LEFT:
            t = timeline.step(t, -1)
        elif key in KEYS_RIGHT:
            t = timeline.step(t, +1)
        elif key in (ord("r"), ord("R")):
            t = 0.0
        elif key in (ord("v"), ord("V")):
            virtual = not virtual
        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
