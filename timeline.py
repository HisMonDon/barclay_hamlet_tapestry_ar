import math

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
        self.aspect = cfg["tapestry"]["height"] / cfg["tapestry"]["width"]
        out = cfg.get("output", {"width": 1920, "height": 1080})
        self.out_aspect = out["width"] / out["height"]
        self.keys = sorted(
            (start + k["t"], k["x"], k["y"], self._key_w(k), k.get("f", 0.0))
            for start, b in zip(self.starts, self.beats)
            for k in b.get("camera", [])
        )

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

    def seeds(self):
        for s, t0, _ in self.items("seeds"):
            s = dict(s)
            if "bounds" in s and "clip" not in s:
                s["clip"] = s["bounds"]
            poly = self.polygon(s.get("clip"))
            if poly is not None:
                s["clip"] = poly
                xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                s["bounds"] = [min(xs), min(ys), max(xs), max(ys)]
            yield s, t0

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

    def camera_full(self, t):
        K = self.keys
        if not K:
            x, y, w, f = 0.5, 0.5, 1.0, 0.0
        elif t <= K[0][0]:
            x, y, w, f = K[0][1:]
        elif t >= K[-1][0]:
            x, y, w, f = K[-1][1:]
        else:
            j = next(i for i in range(len(K) - 1) if t <= K[i + 1][0])
            a, b = K[j], K[j + 1]
            p = self.ease(min(1.0, max(0.0, (t - a[0]) / max(1e-9, b[0] - a[0]))))
            dist = math.hypot(b[1] - a[1], (b[2] - a[2]) * self.aspect)
            dip = min(self.dip_max, max(0.0, dist / max(a[3], b[3]) * self.dip))
            x = a[1] + (b[1] - a[1]) * p
            y = a[2] + (b[2] - a[2]) * p
            w = math.exp(math.log(a[3]) + (math.log(b[3]) - math.log(a[3])) * p) / (1.0 - dip * math.sin(math.pi * p))
            f = a[4] + (b[4] - a[4]) * p
        if self.drift:
            amp, freq, ph = self.drift["amp"], self.drift["freq"], self.drift["phase"]
            x += amp[0] * math.sin(freq[0] * t + ph[0])
            y += amp[1] * math.sin(freq[1] * t + ph[1])
        return x, y, w, f
