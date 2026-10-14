import math

import cv2
import numpy as np

from fields import raster

KINDS = {
    "sprout": {"speed": 1.0, "branch": 0.6, "depth": 2, "nb": (3, 2), "curl": 0.07, "trop": 0.16, "leaf": 0.6, "knot": 0.2, "tendril": 0.3, "spread": 28},
    "meadow": {"speed": 1.1, "branch": 0.55, "depth": 2, "nb": (3, 1), "curl": 0.06, "trop": 0.2, "leaf": 0.65, "knot": 0.2, "tendril": 0.2, "spread": 20},
    "creeper": {"speed": 1.5, "branch": 0.7, "depth": 1, "nb": (6, 0), "curl": 0.035, "trop": 0.3, "leaf": 0.7, "knot": 0.15, "tendril": 0.35, "spread": 6},
    "gaps": {"speed": 0.9, "branch": 0.5, "depth": 1, "nb": (2, 0), "curl": 0.08, "trop": 0.18, "leaf": 0.55, "knot": 0.3, "tendril": 0.25, "spread": 30},
    "spill": {"speed": 4.2, "branch": 0.55, "depth": 2, "nb": (4, 1), "curl": 0.055, "trop": 0.1, "leaf": 0.6, "knot": 0.2, "tendril": 0.5, "spread": 34},
}


def hex_bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


class Builder:
    def __init__(self):
        self.s = []
        self.k = []

    def stroke(self, p0, p1, ta, tb, w, col, kind=0):
        self.s.append((p0[0], p0[1], p1[0], p1[1], ta, tb, w, col, kind))

    def knot(self, p, ta, r, col):
        self.k.append((p[0], p[1], ta, r, col))


