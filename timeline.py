import math
import random

EASES = {
    "settle": lambda p: 0.5 - 0.5 * math.cos(math.pi * p),
    "linear": lambda p: p,
    "seep": lambda p: 1.0 - (1.0 - p) ** 3,
}


def rect_polygon(r):
    x0, y0, x1, y1 = r
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class Timeline:
    def __init__(self, cfg):
        self.cfg = cfg
        self.beats = cfg["beats"]
        self.starts = []
        t = 0.0
        for b in self.beats:
            self.starts.append(t)
            t += b["duration"]
        self.end = t
        self.ids = {b.get("id", f"B{k + 1:02d}"): k for k, b in enumerate(self.beats)}
        self.regions = cfg.get("regions", {})
        cam = cfg["camera"]
        self.ease = EASES[cam.get("ease", "settle")]
        self.dip = cam.get("dip", 0.0)
        self.dip_max = cam.get("dip_max", 0.0)
        self.drift = cam.get("drift")
        self.handheld = cam.get("handheld")
        self.lens = cfg.get("lens", {})
        self.aspect = cfg["tapestry"]["height"] / cfg["tapestry"]["width"]
        out = cfg.get("output", {"width": 1920, "height": 1080})
        self.out_aspect = out["width"] / out["height"]
        self.keys = sorted(
            (start + k["t"], k["x"], k["y"], self._key_w(k), k.get("f", 0.0), k.get("fx", k["x"]), k.get("fy", k["y"]))
            for start, b in zip(self.starts, self.beats)
            for k in b.get("camera", [])
        )
        self.hold_t = self.starts[self.ids["B21"]] if "B21" in self.ids else self.end
        self.shake = [b.get("handheld", 1.0) for b in self.beats]
        self.pulls = [(start + e.get("t", 0.0), max(1e-3, e.get("dur", 0.8)), e.get("amount", 0.4))
                      for start, b in zip(self.starts, self.beats) for e in b.get("lens", [])]
        self.hh = None
        if self.handheld:
            rng = random.Random(self.handheld.get("seed", 13))
            freqs = self.handheld.get("freqs", [0.11, 0.23, 0.47, 0.93, 1.9])
            amps = self.handheld.get("weights", [1.0, 0.7, 0.4, 0.2, 0.08])
            norm = sum(amps) * 0.55
            self.hh = [[(a / norm, f * rng.uniform(0.85, 1.15), rng.uniform(0.0, 2 * math.pi)) for a, f in zip(amps, freqs)]
                       for _ in range(4)]

    def _key_w(self, k):
        if "h" in k:
            return k["h"] * self.aspect * self.out_aspect
        return k["w"]

    def at(self, ref, offset=0.0):
        if isinstance(ref, (int, float)):
            return float(ref) + offset
        return self.starts[self.ids[ref]] + offset

    def polygon(self, clip):
        if clip is None:
            return None
        if isinstance(clip, str):
            r = self.regions[clip]
            return r["polygon"] if "polygon" in r else rect_polygon(r["rect"])
        if len(clip) == 4 and not isinstance(clip[0], list):
            return rect_polygon(clip)
        return clip

    def items(self, kind):
        for k, (start, b) in enumerate(zip(self.starts, self.beats)):
            for x in b.get(kind, []):
                yield x, start + x.get("t", 0.0), k

    def resolve(self, s):
        s = dict(s)
        if "bounds" in s and "clip" not in s:
            s["clip"] = s["bounds"]
        poly = self.polygon(s.get("clip"))
        if poly is not None:
            s["clip"] = poly
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            s["bounds"] = [min(xs), min(ys), max(xs), max(ys)]
        return s

    def seeds(self):
        for s, t0, _ in self.items("seeds"):
            yield self.resolve(s), t0

    def weaves(self):
        for w, t0, _ in self.items("weaves"):
            yield w, t0
        for s, t0, _ in self.items("seeds"):
            if s.get("lift"):
                yield {"bounds": s["bounds"], "speed": s["speed"]}, t0

    def beat_at(self, t):
        i = 0
        for k, s in enumerate(self.starts):
            if t + 1e-6 >= s:
                i = k
        return i

    def step(self, t, direction):
        i = self.beat_at(t) + direction
        if i < 0:
            return 0.0
        if i >= len(self.beats):
            return self.end
        return self.starts[i]

    def camera(self, t):
        x, y, w, f = self.camera_full(t)
        return x, y, w

    def _interp(self, t):
        K = self.keys
        if not K:
            return 0.5, 0.5, 1.0, 0.0, 0.5, 0.5
        if t <= K[0][0]:
            return K[0][1:]
        if t >= K[-1][0]:
            return K[-1][1:]
        j = next(i for i in range(len(K) - 1) if t <= K[i + 1][0])
        a, b = K[j], K[j + 1]
        p = self.ease(min(1.0, max(0.0, (t - a[0]) / max(1e-9, b[0] - a[0]))))
        dist = math.hypot(b[1] - a[1], (b[2] - a[2]) * self.aspect)
        dip = min(self.dip_max, max(0.0, dist / max(a[3], b[3]) * self.dip))
        x = a[1] + (b[1] - a[1]) * p
        y = a[2] + (b[2] - a[2]) * p
        w = math.exp(math.log(a[3]) + (math.log(b[3]) - math.log(a[3])) * p) / (1.0 - dip * math.sin(math.pi * p))
        f = a[4] + (b[4] - a[4]) * p
        return x, y, w, f, a[5] + (b[5] - a[5]) * p, a[6] + (b[6] - a[6]) * p

    def shake_level(self, t):
        i = self.beat_at(t)
        cur = self.shake[i]
        prev = self.shake[i - 1] if i else cur
        p = min(1.0, max(0.0, (t - self.starts[i]) / self.handheld.get("fade_s", 0.9))) if self.handheld else 1.0
        return prev + (cur - prev) * (0.5 - 0.5 * math.cos(math.pi * p))

    def _noise(self, t, ch):
        return sum(a * math.sin(2 * math.pi * f * t + ph) for a, f, ph in self.hh[ch])

    def camera_pose(self, t):
        """Camera centre, width, full-frame blend and roll (radians), including handheld motion."""
        x, y, w, f = self._interp(t)[:4]
        roll = 0.0
        if self.drift:
            amp, freq, ph = self.drift["amp"], self.drift["freq"], self.drift["phase"]
            x += amp[0] * math.sin(freq[0] * t + ph[0])
            y += amp[1] * math.sin(freq[1] * t + ph[1])
        if self.hh:
            hh = self.handheld
            tt = min(t, self.hold_t)
            k = self.shake_level(tt) * (1.0 - f)
            if k > 0:
                amp = hh.get("amp", [0.004, 0.003])
                x += w * amp[0] * k * self._noise(tt, 0)
                y += w / (self.aspect * self.out_aspect) * amp[1] * k * self._noise(tt, 1)
                roll = math.radians(hh.get("roll_deg", 0.25)) * k * self._noise(tt, 2)
                w *= math.exp(hh.get("zoom", 0.004) * k * self._noise(tt, 3))
        return x, y, w, f, roll

    def camera_full(self, t):
        return self.camera_pose(t)[:4]

    def _speed(self, t, dt=0.04):
        a, b = self._interp(t - dt), self._interp(t + dt)
        w = max(1e-3, 0.5 * (a[2] + b[2]))
        move = math.hypot(b[0] - a[0], (b[1] - a[1]) * self.aspect) / w + abs(math.log(b[2] / a[2]))
        return move / (2 * dt)

    def focus(self, t):
        """Focus target (tapestry coordinates, trailing the camera) and global defocus 0-1 for rack focus."""
        L = self.lens
        lag = L.get("lag", 0.2)
        fx, fy = self._interp(t - lag)[4:6]
        if t >= self.hold_t:
            return fx, fy, 0.0
        d = 0.0
        rack = L.get("rack", 0.0)
        if rack > 0:
            sp = sum(self._speed(t - lag * k / 3.0) for k in range(4)) / 4.0
            s0, s1 = L.get("rack_speed", [0.15, 0.9])
            q = min(1.0, max(0.0, (sp - s0) / max(1e-6, s1 - s0)))
            d = rack * q * q * (3 - 2 * q)
        for ts, dur, amt in self.pulls:
            p = (t - ts) / dur
            if 0.0 < p < 1.0:
                d += amt * math.sin(math.pi * p) ** 2
        return fx, fy, min(1.0, d)
