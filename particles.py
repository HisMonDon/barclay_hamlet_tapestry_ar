import math

import cv2
import numpy as np

from props import INK_DROP, Prop, Sprite, hex_bgr, teardrop
from timeline import hold_time


class Splash(Prop):
    """Droplets thrown up from an impact in the picture plane. Each landing seeds a small satellite stain."""

    def __init__(self, spec, t0, ctx):
        super().__init__(spec, t0, ctx)
        s = spec
        self.at = np.array(s["at"], np.float64)
        rng = np.random.default_rng(s.get("seed", 0))
        n = int(s.get("count", 6))
        e = s.get("energy", 0.18) * self.tw
        self.g = s.get("gravity", 1.6) * self.tw
        size = s.get("size", 0.0024) * self.tw
        spread = math.radians(s.get("spread", 65))
        lean = math.radians(s.get("lean", 0.0))
        depth = s.get("depth", 0.006) * self.tw
        self.drops = []
        for k in range(n):
            ang = -math.pi / 2 + lean + rng.uniform(-spread, spread)
            v = e * rng.uniform(0.4, 1.0)
            vx, vy = v * math.cos(ang), v * math.sin(ang)
            dz = rng.uniform(-0.4, 1.0) * depth
            disc = vy * vy + 2 * self.g * dz
            if disc < 0:
                dz, disc = 0.0, vy * vy
            tl = (-vy + math.sqrt(disc)) / self.g
            r = size * rng.uniform(0.55, 1.0) * (1.25 - 0.45 * v / max(e, 1e-6))
            self.drops.append((vx, vy, max(0.05, tl), r, rng.uniform(0.0, s.get("stagger", 0.04)), dz))
        self.colour = hex_bgr(s["colour"]) if "colour" in s else INK_DROP
        self.ring = s.get("ring", 0.0) * self.tw
        self.ring_colour = hex_bgr(s.get("ring_colour", "#c8cdc3"))
        self.end = t0 + (max(d[4] + d[2] for d in self.drops) if self.drops else 0.0) + 0.3
        if self.ring:
            self.end = max(self.end, t0 + 0.9)

    def window(self):
        return self.t0, self.end

    def landing(self, d):
        vx, vy, tl, r, delay, dz = d
        return self.at + np.array([vx * tl, dz]), self.t0 + delay + tl

    def seeds(self):
        if not self.spec.get("stain", True):
            return []
        out = []
        for d in self.drops:
            (x, y), tl = self.landing(d)
            u, v = x / self.tw, y / self.th
            if not (0.0 <= u < 1.0 and 0.0 <= v < 1.0):
                continue
            s = {"x": u, "y": v, "speed": self.spec.get("stain_speed", 0.03),
                 "radius": d[3] * self.spec.get("stain_scale", 2.6) / self.tw, "provisional": True}
            if self.spec.get("clip"):
                s["clip"] = self.spec["clip"]
            out.append((s, tl + self.spec.get("stain_delay", 0.22)))
        return out

    def stamps(self, t):
        if t < self.t0 or t > self.end or not self.drops:
            return []
        pts = [self.at]
        top = self.at[1]
        for vx, vy, tl, r, delay, dz in self.drops:
            pts.append(self.at + np.array([vx * tl, dz]))
            top = min(top, self.at[1] - vy * vy / (2 * self.g))
        pts = np.array(pts)
        m = max(d[3] for d in self.drops) * 4 + self.ring * 1.2 + 2
        sp = Sprite(self.ctx.canvas, self.tw, pts[:, 0].min() - m, top - m, pts[:, 0].max() + m, pts[:, 1].max() + m)
        if not sp.ok:
            return []
        if self.ring:
            p = (t - self.t0) / 0.9
            if 0 < p < 1:
                th = np.linspace(0, 2 * np.pi, 40)
                for k, lag in enumerate((0.0, 0.3)):
                    q = p - lag
                    if q <= 0:
                        continue
                    rr = self.ring * (0.15 + 0.85 * (1 - (1 - q) ** 2))
                    ring = np.stack([self.at[0] + rr * np.cos(th), self.at[1] + rr * 0.32 * np.sin(th)], 1)
                    sp.line(ring, self.ring_colour, 0.35, closed=True, alpha=0.65 * (1 - q) * (1 - 0.4 * k))
        for vx, vy, tl, r, delay, dz in self.drops:
            tau = t - self.t0 - delay
            if tau < 0:
                continue
            if tau < tl:
                pos = self.at + np.array([vx * tau, vy * tau + 0.5 * self.g * tau * tau])
                vel = np.array([vx, vy + self.g * tau])
                speed = float(np.linalg.norm(vel)) + 1e-9
                tail = pos - vel / speed * (r * 1.3 + speed * 0.012)
                sp.fill(teardrop(tail, pos, r), self.colour)
                sp.disc(pos + np.array([-0.32 * r, -0.36 * r]), r * 0.3, (214, 224, 232), 0.75)
            else:
                q = (tau - tl) / 0.25
                if q < 1:
                    land = self.at + np.array([vx * tl, dz])
                    th = np.linspace(0, 2 * np.pi, 20, endpoint=False)
                    rx, ry = r * (1.0 + 0.9 * q), r * (0.55 - 0.15 * q)
                    sp.fill(np.stack([land[0] + rx * np.cos(th), land[1] + ry * np.sin(th)], 1), self.colour, 1.0 - 0.7 * q)
                    if q < 0.5:
                        for side in (-1, 1):
                            sp.disc(land + np.array([side * r * (1.4 + 2.5 * q), -r * 0.4 * (1 - 2 * q)]), r * 0.28 * (1 - 2 * q),
                                    self.colour)
        out = sp.done()
        return [out] if out else []


