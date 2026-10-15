import math

import cv2
import numpy as np

SS = 4
INK = (16, 26, 42)
INK_DROP = (14, 12, 16)


def hex_bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def ease(p):
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


class Sprite:
    def __init__(self, canvas, tw, x0, y0, x1, y1):
        self.c = canvas
        self.k = canvas.w0 / tw
        cx0 = int(math.floor(x0 * self.k + canvas.pad)) - 2
        cy0 = int(math.floor(y0 * self.k + canvas.pad)) - 2
        cx1 = int(math.ceil(x1 * self.k + canvas.pad)) + 2
        cy1 = int(math.ceil(y1 * self.k + canvas.pad)) + 2
        self.cx0, self.cy0 = max(0, cx0), max(0, cy0)
        self.cx1, self.cy1 = min(canvas.w, cx1), min(canvas.h, cy1)
        self.ok = self.cx1 > self.cx0 + 1 and self.cy1 > self.cy0 + 1
        self.buf = np.zeros(((self.cy1 - self.cy0) * SS, (self.cx1 - self.cx0) * SS, 4), np.uint8) if self.ok else None

    def q(self, pts):
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        x = ((pts[:, 0] * self.k + self.c.pad) - self.cx0) * SS
        y = ((pts[:, 1] * self.k + self.c.pad) - self.cy0) * SS
        return np.round(np.stack([x, y], 1) * 8).astype(np.int32)

    def px(self, v):
        return v * self.k * SS

    def fill(self, pts, col, alpha=1.0):
        if alpha >= 0.999:
            cv2.fillPoly(self.buf, [self.q(pts)], tuple(col) + (255,), cv2.LINE_AA, 3)
            return
        layer = np.zeros_like(self.buf)
        cv2.fillPoly(layer, [self.q(pts)], tuple(int(c * alpha) for c in col) + (int(255 * alpha),), cv2.LINE_AA, 3)
        self._over(layer)

    def line(self, pts, col, width, closed=False, alpha=1.0):
        th = max(1, int(round(self.px(width))))
        if alpha >= 0.999:
            cv2.polylines(self.buf, [self.q(pts)], closed, tuple(col) + (255,), th, cv2.LINE_AA, 3)
            return
        layer = np.zeros_like(self.buf)
        cv2.polylines(layer, [self.q(pts)], closed, tuple(int(c * alpha) for c in col) + (int(255 * alpha),), th, cv2.LINE_AA, 3)
        self._over(layer)

    def disc(self, p, r, col, alpha=1.0):
        c = self.q([p])[0]
        rr = max(1, int(round(self.px(r) * 8)))
        if alpha >= 0.999:
            cv2.circle(self.buf, (int(c[0]), int(c[1])), rr, tuple(col) + (255,), -1, cv2.LINE_AA, 3)
            return
        layer = np.zeros_like(self.buf)
        cv2.circle(layer, (int(c[0]), int(c[1])), rr, tuple(int(v * alpha) for v in col) + (int(255 * alpha),), -1, cv2.LINE_AA, 3)
        self._over(layer)

    def _over(self, layer):
        clear = cv2.subtract(255, layer[..., 3])
        self.buf[:] = cv2.add(layer, cv2.multiply(self.buf, cv2.merge([clear, clear, clear, clear]), scale=1 / 255))

    def done(self):
        if not self.ok:
            return None
        h, w = self.cy1 - self.cy0, self.cx1 - self.cx0
        small = cv2.resize(self.buf, (w, h), interpolation=cv2.INTER_AREA)
        return slice(self.cy0, self.cy1), slice(self.cx0, self.cx1), small


def area(poly):
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def clip_below(poly, ys):
    out = []
    n = len(poly)
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        ia, ib = a[1] >= ys, b[1] >= ys
        if ia:
            out.append(a)
        if ia != ib:
            f = (ys - a[1]) / (b[1] - a[1])
            out.append(a + (b - a) * f)
    return np.array(out)


