import bisect
import math

import cv2
import numpy as np

from fields import fractal_noise
from ink import hex_bgr, smoothstep

SPEC_PX = 1100.0
PROFILE = ((0.0, 15.0), (0.035, 18.0), (0.1, 14.0), (0.3, 22.0), (0.65, 13.0), (1.0, 2.0))


def travel(p, a=0.25):
    p = min(1.0, max(0.0, p))
    v = 1.0 / (1.0 - a)
    if p < a:
        return v * p * p / (2 * a)
    if p > 1 - a:
        return 1.0 - v * (1 - p) ** 2 / (2 * a)
    return v * (a / 2 + p - a)


def ease(p):
    p = min(1.0, max(0.0, p))
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def catmull(points, step, alpha=0.5):
    P = np.asarray(points, np.float64)
    P = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out, marks = [P[1][None]], [0]
    for x in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[x - 1], P[x], P[x + 1], P[x + 2]
        t1 = max(1e-9, float(np.linalg.norm(p1 - p0))) ** alpha
        t2 = t1 + max(1e-9, float(np.linalg.norm(p2 - p1))) ** alpha
        t3 = t2 + max(1e-9, float(np.linalg.norm(p3 - p2))) ** alpha
        n = max(2, int(np.linalg.norm(p2 - p1) / step))
        t = np.linspace(t1, t2, n + 1)[1:, None]
        A1 = (t1 - t) / t1 * p0 + t / t1 * p1
        A2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
        A3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
        B1 = (t2 - t) / t2 * A1 + t / t2 * A2
        B2 = (t3 - t) / (t3 - t1) * A2 + (t - t1) / (t3 - t1) * A3
        out.append((t2 - t) / (t2 - t1) * B1 + (t - t1) / (t2 - t1) * B2)
        marks.append(marks[-1] + n)
    return np.vstack(out), marks


def arc(xy):
    return np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