class WeedEngine:
    def __init__(self, cfg, timeline, T, canvas, over_canvas):
        self.cfg = cfg
        self.p = cfg.get("weeds", {})
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        self.T = T
        self.ih, self.iw = T.shape
        self.canvas = over_canvas
        px_mm = self.tw / cfg["board"]["drawing_mm"][0]
        self.stitch = self.p.get("stitch_mm", 3.6) * px_mm
        self.width = self.p.get("width_mm", 0.95) * px_mm
        self.soak = self.p.get("soak_s", 1.4)
        self.speed = self.p.get("speed", 34.0)
        cols = self.p.get("colours", {"olive": "#5a5a2a"})
        his = self.p.get("highlight", {})
        self.names = list(cols)
        self.body = [hex_bgr(cols[n]) for n in self.names]
        self.hi = [hex_bgr(his.get(n, cols[n])) for n in self.names]
        self.shadow = hex_bgr(self.p.get("shadow", "#090b06"))
        mix = self.p.get("mix", {})
        w = np.array([mix.get(n, 1.0) for n in self.names], np.float64)
        self.mix = w / w.sum()
        self.knot_cols = [self.names.index(n) for n in self.p.get("knot_colours", ["rust"]) if n in self.names] or [0]
        L = np.array(self.p.get("light", [-0.6, -0.8]), np.float64)
        self.light = L / np.linalg.norm(L)
        self.avoid = {}
        for name in self.p.get("avoid_regions", []):
            if name in cfg.get("regions", {}):
                self.avoid[name] = raster(cfg["regions"][name]["polygon"], self.iw, self.ih) > 0.5
        self.margin = cfg.get("overlay", {}).get("margin", 0.0)
        b = Builder()
        self.roots = []
        for spec, t0, _ in timeline.items("weeds"):
            self._colony(b, spec, t0, timeline)
        for spec, t0, _ in timeline.items("props"):
            if spec.get("kind") == "thread":
                self._thread(b, spec, t0)
        S = np.array(b.s, np.float64).reshape(-1, 9)
        order = np.argsort(S[:, 4], kind="stable")
        self.S = S[order]
        K = np.array(b.k, np.float64).reshape(-1, 5)
        self.K = K[np.argsort(K[:, 2], kind="stable")]
        plant = self.S[:, 8] != 2
        self.first_plant = float(min(self.S[plant, 4].min() if plant.any() else 1e9, self.K[:, 2].min() if len(self.K) else 1e9))
        self.first = float(min(self.S[:, 4].min() if len(self.S) else 1e9, self.K[:, 2].min() if len(self.K) else 1e9))
        self._xy()
        self.drawn = -1.0
        self.bbox = None

    def _xy(self):
        c = self.canvas
        s = c.w0 / self.tw
        self.cs = s
        self.P = np.empty((len(self.S), 4), np.float64)
        self.P[:, 0] = self.S[:, 0] * s + c.pad
        self.P[:, 1] = self.S[:, 1] * s + c.pad
        self.P[:, 2] = self.S[:, 2] * s + c.pad
        self.P[:, 3] = self.S[:, 3] * s + c.pad
        self.KP = np.empty((len(self.K), 2), np.float64)
        self.KP[:, 0] = self.K[:, 0] * s + c.pad
        self.KP[:, 1] = self.K[:, 1] * s + c.pad

    def arrival(self, x, y):
        u, v = x / self.tw, y / self.th
        if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
            return float(self.T[int(v * self.ih), int(u * self.iw)])
        return math.inf

    def inside(self, x, y):
        m = self.margin
        return -m * self.tw < x < (1 + m) * self.tw and -m * self.tw < y < self.th + m * self.tw

    def allowed(self, x, y, t, spill):
        u, v = x / self.tw, y / self.th
        if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
            i, j = int(v * self.ih), int(u * self.iw)
            if not spill["in_banner"] and any(m[i, j] for m in self.avoid.values()):
                return False
            return self.T[i, j] <= t
        if spill["t_spill"] is None:
            return False
        return self.inside(x, y) and t >= spill["t_spill"]

    def _roots(self, spec, t0, rng):
        n = spec.get("count", 5)
        kind = spec.get("kind", "sprout")
        out = []
        tries = 0
        if kind == "spill":
            while len(out) < n and tries < n * 60:
                tries += 1
                side = rng.integers(0, 4)
                u = rng.uniform(0.04, 0.96)
                d = rng.uniform(0.004, 0.03)
                if side == 0:
                    x, y, a = u * self.tw, d * self.tw, -math.pi / 2
                elif side == 1:
                    x, y, a = (1 - d) * self.tw, u * self.th, 0.0
                elif side == 2:
                    x, y, a = u * self.tw, self.th - d * self.tw, math.pi / 2
                else:
                    x, y, a = d * self.tw, u * self.th, math.pi
                T = self.arrival(x, y)
                if math.isfinite(T):
                    out.append((x, y, a, max(t0 + rng.uniform(0.0, 1.6), T + self.soak)))
            return out
        poly = None
        if "region" in spec:
            poly = np.array(self.cfg["regions"][spec["region"]]["polygon"], np.float64)
        base = math.radians(spec.get("dir", -90))
        while len(out) < n and tries < n * 80:
            tries += 1
            if poly is not None:
                u = rng.uniform(poly[:, 0].min(), poly[:, 0].max())
                v = rng.uniform(poly[:, 1].min(), poly[:, 1].max())
                if cv2.pointPolygonTest(poly.astype(np.float32), (float(u), float(v)), False) < 0:
                    continue
            else:
                r = spec.get("spread", 0.05) * math.sqrt(rng.random())
                th = rng.uniform(0, 2 * math.pi)
                u = spec["x"] + r * math.cos(th)
                v = spec["y"] + r * math.sin(th) / (self.th / self.tw)
            x, y = u * self.tw, v * self.th
            T = self.arrival(x, y)
            if not math.isfinite(T):
                continue
            i, j = int(min(self.ih - 1, max(0, v * self.ih))), int(min(self.iw - 1, max(0, u * self.iw)))
            if kind != "gaps" and any(m[i, j] for m in self.avoid.values()):
                continue
            out.append((x, y, base, max(t0 + rng.uniform(0.0, spec.get("stagger", 1.2)), T + self.soak)))
        return out

    def _colony(self, b, spec, t0, timeline):
        rng = np.random.default_rng(spec.get("seed", 0) * 7919 + 17)
        kind = spec.get("kind", "sprout")
        k = KINDS.get(kind, KINDS["sprout"])
        spill = {"t_spill": t0 if kind == "spill" else None, "in_banner": kind == "gaps"}
        L = spec.get("length", 0.1) * self.tw
        for x, y, a, tr in self._roots(spec, t0, rng):
            self.roots.append((x, y, tr, kind))
            a0 = a + math.radians(rng.normal(0.0, k["spread"]))
            ci = int(rng.choice(len(self.names), p=self.mix))
            if self.names[ci] == "rust":
                ci = 0
            target = a if kind in ("creeper", "spill") else math.radians(spec.get("dir", -90))
            self._stem(b, rng, (x, y), a0, target, L * rng.uniform(0.6, 1.15), 0, tr,
                       self.width * rng.uniform(0.9, 1.15), ci, k, spill, self.speed * k["speed"] * rng.uniform(0.85, 1.15))

    def _stem(self, b, rng, p, a, target, L, depth, ts, w, ci, k, spill, v):
        step = self.stitch * 0.5
        pts = [p]
        times = [ts]
        x, y = p
        kap = 0.0
        s = 0.0
        branches = []
        leaves = []
        next_branch = step * rng.uniform(3.0, 5.0)
        next_leaf = step * rng.uniform(1.5, 2.5)
        side = 1 if rng.random() < 0.5 else -1
        while s < L:
            kap = 0.82 * kap + rng.normal(0.0, k["curl"])
            a += kap + k["trop"] * math.sin(target - a) * 0.5
            t_here = ts + (s + step) / v
            ok = False
            for d in (0.0, 0.35, -0.35, 0.7, -0.7):
                nx, ny = x + step * math.cos(a + d), y + step * math.sin(a + d)
                if self.allowed(nx, ny, t_here, spill):
                    a += d
                    ok = True
                    break
            if not ok:
                break
            x, y = nx, ny
            s += step
            pts.append((x, y))
            times.append(t_here)
            if depth < k["depth"] and len(branches) < k["nb"][min(depth, 1)] and s >= next_branch and rng.random() < k["branch"]:
                side = -side
                branches.append((len(pts) - 1, a + side * math.radians(rng.uniform(28, 58)), (L - s) * rng.uniform(0.45, 0.75)))
                next_branch = s + step * rng.uniform(3.0, 6.0)
            if s >= next_leaf and rng.random() < k["leaf"]:
                side = -side
                leaves.append((len(pts) - 1, a + side * math.radians(rng.uniform(35, 65))))
                next_leaf = s + step * rng.uniform(1.4, 2.6)
        if len(pts) < 3:
            return
        tip_a = a
        if rng.random() < k["tendril"] * (0.6 if depth == 0 else 1.0):
            curl = rng.choice([-1, 1])
            kk = 0.12
            n = int(rng.integers(10, 18))
            st = step * 0.6
            for x_ in range(n):
                kk *= 1.13
                a += curl * kk
                nx, ny = x + st * math.cos(a), y + st * math.sin(a)
                t_here = times[-1] + st / v
                if not self.allowed(nx, ny, t_here, spill):
                    break
                x, y = nx, ny
                pts.append((x, y))
                times.append(t_here)
        self._stitches(b, rng, pts, times, w * (0.82 ** depth), ci)
        for idx, ang in leaves:
            if idx < len(pts):
                ln = self.stitch * 1.9
                tip = (pts[idx][0] + ln * math.cos(ang), pts[idx][1] + ln * math.sin(ang))
                if self.allowed(tip[0], tip[1], times[idx] + 0.5, spill):
                    self._leaf(b, rng, pts[idx], ang, times[idx] + 0.08, w * (0.82 ** depth) * 0.72, ci, rng.random() < 0.06)
        for idx, ang, bl in branches:
            if idx < len(pts):
                self._stem(b, rng, pts[idx], ang, target, bl, depth + 1, times[idx], w, ci if rng.random() < 0.7 else
                           int(rng.choice(len(self.names), p=self.mix)), k, spill, v * 0.9)
        if rng.random() < k["knot"] * (1.0 if depth else 0.7):
            self._knots(b, rng, pts[-1], tip_a, times[-1], w, spill)

    def _thread(self, b, spec, t0):
        col = hex_bgr(spec.get("colour", "#c3cad1"))
        self.names.append("thread")
        self.body.append(col)
        self.hi.append(tuple(min(255, int(c * 1.15) + 10) for c in col))
        ci = len(self.names) - 1
        pts = np.array([[x * self.tw, y * self.th] for x, y in spec["path"]], np.float64)
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        s = np.concatenate([[0.0], np.cumsum(seg)])
        L = float(s[-1])
        dur = spec.get("dur", 1.8)
        dash, gap = self.stitch * spec.get("dash", 0.9), self.stitch * spec.get("gap", 0.3)
        w = self.width * spec.get("width", 0.65)
        u = 0.0
        while u < L:
            e = min(L, u + dash)
            n = max(2, int((e - u) / 2) + 1)
            us = np.linspace(u, e, n)
            q = np.stack([np.interp(us, s, pts[:, 0]), np.interp(us, s, pts[:, 1])], 1)
            for k in range(n - 1):
                b.stroke(q[k], q[k + 1], t0 + dur * us[k] / L, t0 + dur * us[k + 1] / L + 1e-3, w, ci, 2)
            u = e + gap

    def _stitches(self, b, rng, pts, times, w, ci):
        n = len(pts)
        slant = math.radians(16.0)
        for k in range(0, n - 1):
            j = min(n - 1, k + 2)
            (x0, y0), (x1, y1) = pts[k], pts[j]
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            dx, dy = (x1 - x0) / 2, (y1 - y0) / 2
            c, s = math.cos(slant), math.sin(slant)
            rx, ry = dx * c - dy * s, dx * s + dy * c
            ww = w * rng.uniform(0.85, 1.15)
            b.stroke((mx - rx, my - ry), (mx + rx, my + ry), times[k], times[j] + 1e-3, ww, ci, 0)

    def _leaf(self, b, rng, p, a, t0, w, ci, rust):
        L = self.stitch * rng.uniform(1.1, 1.8)
        W = L * rng.uniform(0.28, 0.38)
        if rust:
            ci = self.names.index("rust") if "rust" in self.names else ci
        elif rng.random() < 0.5:
            ci = int(rng.choice(len(self.names), p=self.mix))
            if self.names[ci] == "rust":
                ci = 0
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -uy, ux
        m = 14
        prev = p
        dur = rng.uniform(0.3, 0.5)
        for k in range(1, m + 1):
            ph = 2 * math.pi * k / m
            al = L * (1 - math.cos(ph)) / 2
            ac = W * math.sin(ph) * (0.35 + 0.65 * (1 - math.cos(ph)) / 2)
            q = (p[0] + ux * al + nx * ac, p[1] + uy * al + ny * ac)
            b.stroke(prev, q, t0 + dur * (k - 1) / m, t0 + dur * k / m, w, ci, 1)
            prev = q
        tip = (p[0] + ux * L, p[1] + uy * L)
        b.stroke(tip, (tip[0] + ux * w * 1.6, tip[1] + uy * w * 1.6), t0 + dur, t0 + dur + 0.05, w * 0.9, ci, 1)
        if rng.random() < 0.35:
            b.stroke(p, (p[0] + ux * L * 0.6, p[1] + uy * L * 0.6), t0 + dur, t0 + dur + 0.15, w * 0.6, ci, 1)

    def _knots(self, b, rng, p, a, t0, w, spill):
        n = int(rng.integers(1, 4))
        r = w * 1.05
        for k in range(n):
            ang = a + rng.normal(0.0, 0.9)
            d = r * 1.6 * math.sqrt(k)
            q = (p[0] + d * math.cos(ang), p[1] + d * math.sin(ang))
            if not self.allowed(q[0], q[1], t0 + 0.3, spill):
                continue
            b.knot(q, t0 + 0.06 * k, r * rng.uniform(0.85, 1.15), int(rng.choice(self.knot_cols)))

    def clear(self, over):
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            over[y0:y1, x0:x1] = 0
        self.bbox = None
        self.drawn = -1.0

    def _grow(self, x0, y0, x1, y1):
        c = self.canvas
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(c.w, int(math.ceil(x1))), min(c.h, int(math.ceil(y1)))
        if self.bbox is None:
            self.bbox = (x0, y0, x1, y1)
        else:
            a = self.bbox
            self.bbox = (min(a[0], x0), min(a[1], y0), max(a[2], x1), max(a[3], y1))

    def draw(self, over, t):
        if t < self.drawn:
            self.clear(over)
        if t < self.first:
            self.drawn = t
            return self.bbox is not None
        prev = self.drawn
        S, P = self.S, self.P
        hi = np.searchsorted(S[:, 4], t, side="right")
        sel = np.nonzero(S[:hi, 5] > prev)[0]
        if len(sel):
            self._draw_strokes(over, sel, t)
        K = self.K
        khi = np.searchsorted(K[:, 2], t, side="right")
        ksel = np.nonzero(K[:khi, 2] + 0.25 > prev)[0]
        if len(ksel):
            self._draw_knots(over, ksel, t)
        self.drawn = t
        return self.bbox is not None

    def _draw_strokes(self, over, sel, t):
        S, P = self.S[sel], self.P[sel]
        f = np.clip((t - S[:, 4]) / np.maximum(1e-6, S[:, 5] - S[:, 4]), 0.0, 1.0)
        x0, y0 = P[:, 0], P[:, 1]
        x1 = x0 + (P[:, 2] - x0) * f
        y1 = y0 + (P[:, 3] - y0) * f
        keep = np.hypot(x1 - x0, y1 - y0) > 0.3
        if not keep.any():
            return
        x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
        w = S[keep, 6] * self.cs
        col = S[keep, 7].astype(int)
        kind = S[keep, 8].astype(int)
        mg = 1.3 * float(w.max()) + 5.0
        self._grow(min(x0.min(), x1.min()) - mg, min(y0.min(), y1.min()) - mg, max(x0.max(), x1.max()) + mg, max(y0.max(), y1.max()) + mg)
        th = np.maximum(1, np.round(w)).astype(int)
        dx, dy = x1 - x0, y1 - y0
        L = np.hypot(dx, dy) + 1e-9
        nxv, nyv = -dy / L, dx / L
        sgn = np.sign(nxv * self.light[0] + nyv * self.light[1])
        sgn[sgn == 0] = 1
        sh = np.stack([x0 + 0.45 * w, y0 + 0.6 * w, x1 + 0.45 * w, y1 + 0.6 * w], 1)
        body = np.stack([x0, y0, x1, y1], 1)
        o = 0.28 * w * sgn
        a0, a1 = np.where(kind == 0, 0.3, 0.15), np.where(kind == 0, 0.62, 0.85)
        hl = np.stack([x0 + dx * a0 + nxv * o, y0 + dy * a0 + nyv * o, x0 + dx * a1 + nxv * o, y0 + dy * a1 + nyv * o], 1)
        cr = 0.5 * w
        c0 = np.stack([x0 + dx * 0.08 - nxv * cr, y0 + dy * 0.08 - nyv * cr, x0 + dx * 0.08 + nxv * cr, y0 + dy * 0.08 + nyv * cr], 1)
        stem = (kind == 0) & (L > 2.5 * w)
        self._lines(over, sh, th + 1, None, col)
        self._lines(over, body, th, self.body, col)
        if stem.any():
            self._lines(over, c0[stem], np.ones(int(stem.sum()), int), None, col[stem])
        self._lines(over, hl, np.maximum(1, np.round(th * 0.25)).astype(int), self.hi, col)

    def _lines(self, over, segs, th, palette, col):
        segs = np.round(segs * 8).astype(np.int32)
        keys = th * 64 + (0 if palette is None else col + 1)
        for key in np.unique(keys):
            m = keys == key
            thick = int(key // 64)
            colour = self.shadow if palette is None else palette[int(key % 64) - 1]
            lines = [s.reshape(2, 1, 2) for s in segs[m]]
            cv2.polylines(over, lines, False, colour + (255,), thick, cv2.LINE_AA, 3)

    def _draw_knots(self, over, ksel, t):
        K, P = self.K[ksel], self.KP[ksel]
        g = np.clip((t - K[:, 2]) / 0.25, 0.0, 1.0)
        for (x, y), (kx, ky, ta, r, c), gg in zip(P, K, g):
            rr = max(1.0, r * self.cs * (0.4 + 0.6 * gg))
            self._grow(x - rr * 2 - 3, y - rr * 2 - 3, x + rr * 2 + 3, y + rr * 2 + 3)
            c = int(c)
            sh = (int(round((x + 0.4 * rr) * 8)), int(round((y + 0.5 * rr) * 8)))
            cv2.circle(over, sh, int(round(rr * 1.1 * 8)), self.shadow + (255,), -1, cv2.LINE_AA, 3)
            cv2.circle(over, (int(round(x * 8)), int(round(y * 8))), int(round(rr * 8)), self.body[c] + (255,), -1, cv2.LINE_AA, 3)
            hx, hy = x + self.light[0] * rr * 0.4, y + self.light[1] * rr * 0.4
            cv2.circle(over, (int(round(hx * 8)), int(round(hy * 8))), int(round(rr * 0.4 * 8)), self.hi[c] + (255,), -1, cv2.LINE_AA, 3)
