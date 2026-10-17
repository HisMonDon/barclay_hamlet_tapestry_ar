import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np

from config import read, resolve
from fields import BIG, geodesic, line_map, raster, ridged, warped_noise
from ink import Canvas, hex_bgr, smoothstep
from serpent import Route, Serpent, travel

ORDER = ("breach", "flood", "blink", "recede")
FIELDS = ("tin", "tout", "vd", "vc", "tc")
VERSION = 2


def ease_flood(p):
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def ease_recede(p):
    return 1 - (1 - p) ** 2


def load_art(cfg, key="corrupt"):
    rel = cfg.get("assets", {}).get(key)
    if not rel:
        return None
    p = resolve(cfg, rel)
    if not p.exists():
        return None
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read reveal art: {p}. Rerun python -m tools.align_layers.")
    return img


def load_panels(cfg):
    rel = cfg.get("assets", {}).get("panels")
    p = resolve(cfg, rel) if rel else None
    if p is None or not p.exists():
        raise ValueError("Reveal panels are missing: run python -m tools.align_layers")
    return {x["id"]: x for x in read(p)["panels"]}


def lut(f, lo, hi):
    v = lo + (np.arange(256, dtype=np.float64) + 0.5) * (hi - lo) / 255.0
    return np.clip(np.round(f(v) * 255), 0, 255).astype(np.uint8)


def index(base, add):
    return np.clip(base + np.float32(add), 0, 255).astype(np.uint8)


def scale(a, m):
    return cv2.multiply(a, m, scale=1 / 255)


def three(img):
    return cv2.merge([img, img, img])


def union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def pack(k, arrays):
    return {f"{k}_{x}": arrays[x] for x in arrays}


def unpack(cached, k):
    if cached is None or not all(f"{k}_{x}" in cached for x in FIELDS):
        return None
    return {x: cached[f"{k}_{x}"] for x in FIELDS}