def fill_level(poly, frac):
    lo, hi = poly[:, 1].min(), poly[:, 1].max()
    target = area(poly) * frac
    for x in range(14):
        mid = (lo + hi) / 2
        c = clip_below(poly, mid)
        if len(c) >= 3 and area(c) > target:
            lo = mid
        else:
            hi = mid
    return clip_below(poly, (lo + hi) / 2)


def teardrop(tip, base, r, n=18):
    tip, base = np.asarray(tip, float), np.asarray(base, float)
    d = base - tip
    L = np.linalg.norm(d) + 1e-9
    u = d / L
    v = np.array([-u[1], u[0]])
    pts = []
    for k in range(n + 1):
        a = math.pi * k / n
        pts.append(base + u * r * math.sin(a) + v * r * math.cos(a))
    neck = max(r * 0.12, 0.05)
    pts.append(tip + v * -neck)
    pts.append(tip + v * neck)
    return np.array(pts)


class Prop:
    under = False

    def __init__(self, spec, t0, ctx):
        self.spec = spec
        self.t0 = t0
        self.ctx = ctx
        self.tw, self.th = ctx.tw, ctx.th

    def P(self, xy):
        return np.array([xy[0] * self.tw, xy[1] * self.th], np.float64)

    def seeds(self):
        return []

    def impacts(self):
        return []

    def cues(self):
        return []

    def window(self):
        return self.t0, self.t0 + 3.0

    def stamps(self, t):
        return []


class Vial(Prop):
    def __init__(self, spec, t0, ctx):
        super().__init__(spec, t0, ctx)
        s = spec
        self.hand = self.P(s["hand"])
        self.ear = self.P(s["ear"])
        self.tilt = math.radians(s.get("tilt", -125))
        self.tilt_s = s.get("tilt_s", 1.0)
        self.bead_s = s.get("bead_s", 1.0)
        self.fall_s = s.get("fall_s", 0.32)
        self.size = s.get("size", 0.034) * self.tw
        self.t_detach = t0 + self.tilt_s + self.bead_s
        self.t_impact = self.t_detach + self.fall_s

    def window(self):
        return self.t0, self.t_impact + s_after(self.spec)

    def seeds(self):
        x, y = self.spec["ear"]
        return [({"x": x, "y": y, "speed": self.spec.get("bloom_speed", 0.025), "radius": self.spec.get("bloom", 0.012),
                  "provisional": True}, self.t_impact)]

    def cues(self):
        return [("vial", self.t_impact)]

    def impacts(self):
        return [{"at": self.ear.tolist(), "t": self.t_impact, "count": 9, "energy": 0.17, "size": 0.0028,
                 "spread": 70, "clip": self.spec.get("clip"), "seed": 31}]

    def angle(self, t):
        return self.tilt * ease((t - self.t0) / self.tilt_s)

    def frame(self, ang):
        c, s = math.cos(ang), math.sin(ang)
        R = np.array([[c, -s], [s, c]])
        return lambda pts: (np.asarray(pts, float) * self.size) @ R.T + self.hand

    def lip(self, t):
        return self.frame(self.angle(t))([[0.0, -0.42]])[0]

    def stamps(self, t):
        if t < self.t0 or t > self.t_impact + s_after(self.spec):
            return []
        ang = self.angle(t)
        F = self.frame(ang)
        lip = self.lip(t)
        drop = self.ear
        sz = self.size
        x0, y0 = min(lip[0], drop[0], self.hand[0]) - sz * 1.6, min(lip[1], self.hand[1]) - sz * 1.6
        x1, y1 = max(lip[0], drop[0], self.hand[0]) + sz * 1.6, max(drop[1], self.hand[1]) + sz * 1.4
        sp = Sprite(self.ctx.canvas, self.tw, x0, y0, x1, y1)
        if not sp.ok:
            return []
        th = np.linspace(0, 2 * np.pi, 36, endpoint=False)
        body = np.stack([0.34 * np.cos(th), 0.3 + 0.32 * np.sin(th)], 1)
        neck = [[-0.11, -0.05], [-0.09, -0.36], [-0.14, -0.42], [0.14, -0.42], [0.09, -0.36], [0.11, -0.05]]
        glass = (196, 214, 200)
        sp.fill(F(body), glass, 0.55)
        sp.fill(F(neck), glass, 0.55)
        level = 0.55 - 0.2 * ease((t - self.t0 - self.tilt_s) / max(0.1, self.bead_s))
        liq = fill_level(F(np.stack([0.3 * np.cos(th), 0.3 + 0.28 * np.sin(th)], 1)), level)
        if len(liq) >= 3:
            sp.fill(liq, (20, 24, 18), 0.92)
        sp.line(F(body), INK, 0.9, closed=True)
        sp.line(F(neck), INK, 0.9)
        sp.line(F([[0.18, 0.12], [0.24, 0.28], [0.2, 0.42]]), (240, 248, 250), 0.6, alpha=0.8)
        sp.line(F([[-0.12, -0.42], [0.12, -0.42]]), (60, 80, 110), 1.1)
        if t < self.t_detach:
            p = (t - (self.t0 + self.tilt_s - 0.15)) / (self.bead_s + 0.15)
            if p > 0:
                r = sz * 0.07 * (0.4 + 0.6 * min(1.0, p * 1.6))
                stretch = sz * 0.28 * max(0.0, (p - 0.45) / 0.55) ** 1.6
                base = lip + np.array([0.0, r * 0.6 + stretch])
                sp.fill(teardrop(lip, base, r), INK_DROP)
                sp.disc(base + np.array([-r * 0.35, -r * 0.3]), r * 0.25, (200, 210, 220), 0.6)
        elif t < self.t_impact:
            p = (t - self.t_detach) / self.fall_s
            pos = lip + (drop - lip) * np.array([p, p * p])
            vel = (drop - lip) * np.array([1.0, 2 * p]) / self.fall_s
            r = sz * 0.07
            tail = pos - vel / (np.linalg.norm(vel) + 1e-9) * r * (1.5 + 2.5 * p)
            sp.fill(teardrop(tail, pos, r), INK_DROP)
            if p < 0.25:
                nub = lip + np.array([0.0, sz * 0.05 * (1 - p * 4)])
                sp.disc(nub, sz * 0.03 * (1 - p * 4), INK_DROP)
        else:
            p = (t - self.t_impact) / 0.45
            if p < 1.0:
                sp.line(drop + sz * 0.12 * (0.4 + p) * np.stack([np.cos(th), 0.45 * np.sin(th)], 1), INK_DROP, 0.5,
                        closed=True, alpha=0.7 * (1 - p))
        out = sp.done()
        return [out] if out else []


