import math

import cv2
import numpy as np

WARP = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP


def block_stats(gray, size):
    g = gray.astype(np.float32)
    mean = cv2.boxFilter(g, -1, (size, size))
    var = cv2.boxFilter(g * g, -1, (size, size)) - mean * mean
    return mean, var


def sharpness(gray):
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 0.8)
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def robust_sigma(gray):
    g = gray.astype(np.float32)
    hp = g - cv2.GaussianBlur(g, (0, 0), 1.5)
    return 1.4826 * float(np.median(np.abs(hp - np.median(hp))))


class Realism:
    def __init__(self, cfg, clean, art, work, panels):
        p = cfg.get("reveal", {}).get("realism", {})
        self.p = p
        self.on = bool(p.get("on", True)) and cfg.get("fx", {}).get("realism", True)
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        self.live = False
        self.state = {}
        gw = int(p.get("light_grid", 96))
        gh = int(round(gw * self.th / self.tw))
        self.grid = (gw, gh)
        self.blur = p.get("light_blur_px", 40) / 1100.0 * gw
        self.lo, self.hi = p.get("light_clamp", [0.5, 1.6])
        self.alpha = p.get("ema", 0.1)
        ref = cv2.resize(clean, (gw * 3, gh * 3), interpolation=cv2.INTER_AREA)
        ref = cv2.resize(ref, (gw, gh), interpolation=cv2.INTER_AREA).astype(np.float32)
        self.ref = cv2.GaussianBlur(ref, (0, 0), self.blur) + 1.0
        s = 3.0
        self.S = np.array([[self.tw / (gw * s), 0, 0], [0, self.th / (gh * s), 0], [0, 0, 1]], np.float64)
        self.aw, self.ah = art.shape[1], art.shape[0]
        self.A = np.diag([self.tw / self.aw, self.th / self.ah, 1.0])
        inside = np.zeros(clean.shape[:2], np.uint8)
        for x in panels:
            cv2.fillPoly(inside, [np.round(np.array(x["polygon"]) * [self.aw, self.ah]).astype(np.int32)], 255)
        inside = cv2.erode(inside, np.ones((25, 25), np.uint8)) > 0
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        n = int(p.get("patch_px", 24))
        mean, var = block_stats(gray, n)
        flat = np.where(inside & (mean > 150), var, np.inf)
        self.flat = self._patch(np.unravel_index(int(np.argmin(flat)), flat.shape), n)
        lap = cv2.boxFilter(np.abs(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F)), -1, (48, 48))
        self.tex = self._patch(np.unravel_index(int(np.argmax(np.where(inside, lap, -1))), lap.shape), 48)
        self.sigmas = p.get("focus_levels_px", [0.0, 0.6, 1.2, 1.8, 2.6, 3.4])
        self.art_levels = [art] + [cv2.GaussianBlur(art, (0, 0), x) for x in self.sigmas[1:]]
        self.tex_box = self._grow(self.tex, 24)
        bx0, by0, bx1, by1 = self.tex_box
        crop = gray[by0:by1, bx0:bx1]
        self.tex_levels = [crop] + [cv2.GaussianBlur(crop, (0, 0), x) for x in self.sigmas[1:]]
        self.flat_box = self._grow(self.flat, 24)
        bx0, by0, bx1, by1 = self.flat_box
        self.flat_ref = gray[by0:by1, bx0:bx1]
        self.banks = {}
        self.work = work
        self.fps = p.get("grain_fps", 24)

    def _grow(self, box, m):
        x0, y0, x1, y1 = box
        return max(0, x0 - m), max(0, y0 - m), min(self.aw, x1 + m), min(self.ah, y1 + m)

    def _reference(self, img, box, origin, size, H):
        M = np.linalg.inv(H @ self.A) @ np.array([[1, 0, origin[0]], [0, 1, origin[1]], [0, 0, 1]], np.float64)
        M = np.array([[1, 0, -box[0]], [0, 1, -box[1]], [0, 0, 1]], np.float64) @ M
        return cv2.warpPerspective(img, M, size, flags=WARP, borderMode=cv2.BORDER_REPLICATE)

    def _patch(self, centre, n):
        cy, cx = centre
        return max(0, cx - n // 2), max(0, cy - n // 2), min(self.aw, cx + n // 2), min(self.ah, cy + n // 2)

    def _crop(self, frame, H, box, shrink=0.6):
        x0, y0, x1, y1 = box
        q = cv2.perspectiveTransform(np.float64([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]), H @ self.A)[0]
        cx, cy = q[:, 0].mean(), q[:, 1].mean()
        hw, hh = (q[:, 0].max() - q[:, 0].min()) * shrink / 2, (q[:, 1].max() - q[:, 1].min()) * shrink / 2
        c0, r0 = max(0, int(cx - hw)), max(0, int(cy - hh))
        c1, r1 = min(frame.shape[1], int(cx + hw) + 1), min(frame.shape[0], int(cy + hh) + 1)
        if c1 - c0 < 6 or r1 - r0 < 6:
            return None, None
        return frame[r0:r1, c0:c1], (c0, r0)

    def _smooth(self, key, value):
        if not self.live or key not in self.state:
            self.state[key] = value
        else:
            self.state[key] = self.state[key] + self.alpha * (value - self.state[key])
        return self.state[key]

    def light(self, frame, H):
        gw, gh = self.grid
        cam = cv2.warpPerspective(frame, H @ self.S, (gw * 3, gh * 3), flags=WARP, borderMode=cv2.BORDER_REPLICATE)
        cam = cv2.resize(cam, (gw, gh), interpolation=cv2.INTER_AREA).astype(np.float32)
        L = np.clip(cv2.GaussianBlur(cam, (0, 0), self.blur) / self.ref, self.lo, self.hi)
        return self._smooth("light", L)

    def grain(self, frame, H):
        crop, origin = self._crop(frame, H, self.flat)
        if crop is None:
            return 0.0
        ref = self._reference(self.flat_ref, self.flat_box, origin, (crop.shape[1], crop.shape[0]), H)
        cam = float(np.mean([robust_sigma(x) ** 2 for x in cv2.split(crop)]))
        excess = cam - robust_sigma(ref) ** 2
        return self._smooth("grain", math.sqrt(max(0.0, excess)))

    def focus(self, frame, H):
        crop, origin = self._crop(frame, H, self.tex, 1.0)
        if crop is None:
            return 0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        size = (gray.shape[1], gray.shape[0])
        target = math.log(sharpness(gray) + 1e-6)
        score = [abs(math.log(sharpness(self._reference(x, self.tex_box, origin, size, H)) + 1e-6) - target) for x in self.tex_levels]
        return int(round(self._smooth("focus", float(np.argmin(score)))))

    def measure(self, frame, H, direct=False):
        if not self.on or direct:
            return None
        if not self.live or "out" not in self.state:
            self.state["out"] = {"light": self.light(frame, H), "grain": self.grain(frame, H), "level": self.focus(frame, H)}
            self.state["turn"] = 0
            return dict(self.state["out"])
        turn = self.state["turn"] = (self.state["turn"] + 1) % 3
        key = ("light", "grain", "level")[turn]
        self.state["out"][key] = (self.light, self.grain, self.focus)[turn](frame, H)
        return dict(self.state["out"])

    def _bank(self, size):
        if size not in self.banks:
            rng = np.random.default_rng(self.p.get("seed", 29))
            w, h = size
            bank = []
            for x in range(int(self.p.get("grain_frames", 4))):
                raw = rng.normal(0, 1, (h, w, 3)).astype(np.float32)
                soft = cv2.GaussianBlur(raw, (0, 0), 1.0)
                soft /= max(1e-6, float(soft.std()))
                bank.append((np.clip(128 + 32 * raw, 0, 255).astype(np.uint8), np.clip(128 + 32 * soft, 0, 255).astype(np.uint8)))
            self.banks[size] = bank
        return self.banks[size]

    def noise(self, t, scale, size):
        bank = self._bank(size)
        return bank[int(math.floor(t * self.fps)) % len(bank)][1 if scale > 1.5 else 0]