class Route:
    def __init__(self, cfg, timeline, avoid=None, swim=None):
        sp = cfg.get("reveal", {}).get("serpent", {})
        self.p = sp
        self.aspect = cfg["tapestry"]["height"] / cfg["tapestry"]["width"]
        self.u = 1.0 / SPEC_PX
        self.L = sp.get("length_px", 260) * self.u
        self.avoid = avoid or []
        self.swim = swim or {}
        self.reach = sp.get("swim_reach", 0.8)
        self.pts, self.wave, self.stop = [], [], []
        stages = self._stages(timeline)
        self.stages = stages
        self._geometry()
        self._plan(stages)
        self.t_birth = self.plan[0][1]
        self.t_end = timeline.end
        tt = np.arange(0.0, timeline.end + 0.02, 0.01)
        states = [self.state(x) for x in tt]
        heads = np.array([x["head"] for x in states])
        moving = np.array([x["kind"] in ("move", "rise", "swim", "spill") and x["visible"] for x in states])
        self.tt, self.vv = tt, np.where(moving, np.abs(np.gradient(heads, tt)), 0.0)

    def _add(self, p, wave, stop):
        if self.pts and math.hypot(p[0] - self.pts[-1][0], p[1] - self.pts[-1][1]) < 1e-4:
            self.stop[-1] = self.stop[-1] or stop
            return len(self.pts) - 1
        self.pts.append([float(p[0]), float(p[1])])
        self.wave.append(list(wave))
        self.stop.append(stop)
        return len(self.pts) - 1

    def _extend(self, points, wave):
        k = len(self.pts) - 1
        for x in points:
            k = self._add(x, wave, False)
        return k

    def _stages(self, timeline):
        out = []
        for x in timeline.panels():
            spec, times, beat = x
            if spec.get("mode") == "light":
                continue
            sb = timeline.beats[beat].get("serpent", {})
            wave = sb.get("wave", [1.0, 1.0])
            stage = {"id": spec["id"], "times": times, "beat": beat, "length": sb.get("length", 1.0), "swim": spec["id"] in self.swim}
            if not self.pts:
                stage["birth"] = self._add(spec["entry"], wave, True)
                stage["pool"] = sb.get("birth", {}).get("pool_s", 0.0)
                stage["entry"] = self._extend(sb.get("rise", []), wave)
                self.stop[stage["entry"]] = True
            else:
                self._extend(sb.get("route", []), wave)
                stage["entry"] = self._add(spec["entry"], wave, True)
            stage["exit"] = self._add(spec["exit"], [0.0, 1.0], True)
            out.append(stage)
        last = timeline.beats[out[-1]["beat"]].get("serpent", {})
        settle = last.get("settle", [])
        self.settle_s = last.get("settle_s", self.p.get("settle_s", 1.6))
        self.end = self._extend(settle, last.get("settle_wave", [0.4, 1.0])) if settle else out[-1]["exit"]
        self.stop[self.end] = True
        self.spill = None
        for x in range(out[-1]["beat"] + 1, len(timeline.beats)):
            sp = timeline.beats[x].get("serpent", {}).get("spill")
            if sp:
                k = self._extend(sp["route"], sp.get("wave", [0.8, 1.0]))
                self.stop[k] = True
                self.spill = {"t": timeline.starts[x] + sp.get("delay", 0.0), "dur": sp.get("dur", 3.0), "tail": sp.get("tail_px", 18) * self.u}
                break
        return out

    def _geometry(self):
        P = np.array(self.pts) * [1.0, self.aspect]
        step = 0.5 * self.u
        dense, marks = catmull(P, step)
        s0 = arc(dense)
        sw = s0[marks]
        amp = np.interp(s0, sw, [x[0] for x in self.wave]) * self.p.get("amplitude_px", 10) * self.u
        lam = np.interp(s0, sw, [x[1] for x in self.wave]) * self.p.get("wavelength_px", 90) * self.u
        stops = sw[np.array(self.stop)]
        near = np.min(np.abs(s0[:, None] - stops[None, :]), axis=1)
        amp = amp * smoothstep(0.0, 0.6 * lam, near)
        self.clearance = None
        if self.avoid:
            self._field()
            sp = self.p
            self.clearance = (sp.get("recoil_px", 6) + max(x[1] for x in PROFILE) / 2 + sp.get("edge_noise_px", 2.5) + sp.get("feather_px", 3.0) / 2) * self.u
            amp = amp * smoothstep(self.clearance, self.clearance + 0.5 * lam, self.gap(dense)[0])
        tan = unit(np.gradient(dense, axis=0))
        nrm = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
        phi = 2 * np.pi * np.concatenate([[0.0], np.cumsum(np.diff(s0) / lam[1:])])
        curve = dense + (amp * np.sin(phi))[:, None] * nrm
        if self.clearance is not None:
            for x in range(8):
                gap, away = self.gap(curve)
                push = np.maximum(0.0, self.clearance - gap) * 1.25
                if not push.any():
                    break
                curve = curve + away * cv2.GaussianBlur(push[:, None], (1, 0), 12.0)
        cs = arc(curve)
        self.s = np.arange(0.0, cs[-1], step)
        self.xy = np.stack([np.interp(self.s, cs, curve[:, 0]), np.interp(self.s, cs, curve[:, 1])], axis=1)
        tan = unit(cv2.GaussianBlur(np.gradient(self.xy, axis=0)[:, None, :].astype(np.float64), (1, 0), 3.0)[:, 0, :])
        self.tan = tan
        self.nrm = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
        self.ws = cs[marks]
        self.total = float(cs[-1])

    def _field(self):
        W, H = int(SPEC_PX), int(round(SPEC_PX * self.aspect))
        mask = np.zeros((H, W), np.uint8)
        for x in self.avoid:
            cv2.fillPoly(mask, [np.round(np.array(x, np.float64) * [W, H] * 16).astype(np.int32)], 255, cv2.LINE_8, 4)
        sd = cv2.distanceTransform(cv2.bitwise_not(mask), cv2.DIST_L2, 5) - cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        gy, gx = np.gradient(cv2.GaussianBlur(sd, (0, 0), 2.0))
        self.sdf = (sd.astype(np.float32), gx.astype(np.float32), gy.astype(np.float32))

    def gap(self, xy):
        """Distance from each point (route units) to the nearest avoided panel, and the direction away from it."""
        n = len(xy)
        pad = -n % 512
        mx = np.pad(xy[:, 0] * SPEC_PX - 0.5, (0, pad)).astype(np.float32).reshape(-1, 512)
        my = np.pad(xy[:, 1] * SPEC_PX - 0.5, (0, pad)).astype(np.float32).reshape(-1, 512)
        sd, gx, gy = (cv2.remap(x, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).reshape(-1)[:n] for x in self.sdf)
        return sd.astype(np.float64) * self.u, unit(np.stack([gx, gy], axis=1).astype(np.float64))

    def _swim(self, stage, p):
        u0, U = self.swim[stage["id"]]
        q = min(1.0, max(0.0, (U - u0) * travel(p) / max(1e-6, self.reach - u0)))
        return 1 - (1 - q) ** 2

    def swim_track(self, pid):
        """The swimming head's track through a panel and, at each point, the flood level u at which the head passes."""
        stage = next(x for x in self.stages if x["id"] == pid)
        e, o = self.ws[stage["entry"]], self.ws[stage["exit"]]
        s = np.arange(e, o, 0.5 * self.u)
        a = (s - e) / max(1e-9, o - e)
        u0 = self.swim[pid][0]
        return self.at(s) / [1.0, self.aspect], u0 + (self.reach - u0) * (1 - np.sqrt(np.clip(1 - a, 0, 1)))

    def _plan(self, stages):
        plan = [(-math.inf, stages[0]["times"][0], "hidden", 0.0, 0.0, 0.0, None)]
        prev = None
        for x in stages:
            tb, tf, tk, tr, ts = x["times"]
            e, o = self.ws[x["entry"]], self.ws[x["exit"]]
            L = self.L * x["length"]
            if "birth" in x:
                b0 = self.ws[x["birth"]]
                plan.append((tb, tb + x["pool"], "hidden", b0, b0, b0, x))
                plan.append((tb + x["pool"], tf, "rise", b0, e, b0, x))
                start = max(b0, e - L)
            else:
                plan.append((prev[1], tb, "move", prev[0], e, prev[0], x))
                plan.append((tb, tf, "breach", e, e, prev[0], x))
                start = max(prev[0], e - L)
            if x["swim"]:
                plan.append((tf, tk, "swim", e, o, prev[0] if prev else start, x))
                prev = (o, tk)
                continue
            plan.append((tf, tk, "pour", e, e, start, x))
            plan.append((tk, tr, "hidden", o, o, o, x))
            prev = (o, tr)
        last = stages[-1]
        end = self.ws[self.end]
        rest = prev[1] + self.settle_s
        plan.append((prev[1], rest, "move", prev[0], end, prev[0], last))
        if self.spill:
            sp = self.spill
            off = np.nonzero((self.s > end) & (self.xy[:, 1] >= self.aspect))[0]
            edge = float(self.s[off[0]]) if len(off) else self.total
            stop = min(self.total, edge + self.L * last["length"] + sp["tail"])
            sp["edge"], sp["stop"] = edge, stop
            plan.append((rest, sp["t"], "rest", end, end, prev[0], last))
            plan.append((sp["t"], sp["t"] + sp["dur"], "spill", end, stop, prev[0], last))
            plan.append((sp["t"] + sp["dur"], math.inf, "rest", stop, stop, prev[0], last))
        else:
            plan.append((rest, math.inf, "rest", end, end, prev[0], last))
        self.plan = plan
        self.starts = [x[0] for x in plan]

    def state(self, t):
        t0, t1, kind, a, b, anchor, stage = self.plan[max(0, bisect.bisect_right(self.starts, t) - 1)]
        p = (t - t0) / (t1 - t0) if math.isfinite(t0) and math.isfinite(t1) and t1 > t0 else 0.0
        L = self.L * (stage["length"] if stage else 1.0)
        out = {"kind": kind, "head": a, "tail": a, "swell": 0.0, "glint": 0.0, "L": L, "stage": stage, "p": p}
        if kind == "hidden":
            out["visible"] = False
            return out
        if kind in ("move", "rise", "spill", "swim"):
            head = a + (b - a) * (self._swim(stage, p) if kind == "swim" else travel(p))
            out["head"], out["tail"] = head, max(anchor, head - L)
            if kind == "rise":
                out["glint"] = math.sin(math.pi * p) ** 2
        elif kind == "pour":
            out["tail"] = anchor + (a - anchor) * ease(p)
        else:
            out["tail"] = max(anchor, a - L)
            if kind == "breach":
                e = math.sin(math.pi * min(1.0, p * 1.6)) ** 2
                out["swell"] = out["glint"] = e
        out["visible"] = out["head"] - out["tail"] > 0.5 * self.u
        return out

    def at(self, s):
        s = np.asarray(s, np.float64)
        return np.stack([np.interp(s, self.s, self.xy[:, 0]), np.interp(s, self.s, self.xy[:, 1])], axis=-1)

    def frame(self, s):
        s = np.asarray(s, np.float64)
        t = np.stack([np.interp(s, self.s, self.tan[:, 0]), np.interp(s, self.s, self.tan[:, 1])], axis=-1)
        t = unit(t.reshape(-1, 2)).reshape(t.shape)
        return t, np.stack([-t[..., 1], t[..., 0]], axis=-1)

    def speed(self, t):
        return np.interp(t, self.tt, self.vv)

    def width(self, sig):
        w = np.interp(sig, [x[0] for x in PROFILE], [x[1] for x in PROFILE])
        return w * self.u