def s_after(spec):
    return spec.get("linger", 1.6)


def align(a):
    u = np.array([0.0, -1.0, 0.0])
    a = a / np.linalg.norm(a)
    v = np.cross(u, a)
    c = float(u @ a)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def spin(phi):
    c, s = math.cos(phi), math.sin(phi)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


class Crown(Prop):
    under = True

    def __init__(self, spec, t0, ctx):
        super().__init__(spec, t0, ctx)
        s = spec
        self.at = self.P(s["at"])
        self.R = s.get("size", 0.03) * self.tw / 2
        self.floor = s.get("floor", 0.9) * self.th
        self.d = s.get("dir", 1)
        self.roll = s.get("roll", 0.085) * self.tw
        self.tA = s.get("tip_s", 0.55)
        self.tC = s.get("roll_s", 1.3)
        self.tD = s.get("settle_s", 1.2)
        self.yaw = math.radians(s.get("yaw", 52))
        self.pitch = math.radians(s.get("view_pitch", 16))
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        self.view = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
        n = 60
        a = np.linspace(0, 2 * np.pi, n, endpoint=False)
        k = s.get("points", 5)
        sp = np.maximum(0, np.cos(k * a)) ** 2.5
        self.a = a
        self.bottom = np.stack([np.cos(a), np.zeros(n), np.sin(a)], 1)
        self.band = np.stack([np.cos(a), np.full(n, -0.38), np.sin(a)], 1)
        self.top = np.stack([np.cos(a), -0.38 - 0.62 * sp, np.sin(a)], 1)
        self.jewels = [int(j) for j in np.round(np.arange(k) * n / k + n / (2 * k)).astype(int) % n]
        self.g = 1500.0 * self.th / 1484.0
        self.ref = np.array([0.0, -0.4, 0.0])
        self.piv = np.array([self.d * 1.0, 0.0, 0.0])
        self.th_tip = math.radians(68)
        self.w0 = 2 * self.th_tip / self.tA
        p0, v0 = self._tip_end()
        self.release = p0
        self.v0 = v0
        land_y = self.floor - self.R * 0.98
        dy = land_y - p0[1]
        vy = v0[1]
        self.tB = max(0.12, (-vy + math.sqrt(max(0.0, vy * vy + 2 * self.g * max(1.0, dy)))) / self.g)
        self.land = np.array([p0[0] + v0[0] * self.tB, land_y])
        self.settled = None

    def ungild(self):
        return (self.at[0] / self.tw, self.at[1] / self.th, self.R * 1.6 / self.tw)

    def _M(self, tilt, yaw, phi):
        axis = np.array([self.d * math.sin(tilt) * math.cos(yaw), -math.cos(tilt), math.sin(tilt) * math.sin(yaw)])
        return align(axis) @ spin(phi)

    def _tip_end(self):
        R = self.R
        pivot = self.at + np.array([self.d * R, 0.0])
        M = self._M(self.th_tip, 0.0, 0.0)
        c = pivot + (self.view @ M @ ((self.ref - self.piv) * R))[:2]
        M2 = self._M(self.th_tip - 0.01, 0.0, 0.0)
        c2 = pivot + (self.view @ M2 @ ((self.ref - self.piv) * R))[:2]
        v = (c - c2) / (0.01 / self.w0)
        return c, v * 0.6

    def window(self):
        return self.t0, self.t0 + 60.0

    def impacts(self):
        return [{"at": [float(self.land[0]), float(self.floor - self.R * 0.1)], "t": self.t0 + self.tA + self.tB, "count": 8,
                 "energy": 0.2, "size": 0.0026, "spread": 75, "clip": self.spec.get("clip"), "seed": 47}]

    def pose(self, t):
        tau = t - self.t0
        R = self.R
        if tau < self.tA:
            th = self.th_tip * max(0.0, tau / self.tA) ** 2
            M = self._M(th, 0.0, 0.0)
            return M, self.at + np.array([self.d * R, 0.0]), self.piv
        tau -= self.tA
        if tau < self.tB:
            p = tau / self.tB
            th = self.th_tip + (math.radians(90) - self.th_tip) * ease(p * 1.2)
            pos = self.release + self.v0 * tau + np.array([0.0, 0.5 * self.g * tau * tau])
            return self._M(th, self.yaw * ease(p), 0.6 * p), pos, self.ref
        tau -= self.tB
        if tau < self.tC:
            p = tau / self.tC
            dist = self.roll * (1 - (1 - p) ** 2)
            hop = R * 0.3 * abs(math.sin(math.pi * min(1.0, p * 2.5))) * (1 - p) ** 2
            rock = math.radians(7) * math.sin(p * 12) * (1 - p)
            pos = self.land + np.array([self.d * dist, -hop])
            return self._M(math.radians(90) + rock, self.yaw, 0.6 + dist / R), pos, self.ref
        tau -= self.tC
        p = min(1.0, tau / self.tD)
        wob = math.radians(10) * math.exp(-3.5 * p) * math.sin(p * 16) * (1.0 - ease(p))
        lean = math.radians(90) + math.radians(9) * ease(p)
        pos = self.land + np.array([self.d * self.roll, R * 0.06 * ease(p)])
        return self._M(lean + wob, self.yaw + wob * 0.4, 0.6 + self.roll / R), pos, self.ref

    def stamps(self, t):
        if t < self.t0 - 2.0:
            return []
        finish = self.t0 + self.tA + self.tB + self.tC + self.tD
        if t >= finish and self.settled is not None:
            return self.settled
        M, pos, ref = self.pose(t)
        R = self.R
        V = self.view
        anchor = pos

        def world(local):
            w = (M @ ((local - ref) * R).T).T
            v = (V @ w.T).T
            return v[:, :2] + anchor, v[:, 2]

        bot, zb = world(self.bottom)
        top, zt = world(self.top)
        band, zd = world(self.band)
        nrm = (V @ (M @ np.stack([np.cos(self.a), np.zeros_like(self.a), np.sin(self.a)], 1).T)).T
        lo = np.vstack([bot, top]).min(axis=0)
        hi = np.vstack([bot, top]).max(axis=0)
        sp = Sprite(self.ctx.canvas, self.tw, lo[0] - R * 0.8, lo[1] - R * 0.8, hi[0] + R * 0.8, max(hi[1], self.floor) + R * 0.5)
        if not sp.ok:
            return []
        h = self.floor - hi[1]
        sh = float(np.clip(1.0 - h / (R * 5), 0.0, 1.0))
        if sh > 0:
            cx = (lo[0] + hi[0]) / 2
            wx = (hi[0] - lo[0]) / 2 + R * 0.2
            e = np.stack([cx + wx * np.cos(self.a), self.floor + R * 0.16 * np.sin(self.a)], 1)
            sp.fill(e, (6, 6, 6), 0.4 * sh)
        L = np.array([-0.45, -0.7, 0.55])
        L /= np.linalg.norm(L)
        gold = np.array(hex_bgr(self.spec.get("colour", "#c99b45")), float)
        hl = np.array(hex_bgr("#f3dc98"), float)
        dk = np.array(hex_bgr("#5c3f14"), float)
        n = len(self.a)
        faces = []
        for k in range(n):
            j = (k + 1) % n
            quad = np.array([bot[k], bot[j], top[j], top[k]])
            z = (zb[k] + zb[j] + zt[k] + zt[j]) / 4
            nm = nrm[k] + nrm[j]
            nm /= np.linalg.norm(nm) + 1e-9
            faces.append((z, quad, nm))
        faces.sort(key=lambda f: f[0])
        for z, quad, nm in faces:
            outer = nm[2] > 0
            lam = float(nm @ L) if outer else float(-nm @ L) * 0.5
            lam = max(0.0, lam)
            col = dk + (gold - dk) * min(1.0, 0.25 + lam)
            if outer and lam > 0.7:
                col = col + (hl - col) * min(1.0, (lam - 0.7) / 0.3)
            if not outer:
                col = col * 0.6
            sp.fill(quad, tuple(int(v) for v in col))
        for arr, z, w in ((top, zt, 0.5), (bot, zb, 0.55), (band, zd, 0.3)):
            for k in range(n):
                j = (k + 1) % n
                sp.line(arr[[k, j]], INK if (z[k] + z[j]) > 0 else (40, 46, 60), w)
        for k in self.jewels:
            if nrm[k, 2] > 0.25:
                q = (band[k] + bot[k]) / 2
                sp.disc(q, R * 0.11, (40, 30, 150) if k % 2 == 0 else (130, 70, 30))
                sp.disc(q + np.array([-R * 0.035, -R * 0.035]), R * 0.035, (225, 225, 240), 0.85)
        out = sp.done()
        result = [out] if out else []
        if t >= finish:
            self.settled = result
        return result