def _bgr(h):
    return np.array(hex_bgr(h), np.float32)


class Motes:
    """Dust drifting through the candlelight between the lens and the cloth, as soft additive bokeh."""

    def __init__(self, cfg, size, timeline=None):
        p = cfg.get("motes", {})
        self.on = bool(p.get("on", True)) and cfg.get("fx", {}).get("motes", True)
        self.w, self.h = size
        if not self.on:
            return
        rng = np.random.default_rng(p.get("seed", 5))
        n = int(p.get("count", 60))
        self.X, self.Y = rng.random(n), rng.random(n)
        self.z = rng.random(n) ** 1.2
        self.vx = rng.normal(0.0, p.get("drift", 0.004), n)
        self.vy = -rng.uniform(0.002, p.get("rise", 0.009), n)
        self.ph = rng.uniform(0, 2 * np.pi, (n, 3))
        self.fq = rng.uniform(0.08, 0.35, (n, 3))
        self.lum = rng.uniform(0.35, 1.0, n)
        scale = self.w / 1920.0
        self.rad = (1.0 + p.get("near_px", 15.0) * (1 - self.z) ** 2.2) * scale + 0.4
        self.par = p.get("parallax", 0.35) * (0.4 + 1.2 * (1 - self.z))
        self.level = p.get("brightness", 24.0)
        self.col = _bgr(p.get("colour", "#ffd8a8")) / 255.0
        self.light = p.get("light", [0.22, 0.2])
        self.wref = p.get("wref", 0.6)
        self.margin = 0.06
        out = cfg.get("output", {"width": 1920, "height": 1080})
        self.vy_k = cfg["tapestry"]["height"] / cfg["tapestry"]["width"] * out["width"] / out["height"]
        self.hold = hold_time(cfg["beats"])
        self.cache = {}

    def sprite(self, r):
        key = max(2, int(round(r * 4)))
        if key in self.cache:
            return self.cache[key]
        R = key / 4.0
        s = int(math.ceil(R + 2))
        yy, xx = np.mgrid[-s:s + 1, -s:s + 1].astype(np.float32)
        d = np.sqrt(xx * xx + yy * yy)
        if R < 2.2:
            a = np.exp(-(d / max(0.55, R * 0.75)) ** 2)
        else:
            edge = np.clip(R - d + 0.5, 0, 1)
            rim = 0.7 + 0.3 * np.clip((d - 0.55 * R) / (0.45 * R), 0, 1)
            a = edge * rim * (2.2 / R) ** 0.4
        img = (a[..., None] * self.col[None, None, :] * 255.0).astype(np.float32)
        self.cache[key] = img
        return img

    def apply(self, img, t, cam):
        if not self.on:
            return img
        t = min(t, self.hold)
        x, y, w = cam[:3]
        m = self.margin
        u = np.mod(self.X + self.vx * t + 0.012 * np.sin(self.fq[:, 0] * t + self.ph[:, 0]) - x / self.wref * self.par, 1.0)
        v = np.mod(self.Y + self.vy * t + 0.01 * np.sin(self.fq[:, 1] * t + self.ph[:, 1]) - y * self.vy_k / self.wref * self.par, 1.0)
        tw = 0.55 + 0.45 * np.sin(self.fq[:, 2] * 6.0 * t + self.ph[:, 2])
        lx, ly = self.light
        beam = np.clip(1.15 - 1.3 * np.hypot(u - lx, (v - ly) * 0.8), 0.15, 1.0)
        bright = self.level * self.lum * tw * beam / 255.0
        H, W = img.shape[:2]
        px = (u * (1 + 2 * m) - m) * W
        py = (v * (1 + 2 * m) - m) * H
        out = img
        for k in np.argsort(self.z)[::-1]:
            if bright[k] < 0.004:
                continue
            spr = self.sprite(self.rad[k])
            s = spr.shape[0] // 2
            cx, cy = int(round(px[k])), int(round(py[k]))
            x0, y0, x1, y1 = cx - s, cy - s, cx + s + 1, cy + s + 1
            sx0, sy0 = max(0, -x0), max(0, -y0)
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            patch = spr[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
            roi = out[y0:y1, x0:x1]
            cv2.add(roi, cv2.convertScaleAbs(patch, alpha=float(bright[k])), dst=roi)
        return out
