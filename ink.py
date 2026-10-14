import math

import cv2
import numpy as np

from fields import FieldBuilder, fractal_noise, raster, warped_noise

TILE = 64
APRON = 2
BIN = 0.05


def hex_bgr(h):
    h = h.lstrip("#")
    return np.array([int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)], np.float32)


class Canvas:
    def __init__(self, tw, th, scale, margin):
        self.tw, self.th = tw, th
        self.scale = scale
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

    def px(self, x, y):
        return x * self.w0 + self.pad, y * self.h0 + self.pad


def smoothstep(e0, e1, x):
    p = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return p * p * (3 - 2 * p)


def tiles(a, ny, nx, fill):
    h, w = a.shape[:2]
    S = TILE + 2 * APRON
    big = np.full((ny * TILE + 2 * APRON, nx * TILE + 2 * APRON) + a.shape[2:], fill, a.dtype)
    big[APRON:APRON + h, APRON:APRON + w] = a
    big[:APRON, APRON:APRON + w] = a[:1]
    big[APRON + h:APRON + h + APRON, APRON:APRON + w] = a[-1:]
    big[:, :APRON] = big[:, APRON:APRON + 1]
    big[:, APRON + w:APRON + w + APRON] = big[:, APRON + w - 1:APRON + w]
    out = np.empty((ny * nx, S, S) + a.shape[2:], a.dtype)
    for k in range(ny * nx):
        r, c = divmod(k, nx)
        out[k] = big[r * TILE:r * TILE + S, c * TILE:c * TILE + S]
    return out