class RevealEngine:
    def __init__(self, cfg, timeline, art, cache_dir=None):
        self.cfg = cfg
        r = cfg["reveal"]
        self.p = r
        tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        self.aspect = th / tw
        self.canvas = Canvas(tw, th, cfg.get("reveal_width", 550) / tw, r.get("margin", 0.0))
        self.w, self.h = self.canvas.w, self.canvas.h
        pal = r.get("palette", {})
        self.ink = hex_bgr(pal.get("ink_rgb", "#0E0B10")) / 255.0
        self.edge = hex_bgr(pal.get("ink_edge", "#1C1620")) / 255.0
        self.ghost_light = hex_bgr(pal.get("ghost_light", "#DCE3EA"))
        self.alpha = r.get("ink_alpha", 0.95)
        self.front, self.rw, self.lead = r.get("front", 0.08), r.get("reveal_width", 0.1), r.get("lead", 0.06)
        self.lag_s, self.u0 = r.get("lag_s", 0.15), r.get("breach_u", 0.03)
        self.damp = r.get("damp", 0.15)
        v = r.get("veins", {})
        self.vein = v
        self.vein_alpha, self.breath, self.period = v.get("alpha", 0.55), v.get("breath", 0.06), v.get("period_s", 4.0)
        self.panels = self._schedule(timeline, load_panels(cfg))
        self.route = None
        if r.get("serpent") is not None:
            swim = {x["id"]: (self.u0, x["U"]) for x in self.panels if x["swim"]}
            self.route = Route(cfg, timeline, [x["geo"]["polygon"] for x in self.panels if x["light"]], swim)
        ghost = cfg.get("regions", {}).get(cfg.get("ghost", {}).get("region", "ghost"))
        self.figure = ghost["polygon"] if ghost else None
        self.lift = r.get("ghost_lift", 0.0)
        self.geo = self._build(art, cache_dir)
        shape = (self.h, self.w)
        self.R8, self.I8, self.D8, self.V8, self.G8, self.L8 = (np.zeros(shape, np.uint8) for x in range(6))
        self.mult, self.Mp, self.Mb = (np.full(shape + (3,), 255, np.uint8) for x in range(3))
        self.add = np.zeros(shape + (3,), np.uint8)
        self.levels = v.get("levels", 16)
        self.ghost_col = self.ghost_light * r.get("ghost_glow", 0.5)
        self.serpent = None
        if self.route is not None:
            self.serpent = Serpent(cfg, self.route, self.canvas, self._exclusion())
        self.keys = [None] * len(self.panels)
        self.mkeys = [None] * len(self.panels)
        self.states = [("dormant", 0.0)] * len(self.panels)
        self.box = None
        self.body = None
        self._t = None
        self._out = None
        self._luts()

    def _schedule(self, timeline, geometry):
        out = []
        for x in timeline.panels():
            spec, times, beat = x
            b = timeline.beats[beat]
            light = spec.get("mode") == "light"
            lag = self._lag(times[2] - times[1])
            U = 1 + self.rw + 0.16 if light else 1 + lag + self.rw
            out.append({"id": spec["id"], "beat": b.get("id"), "spec": spec, "geo": geometry[spec["id"]], "light": light,
                        "swim": spec.get("mode") == "swim", "times": times, "lag": lag, "U": U})
        return out

    def _exclusion(self):
        out = np.zeros((self.h, self.w), np.uint8)
        for x in range(len(self.panels)):
            if self.panels[x]["light"]:
                r0, r1, c0, c1 = self.geo[x]["roi"]
                g = cv2.dilate(np.where(self.geo[x]["own"], 255, 0).astype(np.uint8), np.ones((3, 3), np.uint8))
                np.maximum(out[r0:r1, c0:c1], g, out=out[r0:r1, c0:c1])
        return out

    def _lag(self, flood_s):
        lag = 0.15
        for x in range(6):
            lag = max(0.15, self.front + 0.02, 1.5 * (1 + lag + self.rw) * self.lag_s / max(flood_s, 1e-3))
        return lag

    def _key(self, art):
        h = hashlib.sha1()
        spec = [[x["id"], x["spec"], x["geo"]["polygon"]] for x in self.panels]
        h.update(json.dumps([spec, self.p.get("cost"), self.vein, self.canvas.w0, self.canvas.h0, VERSION], sort_keys=True, default=str).encode())
        h.update(cv2.resize(art, (64, 64), interpolation=cv2.INTER_AREA).tobytes())
        for x in self.panels:
            if x["swim"]:
                xy, u = self.route.swim_track(x["id"])
                h.update(np.round(np.column_stack([xy, u]), 5).tobytes())
                h.update(json.dumps(self.p.get("serpent", {}), sort_keys=True).encode())
        return h.hexdigest()[:16]

    def _build(self, art, cache_dir):
        c = self.canvas
        w0, h0 = c.w0, c.h0
        path = Path(cache_dir) / f"reveal_{self._key(art)}.npz" if cache_dir else None
        cached = None
        if path is not None and path.exists():
            try:
                cached = dict(np.load(str(path)))
            except (OSError, ValueError):
                cached = None
        store = {}
        cost = edge = net = None
        geo = []
        for x in range(len(self.panels)):
            panel = self.panels[x]
            m = raster(panel["geo"]["polygon"], w0, h0, 0.6)
            ys, xs = np.nonzero(m > 0.003)
            r0, r1, c0, c1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
            g = {"inner": (r0, r1, c0, c1), "roi": (r0 + c.pad, r1 + c.pad, c0 + c.pad, c1 + c.pad),
                 "box": (c0 + c.pad, r0 + c.pad, c1 + c.pad, r1 + c.pad)}
            mroi = m[r0:r1, c0:c1]
            g["mask8"] = np.clip(mroi * 255 + 0.5, 0, 255).astype(np.uint8)
            g["own"] = mroi > 0.003
            if panel["light"] and self.figure is not None and self.lift > 0:
                f = raster(self.figure, w0, h0, max(0.6, 0.0025 * w0))[r0:r1, c0:c1]
                g["lift8"] = np.clip(f * self.lift * 255 + 0.5, 0, 255).astype(np.uint8)
            arrays = unpack(cached, x)
            if arrays is None:
                if cost is None:
                    cost, edge = self._cost(art)
                    vc = self.vein
                    net = smoothstep(0.55, 0.85, ridged(w0, h0, vc.get("cells", 26), vc.get("seed", 17), vc.get("sharp", 7.0)))
                arrays = self._fields(panel, (r0, r1, c0, c1), mroi > 0.5, cost[r0:r1, c0:c1], edge[r0:r1, c0:c1], net[r0:r1, c0:c1], x)
            store.update(pack(x, arrays))
            g.update(self._bases(panel, arrays))
            geo.append(g)
        if path is not None and cached is None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(str(path), **store)
            except OSError:
                pass
        return geo

    def _cost(self, art):
        cc = self.p.get("cost", {})
        w0, h0 = self.canvas.w0, self.canvas.h0
        fbm = warped_noise(w0, h0, cc.get("cells", 9), cc.get("octaves", 4), cc.get("warp", 0.03), cc.get("seed", 41))
        edge = line_map(art, w0, h0, cc)
        cost = np.maximum(cc.get("floor", 0.25), 1 + cc.get("fbm", 0.6) * fbm - cc.get("edge", 0.45) * edge)
        return cost.astype(np.float64), edge

    def _sources(self, items, box, inside):
        r0, r1, c0, c1 = box
        w0, h0 = self.canvas.w0, self.canvas.h0
        src = np.zeros(inside.shape, np.uint8)
        rad = max(1, int(round(0.006 * w0)))
        first = None
        for x in items:
            pts = np.array(x, np.float64).reshape(-1, 2) * [w0, h0] - [c0, r0]
            first = pts[0] if first is None else first
            if len(pts) == 1:
                cv2.circle(src, (int(round(pts[0, 0])), int(round(pts[0, 1]))), rad, 255, -1)
            else:
                cv2.polylines(src, [np.round(pts * 16).astype(np.int32)], False, 255, 2, cv2.LINE_8, 4)
        hit = (src > 0) & inside
        if not hit.any():
            ys, xs = np.nonzero(inside)
            k = int(np.argmin((xs - first[0]) ** 2 + (ys - first[1]) ** 2))
            hit[ys[k], xs[k]] = True
        return hit

    def _distance(self, items, box, inside, cost, gravity):
        C = np.where(inside, cost, BIG)
        D = np.where(self._sources(items, box, inside), 0.0, np.inf)
        D = geodesic(D, C, 1.0 / self.canvas.w0, self.p.get("cost", {}).get("aniso", 0.04), (0.0, gravity))
        D[~inside] = np.inf
        return D

    def _wake(self, panel, box, inside, cost):
        """Arrival for a panel the Serpent swims through: each point of its track floods just after the head passes,
        and the ink spreads out from the track over the cost image like a disturbed current."""
        r0, r1, c0, c1 = box
        w0, h0 = self.canvas.w0, self.canvas.h0
        sp = self.p.get("serpent", {})
        k, lag = sp.get("swim_spread", 4.0), sp.get("swim_lag", 0.03)
        xy, u = self.route.swim_track(panel["id"])
        px = np.floor(xy[:, 0] * w0).astype(np.int64) - c0
        py = np.floor(xy[:, 1] * h0).astype(np.int64) - r0
        ok = (px >= 0) & (py >= 0) & (px < inside.shape[1]) & (py < inside.shape[0])
        px, py, u = px[ok], py[ok], u[ok]
        ok = inside[py, px]
        px, py, u = px[ok], py[ok], u[ok]
        D, Z = np.full(inside.shape, np.inf), np.full(inside.shape, np.inf)
        np.minimum.at(D, (py, px), (u + lag) / k)
        Z[py, px] = 0.0
        C = np.where(inside, cost, BIG)
        aniso = self.p.get("cost", {}).get("aniso", 0.04)
        T = geodesic(D, C, 1.0 / w0, aniso) * k
        side = geodesic(Z, C, 1.0 / w0, aniso)
        T[~inside], side[~inside] = np.inf, np.inf
        return T, side

    @staticmethod
    def _normal(D):
        fin = np.isfinite(D)
        top = float(D[fin].max()) if fin.any() else 1.0
        return np.where(fin, D / max(top, 1e-9), 1.0).astype(np.float32)

    def _fields(self, panel, box, inside, cost, edge, net, seed):
        spec = panel["spec"]
        if panel["swim"]:
            tin, side = self._wake(panel, box, inside, cost)
        else:
            entry = spec.get("entry_object") or [[spec["entry"]]]
            tin = self._distance(entry, box, inside, cost, spec.get("gravity", 0.0))
        shape = inside.shape
        empty = np.zeros(shape, np.float32)
        if panel["light"]:
            top = spec["entry"][1] * self.canvas.h0 - box[0]
            fall = np.clip((np.arange(shape[0], dtype=np.float32)[:, None] - top) / max(1.0, shape[0] - top), 0, 1)
            mix = spec.get("fall", 0.6)
            light = (1 - mix) * self._normal(tin) + mix * np.broadcast_to(fall, shape)
            return {"tin": self._normal(np.where(inside, light, np.inf)), "tout": empty, "vd": empty, "vc": empty, "tc": empty}
        tout = self._distance([[spec["exit"]]], box, inside, cost, 0.0)
        v = self.vein
        x0, y0, x1, y1 = panel["geo"]["rect"]
        diag = math.hypot(x1 - x0, (y1 - y0) * self.aspect)
        S = side if panel["swim"] else tin + tout
        fin = np.isfinite(S) & inside
        d = np.where(fin, (S - float(S[fin].min())) / (v.get("spine", 0.035) * diag), 9.0)
        spine = np.clip(1 - d, 0, 1) ** 1.5
        near = np.exp(-np.minimum(d, 9.0) / 3.0)
        vd = np.maximum(spine, 0.75 * net * near * (0.55 + 0.45 * edge)) * inside
        rng = np.random.default_rng(v.get("seed", 17) * 101 + seed)
        poly = np.array(panel["geo"]["polygon"], np.float64) * [self.canvas.w0, self.canvas.h0] - [box[2], box[0]]
        yy, xx = np.indices(shape, dtype=np.float32)
        lo, hi = v.get("corner", [0.04, 0.08])
        dpx = diag * self.canvas.w0
        vc = np.zeros(shape, np.float32)
        tc = np.full(shape, np.inf, np.float32)
        for x in ((0, 0), (1, 0), (1, 1), (0, 1)):
            cx = poly[:, 0].min() if x[0] == 0 else poly[:, 0].max()
            cy = poly[:, 1].min() if x[1] == 0 else poly[:, 1].max()
            k = int(np.argmin((poly[:, 0] - cx) ** 2 + (poly[:, 1] - cy) ** 2))
            px, py = poly[k]
            reach = (lo + (hi - lo) * rng.random()) * dpx
            dist = np.hypot(xx - px, yy - py)
            near_c = np.clip(1 - dist / reach, 0, 1)
            vc = np.maximum(vc, net * (near_c > 0) * (0.45 + 0.55 * near_c))
            tc = np.where(dist < reach, np.minimum(tc, v.get("grow_s", 1.6) * dist / reach), tc)
        vc = vc * inside * 0.8
        tc = np.where(np.isfinite(tc), tc, 0.0)
        blur = lambda a: cv2.GaussianBlur(a.astype(np.float32), (0, 0), 0.6)
        tin = np.where(np.isfinite(tin), np.minimum(tin, 1.0), 1.0).astype(np.float32) if panel["swim"] else self._normal(tin)
        return {"tin": tin, "tout": self._normal(tout), "vd": np.clip(blur(vd), 0, 1),
                "vc": np.clip(blur(vc), 0, 1), "tc": tc.astype(np.float32)}

    def _bases(self, panel, a):
        out = {"tin": a["tin"]}
        if panel["light"]:
            lo, hi = -0.06, self.rw + 0.15
            K = 255.0 / (hi - lo)
            out["base"] = (-a["tin"] * K).astype(np.float32)
            out["range"] = (lo, K)
            return out
        lo, hi = -self.lead, panel["lag"] + self.rw
        K = 255.0 / (hi - lo)
        out["base"] = (-a["tin"] * K).astype(np.float32)
        out["range"] = (lo, K)
        thr = 0.1 + 0.9 * (1.0 - a["tout"])
        K2 = 255.0 / 0.25
        out["base_r"] = (-thr * K2).astype(np.float32)
        out["range_r"] = (-0.1, K2)
        out["vd8"] = np.clip(a["vd"] * 255 + 0.5, 0, 255).astype(np.uint8)
        out["vc8"] = np.clip(a["vc"] * 255 + 0.5, 0, 255).astype(np.uint8)
        K3 = 255.0 / 0.5
        out["base_c"] = (-a["tc"] * K3).astype(np.float32)
        out["creep_end"] = float(a["tc"].max()) + 0.5
        out["vfinal"] = np.maximum(out["vd8"], out["vc8"])
        out["tc"], out["tout"] = a["tc"], a["tout"]
        return out

    def _luts(self):
        ink, edge, al = self.ink, self.edge, self.alpha
        for x in self.panels:
            if x["light"]:
                lo, hi = -0.06, self.rw + 0.15
                x["lut_r"] = lut(lambda v: smoothstep(0.0, self.rw, v), lo, hi)
                x["lut_g"] = lut(lambda v: smoothstep(-0.04, 0.0, v) * (1 - smoothstep(self.rw, self.rw + 0.08, v)), lo, hi)
                continue
            lo, hi, lag = -self.lead, x["lag"] + self.rw, x["lag"]
            x["lut_i"] = lut(lambda v: al * smoothstep(0.0, self.front, v), lo, hi)
            x["lut_d"] = lut(lambda v: smoothstep(-self.lead, 0.0, v), lo, hi)
            x["lut_r"] = lut(lambda v: smoothstep(lag, lag + self.rw, v), lo, hi)
            x["lut_ri"] = lut(lambda v: al * (1 - smoothstep(-0.1, 0.0, v)), -0.1, 0.15)
            x["lut_rd"] = lut(lambda v: 1 - smoothstep(0.0, 0.15, v), -0.1, 0.15)
            x["lut_rv"] = lut(lambda v: smoothstep(0.0, 0.15, v), -0.1, 0.15)
        self.lut_c = lut(lambda v: np.clip(v / 0.5, 0, 1), 0.0, 0.5)
        a = np.arange(256, dtype=np.float64) / 255.0
        col = edge[None, :] + (ink - edge)[None, :] * smoothstep(0.3, 0.9, a / max(al, 1e-6))[:, None]
        self.lut_im = np.clip(255 * (1 - a[:, None] * (1 - col)) + 0.5, 0, 255).astype(np.uint8).reshape(1, 256, 3)
        d = np.clip(255 * (1 - self.damp * a) + 0.5, 0, 255).astype(np.uint8)
        self.lut_dm = np.repeat(d[:, None], 3, axis=1).reshape(1, 256, 3)
        ks = [self.vein_alpha + self.breath * math.sin(2 * math.pi * x / self.levels) for x in range(self.levels)] + [self.vein_alpha]
        self.lut_vs = [np.clip(255 * (1 - a[:, None] * x * (1 - ink[None, :])) + 0.5, 0, 255).astype(np.uint8).reshape(1, 256, 3) for x in ks]

    def phase(self, panel, t):
        times = panel["times"]
        if t < times[0]:
            return "dormant", 0.0
        if t >= times[4]:
            return "settled", t - times[4]
        for x in range(4):
            if t < times[x + 1]:
                return ORDER[x], (t - times[x]) / max(1e-6, times[x + 1] - times[x])
        return "settled", 0.0

    def _render(self, k, phase, p):
        panel, g = self.panels[k], self.geo[k]
        if phase == "dormant":
            key = ("dormant",)
        elif phase == "settled":
            key = ("settled",) if panel["light"] or p >= g["creep_end"] else ("settled", round(p, 4))
        elif phase == "blink":
            key = ("blink",)
        else:
            key = (phase, round(p, 5))
        if key == self.keys[k]:
            return
        self.keys[k] = key
        m8 = g["mask8"]
        zero = np.zeros_like(m8)
        R = I = D = V = G = zero
        if panel["light"]:
            if phase in ("breach", "flood"):
                lo, K = g["range"]
                u = panel["U"] * ease_flood(p) if phase == "flood" else 0.0
                idx = index(g["base"], (u - lo) * K)
                R, G = cv2.LUT(idx, panel["lut_r"]), cv2.LUT(idx, panel["lut_g"])
            elif phase != "dormant":
                R = np.full_like(m8, 255)
        elif phase in ("breach", "flood"):
            lo, K = g["range"]
            u = self.u0 * p if phase == "breach" else self.u0 + (panel["U"] - self.u0) * (travel(p) if panel["swim"] else ease_flood(p))
            idx = index(g["base"], (u - lo) * K)
            I, D = cv2.LUT(idx, panel["lut_i"]), cv2.LUT(idx, panel["lut_d"])
            if phase == "flood":
                R = cv2.LUT(idx, panel["lut_r"])
        elif phase == "blink":
            R, D = np.full_like(m8, 255), np.full_like(m8, 255)
            I = np.full_like(m8, int(round(self.alpha * 255)))
        elif phase == "recede":
            lo, K = g["range_r"]
            idx = index(g["base_r"], (1.15 * ease_recede(p) - lo) * K)
            R = np.full_like(m8, 255)
            I, D = cv2.LUT(idx, panel["lut_ri"]), cv2.LUT(idx, panel["lut_rd"])
            V = scale(g["vd8"], cv2.LUT(idx, panel["lut_rv"]))
        elif phase == "settled":
            R = np.full_like(m8, 255)
            if p >= g["creep_end"]:
                V = g["vfinal"]
            else:
                V = np.maximum(g["vd8"], scale(g["vc8"], cv2.LUT(index(g["base_c"], p * 255.0 / 0.5), self.lut_c)))
        r0, r1, c0, c1 = g["roi"]
        sl = (slice(r0, r1), slice(c0, c1))
        for x in ((self.R8, R), (self.I8, I), (self.D8, D), (self.V8, V)):
            np.copyto(x[0][sl], scale(x[1], m8), where=g["own"])
        if panel["light"]:
            np.copyto(self.G8[sl], scale(G, m8), where=g["own"])
            if "lift8" in g:
                np.copyto(self.L8[sl], scale(scale(R, g["lift8"]), m8), where=g["own"])

    def _level(self, k, t):
        return int(math.floor(self.levels * (t / self.period + 0.37 * k))) % self.levels

    def _panel_mult(self, k, level):
        r0, r1, c0, c1 = self.geo[k]["roi"]
        i, v, d = self.I8[r0:r1, c0:c1], self.V8[r0:r1, c0:c1], self.D8[r0:r1, c0:c1]
        self.Mp[r0:r1, c0:c1] = scale(scale(cv2.LUT(three(i), self.lut_im), cv2.LUT(three(v), self.lut_vs[level])), cv2.LUT(three(d), self.lut_dm))
        if "lift8" in self.geo[k]:
            self.Mp[r0:r1, c0:c1] = scale(self.Mp[r0:r1, c0:c1], three(cv2.bitwise_not(self.L8[r0:r1, c0:c1])))

    def _compose(self, rect, sv):
        x0, y0, x1, y1 = rect
        m = scale(self.Mp[y0:y1, x0:x1], self.Mb[y0:y1, x0:x1])
        a = cv2.add(self._tint(self.G8[y0:y1, x0:x1], self.ghost_col), self._tint(self.L8[y0:y1, x0:x1], self.ghost_light))
        if sv is not None:
            sx0, sy0, sx1, sy1 = sv["roi"]
            ix0, iy0, ix1, iy1 = max(x0, sx0), max(y0, sy0), min(x1, sx1), min(y1, sy1)
            if ix1 > ix0 and iy1 > iy0:
                here = (slice(iy0 - y0, iy1 - y0), slice(ix0 - x0, ix1 - x0))
                there = (slice(iy0 - sy0, iy1 - sy0), slice(ix0 - sx0, ix1 - sx0))
                m[here] = scale(m[here], sv["mult"][there])
                a[here] = cv2.add(a[here], sv["add"][there])
        self.mult[y0:y1, x0:x1] = m
        self.add[y0:y1, x0:x1] = a

    def layers(self, t):
        if self._t == t:
            return self._out
        dirty = []
        box = rbox = abox = None
        for x in range(len(self.panels)):
            panel, g = self.panels[x], self.geo[x]
            phase, p = self.phase(panel, t)
            self.states[x] = (phase, p)
            self._render(x, phase, p)
            level = self._level(x, t) if phase in ("recede", "settled") and not panel["light"] else 0
            if (self.keys[x], level) != self.mkeys[x]:
                self.mkeys[x] = (self.keys[x], level)
                self._panel_mult(x, level)
                dirty.append(g["box"])
            if phase != "dormant":
                box = union(box, g["box"])
            if phase not in ("dormant", "breach"):
                rbox = union(rbox, g["box"])
            if panel["light"] and (phase == "flood" or (phase == "settled" and "lift8" in g)):
                abox = union(abox, g["box"])
        sv = None
        if self.serpent is not None:
            sp = self.serpent
            sv = sp.render(t)
            if sv is not None:
                sv["mult"] = scale(scale(cv2.LUT(three(sv["I"]), self.lut_im), cv2.LUT(three(sv["D"]), self.lut_dm)), cv2.bitwise_not(three(sv["H"])))
                sv["add"] = cv2.add(self._tint(sv["S"], sp.sheen_col), self._tint(sv["G"], sp.glint_col))
                box, abox = union(box, sv["roi"]), union(abox, sv["roi"])
                dirty.append(sv["roi"])
            if self.body is not None:
                dirty.append(self.body["roi"])
            before, old = sp.blot_key, sp.blot_box
            blots = sp.residue(t)
            if sp.blot_key != before:
                changed = union(old, blots)
                if changed is not None:
                    x0, y0, x1, y1 = changed
                    self.Mb[y0:y1, x0:x1] = cv2.LUT(three(sp.B8[y0:y1, x0:x1]), self.lut_vs[-1])
                    dirty.append(changed)
            box = union(box, blots)
        for x in dirty:
            self._compose(x, sv)
        self.body = sv
        self.box = box
        self._out = {"R": self.R8, "mult": self.mult, "add": self.add if abox is not None else None, "box": box, "rbox": rbox, "abox": abox, "t": t,
                     "sheen": None if sv is None else sv["add"], "sbody": None if sv is None else sv["I"],
                     "sroi": None if sv is None else sv["roi"]}
        self._t = t
        return self._out

    @staticmethod
    def _tint(img, col):
        return cv2.multiply(three(img), tuple(float(x) for x in col) + (0.0,), scale=1 / 255)

    def sound_events(self):
        s = self.p.get("sounds", {})
        out = []
        for x in self.panels:
            if x["light"]:
                continue
            tb, tf, tk, tr, ts = x["times"]
            if "breach" in s:
                out.append((s["breach"], tb))
            if "blink" in s and tr > tk:
                out.append((s["blink"], tk))
        if self.serpent is not None and "slither" in s:
            cue = dict(s["slither"], loop=True, until=self.serpent.path.t_end, envelope=self.slither)
            out.append((cue, self.serpent.path.t_birth))
        return out

    def slither(self, times):
        path = self.serpent.path
        g = np.clip(path.speed(times) / self.p.get("serpent", {}).get("slither_speed", 0.12), 0, 1) ** 0.7
        for x in self.panels:
            if x["light"]:
                g = np.where((times >= x["times"][0]) & (times < x["times"][4]), 0.0, g)
        return g.astype(np.float32)

    def weed_field(self, shape, cover=None):
        """When weeds may grow at each point of the sheet: a flood panel opens once it settles, and the whole sheet
        once a covering banner has finished."""
        h, w = shape
        T = np.full(shape, np.inf if cover is None else cover, np.float32)
        for x in self.panels:
            if not x["light"]:
                m = raster(x["geo"]["polygon"], w, h) > 0.5
                T[m] = np.minimum(T[m], x["times"][4])
        return T

    def vein_roots(self, tw, th):
        """Weed roots per flood panel at the ends of its residue veins: the drain vein's two ends and the tip of each
        corner creep, in tapestry pixels, with the direction the vein grows and the time it gets there."""
        out = {}
        w0, h0 = self.canvas.w0, self.canvas.h0
        for x in range(len(self.panels)):
            panel, g = self.panels[x], self.geo[x]
            if panel["light"]:
                continue
            r0, r1, c0, c1 = g["inner"]
            ts = panel["times"][4]
            to_px = lambda i, j: ((c0 + j + 0.5) / w0 * tw, (r0 + i + 0.5) / h0 * th)
            ends = []
            inside = cv2.erode(np.where(g["mask8"] > 250, 255, 0).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            spine = (g["vd8"] > 200) & inside
            for f in (g["tout"], g["tin"]):
                if spine.any():
                    i, j = np.unravel_index(np.argmin(np.where(spine, f, np.inf)), spine.shape)
                    ends.append(to_px(i, j) + (-math.pi / 2, ts))
            tips = []
            creep = (g["vc8"] > 110) & inside
            ci, cj = (r1 - r0) / 2.0, (c1 - c0) / 2.0
            ii, jj = np.indices(creep.shape)
            for q in ((0, 0), (1, 1), (0, 1), (1, 0)):
                qi, qj = q
                quad = creep & ((ii >= ci) == bool(qi)) & ((jj >= cj) == bool(qj))
                if not quad.any():
                    continue
                i, j = np.unravel_index(np.argmax(np.where(quad, g["tc"], -1.0)), quad.shape)
                ci0, cj0 = (r1 - r0) * qi, (c1 - c0) * qj
                a = math.atan2((i - ci0) * th / h0, (j - cj0) * tw / w0)
                tips.append(to_px(i, j) + (a, ts + float(g["tc"][i, j])))
            pairs = zip(ends + [None] * 4, tips + [None] * 4)
            out[panel["id"]] = [y for pair in pairs for y in pair if y is not None]
        return out

    def heat(self, t):
        out = np.zeros((self.h, self.w, 4), np.uint8)
        hot = False
        for x in range(len(self.panels)):
            if self.phase(self.panels[x], t)[0] not in ("breach", "flood"):
                continue
            g = self.geo[x]
            r0, r1, c0, c1 = g["roi"]
            col = cv2.applyColorMap(np.clip(g["tin"] * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
            out[r0:r1, c0:c1, :3] = col
            out[r0:r1, c0:c1, 3] = (g["mask8"].astype(np.float32) * 0.45).astype(np.uint8)
            hot = True
        return out if hot else None
