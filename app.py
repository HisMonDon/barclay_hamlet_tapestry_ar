import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from ink import InkEngine, hex_bgr
from compositor import Compositor
from config import CONFIG, default_beats, load
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
        self.scene = self.ink
        self.tracker = BoardTracker(cfg)
        self.comp = Compositor(cfg, self.scene, self.timeline)
        self.ow, self.oh = self.comp.ow, self.comp.oh

    def composite(self, frame, H, mult, over):
        return self.comp.composite(frame, H, mult, over)

    def fit(self, frame):
        return self.comp.fit(frame)

    def frame(self, frame, H, t, virtual=True):
        return self.comp.frame(frame, H, t, virtual)

    def render(self, frame, t, virtual=True):
        H = self.tracker.update(frame)
        if H is None:
            return self.fit(frame), False
        return self.frame(frame, H, t, virtual), True


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
    ap.add_argument("--beats", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--camera", default="0", help="device index or stream URL")
    ap.add_argument("--capture-size", default="1920x1080")
    ap.add_argument("--tone", default=None)
    ap.add_argument("--windowed", action="store_true")
    args = ap.parse_args()

    cfg = load(args.beats or default_beats(args.config), args.config)
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
