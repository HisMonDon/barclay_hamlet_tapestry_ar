import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np

BIG = 1.0e4
SQ2 = math.sqrt(2.0)
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))
_SHEARS = {}


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


def warped_noise(w, h, cells, octaves, warp, seed):
    base = fractal_noise(w, h, cells, octaves, 0.55, seed)
    wx = fractal_noise(w, h, max(2, cells / 2), 3, 0.5, seed + 101)
    wy = fractal_noise(w, h, max(2, cells / 2), 3, 0.5, seed + 202)
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    k = warp * w
    out = cv2.remap(base, xs + k * wx, ys + k * wy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    out -= out.mean()
    return out / max(1e-6, float(np.abs(out).max()))


def streaks(w, h, along, seed):
    rng = np.random.default_rng(seed)
    if along == "x":
        g = rng.random((h, max(2, w // 24)), dtype=np.float32)
    else:
        g = rng.random((max(2, h // 24), w), dtype=np.float32)
    out = cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
    out -= out.mean()
    return out / max(1e-6, float(np.abs(out).max()))


def thread_texture(w, h, pitch, seed):
    rng = np.random.default_rng(seed)
    ny, nx = int(h / pitch) + 2, int(w / pitch) + 2
    rows = rng.normal(0.0, 1.0, ny).astype(np.float32)
    cols = rng.normal(0.0, 1.0, nx).astype(np.float32)
    y = np.arange(h, dtype=np.float32) / pitch
    x = np.arange(w, dtype=np.float32) / pitch
    ry = np.clip(np.floor(y).astype(int), 0, ny - 1)
    rx = np.clip(np.floor(x).astype(int), 0, nx - 1)
    py = 0.5 + 0.5 * np.cos(2 * np.pi * (y - ry - 0.5))
    px = 0.5 + 0.5 * np.cos(2 * np.pi * (x - rx - 0.5))
    weft = (rows[ry] * 0.7 + 0.6 * (1 - py))[:, None]
    warp = (cols[rx] * 0.7 + 0.6 * (1 - px))[None, :]
    fine = fractal_noise(w, h, max(4, w / 40), 2, 0.5, seed + 9)
    out = 0.55 * weft + 0.45 * warp + 0.3 * fine
    out -= out.mean()
    return (out / max(1e-6, float(np.abs(out).max()))).astype(np.float32)


def raster(poly, w, h, feather=0.0, offset=(0, 0)):
    m = np.zeros((h, w), np.float32)
    pts = np.array([[(x * w + offset[0]) * 16, (y * h + offset[1]) * 16] for x, y in poly], np.float64)
    cv2.fillPoly(m, [np.round(pts).astype(np.int32)], 1.0, cv2.LINE_AA, 4)
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), feather)
    return m


def _shear(h, w, kind):
    key = (h, w, kind)
    if key in _SHEARS:
        return _SHEARS[key]
    i = np.arange(h)[:, None]
    q = np.arange(w + h - 1)[None, :]
    j = q - (h - 1) + i if kind == 0 else q - i
    valid = (j >= 0) & (j < w)
    idx = np.where(valid, i * w + j, h * w)
    _SHEARS[key] = (idx, valid, idx[valid])
    return _SHEARS[key]


def _sweep(G, C, mult):
    step = np.empty_like(C)
    step[0] = 0.0
    np.add(C[1:], C[:-1], out=step[1:])
    step[1:] *= 0.5 * mult
    S = np.cumsum(step, axis=0)
    G -= S
    np.minimum.accumulate(G, axis=0, out=G)
    G += S
    return G


def _axis(D, C, axis, rev, mult):
    if axis == 1:
        D, C = D.T, C.T
    if rev:
        D, C = D[::-1], C[::-1]
    G = _sweep(np.ascontiguousarray(D), np.ascontiguousarray(C), mult)
    if rev:
        G = G[::-1]
    if axis == 1:
        G = G.T
    return G


def _diag(D, C, kind, rev, mult):
    h, w = D.shape
    idx, valid, flat = _shear(h, w, kind)
    Dp = np.append(D.ravel(), np.inf)
    Cp = np.append(C.ravel(), 0.0)
    G = Dp[idx]
    Cg = Cp[idx]
    if rev:
        G, Cg = G[::-1], Cg[::-1]
    G = _sweep(np.ascontiguousarray(G), np.ascontiguousarray(Cg), mult)
    if rev:
        G = G[::-1]
    out = D.ravel().copy()
    out[flat] = G[valid]
    return out.reshape(h, w)


def geodesic(D, C, ell, aniso=0.0, flow=(0.0, 0.0), rounds=5, tol=1e-6):
    fx, fy = flow
    for r in range(rounds):
        before = float(np.sum(np.where(np.isfinite(D), D, 0.0)))
        for dy, dx in DIRS:
            n = math.hypot(dx, dy)
            m = ell * n * (1.0 + (aniso if dx and dy else 0.0)) * max(0.15, 1.0 - (dx * fx + dy * fy) / n)
            if dx == 0:
                D = _axis(D, C, 0, dy < 0, m)
            elif dy == 0:
                D = _axis(D, C, 1, dx < 0, m)
            else:
                kind = 0 if dx == dy else 1
                D = _diag(D, C, kind, dy < 0, m)
        after = float(np.sum(np.where(np.isfinite(D), D, 0.0)))
        if r and abs(after - before) <= tol * max(1.0, abs(after)):
            break
    return D


def line_map(scan, w, h, cfg):
    gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
    k = max(3, int(round(0.006 * scan.shape[1])) | 1)
    hat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    line = np.clip(hat.astype(np.float32) / cfg.get("line_contrast", 70.0), 0.0, 1.0)
    line = cv2.resize(line, (w, h), interpolation=cv2.INTER_AREA)
    return np.clip(cv2.GaussianBlur(line, (0, 0), 0.6) * 1.4, 0.0, 1.0)


def gold_map(scan, w, h, cfg):
    hsv = cv2.cvtColor(scan, cv2.COLOR_BGR2HSV).astype(np.float32)
    hue, sat, val = hsv[..., 0], hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    lo, hi = cfg.get("gold_hue", [12, 30])
    g = ((hue >= lo) & (hue <= hi)).astype(np.float32) * np.clip((sat - 0.35) / 0.2, 0, 1) * np.clip((val - 0.45) / 0.15, 0, 1)
    return cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA)


class FieldBuilder:
    def __init__(self, cfg, iw, ih, scan=None, cache_dir=None):
        self.cfg = cfg
        self.ink = cfg["ink"]
        self.iw, self.ih = iw, ih
        self.aspect = cfg["tapestry"]["height"] / cfg["tapestry"]["width"]
        self.ell = 1.0 / iw
        self.scan = scan
        self.cache_dir = Path(cache_dir) if cache_dir else None
        c = self.ink.get("cost", {})
        self.cp = c
        seed = c.get("seed", 7)
        lobes = warped_noise(iw, ih, c.get("lobe_cells", 3.5), 3, c.get("warp", 0.035) * 2, seed + 5)
        noise = warped_noise(iw, ih, c.get("cells", 9), c.get("octaves", 5), c.get("warp", 0.035), seed)
        fib = 0.5 * streaks(iw, ih, "x", seed + 1) + 0.5 * streaks(iw, ih, "y", seed + 2)
        px_mm = iw / cfg["board"]["drawing_mm"][0]
        self.weave = thread_texture(iw, ih, max(2.2, c.get("weave_mm", 1.4) * px_mm), seed + 3)
        cost = np.exp(c.get("lobes", 0.6) * lobes + c.get("noise", 0.55) * noise + c.get("fibre", 0.3) * fib
                      + c.get("weave", 0.12) * self.weave)
        self.line = line_map(scan, iw, ih, c) if scan is not None else np.zeros((ih, iw), np.float32)
        self.gold = gold_map(scan, iw, ih, c) if scan is not None else np.zeros((ih, iw), np.float32)
        slow, fast = c.get("blank_slow", 1.12), c.get("line_fast", 0.4)
        cost *= slow - (slow - fast) * self.line
        cost /= float(np.median(cost))
        self.ghost = self._ghost_mask()
        cost[self.ghost > 0.5] = BIG
        self.cost = cost.astype(np.float64)
        self.fibre = fib
        self.noise = noise

    def _ghost_mask(self):
        g = self.cfg.get("ghost")
        if not g:
            return np.zeros((self.ih, self.iw), np.float32)
        poly = self.cfg["regions"][g["region"]]["polygon"]
        return raster(poly, self.iw, self.ih, g.get("feather", 0.0) * self.iw)

    def key(self, seeds, tides):
        h = hashlib.sha1()
        h.update(json.dumps([seeds, tides, self.ink.get("cost"), self.ink.get("gravity"), self.cfg.get("ghost"),
                             self.cfg.get("regions", {}).get(self.cfg.get("ghost", {}).get("region", ""), None),
                             self.iw, self.ih, 3], sort_keys=True, default=str).encode())
        if self.scan is not None:
            h.update(cv2.resize(self.scan, (64, 64), interpolation=cv2.INTER_AREA).tobytes())
        return h.hexdigest()[:16]

    def box(self, s):
        pts = [(s["x"], s["y"])] + [tuple(p) for p in s.get("path", [])]
        r = s.get("radius", math.inf)
        reach = r * 4.0 if math.isfinite(r) else 2.0
        x0 = min(p[0] for p in pts) - reach
        x1 = max(p[0] for p in pts) + reach
        y0 = min(p[1] for p in pts) - reach / self.aspect
        y1 = max(p[1] for p in pts) + reach / self.aspect
        if "bounds" in s:
            bx0, by0, bx1, by1 = s["bounds"]
            x0, y0, x1, y1 = max(x0, bx0), max(y0, by0), min(x1, bx1), min(y1, by1)
        c0, c1 = max(0, int(math.floor(x0 * self.iw)) - 2), min(self.iw, int(math.ceil(x1 * self.iw)) + 2)
        r0, r1 = max(0, int(math.floor(y0 * self.ih)) - 2), min(self.ih, int(math.ceil(y1 * self.ih)) + 2)
        return r0, r1, c0, c1

    def seed_field(self, s, t0):
        r0, r1, c0, c1 = self.box(s)
        if r1 - r0 < 2 or c1 - c0 < 2:
            return None
        h, w = r1 - r0, c1 - c0
        C = self.cost[r0:r1, c0:c1].copy()
        if "clip" in s:
            m = raster(s["clip"], self.iw, self.ih)[r0:r1, c0:c1]
            C[m < 0.5] = BIG
        if s.get("path"):
            ch = np.zeros((h, w), np.uint8)
            pts = np.array([[(x * self.iw - c0) * 16, (y * self.ih - r0) * 16] for x, y in s["path"]])
            width = max(2, int(round(s.get("width", 0.005) * self.iw)))
            cv2.polylines(ch, [np.round(pts).astype(np.int32)], False, 255, width, cv2.LINE_AA, 4)
            k = s.get("channel", 0.2)
            C *= 1.0 - (1.0 - k) * (ch.astype(np.float64) / 255.0)
        D = np.full((h, w), np.inf)
        sx, sy = s["x"] * self.iw - c0, s["y"] * self.ih - r0
        rr = max(1.0, s.get("r0", 0.002) * self.iw)
        yy, xx = np.ogrid[0:h, 0:w]
        src = (xx - sx) ** 2 + (yy - sy) ** 2 <= rr * rr
        if not src.any():
            iy, ix = int(min(h - 1, max(0, round(sy)))), int(min(w - 1, max(0, round(sx))))
            src[iy, ix] = True
        D[src & (C < BIG)] = 0.0
        g = self.ink.get("gravity", 0.0) + s.get("gravity", 0.0)
        fl = s.get("flow", [0.0, 0.0])
        D = geodesic(D, C, self.ell, self.cp.get("aniso", 0.12), (fl[0], fl[1] + g))
        D[C >= BIG] = np.inf
        r = s.get("radius", math.inf)
        D[D > (r if math.isfinite(r) else 50.0)] = np.inf
        T = t0 + D / s["speed"]
        return (r0, r1, c0, c1), T

    def tide_field(self, T, tide, t0):
        src = np.isfinite(T) & (T <= t0)
        if not src.any():
            return None
        D = np.where(src, 0.0, np.inf)
        D = geodesic(D, self.cost, self.ell, self.cp.get("aniso", 0.12), (0.0, self.ink.get("gravity", 0.0) + tide.get("gravity", 0.0)))
        D[self.cost >= BIG] = np.inf
        D[D > 50.0] = np.inf
        return t0 + D / tide["speed"]

    def arrival(self, seeds, tides):
        key = self.key([[s, t] for s, t in seeds], [[d, t] for d, t in tides])
        path = self.cache_dir / f"arrival_{key}.npy" if self.cache_dir else None
        if path is not None and path.exists():
            try:
                return np.load(str(path))
            except Exception:
                pass
        T = np.full((self.ih, self.iw), np.inf)
        for s, t0 in sorted(seeds, key=lambda x: x[1]):
            res = self.seed_field(s, t0)
            if res is None:
                continue
            (r0, r1, c0, c1), Ts = res
            np.minimum(T[r0:r1, c0:c1], Ts, out=T[r0:r1, c0:c1])
        for tide, t0 in sorted(tides, key=lambda x: x[1]):
            Tt = self.tide_field(T, tide, t0)
            if Tt is not None:
                np.minimum(T, Tt, out=T)
        T = T.astype(np.float32)
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(str(path), T)
            except OSError:
                pass
        return T
