import math

import cv2
import numpy as np

from fields import raster

KINDS = {
    "sprout": {"speed": 1.0, "branch": 0.6, "depth": 2, "nb": (3, 2), "curl": 0.07, "trop": 0.16, "leaf": 0.6, "knot": 0.12, "bud": 0.2, "flower": 0.05, "tendril": 0.3, "spread": 28, "taper": 0.45},
    "meadow": {"speed": 1.1, "branch": 0.55, "depth": 2, "nb": (3, 1), "curl": 0.06, "trop": 0.2, "leaf": 0.65, "knot": 0.12, "bud": 0.16, "flower": 0.2, "tendril": 0.2, "spread": 20, "taper": 0.45},
    "creeper": {"speed": 1.5, "branch": 0.7, "depth": 1, "nb": (6, 0), "curl": 0.035, "trop": 0.3, "leaf": 0.7, "knot": 0.1, "bud": 0.12, "flower": 0.0, "tendril": 0.35, "spread": 6, "taper": 0.35},
    "gaps": {"speed": 0.9, "branch": 0.5, "depth": 1, "nb": (2, 0), "curl": 0.08, "trop": 0.18, "leaf": 0.55, "knot": 0.2, "bud": 0.22, "flower": 0.06, "tendril": 0.25, "spread": 30, "taper": 0.45},
    "spill": {"speed": 4.2, "branch": 0.55, "depth": 2, "nb": (4, 1), "curl": 0.055, "trop": 0.1, "leaf": 0.6, "knot": 0.12, "bud": 0.16, "flower": 0.06, "tendril": 0.5, "spread": 34, "taper": 0.4},
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
        fx = cfg.get("fx", {})
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
        self.bud_ci = self.names.index("bud") if "bud" in self.names else self.knot_cols[0]
        self.petal_ci = self.names.index("petal") if "petal" in self.names else self.knot_cols[0]
        L = np.array(self.p.get("light", [-0.6, -0.8]), np.float64)
        self.light = L / np.linalg.norm(L)
        self.fine = bool(fx.get("stitch_detail", True))
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
        self._palettes()
        self._xy()
        sh = self.p.get("shadow_soft", {})
        self.soft_off = tuple(sh.get("offset", [0.75, 1.0]))
        self.shadow_strength = sh.get("strength", 0.55)
        self.shade = None
        self.soft = None
        if sh.get("on", True) and fx.get("weed_shadow", True):
            c = self.canvas
            self.sk = 4
            self.ssig = max(0.6, sh.get("blur_mm", 1.4) * px_mm * self.cs / self.sk)
            # Shadows are drawn straight into a quarter-resolution buffer and only the freshly drawn region is re-blurred.
            self.shade = np.zeros((c.h // self.sk + 1, c.w // self.sk + 1), np.uint8)
            self.soft = np.full(self.shade.shape + (3,), 255, np.uint8)
            k = self.sk
            self.soft_matrix = np.array([[1.0 / k, 0, -(k - 1) / (2.0 * k)], [0, 1.0 / k, -(k - 1) / (2.0 * k)], [0, 0, 1]], np.float64)
        self.sdirty = None
        self._soft_t = None
        self.drawn = -1.0
        self.bbox = None
        self._init_redraw()

    def _init_redraw(self):
        """Cache completed stitches; moving tips redraw clean tiles instead of accumulating paint."""
        c = self.canvas
        # Align overlay tiles with shadow pixels so both layers have identical ownership.
        self.tile = max(32, int(96 * self.cs) // 4 * 4)
        self.tile_cols = math.ceil(c.w / self.tile)
        self.tile_rows = math.ceil(c.h / self.tile)
        self.complete = np.zeros((c.h, c.w, 4), np.uint8)
        self.complete_shade = np.zeros_like(self.shade) if self.shade is not None else None
        # A fixed completion order gives seeks and different playback frame rates
        # the same overlapping stitch order, without redrawing the established canopy.
        self.completion_hz = 30
        self.stroke_bins = np.ceil(self.S[:, 5] * self.completion_hz).astype(np.int64)
        self.knot_bins = np.ceil((self.K[:, 2] + 0.25) * self.completion_hz).astype(np.int64)
        self.finish_bins = np.union1d(self.stroke_bins, self.knot_bins)
        so, ko = np.argsort(self.stroke_bins, kind="stable"), np.argsort(self.knot_bins, kind="stable")
        self.bin_strokes = np.split(so, np.searchsorted(self.stroke_bins[so], self.finish_bins[1:]))
        self.bin_knots = np.split(ko, np.searchsorted(self.knot_bins[ko], self.finish_bins[1:]))
        self.finished_bins = 0
        self.active_tiles = np.empty(0, np.int64)
        off = max(abs(v) for v in self.soft_off)
        w = self.S[:, 6] * self.cs
        # Include line caps, fixed-point AA and quarter-resolution shadow AA.
        m = np.maximum((1.3 + off) * w + 6.0, off * w + 12.0)
        self.stroke_tiles = self._bounds_tiles(np.stack([
            self.P[:, [0, 2]].min(axis=1) - m, self.P[:, [1, 3]].min(axis=1) - m,
            self.P[:, [0, 2]].max(axis=1) + m, self.P[:, [1, 3]].max(axis=1) + m,
        ], axis=1))
        r = np.maximum(1.0, self.K[:, 3] * self.cs)
        m = max(3.0, off + 1.25) * r + 12.0
        self.knot_tiles = self._bounds_tiles(np.stack([
            self.KP[:, 0] - m, self.KP[:, 1] - m,
            self.KP[:, 0] + m, self.KP[:, 1] + m,
        ], axis=1))

    def _bounds_tiles(self, bounds):
        c, tile = self.canvas, self.tile
        lo = np.floor(np.maximum(bounds[:, :2], 0) / tile).astype(np.int32)
        hi = np.floor(np.minimum(bounds[:, 2:], [c.w - 1, c.h - 1]) / tile).astype(np.int32)
        # Empty off-canvas shapes must not become negative array indices.
        size = np.maximum(0, hi - lo + 1)
        return np.stack([lo[:, 0], lo[:, 1], size[:, 0], size[:, 1]], axis=1)

    def _tile_members(self, shapes):
        count = shapes[:, 2] * shapes[:, 3]
        rows = np.repeat(np.arange(len(shapes)), count)
        starts = np.cumsum(count) - count
        offset = np.arange(len(rows)) - np.repeat(starts, count)
        width = shapes[rows, 2]
        tiles = ((shapes[rows, 1] + offset // width) * self.tile_cols
                 + shapes[rows, 0] + offset % width)
        return rows, tiles

    def _palettes(self):
        body = np.array(self.body, np.float64)
        hi = np.array(self.hi, np.float64)
        self.nc = len(body)
        tup = lambda a: [tuple(int(round(v)) for v in c) for c in np.clip(a, 0, 255)]
        self.lo = tup(body * 0.55 + 1)
        self.hipal = tup(hi) + tup(body + (hi - body) * 0.4)
        self.sppal = tup(hi + (hi - body) * 1.1 + 22)

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
        # One root per jittered cell keeps large colonies continuous instead of
        # leaving the empty patches produced by independent random sampling.
        cells = None
        if spec.get("distribution") == "even":
            bounds = (poly.min(axis=0), poly.max(axis=0)) if poly is not None else (np.zeros(2), np.ones(2))
            lo, hi = bounds
            aspect = (hi[1] - lo[1]) * self.th / max(1e-6, (hi[0] - lo[0]) * self.tw)
            nx = max(1, int(round(math.sqrt(n / max(1e-6, aspect)))))
            ny = max(1, int(math.ceil(n / nx)))
            cells = [(col, row) for row in range(ny) for col in range(nx)]
            rng.shuffle(cells)
        base = math.radians(spec.get("dir", -90))
        while len(out) < n and tries < n * 80:
            tries += 1
            if cells is not None:
                if not cells:
                    break
                col, row = cells.pop()
                u, v = lo + (hi - lo) * ((np.array([col, row]) + rng.uniform(0.15, 0.85, 2)) / [nx, ny])
            elif poly is not None:
                u = rng.uniform(poly[:, 0].min(), poly[:, 0].max())
                v = rng.uniform(poly[:, 1].min(), poly[:, 1].max())
            else:
                r = spec.get("spread", 0.05) * math.sqrt(rng.random())
                th = rng.uniform(0, 2 * math.pi)
                u = spec["x"] + r * math.cos(th)
                v = spec["y"] + r * math.sin(th) / (self.th / self.tw)
            if poly is not None and cv2.pointPolygonTest(poly.astype(np.float32), (float(u), float(v)), False) < 0:
                continue
            x, y = u * self.tw, v * self.th
            T = self.arrival(x, y)
            if not math.isfinite(T):
                continue
            i, j = int(min(self.ih - 1, max(0, v * self.ih))), int(min(self.iw - 1, max(0, u * self.iw)))
            if kind != "gaps" and any(m[i, j] for m in self.avoid.values()):
                continue
            out.append((x, y, base, max(t0 + rng.uniform(0.0, spec.get("stagger", 1.2)), T + self.soak)))
        return out

    def _pick(self, rng):
        ci = int(rng.choice(len(self.mix), p=self.mix))
        return 0 if self.names[ci] == "rust" else ci

    def _colony(self, b, spec, t0, timeline):
        rng = np.random.default_rng(spec.get("seed", 0) * 7919 + 17)
        kind = spec.get("kind", "sprout")
        k = KINDS.get(kind, KINDS["sprout"])
        spill = {"t_spill": t0 if kind == "spill" else None, "in_banner": kind == "gaps"}
        L = spec.get("length", 0.1) * self.tw
        first_stroke, first_knot = len(b.s), len(b.k)
        for x, y, a, tr in self._roots(spec, t0, rng):
            self.roots.append((x, y, tr, kind))
            jitter = spec.get("direction_jitter", 0.0)
            if jitter:
                a += math.radians(rng.uniform(-jitter, jitter))
            a0 = a + math.radians(rng.normal(0.0, k["spread"]))
            ci = self._pick(rng)
            target = a
            self._stem(b, rng, (x, y), a0, target, L * rng.uniform(0.6, 1.15), 0, tr,
                       self.width * rng.uniform(0.9, 1.15), ci, k, spill, self.speed * k["speed"] * rng.uniform(0.85, 1.15))
        if kind != "spill":
            self._clip_colony(b, first_stroke, first_knot)

    def _clip_colony(self, b, first_stroke, first_knot):
        """Leaves, loops and slanted stitches must respect the cloth edge too."""
        clipped = []
        for stroke in b.s[first_stroke:]:
            x0, y0, x1, y1, ta, tb, width, colour, kind = stroke
            if 0 <= x0 <= self.tw and 0 <= x1 <= self.tw and 0 <= y0 <= self.th and 0 <= y1 <= self.th:
                clipped.append(stroke)
                continue
            dx, dy = x1 - x0, y1 - y0
            enter, leave = 0.0, 1.0
            for p, q in ((-dx, x0), (dx, self.tw - x0), (-dy, y0), (dy, self.th - y0)):
                if abs(p) < 1e-12:
                    if q < 0:
                        leave = -1.0
                        break
                elif p < 0:
                    enter = max(enter, q / p)
                else:
                    leave = min(leave, q / p)
            if enter < leave:
                clipped.append((x0 + enter * dx, y0 + enter * dy, x0 + leave * dx, y0 + leave * dy,
                                ta + enter * (tb - ta), ta + leave * (tb - ta), width, colour, kind))
        b.s[first_stroke:] = clipped
        b.k[first_knot:] = [k for k in b.k[first_knot:] if k[3] <= k[0] <= self.tw - k[3] and k[3] <= k[1] <= self.th - k[3]]

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
        wd = w * (0.82 ** depth)
        self._stitches(b, rng, pts, times, wd, ci, k.get("taper", 0.0))
        for idx, ang in leaves:
            if idx < len(pts):
                ln = self.stitch * 1.9
                tip = (pts[idx][0] + ln * math.cos(ang), pts[idx][1] + ln * math.sin(ang))
                if self.allowed(tip[0], tip[1], times[idx] + 0.5, spill):
                    self._leaf(b, rng, pts[idx], ang, times[idx] + 0.08, wd * 0.72, ci, rng.random() < 0.06)
        for idx, ang, bl in branches:
            if idx < len(pts):
                self._stem(b, rng, pts[idx], ang, target, bl, depth + 1, times[idx], w, ci if rng.random() < 0.7 else
                           self._pick(rng), k, spill, v * 0.9)
        scale = 1.0 if depth else 0.7
        r = rng.random()
        pf, pb, pk = k.get("flower", 0.0) * scale, k.get("bud", 0.0) * scale, k["knot"] * scale
        if r < pf:
            self._flower(b, rng, pts[-1], tip_a, times[-1], wd, spill)
        elif r < pf + pb:
            self._bud(b, rng, pts[-1], tip_a, times[-1], wd, ci, spill)
        elif r < pf + pb + pk:
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

    def _stitches(self, b, rng, pts, times, w, ci, taper=0.0):
        n = len(pts)
        slant = math.radians(16.0)
        c, s = math.cos(slant), math.sin(slant)
        for k in range(0, n - 1):
            j = min(n - 1, k + 2)
            (x0, y0), (x1, y1) = pts[k], pts[j]
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            dx, dy = (x1 - x0) / 2, (y1 - y0) / 2
            rx, ry = dx * c - dy * s, dx * s + dy * c
            ww = w * (1.0 - taper * k / max(1, n - 2)) * rng.uniform(0.85, 1.15)
            b.stroke((mx - rx, my - ry), (mx + rx, my + ry), times[k], times[j] + 1e-3, ww, ci, 0)

    def _leaf(self, b, rng, p, a, t0, w, ci, rust):
        """Fishbone satin leaf: slanted stitches from each edge into the midrib, laid base to tip."""
        L = self.stitch * rng.uniform(1.2, 1.9)
        W = L * rng.uniform(0.32, 0.42)
        if rust:
            ci = self.names.index("rust") if "rust" in self.names else ci
        elif rng.random() < 0.5:
            ci = self._pick(rng)
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -uy, ux
        bend = rng.normal(0.0, 0.12)
        sw = w * 0.85

        def mid(u):
            return (p[0] + ux * L * u + nx * bend * L * u * u, p[1] + uy * L * u + ny * bend * L * u * u)

        m = max(4, int(L / max(1e-6, sw * 1.05)))
        dur = rng.uniform(0.35, 0.55)
        for i in range(m):
            u = (i + 0.5) / m
            half = 0.5 * W * math.sin(math.pi * min(1.0, u * 1.02)) ** 0.7 * (1.0 - 0.2 * u)
            c = mid(u)
            vein = mid(min(1.0, u + 0.16))
            ta, tb = t0 + dur * i / m, t0 + dur * (i + 1) / m
            b.stroke((c[0] + nx * half, c[1] + ny * half), vein, ta, tb, sw, ci, 1)
            b.stroke((c[0] - nx * half, c[1] - ny * half), vein, ta + 0.4 * dur / m, tb + 0.4 * dur / m, sw, ci, 1)
        tip = mid(1.0)
        b.stroke(mid(0.84), (tip[0] + ux * sw * 0.7, tip[1] + uy * sw * 0.7), t0 + dur, t0 + dur + 0.06, sw * 0.9, ci, 1)

    def _loop(self, b, p, a, L, W, t0, w, ci, dur):
        """Lazy-daisy loop: a teardrop of thread caught at its tip."""
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -uy, ux
        m = 10
        prev = p
        for k in range(1, m + 1):
            ph = 2 * math.pi * k / m
            al = L * (1 - math.cos(ph)) / 2
            ac = W * math.sin(ph) * (0.35 + 0.65 * (1 - math.cos(ph)) / 2)
            q = (p[0] + ux * al + nx * ac, p[1] + uy * al + ny * ac)
            b.stroke(prev, q, t0 + dur * (k - 1) / m, t0 + dur * k / m, w, ci, 1)
            prev = q
        tip = (p[0] + ux * L, p[1] + uy * L)
        b.stroke(tip, (tip[0] + ux * w * 1.4, tip[1] + uy * w * 1.4), t0 + dur, t0 + dur + 0.05, w * 0.9, ci, 1)

    def _bud(self, b, rng, p, a, t0, w, ci, spill):
        L = self.stitch * rng.uniform(0.75, 1.1)
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -uy, ux
        tip = (p[0] + ux * L, p[1] + uy * L)
        if not self.allowed(tip[0], tip[1], t0 + 0.6, spill):
            return False
        for s in (-1, 1):
            ang = a + s * 0.6
            q = (p[0] + math.cos(ang) * L * 0.4, p[1] + math.sin(ang) * L * 0.4)
            b.stroke(p, q, t0, t0 + 0.12, w * 0.75, ci, 1)
        base = (p[0] + ux * L * 0.18, p[1] + uy * L * 0.18)
        for j, off in enumerate((-0.17, 0.17, 0.0)):
            s0 = (base[0] + nx * off * L, base[1] + ny * off * L)
            b.stroke(s0, tip, t0 + 0.1 + 0.07 * j, t0 + 0.34 + 0.07 * j, w * (1.2 if off == 0.0 else 0.95), self.bud_ci, 1)
        return True

    def _flower(self, b, rng, p, a, t0, w, spill):
        R = self.stitch * rng.uniform(0.5, 0.75)
        c = (p[0] + math.cos(a) * R * 0.85, p[1] + math.sin(a) * R * 0.85)
        far = (c[0] + math.cos(a) * R, c[1] + math.sin(a) * R)
        if not (self.allowed(c[0], c[1], t0 + 0.6, spill) and self.allowed(far[0], far[1], t0 + 0.6, spill)):
            return False
        n = int(rng.integers(5, 7))
        rot = rng.uniform(0, 2 * math.pi)
        for i in range(n):
            self._loop(b, c, rot + 2 * math.pi * i / n, R, R * 0.4, t0 + 0.05 * i, w * 0.62, self.petal_ci, 0.24)
        b.knot(c, t0 + 0.05 * n + 0.2, w * 1.05, int(rng.choice(self.knot_cols)))
        return True

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
            if self.shade is not None:
                self.shade[:] = 0
                self.soft[:] = 255
        self.bbox = None
        self.sdirty = None
        self.drawn = -1.0
        self._soft_t = None
        self.complete[:] = 0
        if self.complete_shade is not None:
            self.complete_shade[:] = 0
        self.finished_bins = 0
        self.active_tiles = np.empty(0, np.int64)

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
        if t == self.drawn:
            return self.bbox is not None
        if t < self.drawn:
            self.clear(over)
        if t < self.first:
            self.drawn = t
            return self.bbox is not None
        S = self.S
        hi = np.searchsorted(S[:, 4], t, side="right")
        K = self.K
        khi = np.searchsorted(K[:, 2], t, side="right")
        bucket = math.floor(t * self.completion_hz + 1e-9)
        sel = np.nonzero(self.stroke_bins[:hi] > bucket)[0]
        ksel = np.nonzero(self.knot_bins[:khi] > bucket)[0]
        end = np.searchsorted(self.finish_bins, bucket, side="right")
        completed = []
        shade, pending = self.shade, self.sdirty
        self.shade = self.complete_shade
        try:
            for k in range(self.finished_bins, end):
                ss, kk = self.bin_strokes[k], self.bin_knots[k]
                if len(ss):
                    self._draw_strokes(self.complete, ss, t)
                    completed.append(self.stroke_tiles[ss])
                if len(kk):
                    self._draw_knots(self.complete, kk, t)
                    completed.append(self.knot_tiles[kk])
        finally:
            self.shade, self.sdirty = shade, pending
        self.finished_bins = end
        _, tiles = self._tile_members(np.concatenate([self.stroke_tiles[sel], self.knot_tiles[ksel]]))
        active = np.unique(tiles)
        dirty = np.union1d(self.active_tiles, active)
        if completed:
            _, tiles = self._tile_members(np.concatenate(completed))
            dirty = np.union1d(dirty, tiles)
        if len(dirty):
            self._restore_tiles(over, dirty)
        if len(sel):
            self._draw_strokes(over, sel, t)
        if len(ksel):
            self._draw_knots(over, ksel, t)
        self.active_tiles = active
        self.drawn = t
        return self.bbox is not None

    def _restore_tiles(self, over, dirty):
        """Remove last frame's growing tips and restore the immutable stitch backing."""
        c, tile = self.canvas, self.tile
        for k in dirty:
            row, col = divmod(int(k), self.tile_cols)
            x0, y0 = col * tile, row * tile
            x1, y1 = min(c.w, x0 + tile), min(c.h, y0 + tile)
            box = (slice(y0, y1), slice(x0, x1))
            over[box] = self.complete[box]
            if self.shade is not None:
                sx1 = self.shade.shape[1] if x1 == c.w else x1 // self.sk
                sy1 = self.shade.shape[0] if y1 == c.h else y1 // self.sk
                sb = (slice(y0 // self.sk, sy1), slice(x0 // self.sk, sx1))
                self.shade[sb] = self.complete_shade[sb]
                self._dirty((sb[1].start, sb[0].start, sb[1].stop, sb[0].stop))

    def _shade_lines(self, segs, th):
        """Draw shadow segments (overlay pixels) into the quarter-resolution shadow buffer."""
        k = self.sk
        q = (segs - (k - 1) / 2.0) / k
        t = np.maximum(1, np.round((th + 0.5) / k)).astype(int)
        m = float(t.max()) + 2
        box = (q[:, [0, 2]].min() - m, q[:, [1, 3]].min() - m, q[:, [0, 2]].max() + m, q[:, [1, 3]].max() + m)
        self._dirty(box)
        self._lines(self.shade, q, t, None, None, mono=True)

    def _dirty(self, box):
        h, w = self.shade.shape
        x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
        x1, y1 = min(w, int(math.ceil(box[2])) + 1), min(h, int(math.ceil(box[3])) + 1)
        if x1 <= x0 or y1 <= y0:
            return
        d = self.sdirty
        self.sdirty = (x0, y0, x1, y1) if d is None else (min(d[0], x0), min(d[1], y0), max(d[2], x1), max(d[3], y1))

    def soft_shade(self):
        """Soft drop-shadow multiplier (3-channel, quarter resolution, 255 = no shadow); None before the first stitch."""
        if self.shade is None or self.bbox is None:
            return None
        if self.sdirty is not None:
            h, w = self.shade.shape
            p = int(math.ceil(3 * self.ssig)) + 1
            x0, y0, x1, y1 = self.sdirty
            ox0, oy0, ox1, oy1 = max(0, x0 - 2 * p), max(0, y0 - 2 * p), min(w, x1 + 2 * p), min(h, y1 + 2 * p)
            ix0, iy0, ix1, iy1 = max(0, x0 - p), max(0, y0 - p), min(w, x1 + p), min(h, y1 + p)
            blur = cv2.GaussianBlur(self.shade[oy0:oy1, ox0:ox1], (0, 0), self.ssig)[iy0 - oy0:iy1 - oy0, ix0 - ox0:ix1 - ox0]
            inv = cv2.subtract(255, cv2.convertScaleAbs(blur, alpha=self.shadow_strength))
            self.soft[iy0:iy1, ix0:ix1] = cv2.merge([inv, inv, inv])
            self.sdirty = None
        return self.soft

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
        so = self.soft_off
        mg = (1.3 + max(abs(so[0]), abs(so[1]))) * float(w.max()) + 5.0
        self._grow(min(x0.min(), x1.min()) - mg, min(y0.min(), y1.min()) - mg, max(x0.max(), x1.max()) + mg, max(y0.max(), y1.max()) + mg)
        th = np.maximum(1, np.round(w)).astype(int)
        dx, dy = x1 - x0, y1 - y0
        L = np.hypot(dx, dy) + 1e-9
        ux, uy = dx / L, dy / L
        nxv, nyv = -uy, ux
        lx, ly = self.light
        sgn = np.sign(nxv * lx + nyv * ly)
        sgn[sgn == 0] = 1
        across = np.abs(ux * ly - uy * lx)
        bright = across > 0.55
        if self.shade is not None:
            soft = np.stack([x0 + so[0] * w, y0 + so[1] * w, x1 + so[0] * w, y1 + so[1] * w], 1)
            self._shade_lines(soft, th + 1)
        sh = np.stack([x0 + 0.3 * w, y0 + 0.42 * w, x1 + 0.3 * w, y1 + 0.42 * w], 1)
        self._lines(over, sh, th + 1, None, col)
        body = np.stack([x0, y0, x1, y1], 1)
        if self.fine:
            self._lines(over, body, th, self.lo, col)
            o1 = 0.11 * w * sgn
            core = body + np.stack([nxv * o1, nyv * o1, nxv * o1, nyv * o1], 1)
            self._lines(over, core, np.maximum(1, np.round(w * 0.64)).astype(int), self.body, col)
        else:
            self._lines(over, body, th, self.body, col)
        cr = 0.5 * w
        stem = (kind == 0) & (L > 2.5 * w)
        if stem.any():
            c0 = np.stack([x0 + dx * 0.08 - nxv * cr, y0 + dy * 0.08 - nyv * cr, x0 + dx * 0.08 + nxv * cr, y0 + dy * 0.08 + nyv * cr], 1)
            self._lines(over, c0[stem], np.ones(int(stem.sum()), int), None, col[stem])
        o = 0.27 * w * sgn
        a0, a1 = np.where(kind == 0, 0.28, 0.14), np.where(kind == 0, 0.64, 0.86)
        hl = np.stack([x0 + dx * a0 + nxv * o, y0 + dy * a0 + nyv * o, x0 + dx * a1 + nxv * o, y0 + dy * a1 + nyv * o], 1)
        self._lines(over, hl, np.maximum(1, np.round(th * 0.27)).astype(int), self.hipal, np.where(bright, col, col + self.nc))
        if not self.fine:
            return
        big = (th >= 3) & (L > 1.6 * w) & (kind != 2)
        if big.any():
            hw = 0.48 * w
            vx, vy = nxv * 0.8 + ux * 0.6, nyv * 0.8 + uy * 0.6
            ones = np.ones(int(big.sum()), int)
            for a in (0.38, 0.72):
                cx, cy = x0 + dx * a, y0 + dy * a
                seg = np.stack([cx - vx * hw, cy - vy * hw, cx + vx * hw, cy + vy * hw], 1)
                self._lines(over, seg[big], ones, self.lo, col[big])
        hot = bright & (th >= 2) & (L > 1.2 * w)
        if hot.any():
            o2 = 0.22 * w * sgn
            seg = np.stack([x0 + dx * 0.42 + nxv * o2, y0 + dy * 0.42 + nyv * o2, x0 + dx * 0.52 + nxv * o2, y0 + dy * 0.52 + nyv * o2], 1)
            self._lines(over, seg[hot], np.ones(int(hot.sum()), int), self.sppal, col[hot])

    def _lines(self, img, segs, th, palette, col, mono=False):
        segs = np.round(segs * 8).astype(np.int32)
        th = np.asarray(th, int)
        if mono:
            for thick in np.unique(th):
                m = th == thick
                cv2.polylines(img, [s.reshape(2, 1, 2) for s in segs[m]], False, 255, int(thick), cv2.LINE_AA, 3)
            return
        keys = th * 64 + (0 if palette is None else col + 1)
        for key in np.unique(keys):
            m = keys == key
            thick = int(key // 64)
            colour = self.shadow if palette is None else palette[int(key % 64) - 1]
            lines = [s.reshape(2, 1, 2) for s in segs[m]]
            cv2.polylines(img, lines, False, colour + (255,), thick, cv2.LINE_AA, 3)

    def _draw_knots(self, over, ksel, t):
        K, P = self.K[ksel], self.KP[ksel]
        g = np.clip((t - K[:, 2]) / 0.25, 0.0, 1.0)
        so = self.soft_off
        lx, ly = self.light
        for (x, y), (kx, ky, ta, r, c), gg in zip(P, K, g):
            rr = max(1.0, r * self.cs * (0.4 + 0.6 * gg))
            self._grow(x - rr * 3 - 3, y - rr * 3 - 3, x + rr * 3 + 3, y + rr * 3 + 3)
            c = int(c)
            P8 = lambda px, py: (int(round(px * 8)), int(round(py * 8)))
            if self.shade is not None:
                k = self.sk
                sx, sy = (x + so[0] * rr - (k - 1) / 2.0) / k, (y + so[1] * rr - (k - 1) / 2.0) / k
                sr = rr * 1.25 / k
                self._dirty((sx - sr - 2, sy - sr - 2, sx + sr + 2, sy + sr + 2))
                cv2.circle(self.shade, P8(sx, sy), max(4, int(round(sr * 8))), 255, -1, cv2.LINE_AA, 3)
            cv2.circle(over, P8(x + 0.4 * rr, y + 0.5 * rr), int(round(rr * 1.1 * 8)), self.shadow + (255,), -1, cv2.LINE_AA, 3)
            cv2.circle(over, P8(x, y), int(round(rr * 8)), self.lo[c] + (255,), -1, cv2.LINE_AA, 3)
            cv2.circle(over, P8(x + lx * rr * 0.14, y + ly * rr * 0.14), int(round(rr * 0.8 * 8)), self.body[c] + (255,), -1, cv2.LINE_AA, 3)
            if self.fine and rr >= 2.5:
                cv2.ellipse(over, P8(x, y), (int(round(rr * 0.55 * 8)), int(round(rr * 0.3 * 8))), 35.0, 0, 360,
                            self.lo[c] + (255,), 1, cv2.LINE_AA, 3)
            hx, hy = x + lx * rr * 0.4, y + ly * rr * 0.4
            cv2.circle(over, P8(hx, hy), int(round(rr * 0.36 * 8)), self.hi[c] + (255,), -1, cv2.LINE_AA, 3)
            if self.fine:
                cv2.circle(over, P8(hx + lx * rr * 0.08, hy + ly * rr * 0.08), int(round(max(0.6, rr * 0.13) * 8)),
                           self.sppal[c] + (255,), -1, cv2.LINE_AA, 3)
