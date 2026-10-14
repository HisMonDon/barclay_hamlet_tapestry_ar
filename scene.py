import cv2
import numpy as np

from banner import Weave
from config import ROOT, resolve
from ink import Canvas, InkEngine


def load_scan(cfg):
    p = resolve(cfg, cfg.get("scan"))
    if p is None or not p.exists():
        return None
    return cv2.imread(str(p), cv2.IMREAD_COLOR)


class Scene:
    def __init__(self, cfg, timeline, scan=None):
        self.cfg = cfg
        self.timeline = timeline
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        margin = cfg.get("overlay", {}).get("margin", 0.0)
        self.scan = load_scan(cfg) if scan is None else scan
        cache = cfg.get("cache_dir")
        cache = (ROOT / cache) if cache else None
        self.over_canvas = Canvas(self.tw, self.th, cfg.get("overlay", {}).get("scale", 1.0), margin)
        self.props = []
        extra = []
        self.ink = InkEngine(cfg, timeline, self.scan, extra, cache)
        self.ink_canvas = self.ink.ink_canvas
        glints = [(g, t) for g, t, _ in timeline.items("props") if g.get("kind") == "glint"]
        self.weaves = [Weave(w, t0, self.over_canvas, cfg.get("banner"), glints[0] if glints else None)
                       for w, t0 in timeline.weaves()]
        self.over = np.zeros((self.over_canvas.h, self.over_canvas.w, 4), np.uint8)
        self._saved = []
        self._content = False
        self._cache_t = None
        self._cache = None

    def mult_to_tapestry(self):
        return self.ink_canvas.to_tapestry()

    def over_to_tapestry(self):
        return self.over_canvas.to_tapestry()

    def set_tone(self, name):
        self.ink.set_tone(self.cfg, name)
        self._cache_t = None

    def _restore(self):
        for rows, cols, data in reversed(self._saved):
            self.over[rows, cols] = data
        self._saved = []

    def _stamp(self, rows, cols, rgba, under):
        dst = self.over[rows, cols]
        self._saved.append((rows, cols, dst.copy()))
        a = dst if under else rgba
        b = rgba if under else dst
        k = (255 - a[..., 3:4].astype(np.uint16))
        out = a.astype(np.uint16) + (b.astype(np.uint16) * k + 127) // 255
        self.over[rows, cols] = np.minimum(out, 255).astype(np.uint8)

    def banner_layer(self, t):
        out = []
        for w in self.weaves:
            img = w.render(t)
            if img is not None:
                out.append((w.rows, w.cols, img))
        return out

    def over_banner_only(self, t):
        over = np.zeros_like(self.over)
        for rows, cols, img in self.banner_layer(t):
            over[rows, cols] = img
        return over

    def overlay(self, t):
        self._restore()
        drew = self._content
        for w in self.weaves:
            img = w.render(t)
            if img is not None:
                self._stamp(w.rows, w.cols, img, True)
                drew = True
        return self.over if drew else None

    def layers(self, t):
        if self._cache_t != t:
            self._cache = (self.ink.layer(t), self.overlay(t))
            self._cache_t = t
        return self._cache
