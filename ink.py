import math

import cv2
import numpy as np


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


class Seed:
    __slots__ = ("t0", "speed", "radius", "lift", "rows", "cols", "base")

    def __init__(self, t0, speed, radius, lift, rows, cols, base):
        self.t0, self.speed, self.radius, self.lift = t0, speed, radius, lift
        self.rows, self.cols, self.base = rows, cols, base


class InkEngine:
    def __init__(self, cfg, timeline):
        ink = cfg["ink"]
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        self.iw = max(8, int(round(self.tw * ink.get("resolution", 1.0))))
        self.ih = max(8, int(round(self.th * ink.get("resolution", 1.0))))
        self.edge = max(1e-4, ink["edge"])
        self.aspect = self.th / self.tw
        n = ink["noise"]
        self.noise = fractal_noise(self.iw, self.ih, n["cells"], n["octaves"], n["persistence"], n["seed"])
        self.xs = ((np.arange(self.iw, dtype=np.float32) + 0.5) / self.iw)[None, :]
        self.ys = ((np.arange(self.ih, dtype=np.float32) + 0.5) / self.ih)[:, None]
        self.seeds = [self._build(s, t0, ink["alpha"]) for s, t0 in timeline.seeds()]
        self.seeds = [s for s in self.seeds if s is not None]
        self.keep = self._resist(ink.get("resist", []), ink.get("feather", 0.0))
        self.set_tone(cfg, ink["tone"])
        self._cache_t = None
        self._cache = None

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
        return Seed(t0, s["speed"], radius, bool(s.get("lift", False)), slice(r0, r1), slice(c0, c1), base.astype(np.float32))

    def _resist(self, shapes, feather):
        mask = np.zeros((self.ih, self.iw), np.float32)
        for e in shapes:
            centre = (int(round(e["x"] * self.iw)), int(round(e["y"] * self.ih)))
            axes = (max(1, int(round(e["rx"] * self.iw))), max(1, int(round(e["ry"] * self.ih))))
            cv2.ellipse(mask, centre, axes, e.get("angle", 0), 0, 360, 1.0, -1, cv2.LINE_AA)
        if feather > 0 and shapes:
            sigma = feather * self.iw
            mask = cv2.GaussianBlur(mask, (0, 0), sigma)
        return 1.0 - np.clip(mask, 0.0, 1.0)

    def set_tone(self, cfg, name):
        ink = cfg["ink"]
        tone = {k: hex_bgr(v) for k, v in ink["tones"][name].items()}
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

    def to_tapestry(self):
        sx, sy = self.tw / self.iw, self.th / self.ih
        return np.array([[sx, 0, 0.5 * sx - 0.5], [0, sy, 0.5 * sy - 0.5], [0, 0, 1]], np.float64)

    def darkness(self, t):
        dark = np.zeros((self.ih, self.iw), np.float32)
        lift = None
        inv = 1.0 / self.edge
        for s in self.seeds:
            if t < s.t0:
                continue
            front = min((t - s.t0) * s.speed, s.radius)
            patch = s.base + np.float32(front)
            patch *= inv
            np.clip(patch, 0.0, 1.0, out=patch)
            if s.lift:
                if lift is None:
                    lift = np.zeros_like(dark)
                np.maximum(lift[s.rows, s.cols], patch, out=lift[s.rows, s.cols])
            else:
                np.maximum(dark[s.rows, s.cols], patch, out=dark[s.rows, s.cols])
        if lift is not None:
            dark *= 1.0 - lift
        dark *= self.keep
        return dark

    def overlay(self, t):
        if self._cache_t == t:
            return self._cache
        idx = cv2.convertScaleAbs(self.darkness(t), alpha=255.0)
        self._cache = cv2.LUT(cv2.merge([idx, idx, idx]), self.lut)
        self._cache_t = t
        return self._cache