class Petals(Prop):
    def __init__(self, spec, t0, ctx):
        super().__init__(spec, t0, ctx)
        self.src = self.P(spec["from"])
        self.land = [self.P(p) for p in spec["land"]]
        self.stagger = spec.get("stagger", 0.26)
        self.fall = spec.get("fall", 1.1)
        rng = np.random.default_rng(spec.get("rng", 5))
        self.ph = rng.uniform(0, 2 * np.pi, len(self.land))
        self.cols = [hex_bgr(c) for c in spec.get("colours", ["#b8485a", "#c9786a", "#d9a0a0", "#9a2f3a"])]

    def times(self):
        return [self.t0 + k * self.stagger for k in range(len(self.land))]

    def seeds(self):
        out = []
        base = self.spec.get("seed", {}) if isinstance(self.spec.get("seed"), dict) else {}
        for (x, y), ts in zip(self.spec["land"], self.times()):
            s = {"x": x, "y": y, "speed": 0.15, "provisional": True}
            s.update(base)
            out.append((s, ts + self.fall))
        return out

    def window(self):
        return self.t0, self.times()[-1] + self.fall + 1.5

    def impacts(self):
        return [{"at": d.tolist(), "t": ts + self.fall, "count": 3, "energy": 0.1, "size": 0.0016, "spread": 55,
                 "stain": False, "colour": "#3b4a52", "seed": 60 + k} for k, (d, ts) in enumerate(zip(self.land, self.times()))]

    def stamps(self, t):
        a, b = self.window()
        if t < a or t > b:
            return []
        L = 0.012 * self.tw
        xs = [self.src[0]] + [p[0] for p in self.land]
        ys = [self.src[1]] + [p[1] for p in self.land]
        sp = Sprite(self.ctx.canvas, self.tw, min(xs) - L * 4, min(ys) - L * 3, max(xs) + L * 4, max(ys) + L * 3)
        if not sp.ok:
            return []
        for k, (dst, ts) in enumerate(zip(self.land, self.times())):
            p = (t - ts) / self.fall
            if p < 0:
                continue
            col = self.cols[k % len(self.cols)]
            if p < 1:
                q = ease(p) * 0.35 + p * 0.65
                pos = self.src + (dst - self.src) * np.array([q, p ** 1.4])
                pos[0] += L * 1.6 * math.sin(p * 9 + self.ph[k]) * (1 - p) ** 0.5
                ang = math.sin(p * 8 + self.ph[k]) * 1.1 + self.ph[k]
                alpha = 1.0
            else:
                after = t - ts - self.fall
                pos = dst + np.array([after * 0.006 * self.tw, 0.0])
                ang = self.ph[k] + after * 0.4
                alpha = max(0.0, 1.0 - after / 1.4)
                r = L * (0.4 + after * 1.6)
                th = np.linspace(0, 2 * np.pi, 32)
                ring = np.stack([dst[0] + r * np.cos(th), dst[1] + r * 0.35 * np.sin(th)], 1)
                sp.line(ring, (200, 205, 195), 0.35, closed=True, alpha=max(0.0, 0.6 * (1 - after / 1.2)))
            if alpha <= 0:
                continue
            u = np.array([math.cos(ang), math.sin(ang)])
            v = np.array([-u[1], u[0]])
            th = np.linspace(0, 2 * np.pi, 20, endpoint=False)
            al = L * (1 - np.cos(th)) / 2
            ac = L * 0.42 * np.sin(th) * (0.4 + 0.6 * (1 - np.cos(th)) / 2)
            pts = pos + np.outer(al - L / 2, u) + np.outer(ac, v)
            sp.fill(pts, col, alpha)
            sp.line(pts, tuple(int(c * 0.55) for c in col), 0.35, closed=True, alpha=alpha)
            sp.line([pos - u * L * 0.3, pos + u * L * 0.25], tuple(min(255, int(c * 1.25)) for c in col), 0.25, alpha=alpha * 0.8)
        out = sp.done()
        return [out] if out else []