class Serpent:
    def __init__(self, cfg, path, canvas, exclude=None):
        r = cfg["reveal"]
        sp = r.get("serpent", {})
        pal = r.get("palette", {})
        self.p = sp
        self.path, self.canvas = path, canvas
        self.k = canvas.w0 / SPEC_PX
        self.keep = None if exclude is None else cv2.bitwise_not(exclude)
        self.sheen_col = hex_bgr(pal.get("sheen_rgb", "#6B7A5A")) * sp.get("sheen", {}).get("strength", 0.12)
        self.glint_col = hex_bgr(pal.get("poison_glint", "#8FD14F")) * sp.get("glint_alpha", 0.7)
        self.noise = fractal_noise(canvas.w, canvas.h, max(4, canvas.w0 / (14 * self.k)), 3, 0.5, sp.get("seed", 23) + 1)
        rng = np.random.default_rng(sp.get("seed", 23))
        self.caps = self._capillaries(rng)
        self.blots = self._blots(rng)
        self.B8 = np.zeros((canvas.h, canvas.w), np.uint8)
        self.blot_key = None
        self.blot_box = None

    def px(self, xy):
        c = self.canvas
        return np.stack([xy[..., 0] * c.w0 + c.pad, xy[..., 1] / self.path.aspect * c.h0 + c.pad], axis=-1)

    def _capillaries(self, rng):
        path, u = self.path, self.path.u
        out = []
        t = path.t_birth
        rate = self.p.get("capillary_rate", 3.0)
        while t < path.t_end:
            t += float(rng.exponential(1.0 / rate))
            st = path.state(t)
            sig = 0.85 * float(rng.random()) ** 2
            side = 1.0 if rng.random() < 0.5 else -1.0
            if not st["visible"] or st["head"] - sig * st["L"] < st["tail"]:
                continue
            s = st["head"] - sig * st["L"]
            tan, nrm = path.frame([s])
            base = path.at([s])[0] + nrm[0] * side * path.width(sig) / 2
            ang = math.atan2(nrm[0, 1] * side, nrm[0, 0] * side) + math.radians(float(rng.uniform(-25, 25)))
            length = float(rng.uniform(8, 20)) * u
            cap = (t, base, ang, length, float(rng.uniform(1, 2)), rng.normal(0, 1, 4))
            if path.clearance is not None:
                tip = (base + np.array([math.cos(ang), math.sin(ang)]) * length)[None]
                if path.gap(tip)[0][0] < self.p.get("recoil_px", 6) * u:
                    continue
            out.append(cap)
        for x in path.stages:
            if "birth" not in x:
                out += self._tendrils(rng, x)
        return sorted(out, key=lambda x: x[0])

    def _tendrils(self, rng, stage):
        path = self.path
        s = path.ws[stage["entry"]]
        tan, nrm = path.frame([s])
        head = path.at([s])[0] + tan[0] * path.width(0.0) / 2
        out = []
        for x in range(int(rng.integers(3, 6))):
            ang = math.atan2(tan[0, 1], tan[0, 0]) + math.radians(float(rng.uniform(-60, 60)))
            out.append((stage["times"][0] + float(rng.uniform(0, 0.15)), head, ang, float(rng.uniform(12, 26)) * path.u, 1.5, rng.normal(0, 1, 4)))
        return out

    def _blots(self, rng):
        path = self.path
        lo, hi = self.p.get("blot_every_s", [2.0, 4.0])
        out = []
        t = path.t_birth + float(rng.uniform(lo, hi))
        while t < path.t_end:
            st = path.state(t)
            if st["visible"] and path.speed(t) > 0.02 and st["kind"] == "move":
                xy = self.px(path.at([st["tail"] + 3 * path.u]))[0]
                out.append((t, xy, float(rng.uniform(3, 6)) * self.k, int(rng.integers(1 << 30))))
            t += float(rng.uniform(lo, hi))
        return out

    def residue(self, t):
        key = tuple(round(min(1.0, (t - x[0]) / 0.5), 2) for x in self.blots if x[0] <= t)
        if key == self.blot_key:
            return self.blot_box
        self.B8[:] = 0
        box = None
        for x in range(len(key)):
            t0, (cx, cy), r, seed = self.blots[x]
            g = key[x]
            rr = r * (0.35 + 0.65 * g)
            n = int(math.ceil(rr * 1.6)) + 3
            x0, y0 = int(cx) - n, int(cy) - n
            x1, y1 = min(self.canvas.w, x0 + 2 * n + 1), min(self.canvas.h, y0 + 2 * n + 1)
            x0, y0 = max(0, x0), max(0, y0)
            if x1 <= x0 or y1 <= y0:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            ang = np.arctan2(yy - cy, xx - cx)
            rng = np.random.default_rng(seed)
            ph = rng.uniform(0, 2 * np.pi, 3)
            edge = rr * (1 + 0.18 * np.sin(3 * ang + ph[0]) + 0.1 * np.sin(5 * ang + ph[1]) + 0.06 * np.sin(9 * ang + ph[2]))
            a = np.clip((edge - np.hypot(xx - cx, yy - cy)) / max(0.8, 0.6 * self.k) + 0.5, 0, 1) * 0.62 * 255
            if self.keep is not None:
                a = a * (self.keep[y0:y1, x0:x1] / 255.0)
            np.maximum(self.B8[y0:y1, x0:x1], a.astype(np.uint8), out=self.B8[y0:y1, x0:x1])
            box = (x0, y0, x1, y1) if box is None else (min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1))
        self.blot_key, self.blot_box = key, box
        return box

    def body(self, t):
        st = self.path.state(t)
        if not st["visible"]:
            return st, None
        path, u = self.path, self.path.u
        head, tail = st["head"], st["tail"]
        n = max(2, int((head - tail) * self.canvas.w0 / 0.75) + 1)
        s = np.linspace(tail, head, n)
        sig = (head - s) / st["L"]
        w = path.width(sig) * smoothstep(0.0, 40 * u, s - tail) * (1 + 0.3 * st["swell"] * np.clip(1 - sig / 0.18, 0, 1))
        return st, (s, sig, w)

    def render(self, t):
        st, body = self.body(t)
        caps = [x for x in self.caps if x[0] <= t < x[0] + 1.9]
        if body is None and not caps:
            return None
        k, c = self.k, self.canvas
        pts = []
        if body is not None:
            s, sig, w = body
            xy = self.px(self.path.at(s))
            wpx = w * c.w0
            pts.append(xy - wpx.max())
            pts.append(xy + wpx.max())
        for x in caps:
            p = self.px(np.asarray(x[1])[None])[0]
            pts.append((p + x[3] * c.w0 * 1.3)[None])
            pts.append((p - x[3] * c.w0 * 1.3)[None])
        allp = np.vstack(pts)
        m = 30 * k + 4
        x0, y0 = max(0, int(allp[:, 0].min() - m)), max(0, int(allp[:, 1].min() - m))
        x1, y1 = min(c.w, int(allp[:, 0].max() + m) + 1), min(c.h, int(allp[:, 1].max() + m) + 1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        shape = (y1 - y0, x1 - x0)
        I, D, H, S, G = (np.zeros(shape, np.uint8) for x in range(5))
        if body is not None:
            self._body(st, body, xy - [x0, y0], wpx, (x0, y0), shape, I, D, H, S, G, t)
        if caps:
            cap = np.zeros(shape, np.uint8)
            for x in caps:
                self._capillary(cap, x, t, (x0, y0))
            np.maximum(I, cap, out=I)
        if self.keep is not None:
            keep = self.keep[y0:y1, x0:x1]
            for x in (I, D, H, S, G):
                cv2.multiply(x, keep, dst=x, scale=1 / 255)
        return {"roi": (x0, y0, x1, y1), "I": I, "D": D, "H": H, "S": S, "G": G}

    def _body(self, st, body, xy, wpx, origin, shape, I, D, H, S, G, t):
        k, c, sp = self.k, self.canvas, self.p
        s, sig, w = body
        mask = np.zeros(shape, np.uint8)
        q = np.round(xy * 4).astype(np.int32)
        rad = np.maximum(1, np.round(wpx * 2)).astype(np.int32)
        for x in range(len(q)):
            cv2.circle(mask, (int(q[x, 0]), int(q[x, 1])), int(rad[x]), 255, -1, cv2.LINE_8, 2)
        sd = cv2.distanceTransform(cv2.bitwise_not(mask), cv2.DIST_L2, 5) - cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        drift = sp.get("edge_drift_px", 18) * k * t
        ox, oy = int(drift) % c.w, int(drift * 0.6) % c.h
        rows = (np.arange(origin[1], origin[1] + shape[0]) + oy) % c.h
        cols = (np.arange(origin[0], origin[0] + shape[1]) + ox) % c.w
        sd += sp.get("edge_noise_px", 2.5) * k * self.noise[np.ix_(rows, cols)]
        f = max(0.6, sp.get("feather_px", 3.0) * k / 2)
        a = 1 - smoothstep(-f, f, sd)
        I[:] = np.clip(a * 0.95 * 255 + 0.5, 0, 255).astype(np.uint8)
        sh = sp.get("shadow", {})
        off = np.array(sh.get("offset_px", [3, 4]), np.float64) * k * (1 + st["swell"])
        blur = cv2.GaussianBlur(a.astype(np.float32), (0, 0), max(0.7, sh.get("blur_px", 2.2) * k))
        moved = cv2.warpAffine(blur, np.float32([[1, 0, off[0]], [0, 1, off[1]]]), (shape[1], shape[0]))
        H[:] = np.clip(moved * sh.get("alpha", 0.22) * 255 + 0.5, 0, 255).astype(np.uint8)
        halo = cv2.GaussianBlur(a.astype(np.float32), (0, 0), max(1.0, sp.get("halo_px", 7) * k))
        D[:] = np.clip(halo * 2.2 * 255, 0, 255).astype(np.uint8)
        tan, nrm = self.path.frame(s)
        flip = np.where((nrm[:, 0] + nrm[:, 1]) > 0, -1.0, 1.0)[:, None]
        lit = xy + nrm * flip * (0.3 * wpx / 2)[:, None]
        she = sp.get("sheen", {})
        dist = (st["head"] - s) * c.w0
        phase = (dist - 1.2 * st["head"] * c.w0 - she.get("drift_px", 15) * k * t) / (she.get("period_px", 40) * k)
        val = (0.5 + 0.5 * np.cos(2 * np.pi * phase)) ** 3 * (0.4 + 0.6 * np.clip(1 - sig, 0, 1))
        th = max(1, int(round(2 * k)))
        p16 = np.round(lit * 16).astype(np.int32)
        for x in range(len(p16) - 1):
            v = int(val[x] * 255)
            if v > 8:
                cv2.line(S, (int(p16[x, 0]), int(p16[x, 1])), (int(p16[x + 1, 0]), int(p16[x + 1, 1])), v, th, cv2.LINE_AA, 4)
        S[sd > -0.8 * k] = 0
        if st["glint"] > 0.01:
            hp = xy[-1] + tan[-1] * wpx[-1] * 0.25
            cv2.circle(G, (int(round(hp[0] * 16)), int(round(hp[1] * 16))), int(max(16, round(1.5 * k * 16))),
                       int(255 * st["glint"]), -1, cv2.LINE_AA, 4)

    def _capillary(self, img, cap, t, origin):
        t0, base, ang, length, width, jit = cap
        g = min(1.0, (t - t0) / 0.4)
        alpha = 1 - min(1.0, max(0.0, (t - t0 - 0.4) / 1.5))
        if g <= 0 or alpha <= 0:
            return
        d = np.array([math.cos(ang), math.sin(ang)])
        perp = np.array([-d[1], d[0]])
        steps = np.linspace(0, 1, 5)[:, None]
        side = 0.15 * np.concatenate([[0.0], jit])[:, None]
        pts = base[None] + (d[None] * steps + perp[None] * side * steps) * length * g
        p = (self.px(pts) - origin) * 16
        th = max(1, int(round(width * self.k)))
        cv2.polylines(img, [np.round(p).astype(np.int32)], False, int(0.9 * alpha * 255), th, cv2.LINE_AA, 4)

    def skeleton(self, t):
        st, body = self.body(t)
        if body is None:
            return None
        return self.path.at(body[0])