class InkEngine:
    def __init__(self, cfg, timeline, scan=None, extra_seeds=(), cache_dir=None):
        self.cfg = cfg
        ink = cfg["ink"]
        self.p = ink
        self.timeline = timeline
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        margin = cfg.get("overlay", {}).get("margin", 0.0)
        self.ink_canvas = Canvas(self.tw, self.th, ink.get("resolution", 0.75), margin)
        self.iw, self.ih = self.ink_canvas.w0, self.ink_canvas.h0
        self.aspect = self.th / self.tw
        seeds = list(timeline.seeds()) + list(extra_seeds)
        seeds = [(s, t) for s, t in seeds if not s.get("lift")]
        seeds += self._drips(seeds)
        tides = [(d, t) for d, t, _ in timeline.items("tides")]
        fb = FieldBuilder(cfg, self.iw, self.ih, scan, cache_dir)
        self.fields = fb
        self.T = fb.arrival(seeds, tides)
        self.seeds = seeds
        self.end = timeline.end
        self._static(fb)
        self.set_tone(cfg, ink["tone"])
        self.mult = np.full((self.ink_canvas.h, self.ink_canvas.w, 3), 255, np.uint8)
        self._cache_t = None

    def _drips(self, seeds):
        out = []
        for s, t0 in seeds:
            d = s.get("drips")
            if not d:
                continue
            rng = np.random.default_rng(d.get("seed", 0))
            r = s.get("radius", 0.02)
            for k in range(d.get("count", 2)):
                x = s["x"] + rng.uniform(-0.8, 0.8) * r
                y = s["y"] + rng.uniform(0.2, 0.9) * r / self.aspect
                L = d.get("length", 0.05) * rng.uniform(0.6, 1.1)
                n = max(3, int(L / 0.008))
                pts = [[x, y]]
                for x_ in range(n):
                    x += rng.normal(0.0, 0.0012)
                    y += L / n / self.aspect
                    pts.append([x, y])
                drip = {"x": pts[0][0], "y": pts[0][1], "speed": s["speed"] * d.get("speed", 1.6), "path": pts,
                        "channel": d.get("channel", 0.12), "width": d.get("width", 0.004),
                        "radius": L * d.get("channel", 0.12) * 1.15 + d.get("pool", 0.01),
                        "gravity": 0.8}
                if "clip" in s:
                    drip["clip"] = s["clip"]
                    drip["bounds"] = s["bounds"]
                out.append((drip, t0 + d.get("t", 0.3) + rng.uniform(0.0, 0.3)))
        return out

    def _static(self, fb):
        p = self.p
        iw, ih = self.iw, self.ih
        T = self.T
        fin = np.isfinite(T)
        self.ny, self.nx = (ih + TILE - 1) // TILE, (iw + TILE - 1) // TILE
        edge = p.get("edge_s", 0.12)
        halo = p.get("halo", {})
        fing = p.get("finger", {})
        self.lead = halo.get("lead_s", 0.45)
        self.famp = fing.get("amp_s", 0.3)
        self.omega = fing.get("omega", 0.6)
        dens = p.get("density", {})
        self.dry_s = dens.get("dry_s", 3.0)
        rim = p.get("rim", {})
        self.age_min = -(self.lead + self.famp + edge + 0.05)
        self.age_max = max(self.dry_s * 1.7, rim.get("stall_s", 0.7) + rim.get("build_s", 1.6) + 0.2)
        self.K = 255.0 / (self.age_max - self.age_min)
        self.B = -self.age_min * self.K
        base = np.where(fin, self.B - T * self.K, -1e4).astype(np.float32)
        seed = fing.get("seed", 9)
        n1 = warped_noise(iw, ih, fing.get("cells", 70), 2, 0.02, seed)
        n2 = warped_noise(iw, ih, fing.get("cells", 70), 2, 0.02, seed + 1)
        Tf = np.where(fin, T, 1e4).astype(np.float32)
        wpx = max(1, int(round(rim.get("width", 2.5) * iw / 960.0)))
        s0, build = rim.get("stall_s", 0.7), rim.get("build_s", 1.6)
        rimv = np.zeros((ih, iw), np.float32)
        for r, wgt in ((1, 0.6), (wpx, 0.4)):
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
            stall = cv2.dilate(Tf, k) - Tf
            rimv += wgt * np.clip((stall - s0) / build, 0.0, 1.0)
        rimv *= fin * rim.get("alpha", 0.55)
        rimv = cv2.GaussianBlur(rimv, (0, 0), 0.6)
        gx = cv2.Sobel(Tf, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(Tf, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx * gx + gy * gy) + 1e-6
        L = np.array(self.cfg.get("weeds", {}).get("light", [-0.6, -0.8]), np.float32)
        L /= np.linalg.norm(L)
        cosl = np.clip((gx * L[0] + gy * L[1]) / mag, 0.0, 1.0)
        cosl[(mag > 50.0) | ~fin] = 0.0
        tex = p.get("texture", {})
        dm = 1.0 + tex.get("grain", 0.08) * fb.weave + tex.get("fibre", 0.06) * fb.fibre + 0.05 * fb.noise
        dm *= 1.0 - p.get("gold_resist", 0.45) * fb.gold
        self.dm_scale = 200.0
        self.soften = p.get("soften_px", 0.7)
        self._ghost(fb)
        ny, nx = self.ny, self.nx
        self.t_base = tiles(base, ny, nx, -1e4)
        self.t_n1 = tiles(n1, ny, nx, 0.0)
        self.t_n2 = tiles(n2, ny, nx, 0.0)
        self.t_rim = tiles(np.clip(rimv * 255, 0, 255).astype(np.uint8), ny, nx, 0)
        self.t_cos = tiles(np.clip(cosl * 255, 0, 255).astype(np.uint8), ny, nx, 0)
        dm8 = np.clip(dm * self.dm_scale, 0, 255).astype(np.uint8)
        self.t_dm = tiles(cv2.merge([dm8, dm8, dm8]), ny, nx, int(self.dm_scale))
        self.boxes = []
        for k in range(ny * nx):
            r, c = divmod(k, nx)
            r0, c0 = r * TILE, c * TILE
            r1, c1 = min(ih, r0 + TILE), min(iw, c0 + TILE)
            self.boxes.append((r0, r1, c0, c1))
        self._activity(T)

    def _ghost(self, fb):
        g = self.cfg.get("ghost")
        self.has_ghost = False
        self.t_gbase = None
        if not g:
            return
        ev = [(e, t) for e, t, _ in self.timeline.items("events") if e.get("kind") == "grey"]
        if not ev:
            return
        e, t0 = ev[0]
        poly = self.cfg["regions"][g["region"]]["polygon"]
        mask = raster(poly, self.iw, self.ih, g.get("feather", 0.003) * self.iw)
        ys = np.array([p[1] for p in poly])
        y0, y1 = ys.min(), ys.max()
        yy = (np.arange(self.ih, dtype=np.float32)[:, None] + 0.5) / self.ih
        frac = np.clip((yy - y0) / max(1e-6, y1 - y0), 0, 1)
        nz = fractal_noise(self.iw, self.ih, 30, 3, 0.5, 31)
        dur = e.get("dur", 1.6)
        Tg = t0 + dur * (0.75 * frac + 0.15 * (nz + 1) / 2)
        self.g_ramp = e.get("ramp", 0.6)
        self.gK = 255.0 / (self.g_ramp + 0.1)
        gb = np.where(mask > 0.02, -Tg * self.gK, -1e4).astype(np.float32)
        tint = hex_bgr(g.get("tint", "#2a74e6"))
        cap = g.get("cap", 0.35)
        lut = np.empty((256, 3), np.float32)
        for i in range(256):
            a = cap * smoothstep(0.0, 1.0, i / 255.0 * (self.g_ramp + 0.1) / self.g_ramp)
            lut[i] = 255.0 * (1.0 - a * (1.0 - tint / 255.0))
        self.g_lut = np.clip(lut + 0.5, 0, 255).astype(np.uint8).reshape(1, 256, 3)
        m8 = (np.clip(mask, 0, 1) * 255).astype(np.uint8)
        self.t_gbase = tiles(gb, self.ny, self.nx, -1e4)
        self.t_gmask = tiles(cv2.merge([m8, m8, m8]), self.ny, self.nx, 0)
        self.g_tiles = np.zeros(self.ny * self.nx, bool)
        self.g_tiles[np.unique(np.nonzero(self.t_gmask[..., 0].reshape(self.ny * self.nx, -1).max(axis=1))[0])] = True
        self.g_window = (t0 - 0.1, t0 + dur + self.g_ramp + 0.2)
        self.has_ghost = True

    def _activity(self, T):
        nb = int(math.ceil((self.end + 1.0) / BIN)) + 2
        self.nb = nb
        n = self.ny * self.nx
        fin = np.isfinite(T)
        slack = 0.45 * self.famp
        lo = np.where(fin, T + self.age_min - slack, 1e6).astype(np.float32)
        hi = np.where(fin, T + self.age_max + slack, -1e6).astype(np.float32)
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * APRON + 1, 2 * APRON + 1))
        lo = cv2.erode(lo, k)
        hi = cv2.dilate(hi, k)
        ys, xs = np.nonzero(hi > -1e5)
        tid = (ys // TILE) * self.nx + xs // TILE
        s = np.clip(np.floor(lo[ys, xs].astype(np.float64) / BIN).astype(np.int64) - 1, 0, nb - 1)
        e = np.clip(np.ceil(hi[ys, xs].astype(np.float64) / BIN).astype(np.int64) + 1, 0, nb - 1)
        diff = np.zeros((n, nb + 1), np.int32)
        np.add.at(diff, (tid, s), 1)
        np.add.at(diff, (tid, e + 1), -1)
        act = np.cumsum(diff, axis=1)[:, :nb] > 0
        if self.has_ghost:
            a, b = self.g_window
            act[self.g_tiles, max(0, int(a / BIN)):min(nb, int(math.ceil(b / BIN)) + 1)] = True
        change = np.zeros_like(act)
        change[:, 1:] = act[:, 1:] != act[:, :-1]
        self.act = act
        self.epoch = np.cumsum(change, axis=1).astype(np.int32)
        self.done = np.full(n, -1, np.int32)

    def set_tone(self, cfg, name):
        p = self.p
        tone = {k: hex_bgr(v) / 255.0 for k, v in p["tones"][name].items()}
        wet, dry, stain = tone.get("wet", tone.get("front")), tone.get("dry", tone.get("deep")), tone["stain"]
        rimc = tone.get("rim", wet)
        dens = p.get("density", {})
        dw, dd = dens.get("wet", 0.965), dens.get("dry", 0.93)
        halo = p.get("halo", {})
        ha = halo.get("alpha", 0.2)
        edge = p.get("edge_s", 0.12)
        rim = p.get("rim", {})
        a = self.age_min + (np.arange(256, dtype=np.float32) + 0.5) / self.K
        front = smoothstep(-edge, edge, a)
        hal = ha * np.clip((a + self.lead) / self.lead, 0, 1) ** 1.5 * (1 - front)
        dried = 1.0 - np.exp(-np.maximum(a, 0) / self.dry_s)
        d = front * (dw + (dd - dw) * dried)
        col = wet[None, :] + (dry - wet)[None, :] * dried[:, None]
        m = (1 - hal[:, None] * (1 - stain[None, :])) * (1 - d[:, None] * (1 - col))
        self.lut = np.clip(m * 255 + 0.5, 0, 255).astype(np.uint8).reshape(1, 256, 3)
        s0, build = rim.get("stall_s", 0.7), rim.get("build_s", 1.6)
        self.rim_lut = np.clip(255 * np.clip((a - s0) / build, 0, 1), 0, 255).astype(np.uint8).reshape(1, 256)
        self.rim_k = float(1.0 - rimc.mean())
        gl = p.get("gloss", 0.12) * front * np.exp(-np.maximum(a, 0) / 0.45) * (1 - dried)
        self.gloss_lut = np.clip(gl * 255, 0, 255).astype(np.uint8).reshape(1, 256)
        self.tone = name
        self.done[:] = -1
        self._cache_t = None

    def mult_to_tapestry(self):
        return self.ink_canvas.to_tapestry()

    def _shade(self, idx_tiles, t):
        k = len(idx_tiles)
        S = TILE + 2 * APRON
        shp = (k * S, S)
        ph = self.omega * t
        F = self.famp * self.K
        acc = cv2.addWeighted(self.t_n1[idx_tiles].reshape(shp), F * math.cos(ph),
                              self.t_n2[idx_tiles].reshape(shp), F * math.sin(ph), t * self.K)
        acc = cv2.add(acc, self.t_base[idx_tiles].reshape(shp))
        np.clip(acc, 0, 255, out=acc)
        idx = acc.astype(np.uint8)
        m = cv2.LUT(cv2.merge([idx, idx, idx]), self.lut)
        r = cv2.min(self.t_rim[idx_tiles].reshape(shp), cv2.LUT(idx, self.rim_lut))
        rf = cv2.subtract(np.full(shp, 255, np.uint8), cv2.multiply(r, np.full(shp, self.rim_k, np.float32), dtype=cv2.CV_8U))
        m = cv2.multiply(m, cv2.merge([rf, rf, rf]), scale=1.0 / 255.0)
        inv = cv2.subtract(np.full_like(m, 255), m)
        inv = cv2.multiply(inv, self.t_dm[idx_tiles].reshape(shp + (3,)), scale=1.0 / self.dm_scale)
        m = cv2.subtract(np.full_like(m, 255), inv)
        g = cv2.multiply(cv2.LUT(idx, self.gloss_lut), self.t_cos[idx_tiles].reshape(shp), scale=1.0 / 255.0)
        m = cv2.add(m, cv2.merge([g, g, g]))
        if self.has_ghost:
            gsel = self.g_tiles[idx_tiles]
            if gsel.any():
                sub = np.nonzero(gsel)[0]
                gt = idx_tiles[sub]
                gs = (len(gt) * S, S)
                ga = cv2.add(self.t_gbase[gt].reshape(gs), float(t * self.gK))
                np.clip(ga, 0, 255, out=ga)
                gi = ga.astype(np.uint8)
                gl = cv2.LUT(cv2.merge([gi, gi, gi]), self.g_lut)
                gl = cv2.subtract(np.full_like(gl, 255), cv2.multiply(cv2.subtract(np.full_like(gl, 255), gl),
                                  self.t_gmask[gt].reshape(gs + (3,)), scale=1.0 / 255.0))
                m3 = m.reshape(k, S, S, 3)
                part = m3[sub].reshape(gs + (3,))
                m3[sub] = cv2.multiply(part, gl, scale=1.0 / 255.0).reshape(len(gt), S, S, 3)
                m = m3.reshape(shp + (3,))
        if self.soften > 0:
            m = cv2.GaussianBlur(m, (3, 3), self.soften)
        return m.reshape(k, S, S, 3)[:, APRON:APRON + TILE, APRON:APRON + TILE]

    def update(self, t):
        b = min(self.nb - 1, max(0, int(t / BIN)))
        ep = self.epoch[:, b]
        need = self.act[:, b] | (ep != self.done)
        idx = np.nonzero(need)[0]
        if len(idx):
            res = self._shade(idx, t)
            pad = self.ink_canvas.pad
            for j, k in enumerate(idx):
                r0, r1, c0, c1 = self.boxes[k]
                self.mult[pad + r0:pad + r1, pad + c0:pad + c1] = res[j, :r1 - r0, :c1 - c0]
            self.done[idx] = ep[idx]
        return len(idx)

    def darkness(self, t):
        return np.clip(1.0 - self.mult[self.ink_canvas.inner].mean(axis=2) / 255.0, 0, 1)

    def layer(self, t):
        if self._cache_t != t:
            self.update(t)
            self._cache_t = t
        return self.mult