class Pearl(Prop):
    def __init__(self, spec, t0, ctx):
        super().__init__(spec, t0, ctx)
        self.a = self.P(spec["from"])
        self.b = self.P(spec["to"])
        self.fall = spec.get("fall", 0.42)

    def window(self):
        return self.t0, self.t0 + self.fall + 0.02

    def impacts(self):
        return [{"at": self.b.tolist(), "t": self.t0 + self.fall, "count": 9, "energy": 0.24, "size": 0.0022, "spread": 48,
                 "ring": 0.01, "ring_colour": "#2a1c12", "clip": self.spec.get("clip"), "seed": 71}]

    def stamps(self, t):
        a, b = self.window()
        if t < a or t > b:
            return []
        r = 0.0045 * self.tw
        sp = Sprite(self.ctx.canvas, self.tw, min(self.a[0], self.b[0]) - r * 8, self.a[1] - r * 4,
                    max(self.a[0], self.b[0]) + r * 8, self.b[1] + r * 6)
        if not sp.ok:
            return []
        p = (t - self.t0) / self.fall
        if p < 1:
            pos = self.a + (self.b - self.a) * np.array([p, p * p])
            sp.disc(pos + np.array([r * 0.3, r * 0.35]), r * 1.05, (10, 10, 10), 0.35)
            sp.disc(pos, r, (214, 226, 232))
            sp.disc(pos + np.array([r * 0.25, r * 0.25]), r * 0.7, (178, 190, 198), 0.6)
            sp.disc(pos - np.array([r * 0.35, r * 0.35]), r * 0.3, (255, 255, 255), 0.9)
            sp.line(np.stack([pos[0] + r * np.cos(np.linspace(0, 6.3, 24)), pos[1] + r * np.sin(np.linspace(0, 6.3, 24))], 1), INK, 0.3)
        out = sp.done()
        return [out] if out else []


