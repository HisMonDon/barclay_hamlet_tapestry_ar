import json
import math

EASES = {
    "settle": lambda p: 0.5 - 0.5 * math.cos(math.pi * p),
    "linear": lambda p: p,
    "seep": lambda p: 1.0 - (1.0 - p) ** 3,
}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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
        cam = cfg["camera"]
        self.ease = EASES[cam.get("ease", "settle")]
        self.dip = cam.get("dip", 0.0)
        self.dip_max = cam.get("dip_max", 0.0)
        self.drift = cam.get("drift")
        self.aspect = cfg["tapestry"]["height"] / cfg["tapestry"]["width"]
        self.keys = sorted(
            (start + k["t"], k["x"], k["y"], k["w"])
            for start, b in zip(self.starts, self.beats)
            for k in b.get("camera", [])
        )

    def seeds(self):
        for start, b in zip(self.starts, self.beats):
            for s in b.get("seeds", []):
                yield s, start + s["t"]

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
        K = self.keys
        if not K:
            x, y, w = 0.5, 0.5, 1.0
        elif t <= K[0][0]:
            x, y, w = K[0][1:]
        elif t >= K[-1][0]:
            x, y, w = K[-1][1:]
        else:
            j = next(i for i in range(len(K) - 1) if t <= K[i + 1][0])
            a, b = K[j], K[j + 1]
            p = self.ease(min(1.0, max(0.0, (t - a[0]) / max(1e-9, b[0] - a[0]))))
            dist = math.hypot(b[1] - a[1], (b[2] - a[2]) * self.aspect)
            dip = min(self.dip_max, max(0.0, dist / max(a[3], b[3]) * self.dip))
            x = a[1] + (b[1] - a[1]) * p
            y = a[2] + (b[2] - a[2]) * p
            w = math.exp(math.log(a[3]) + (math.log(b[3]) - math.log(a[3])) * p) / (1.0 - dip * math.sin(math.pi * p))
        if self.drift:
            amp, freq, ph = self.drift["amp"], self.drift["freq"], self.drift["phase"]
            x += amp[0] * math.sin(freq[0] * t + ph[0])
            y += amp[1] * math.sin(freq[1] * t + ph[1])
        return x, y, w
