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
        wob = 0.05 * np.sin(u / pitch * 1.3 + rowi * 2.3)
        fu = np.mod(u / warp + rowi * w.get("twill", 0.5) + wob * 0.12, 1.0)
        dn = np.minimum(fu, 1.0 - fu)
        g = (1.0 - duty) / 2
        taper = np.clip((dn - g) / 0.07, 0.0, 1.0)
        taper = taper * taper * (3 - 2 * taper)
        fr = fill * rthk[rowi] * (0.62 + 0.38 * taper)
        a_v = np.clip((fr / 2 - np.abs(fv - 0.5)) * pitch / px + 0.5, 0.0, 1.0)
        a_u = np.clip((dn - g) * warp / px + 0.5, 0.0, 1.0)
        across = np.clip((fv - 0.5) / np.maximum(fr, 1e-3) + 0.5, 0.0, 1.0)
        twist = 0.5 + 0.5 * np.sin((u / pitch * 1.6 - across * 0.9 * (1 - 2 * odd)) * 2 * math.pi)
        round_ = np.sin(np.pi * across)
        dip = taper
        shade = (0.42 + 0.58 * round_) * (0.7 + 0.3 * taper) * (0.78 + 0.22 * twist) * rlum[rowi]
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
        self.finished = None
        self.a_final = None
        self.final = None
        self.cover = bool(w.get("cover", False))
        self.through = (w.get("sheen_through", 0.0), w.get("body_through", 0.0))
        self.gap = w.get("gap_shade", 0.0)
        self.gap_col = hex_bgr(w.get("gap_colour", "#0E0B10"))
        self.shimmer = w.get("shimmer", 0.0)
        self.sh_speed = w.get("shimmer_speed", 0.07)
        self.sh_col = hex_bgr(w.get("shimmer_colour", "#fff0c8"))
        self.diag = ((self.u + 0.45 * self.v) / (self.width + 0.45 * self.height)).astype(np.float32)

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

    def _woven(self, t, ys):
        p = np.clip((t - self.start[ys]) / self.row_time, 0.0, 1.0)
        return np.clip((p * self.width - self.along[ys]) / self.px + 0.5, 0.0, 1.0) * (t >= self.start[ys])

    def _paint(self, woven, ys, col):
        a = self.static[ys] * woven
        out = np.empty(a.shape + (4,), np.float32)
        out[..., :3] = col * a[..., None]
        out[..., 3] = 255.0 * a
        if self.gap:
            g = self.gap * (1.0 - self.static[ys]) * woven
            out[..., :3] += self.gap_col * g[..., None]
            out[..., 3] += 255.0 * g
        return np.clip(out + 0.5, 0, 255).astype(np.uint8)

    def _rows(self, t):
        """A plain weave mid-way: finished rows come from the final image and only the row on the shuttle is woven."""
        if self.final is None:
            self.final = self._paint(self._woven(self.finish(), slice(None)), slice(None), self.colour)
        s = self.start[:, 0]
        p = np.clip((t - s) / self.row_time, 0.0, 1.0)
        out = np.zeros_like(self.final)
        done = (p >= 1.0) & (t >= s)
        out[done] = self.final[done]
        act = np.nonzero((t >= s) & (p < 1.0))[0]
        if len(act):
            ys = slice(int(act[0]), int(act[-1]) + 1)
            out[ys] = self._paint(self._woven(t, ys), ys, self.colour[ys])
        return out

    def render(self, t):
        if t < self.t0:
            return None
        gl = self.glint_at(t)
        if not self.shimmer and self.glint is None and t < self.finish():
            return self._rows(t)
        if t >= self.finish() and gl is None and self.finished is not None and not self.shimmer:
            return self.finished
        if t >= self.finish():
            if self.a_final is None:
                p = np.clip((t - self.start) / self.row_time, 0.0, 1.0)
                self.a_final = np.clip((p * self.width - self.along) / self.px + 0.5, 0.0, 1.0) * (t >= self.start)
            woven = self.a_final
        else:
            p = np.clip((t - self.start) / self.row_time, 0.0, 1.0)
            woven = np.clip((p * self.width - self.along) / self.px + 0.5, 0.0, 1.0) * (t >= self.start)
        col = self.colour
        if self.shimmer:
            # Couched metal thread: a slow candlelit sheen slides diagonally across the floats.
            band = (0.5 + 0.5 * np.cos(2 * math.pi * (self.diag * 1.6 - self.sh_speed * t))) ** 8
            k = self.shimmer * (0.8 + 0.2 * math.sin(2.3 * t))
            col = col + (self.sh_col - col) * (band * self.spec_mask * k)[..., None]
        if gl is not None:
            pos, level, width = gl
            span = self.width + self.height
            d = (self.u + 0.6 * self.v) / (self.width + 0.6 * self.height) - pos
            band = np.exp(-(d / (width * span / (self.width + 0.6 * self.height))) ** 2) * level
            boost = (band * (0.35 + 0.65 * self.spec_mask))[..., None]
            col = col + (np.array([235.0, 248.0, 255.0], np.float32) - col) * np.clip(boost, 0, 1)
        result = self._paint(woven, slice(None), col)
        if t >= self.finish() and gl is None:
            self.finished = result
        return result