class Drops(Prop):
    def __init__(self, spec, t0, ctx):
        super().__init__(spec, t0, ctx)
        rng = np.random.default_rng(spec.get("rng", 11))
        n = spec.get("drops", 5)
        self.dur = spec.get("dur", 0.5)
        kind = spec["kind"]
        if kind == "spray":
            c = self.P(spec["at"])
            src = [c] * n
            spread = spec.get("spread", 0.07) * self.tw
            dst = []
            for k in range(n):
                ang = rng.uniform(0, 2 * math.pi)
                d = spread * math.sqrt(rng.uniform(0.15, 1.0))
                dst.append(c + np.array([math.cos(ang) * d, math.sin(ang) * d * 0.8 + d * 0.3]))
            self.height = [spread * rng.uniform(0.2, 0.6) for x in range(n)]
            self.starts = [t0 + rng.uniform(0.0, 0.06) for x in range(n)]
            self.durs = [self.dur * rng.uniform(0.6, 1.1) for x in range(n)]
        else:
            a, b = self.P(spec["from"]), self.P(spec["to"])
            spread = spec.get("spread", 0.012) * self.tw
            src = [a + rng.normal(0, spread * 0.1, 2) for x in range(n)]
            dst = [b + rng.normal(0, spread * 0.6, 2) * np.array([1.0, 0.5]) for x in range(n)]
            h = spec.get("height", 0.05) * self.tw
            self.height = [h * rng.uniform(0.8, 1.2) for x in range(n)]
            self.starts = [t0 + k * 0.05 + rng.uniform(0, 0.04) for k in range(n)]
            self.durs = [self.dur * rng.uniform(0.9, 1.1) for x in range(n)]
        self.src, self.dst = src, dst
        self.size = [spec.get("size", 0.0065) * self.tw * rng.uniform(0.6, 1.2) for x in range(n)]

    def landings(self):
        return [(d, s + du) for d, s, du in zip(self.dst, self.starts, self.durs)]

    def impacts(self):
        clip = self.spec.get("seed", {}).get("clip")
        return [{"at": d.tolist(), "t": tl, "count": 3, "energy": 0.12, "size": float(r) / self.tw * 0.45, "spread": 60,
                 "clip": clip, "seed": 80 + k} for k, ((d, tl), r) in enumerate(zip(self.landings(), self.size))]

    def seeds(self):
        out = []
        base = self.spec.get("seed", {})
        for d, tl in self.landings():
            s = {"x": d[0] / self.tw, "y": d[1] / self.th, "speed": 0.04, "radius": 0.02, "provisional": True}
            s.update(base)
            out.append((s, tl))
        return out

    def window(self):
        return self.t0, max(s + d for s, d in zip(self.starts, self.durs)) + 0.3

    def stamps(self, t):
        a, b = self.window()
        if t < a or t > b:
            return []
        pts = np.array(self.src + self.dst)
        m = max(self.height) + 0.02 * self.tw
        sp = Sprite(self.ctx.canvas, self.tw, pts[:, 0].min() - m, pts[:, 1].min() - m, pts[:, 0].max() + m, pts[:, 1].max() + m * 0.3)
        if not sp.ok:
            return []
        for s0, d0, st, du, h, r in zip(self.src, self.dst, self.starts, self.durs, self.height, self.size):
            p = (t - st) / du
            if p < 0:
                continue
            if p < 1:
                pos = s0 + (d0 - s0) * p - np.array([0.0, 4 * h * p * (1 - p)])
                vel = (d0 - s0) - np.array([0.0, 4 * h * (1 - 2 * p)])
                u = vel / (np.linalg.norm(vel) + 1e-9)
                tail = pos - u * r * 2.6
                sp.fill(teardrop(tail, pos, r), INK_DROP)
        out = sp.done()
        return [out] if out else []


