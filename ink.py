import math

import cv2
import numpy as np

CAP = {"max": 1.0, "tint": "#9fb4dc"}
WEAVE = {"colour": "#c99b45", "speed": 0.15, "pitch": 0.014, "fill": 0.72, "warp": 0.022, "duty": 0.78}


def hex_bgr(h):
    h = h.lstrip("#")
    return np.array([int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)], np.float32)


def fractal_noise(w, h, cells, octaves, persistence, seed):
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    amp = 1.0
    for o in range(octaves):
        nx = max(2, int(round(cells * 2 ** o)))
        ny = max(2, int(round(nx * h / w)))
        grid = rng.random((ny + 1, nx + 1), dtype=np.float32)
        out += amp * cv2.resize(grid, (w, h), interpolation=cv2.INTER_CUBIC)
        amp *= persistence
    out -= out.mean()
    out /= max(1e-6, float(np.abs(out).max()))
    return out


class Canvas:
    def __init__(self, tw, th, scale, margin):
        self.tw, self.th = tw, th
        self.w0 = max(8, int(round(tw * scale)))
        self.h0 = max(8, int(round(th * scale)))
        self.pad = int(round(margin * self.w0))
        self.w, self.h = self.w0 + 2 * self.pad, self.h0 + 2 * self.pad
        self.inner = (slice(self.pad, self.pad + self.h0), slice(self.pad, self.pad + self.w0))

    def to_tapestry(self):
        sx, sy = self.tw / self.w0, self.th / self.h0
        return np.array([[sx, 0, sx * (0.5 - self.pad) - 0.5], [0, sy, sy * (0.5 - self.pad) - 0.5], [0, 0, 1]], np.float64)

    def span(self, x0, y0, x1, y1):
        c0 = max(0, int(math.floor(x0 * self.w0)) + self.pad)
        c1 = min(self.w, int(math.ceil(x1 * self.w0)) + self.pad)
        r0 = max(0, int(math.floor(y0 * self.h0)) + self.pad)
        r1 = min(self.h, int(math.ceil(y1 * self.h0)) + self.pad)
        return slice(r0, r1), slice(c0, c1)

    def coords(self, rows, cols):
        xs = (np.arange(cols.start, cols.stop, dtype=np.float32) - self.pad + 0.5) / self.w0
        ys = (np.arange(rows.start, rows.stop, dtype=np.float32) - self.pad + 0.5) / self.h0
        return xs[None, :], ys[:, None]


class Seed:
    __slots__ = ("t0", "speed", "radius", "rows", "cols", "base")

    def __init__(self, t0, speed, radius, rows, cols, base):
        self.t0, self.speed, self.radius = t0, speed, radius
        self.rows, self.cols, self.base = rows, cols, base


class Weave:
    def __init__(self, spec, t0, canvas):
        w = {**WEAVE, **spec}
        x0, y0, x1, y1 = w["bounds"]
        aspect = canvas.th / canvas.tw
        self.rows, self.cols = canvas.span(x0, y0, x1, y1)
        xs, ys = canvas.coords(self.rows, self.cols)
        px = 1.0 / canvas.w0
        pitch, fill, warp, duty = w["pitch"], w["fill"], w["warp"], w["duty"]
        u = xs - x0
        v = (ys - y0) * aspect
        row = np.floor(v / pitch)
        fv = v / pitch - row
        odd = (row % 2).astype(np.float32)
        fu = np.mod(u / warp + 0.5 * odd, 1.0)
        a_v = np.clip((fill / 2 - np.abs(fv - 0.5)) * pitch / px + 0.5, 0.0, 1.0)
        a_u = np.clip((duty / 2 - np.abs(fu - 0.5)) * warp / px + 0.5, 0.0, 1.0)
        across = np.clip((fv - (1 - fill) / 2) / fill, 0.0, 1.0)
        shade = (0.62 + 0.38 * np.sin(np.pi * across)) * (0.86 + 0.14 * np.sin(np.pi * fu))
        self.static = (a_v * a_u).astype(np.float32)
        self.colour = (hex_bgr(w["colour"]) * shade[..., None]).astype(np.float32)
        self.width = x1 - x0
        self.along = np.broadcast_to(np.where(odd > 0, self.width - u, u), self.static.shape).astype(np.float32)
        self.row_time = pitch / w["speed"]
        self.start = (t0 + np.broadcast_to(row, self.static.shape) * self.row_time).astype(np.float32)
        self.t0 = t0
        self.px = px

    def paint(self, over, t):
        if t < self.t0:
            return False
        p = np.clip((t - self.start) / self.row_time, 0.0, 1.0)
        a = self.static * np.clip((p * self.width - self.along) / self.px + 0.5, 0.0, 1.0)
        dst = over[self.rows, self.cols].astype(np.float32)
        keep = 1.0 - a
        dst[..., :3] = self.colour * a[..., None] + dst[..., :3] * keep[..., None]
        dst[..., 3] = 255.0 * a + dst[..., 3] * keep
        over[self.rows, self.cols] = np.clip(dst + 0.5, 0, 255).astype(np.uint8)
        return True


