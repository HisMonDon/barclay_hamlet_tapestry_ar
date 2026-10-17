import cv2
import numpy as np

import props as propmod
from banner import Weave
from config import ROOT, resolve
from ink import Canvas, InkEngine
from reveal import load_art
from weeds import WeedEngine


def load_scan(cfg):
    source = cfg.get("scan")
    if not source:
        return None
    p = resolve(cfg, source)
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read tapestry artwork: {p}. Check the beat file's 'scan' path.")
    return img


class Scene:
    def __init__(self, cfg, timeline, scan=None):
        self.cfg = cfg
        self.timeline = timeline
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        margin = cfg.get("overlay", {}).get("margin", 0.0)
        self.scan = load_scan(cfg) if scan is None else scan
        self.reveal_art = load_art(cfg)
        cache = cfg.get("cache_dir")
        cache = (ROOT / cache) if cache else None
        self.over_canvas = Canvas(self.tw, self.th, cfg.get("overlay", {}).get("scale", 1.0), margin)
        self.ctx = propmod.Ctx(self.tw, self.th, self.over_canvas)
        self.ctx.splash = dict(cfg.get("splash", {}), on=cfg.get("splash", {}).get("on", True) and cfg.get("fx", {}).get("splash", True))
        self.props = propmod.build(timeline, self.ctx)
        extra = [(timeline.resolve(s), t) for p in self.props for s, t in p.seeds()]
        ungild = [p.ungild() for p in self.props if hasattr(p, "ungild")]
        self.ink = InkEngine(cfg, timeline, self.scan, extra, cache, ungild)
        self.ctx.gold = self.ink.fields.gold
        self.ink_canvas = self.ink.ink_canvas
        glints = [(g, t) for g, t, _ in timeline.items("props") if g.get("kind") == "glint"]
        self.weaves = [Weave(w, t0, self.over_canvas, cfg.get("banner"), glints[0] if glints else None)
                       for w, t0 in timeline.weaves()]
        self.over = np.zeros((self.over_canvas.h, self.over_canvas.w, 4), np.uint8)
        self.weeds = WeedEngine(cfg, timeline, self.ink.T, self.ink_canvas, self.over_canvas)
        self._saved = []
        self._content = False
        self._cache_t = None
        self._cache = None
        self._extras = {}
        self.over_box = None
        self.glow_on = bool(cfg.get("fx", {}).get("sheen_glow", True)) and cfg["ink"].get("sheen", {}).get("glow", 0.6) > 0
        self.glow_sigma = max(0.8, cfg["ink"].get("sheen", {}).get("glow_mm", 2.2) * self.ink_canvas.w0 / cfg["board"]["drawing_mm"][0] / 2)
        self.glow_matrix = np.array([[0.5, 0, -0.25], [0, 0.5, -0.25], [0, 0, 1]], np.float64)

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
        clear = cv2.subtract(255, a[..., 3])
        self.over[rows, cols] = cv2.add(a, cv2.multiply(b, cv2.merge([clear, clear, clear, clear]), scale=1 / 255))

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
        drew = self.weeds.draw(self.over, t)
        box = self.weeds.bbox
        for w in self.weaves:
            img = w.render(t)
            if img is not None:
                self._stamp(w.rows, w.cols, img, True)
                box = self._union(box, w.rows, w.cols)
                drew = True
        for p in self.props:
            for rows, cols, img in p.stamps(t):
                self._stamp(rows, cols, img, p.under)
                box = self._union(box, rows, cols)
                drew = True
        self.over_box = box
        return self.over if drew else None

    @staticmethod
    def _union(box, rows, cols):
        b = (cols.start, rows.start, cols.stop, rows.stop)
        return b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))

    def cues(self):
        out = []
        for p in self.props:
            out += p.cues()
        return out

    def layers(self, t):
        if self._cache_t != t:
            self._cache = (self.ink.layer(t), self.overlay(t))
            self._extras = self._make_extras(t)
            self._cache_t = t
        return self._cache

    def extras(self, t):
        """Additive wet-ink sheen (with glow) and the soft weed shadow, for the frame at t."""
        self.layers(t)
        return self._extras

    def _make_extras(self, t):
        ex = {}
        spec = self.ink.spec_layer(t)
        if spec is not None:
            glow = None
            box = self.ink.spec_box()
            if self.glow_on:
                h, w = spec.shape
                small = cv2.resize(spec, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
                glow = (cv2.GaussianBlur(small, (0, 0), self.glow_sigma), self.glow_matrix)
                g = 6 * self.glow_sigma + 2
                box = (box[0] - g, box[1] - g, box[2] + g, box[3] + g)
            ex["spec"] = (spec, glow, box)
        shade = self.weeds.soft_shade()
        if shade is not None:
            we = self.weeds
            k, g = we.sk, 3 * we.ssig + 3
            bx = we.bbox
            ex["shade"] = (shade, we.soft_matrix, (bx[0] / k - g, bx[1] / k - g, bx[2] / k + g, bx[3] / k + g))
        if self.over_box is not None:
            ex["over_box"] = self.over_box
        return ex