class Sparkle(Prop):
    def __init__(self, spec, t0, ctx):
        super().__init__(spec, t0, ctx)
        self.life = spec.get("life", 0.8)
        self.pts = None

    def _pick(self):
        spec = self.spec
        rng = np.random.default_rng(spec.get("seed", 4))
        gold = self.ctx.gold
        self.pts = []
        if gold is not None:
            ys, xs = np.nonzero(gold > 0.6)
            if len(ys):
                pick = rng.choice(len(ys), size=min(spec.get("count", 8), len(ys)), replace=False)
                ih, iw = gold.shape
                for k in pick:
                    self.pts.append(np.array([(xs[k] + 0.5) / iw * self.tw, (ys[k] + 0.5) / ih * self.th]))
        dur = spec.get("dur", 3.3)
        self.starts = sorted(self.t0 + rng.uniform(0, max(0.1, dur - self.life)) for x in self.pts)

    def window(self):
        return self.t0, self.t0 + self.spec.get("dur", 3.3) + 0.2

    def stamps(self, t):
        a, b = self.window()
        if t < a or t > b:
            return []
        if self.pts is None:
            self._pick()
        out = []
        r = 0.012 * self.tw
        for p, st in zip(self.pts, self.starts):
            q = (t - st) / self.life
            if q <= 0 or q >= 1:
                continue
            lv = math.sin(math.pi * q) ** 2
            sp = Sprite(self.ctx.canvas, self.tw, p[0] - r, p[1] - r, p[0] + r, p[1] + r)
            if not sp.ok:
                continue
            sp.disc(p, r * 0.35, (150, 215, 245), 0.35 * lv)
            for ang, ln in ((0.0, 1.0), (math.pi / 2, 0.8), (math.pi / 4, 0.35), (-math.pi / 4, 0.35)):
                d = np.array([math.cos(ang), math.sin(ang)]) * r * ln * (0.5 + 0.5 * lv)
                sp.line([p - d, p + d], (205, 240, 255), 0.18, alpha=0.9 * lv)
            sp.disc(p, r * 0.08, (235, 250, 255), lv)
            o = sp.done()
            if o:
                out.append(o)
        return out


KINDS = {"vial": Vial, "crown": Crown, "petals": Petals, "pearl": Pearl, "hop": Drops, "spray": Drops, "sparkle": Sparkle}


class Ctx:
    def __init__(self, tw, th, canvas, gold=None):
        self.tw, self.th = tw, th
        self.canvas = canvas
        self.gold = gold


def build(timeline, ctx):
    props = []
    for spec, t0, _ in timeline.items("props"):
        cls = KINDS.get(spec.get("kind"))
        if cls is not None:
            props.append(cls(spec, t0, ctx))
    sc = getattr(ctx, "splash", None) or {}
    if sc.get("on", True):
        from particles import Splash
        for p in list(props):
            for imp in p.impacts():
                spec = {k: v for k, v in imp.items() if v is not None}
                spec["kind"] = "splash"
                spec["energy"] = spec.get("energy", 0.18) * sc.get("energy", 1.0)
                spec["count"] = max(0, int(round(spec.get("count", 6) * sc.get("count", 1.0))))
                if not sc.get("stain", True):
                    spec["stain"] = False
                t = spec.pop("t")
                if spec["count"]:
                    props.append(Splash(spec, t, ctx))
    return props
