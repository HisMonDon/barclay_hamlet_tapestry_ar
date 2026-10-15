import cv2
import numpy as np


class Lens:
    """Shallow depth of field around the subject, rack-focus softening on camera moves, and a soft highlight bloom."""

    def __init__(self, cfg, size):
        p = cfg.get("lens", {})
        fx = cfg.get("fx", {})
        self.w, self.h = size
        d = p.get("dof", {})
        self.dof_on = bool(d.get("on", True)) and fx.get("dof", True)
        self.r0, self.r1 = d.get("r0", 0.22), d.get("r1", 0.8)
        self.ellipse = d.get("ellipse", 0.7)
        self.blur_px = d.get("blur_px", 7.0) * self.w / 1920.0
        self.levels = int(fx.get("dof_levels", d.get("levels", 2)))
        self.radial = bool(fx.get("dof_radial", True))
        b = p.get("bloom", {})
        self.bloom_on = bool(b.get("on", True)) and fx.get("bloom", True)
        self.thr = int(b.get("threshold", 226))
        self.bloom_k = b.get("strength", 0.3)
        self.bloom_px = b.get("radius_px", 22.0) * self.w / 1920.0
        ms = 8
        self.mw, self.mh = max(4, self.w // ms), max(4, self.h // ms)
        self.gx = ((np.arange(self.mw, dtype=np.float32) + 0.5) * self.w / self.mw)[None, :]
        self.gy = ((np.arange(self.mh, dtype=np.float32) + 0.5) * self.h / self.mh)[:, None]

    @property
    def active(self):
        return self.dof_on or self.bloom_on

    def _blur(self, img, px):
        k = 2 if px < 2.5 else 4
        sw, sh = max(2, self.w // k), max(2, self.h // k)
        small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), max(0.5, px / k))
        return cv2.resize(small, (self.w, self.h), interpolation=cv2.INTER_LINEAR)

    def mask(self, focus, defocus, strength):
        fx, fy = focus
        rx = 0.5 * self.w
        r = np.sqrt(((self.gx - fx) / rx) ** 2 + ((self.gy - fy) / (rx * self.ellipse)) ** 2)
        m = np.clip((r - self.r0) / max(1e-3, self.r1 - self.r0), 0.0, 1.0)
        m = m * m * (3 - 2 * m)
        return np.clip(m * strength + defocus, 0.0, 1.0).astype(np.float32)

    def apply(self, img, focus, defocus, strength):
        out = img
        if self.dof_on and not self.radial:
            # Cheap path: no field falloff, only the uniform rack-focus softening while the camera travels.
            if defocus > 0.01:
                out = cv2.addWeighted(img, 1.0 - defocus, self._blur(img, self.blur_px), defocus, 0.0)
        elif self.dof_on and (strength > 0.01 or defocus > 0.01):
            m = self.mask(focus, defocus, strength)
            if float(m.max()) > 0.02:
                mask = cv2.resize(m, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
                far = self._blur(img, self.blur_px)
                if self.levels > 1:
                    near = self._blur(img, self.blur_px * 0.4)
                    m2 = np.clip(mask * 2.0 - 1.0, 0.0, 1.0)
                    m1 = np.clip(mask * 2.0, 0.0, 1.0)
                    mid = cv2.blendLinear(near, far, 1.0 - m2, m2)
                    out = cv2.blendLinear(img, mid, 1.0 - m1, m1)
                else:
                    out = cv2.blendLinear(img, far, 1.0 - mask, mask)
        if self.bloom_on and self.bloom_k > 0:
            sw, sh = max(2, self.w // 4), max(2, self.h // 4)
            small = cv2.resize(out, (sw, sh), interpolation=cv2.INTER_AREA)
            br = cv2.subtract(small, (self.thr, self.thr, self.thr, 0))
            if int(br.max()) > 0:
                br = cv2.GaussianBlur(br, (0, 0), max(0.8, self.bloom_px / 4))
                up = cv2.resize(br, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
                out = cv2.addWeighted(out, 1.0, up, self.bloom_k * 255.0 / max(1, 255 - self.thr), 0.0)
        return out
