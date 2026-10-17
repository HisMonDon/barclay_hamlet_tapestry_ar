import math

import cv2
import numpy as np

from timeline import hold_time


class Atmosphere:
    def __init__(self, cfg, size):
        self.p = cfg.get("atmosphere", {})
        self.w, self.h = size
        self.hold = hold_time(cfg["beats"])
        flicker = self.p.get("flicker", {})
        rng = np.random.default_rng(flicker.get("seed", 3))
        self.phases = rng.uniform(0, 2 * np.pi, 3)
        vignette = self.p.get("vignette", {})
        self.mask = None
        if vignette.get("on", False):
            ys, xs = np.ogrid[-1:1:complex(self.h), -1:1:complex(self.w)]
            radius = np.clip((xs * xs + ys * ys) / 2, 0, 1)
            gain = 1 - vignette.get("strength", 0.14) * radius ** (1 / max(0.1, vignette.get("softness", 0.65)))
            self.mask = np.repeat(np.round(gain[..., None] * 255).astype(np.uint8), 3, axis=2)
        self.grains = []
        grain = self.p.get("grain", {})
        if grain.get("on", False):
            rng = np.random.default_rng(grain.get("seed", 21))
            for x in range(grain.get("frames", 6)):
                data = (128 + rng.normal(0, grain.get("amount", 0.7), (self.h, self.w)).round()).clip(0, 255).astype(np.uint8)
                self.grains.append(np.repeat(data[..., None], 3, axis=2))
        self.luts = []
        if flicker.get("on", False):
            values = np.arange(256, dtype=np.float32)[:, None]
            for x in np.linspace(-1, 1, 129):
                gain = 1 + x * flicker.get("amount", 0.018)
                warm = flicker.get("warmth", 0.008) * (0.5 + 0.5 * x)
                self.luts.append(np.clip(values * np.array([gain - warm, gain, gain + warm]), 0, 255).astype(np.uint8)[None])

    def level(self, t):
        t = min(t, self.hold) * self.p.get("flicker", {}).get("speed", 1)
        return sum(weight * math.sin(t * freq + phase) for weight, freq, phase in zip((0.55, 0.3, 0.15), (0.73, 1.31, 2.17), self.phases))

    def apply(self, frame, t):
        out = frame
        if self.luts:
            out = cv2.LUT(out, self.luts[min(128, max(0, round((self.level(t) + 1) * 64)))])
        if self.mask is not None:
            out = cv2.multiply(out, self.mask, scale=1 / 255)
        if self.grains:
            fps = self.p.get("grain", {}).get("fps", 24)
            out = cv2.addWeighted(out, 1, self.grains[int(min(t, self.hold) * fps) % len(self.grains)], 1, -128)
        return out
