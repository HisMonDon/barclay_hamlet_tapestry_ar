import math

import numpy as np

DEFAULT = {"colour": "#c99b45", "hi": "#f2d488", "lo": "#7a5a22", "speed": 0.15, "pitch": 0.014, "fill": 0.74,
           "warp": 0.022, "duty": 0.78, "jitter": 0.12}


def hex_bgr(h):
    h = h.lstrip("#")
    return np.array([int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)], np.float32)


class Weave:
    def __init__(self, spec, t0, canvas, defaults=None, glint=None):
        w = {**DEFAULT, **(defaults or {}), **spec}
        x0, y0, x1, y1 = w["bounds"]
        self.bounds = w["bounds"]
        aspect = canvas.th / canvas.tw
        self.rows, self.cols = canvas.span(x0, y0, x1, y1)
        xs, ys = canvas.coords(self.rows, self.cols)
        px = 1.0 / canvas.w0
        pitch, fill, warp, duty = w["pitch"], w["fill"], w["warp"], w["duty"]
        rng = np.random.default_rng(w.get("seed", 12))
        nrows = int(math.ceil((y1 - y0) * aspect / pitch)) + 2
        jit = w["jitter"]
        roff = rng.normal(0.0, jit * 0.25, nrows).astype(np.float32)
        rthk = (1.0 + rng.normal(0.0, jit * 0.5, nrows)).astype(np.float32)
        rlum = (1.0 + rng.normal(0.0, jit * 0.6, nrows)).astype(np.float32)
        u = xs - x0
        v = (ys - y0) * aspect
        rowi = np.clip(np.floor(v / pitch).astype(np.int64), 0, nrows - 1)
        fv = v / pitch - rowi - roff[rowi]
        odd = (rowi % 2).astype(np.float32)
        wob = 0.08 * np.sin(u / pitch * 1.7 + rowi * 2.3)
        fu = np.mod(u / warp + 0.5 * odd + wob * 0.2, 1.0)
        fr = fill * rthk[rowi]
        a_v = np.clip((fr / 2 - np.abs(fv - 0.5)) * pitch / px + 0.5, 0.0, 1.0)
        a_u = np.clip((duty / 2 - np.abs(fu - 0.5)) * warp / px + 0.5, 0.0, 1.0)
        across = np.clip((fv - (1 - fr) / 2) / np.maximum(fr, 1e-3), 0.0, 1.0)
        twist = 0.5 + 0.5 * np.sin((u / pitch * 5.0 + across * 3.0 * (1 - 2 * odd)) * math.pi)
        round_ = np.sin(np.pi * across)
        dip = np.sin(np.pi * fu)
        shade = (0.5 + 0.5 * round_) * (0.82 + 0.18 * dip) * (0.9 + 0.1 * twist) * rlum[rowi]
        lo, mid, hi = hex_bgr(w["lo"]), hex_bgr(w["colour"]), hex_bgr(w["hi"])
        s = np.clip(shade, 0.0, 1.25)[..., None]
        col = np.where(s < 0.85, lo + (mid - lo) * (s / 0.85), mid + (hi - mid) * np.clip((s - 0.85) / 0.4, 0, 1))
        self.static = (a_v * a_u).astype(np.float32)
        self.colour = col.astype(np.float32)
        self.spec_mask = (round_ ** 6 * dip * (0.7 + 0.3 * twist)).astype(np.float32)
        self.width = x1 - x0
        self.along = np.broadcast_to(np.where(odd > 0, self.width - u, u), self.static.shape).astype(np.float32)
        self.row_time = pitch / w["speed"]
        self.start = (t0 + np.broadcast_to(rowi, self.static.shape) * self.row_time).astype(np.float32)
        self.t0 = t0
        self.px = px
        self.nrows = nrows
        self.u = np.broadcast_to(u, self.static.shape).astype(np.float32)
        self.v = np.broadcast_to(v, self.static.shape).astype(np.float32)
        self.height = (y1 - y0) * aspect
        self.glint = glint
        self.hi = hi

    def row_events(self):
        return [(self.t0 + k * self.row_time, 1 if k % 2 else -1) for k in range(self.nrows - 2)]

    def finish(self):
        return self.t0 + (self.nrows - 1) * self.row_time

    def glint_at(self, t):
        if not self.glint:
            return None
        g, g0 = self.glint
        dur, falter = g.get("dur", 1.9), g.get("falter", 0.58)
        p = (t - g0) / dur
        if p <= 0.0 or p >= 1.0:
            return None
        reach = falter + (1 - falter) * 0.0
        q = min(p / 0.55, 1.0)
        pos = reach * (1 - (1 - q) ** 2) if p < 0.55 else reach + 0.02 * math.sin((p - 0.55) * 9.0)
        level = 1.0 if p < 0.5 else max(0.0, 1.0 - (p - 0.5) / 0.5)
        flick = 1.0 if p < 0.45 else 0.75 + 0.25 * math.cos((p - 0.45) * 23.0) * math.cos((p - 0.45) * 7.0)
        return pos, level * flick, g.get("width", 0.07)

    def render(self, t):
        if t < self.t0:
            return None
        p = np.clip((t - self.start) / self.row_time, 0.0, 1.0)
        a = self.static * np.clip((p * self.width - self.along) / self.px + 0.5, 0.0, 1.0)
        col = self.colour
        gl = self.glint_at(t)
        if gl is not None:
            pos, level, width = gl
            span = self.width + self.height
            d = (self.u + 0.6 * self.v) / (self.width + 0.6 * self.height) - pos
            band = np.exp(-(d / (width * span / (self.width + 0.6 * self.height))) ** 2) * level
            boost = (band * (0.35 + 0.65 * self.spec_mask))[..., None]
            col = col + (np.array([235.0, 248.0, 255.0], np.float32) - col) * np.clip(boost, 0, 1)
        out = np.empty(a.shape + (4,), np.float32)
        out[..., :3] = col * a[..., None]
        out[..., 3] = 255.0 * a
        return np.clip(out + 0.5, 0, 255).astype(np.uint8)