class InkEngine:
    def __init__(self, cfg, timeline):
        ink = {"alpha": 0.05, "edge": 0.025, "opacity": 1.0, "noise": {"cells": 6, "octaves": 5, "persistence": 0.6, "seed": 7},
               "ramp": [[0.0, "stain", 0.0], [0.6, "stain", 0.35], [0.68, "front", 0.965], [1.0, "deep", 0.965]], **cfg["ink"]}
        cfg = {**cfg, "ink": ink}
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        margin = cfg.get("overlay", {}).get("margin", 0.0)
        self.ink_canvas = Canvas(self.tw, self.th, ink.get("resolution", 1.0), margin)
        self.over_canvas = Canvas(self.tw, self.th, 1.0, margin)
        self.iw, self.ih = self.ink_canvas.w0, self.ink_canvas.h0
        self.edge = max(1e-4, ink["edge"])
        self.aspect = self.th / self.tw
        n = ink["noise"]
        self.noise = fractal_noise(self.iw, self.ih, n["cells"], n["octaves"], n["persistence"], n["seed"])
        self.xs = ((np.arange(self.iw, dtype=np.float32) + 0.5) / self.iw)[None, :]
        self.ys = ((np.arange(self.ih, dtype=np.float32) + 0.5) / self.ih)[:, None]
        seeds = list(timeline.seeds())
        self.seeds = [self._build(s, t0, ink["alpha"]) for s, t0 in seeds if not s.get("lift")]
        self.seeds = [s for s in self.seeds if s is not None]
        weaves = [(self._lift_weave(s), t0) for s, t0 in seeds if s.get("lift")] + list(timeline.weaves())
        self.weaves = [Weave(w, t0, self.over_canvas) for w, t0 in weaves]
        self.opacity = ink["opacity"]
        self.cap = self._caps(ink.get("caps", ink.get("resist", [])), {**CAP, **ink.get("cap", {})}, ink.get("feather", 0.0))
        self.set_tone(cfg, ink["tone"])
        self.mult = np.full((self.ink_canvas.h, self.ink_canvas.w, 3), 255, np.uint8)
        self.over = np.zeros((self.over_canvas.h, self.over_canvas.w, 4), np.uint8)
        self._painted = []
        self._cache_t = None
        self._cache = None

    def _lift_weave(self, s):
        if "bounds" in s:
            bounds = s["bounds"]
        elif math.isfinite(s.get("radius", math.inf)):
            r = s["radius"]
            bounds = [s["x"] - r, s["y"] - r / self.aspect, s["x"] + r, s["y"] + r / self.aspect]
        else:
            bounds = [0.0, 0.0, 1.0, 1.0]
        return {"bounds": bounds, "speed": s["speed"]}

    def _build(self, s, t0, alpha_default):
        alpha = s.get("alpha", alpha_default)
        radius = s.get("radius", math.inf)
        sx, sy = s["x"], s["y"]
        x0, y0, x1, y1 = s.get("bounds", [0.0, 0.0, 1.0, 1.0])
        if math.isfinite(radius):
            reach = radius + abs(alpha) + self.edge
            x0, x1 = max(x0, sx - reach), min(x1, sx + reach)
            y0, y1 = max(y0, sy - reach / self.aspect), min(y1, sy + reach / self.aspect)
        c0, c1 = max(0, int(math.floor(x0 * self.iw))), min(self.iw, int(math.ceil(x1 * self.iw)))
        r0, r1 = max(0, int(math.floor(y0 * self.ih))), min(self.ih, int(math.ceil(y1 * self.ih)))
        if c1 <= c0 or r1 <= r0:
            return None
        dx = self.xs[:, c0:c1] - sx
        dy = (self.ys[r0:r1, :] - sy) * self.aspect
        base = alpha * self.noise[r0:r1, c0:c1] - np.sqrt(dx * dx + dy * dy)
        return Seed(t0, s["speed"], radius, slice(r0, r1), slice(c0, c1), base.astype(np.float32))

    def _caps(self, shapes, default, feather):
        if not shapes:
            return None
        mask = np.zeros((self.ih, self.iw), np.float32)
        top = np.zeros_like(mask)
        tint = np.zeros((self.ih, self.iw, 3), np.float32)
        for e in shapes:
            m = np.zeros_like(mask)
            centre = (int(round(e["x"] * self.iw)), int(round(e["y"] * self.ih)))
            axes = (max(1, int(round(e["rx"] * self.iw))), max(1, int(round(e["ry"] * self.ih))))
            cv2.ellipse(m, centre, axes, e.get("angle", 0), 0, 360, 1.0, -1, cv2.LINE_AA)
            f = e.get("feather", feather)
            if f > 0:
                m = cv2.GaussianBlur(m, (0, 0), f * self.iw)
            np.clip(m, 0.0, 1.0, out=m)
            np.maximum(mask, m, out=mask)
            top += (e.get("max", default["max"]) - top) * m
            tint += (hex_bgr(e.get("tint", default["tint"])) - tint) * m[..., None]
        ys, xs = np.nonzero(mask > 1e-3)
        if not len(ys):
            return None
        rows, cols = slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1)
        return rows, cols, mask[rows, cols, None], top[rows, cols] * self.opacity, 255.0 - tint[rows, cols]

    def set_tone(self, cfg, name):
        ink = cfg["ink"]
        tone = {k: hex_bgr(v) for k, v in ink["tones"][name].items()}
        tone.setdefault("front", tone.get("wet"))
        tone.setdefault("deep", tone.get("dry"))
        stops = [(p, tone[k], a) for p, k, a in ink["ramp"]]
        lut = np.empty((256, 3), np.float32)
        for i in range(256):
            d = i / 255.0
            col, a = stops[-1][1], stops[-1][2]
            for (pa, ca, aa), (pb, cb, ab) in zip(stops, stops[1:]):
                if d <= pb:
                    k = 0.0 if pb <= pa else max(0.0, (d - pa) / (pb - pa))
                    col, a = ca + (cb - ca) * k, aa + (ab - aa) * k
                    break
            a *= ink["opacity"]
            lut[i] = 255.0 * (1.0 - a) + a * col
        self.lut = np.clip(lut + 0.5, 0, 255).astype(np.uint8).reshape(1, 256, 3)
        self.tone = name
        self._cache_t = None

    def mult_to_tapestry(self):
        return self.ink_canvas.to_tapestry()

    def over_to_tapestry(self):
        return self.over_canvas.to_tapestry()

    def darkness(self, t):
        dark = np.zeros((self.ih, self.iw), np.float32)
        inv = 1.0 / self.edge
        for s in self.seeds:
            if t < s.t0:
                continue
            front = min((t - s.t0) * s.speed, s.radius)
            patch = s.base + np.float32(front)
            patch *= inv
            np.clip(patch, 0.0, 1.0, out=patch)
            np.maximum(dark[s.rows, s.cols], patch, out=dark[s.rows, s.cols])
        return dark

    def ink(self, t):
        dark = self.darkness(t)
        idx = cv2.convertScaleAbs(dark, alpha=255.0)
        out = cv2.LUT(cv2.merge([idx, idx, idx]), self.lut)
        if self.cap is not None:
            rows, cols, mask, top, fade = self.cap
            s = np.minimum(dark[rows, cols] * self.opacity, top)
            capped = 255.0 - s[..., None] * fade
            region = out[rows, cols].astype(np.float32)
            out[rows, cols] = np.clip(region + (capped - region) * mask + 0.5, 0, 255).astype(np.uint8)
        return out

    def woven(self, t):
        for rows, cols in self._painted:
            self.over[rows, cols] = 0
        self._painted = [(w.rows, w.cols) for w in self.weaves if w.paint(self.over, t)]
        return self.over if self._painted else None

    def layers(self, t):
        if self._cache_t != t:
            self.mult[self.ink_canvas.inner] = self.ink(t)
            self._cache = (self.mult, self.woven(t))
            self._cache_t = t
        return self._cache
