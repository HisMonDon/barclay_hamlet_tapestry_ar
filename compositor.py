import math

import cv2
import numpy as np

from ink import hex_bgr
from timeline import Timeline
from atmosphere import Atmosphere
from lens import Lens
from particles import Motes

INV = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP


def fit_matrix(src_w, src_h, ow, oh):
    s = min(ow / src_w, oh / src_h)
    x0, y0 = (ow - src_w * s) / 2, (oh - src_h * s) / 2
    return np.array([[1 / s, 0, -x0 / s], [0, 1 / s, -y0 / s], [0, 0, 1]], np.float64)


def blend_matrices(Ma, Mb, f, ow, oh):
    if f <= 0.0:
        return Ma
    if f >= 1.0:
        return Mb
    rect = np.float32([[(0, 0), (ow, 0), (ow, oh), (0, oh)]])
    a = cv2.perspectiveTransform(rect, Ma)[0]
    b = cv2.perspectiveTransform(rect, Mb)[0]
    return cv2.getPerspectiveTransform(rect[0], (a + (b - a) * f).astype(np.float32)).astype(np.float64)


class Compositor:
    def __init__(self, cfg, scene, timeline=None):
        self.cfg = cfg
        self.scene = scene
        self.timeline = timeline or Timeline(cfg)
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        sc = cfg.get("output_scale", 1.0)
        self.ow = int(round(cfg["output"]["width"] * sc))
        self.oh = int(round(cfg["output"]["height"] * sc))
        self.bg = tuple(int(v) for v in hex_bgr(cfg["background"]))
        scale = cfg.get("quality", {}).get(cfg.get("quality_name", "live"), {}).get("composite_scale", 1.0)
        self.work = (max(2, int(self.ow * scale) // 2 * 2), max(2, int(self.oh * scale) // 2 * 2))
        self.mult_inv = np.linalg.inv(scene.mult_to_tapestry())
        self.over_inv = np.linalg.inv(scene.over_to_tapestry())
        self.atmosphere = Atmosphere(cfg, self.work)
        self.lens = Lens(cfg, self.work)
        self.motes = Motes(cfg, self.work, self.timeline)
        sh = cfg.get("ink", {}).get("sheen", {})
        c = hex_bgr(sh.get("colour", "#ffe4c0")) / 255.0
        self.spec_col = (float(c[0]) / max(1e-6, float(c[2])), float(c[1]) / max(1e-6, float(c[2])))
        self.glow = sh.get("glow", 0.6)
        self.shadow_k = cfg.get("weeds", {}).get("shadow_soft", {}).get("strength", 0.55)
        d = cfg.get("lens", {}).get("dof", {})
        self.dof = d.get("strength", 0.85)
        self.dof_wide = d.get("wide", [0.6, 1.4, 0.3])
        self.source_artwork = None
        self.source_artwork_inv = None

    def set_source_artwork(self, image):
        self.source_artwork = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
        self.source_artwork_inv = np.diag([image.shape[1] / self.tw, image.shape[0] / self.th, 1.0])

    def crop_matrix(self, t):
        x, y, w, _, roll = self.timeline.camera_pose(t)
        cw = w * self.tw
        ch = cw * self.oh / self.ow
        sx, sy = cw / self.ow, ch / self.oh
        c, s = math.cos(roll), math.sin(roll)
        hw, hh = self.ow / 2.0, self.oh / 2.0
        return np.array([[c * sx, -s * sy, x * self.tw - c * sx * hw + s * sy * hh],
                         [s * sx, c * sy, y * self.th - s * sx * hw - c * sy * hh],
                         [0, 0, 1]], np.float64)

    def view(self, t, H, frame_shape, virtual=True):
        fit = fit_matrix(frame_shape[1], frame_shape[0], self.ow, self.oh)
        if not virtual or H is None:
            return fit
        f = self.timeline.camera_full(t)[3]
        return blend_matrices(H @ self.crop_matrix(t), fit, f, self.ow, self.oh)

    def fit(self, frame):
        return cv2.warpPerspective(frame, fit_matrix(frame.shape[1], frame.shape[0], self.ow, self.oh),
                                   (self.ow, self.oh), flags=INV, borderMode=cv2.BORDER_CONSTANT, borderValue=self.bg)

    def _warp1(self, img, M, size):
        return cv2.warpPerspective(img, M, size, flags=INV, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    @staticmethod
    def _roi(box, Msrc, w, h, grow=2):
        """Screen rectangle covering a source-space box (Msrc maps screen to source), or None if off screen."""
        if box is None:
            return 0, 0, w, h
        x0, y0, x1, y1 = box
        q = np.linalg.inv(Msrc) @ np.array([[x0, x1, x1, x0], [y0, y0, y1, y1], [1, 1, 1, 1]], np.float64)
        if (q[2] <= 1e-9).any():
            return 0, 0, w, h
        xs, ys = q[0] / q[2], q[1] / q[2]
        rx0, ry0 = max(0, int(math.floor(xs.min())) - grow), max(0, int(math.floor(ys.min())) - grow)
        rx1, ry1 = min(w, int(math.ceil(xs.max())) + grow), min(h, int(math.ceil(ys.max())) + grow)
        if rx1 <= rx0 or ry1 <= ry0:
            return None
        return rx0, ry0, rx1, ry1

    @staticmethod
    def _warp_roi(img, Msrc, roi, border):
        x0, y0, x1, y1 = roi
        T = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float64)
        return cv2.warpPerspective(img, Msrc @ T, (x1 - x0, y1 - y0), flags=INV, borderMode=cv2.BORDER_CONSTANT, borderValue=border)

    def compose(self, frame, H, M, size, mult, over, extra=None):
        w, h = size
        if np.allclose(M, np.eye(3)) and frame.shape[1] == w and frame.shape[0] == h:
            out = frame.copy()
        else:
            out = cv2.warpPerspective(frame, M, (w, h), flags=INV, borderMode=cv2.BORDER_CONSTANT, borderValue=self.bg)
        Mt = np.linalg.inv(H) @ M
        if self.source_artwork is not None:
            art = cv2.warpPerspective(self.source_artwork, self.source_artwork_inv @ Mt, (w, h), flags=INV,
                                      borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
            clear = cv2.subtract(255, art[..., 3])
            out = cv2.add(cv2.multiply(out, cv2.merge([clear, clear, clear]), scale=1 / 255), art[..., :3])
        ink = cv2.warpPerspective(mult, self.mult_inv @ Mt, (w, h), flags=INV,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        out = cv2.multiply(out, ink, scale=1.0 / 255.0)
        if extra:
            spec = extra.get("spec")
            if spec is not None:
                img, glow, box = spec
                Ms = self.mult_inv @ Mt
                roi = self._roi(box, Ms, w, h)
                if roi is not None:
                    x0, y0, x1, y1 = roi
                    s = self._warp_roi(img, Ms, roi, 0)
                    if glow is not None:
                        s = cv2.addWeighted(s, 1.0, self._warp_roi(glow[0], glow[1] @ Ms, roi, 0), self.glow, 0.0)
                    sb = cv2.convertScaleAbs(s, alpha=self.spec_col[0])
                    sg = cv2.convertScaleAbs(s, alpha=self.spec_col[1])
                    out[y0:y1, x0:x1] = cv2.add(out[y0:y1, x0:x1], cv2.merge([sb, sg, s]))
            shade = extra.get("shade")
            if shade is not None:
                img, D, box = shade
                Md = D @ self.over_inv @ Mt
                roi = self._roi(box, Md, w, h)
                if roi is not None:
                    x0, y0, x1, y1 = roi
                    m3 = self._warp_roi(img, Md, roi, (255, 255, 255))
                    out[y0:y1, x0:x1] = cv2.multiply(out[y0:y1, x0:x1], m3, scale=1.0 / 255.0)
        if over is None:
            return out
        Mo = self.over_inv @ Mt
        roi = self._roi(extra.get("over_box"), Mo, w, h) if extra and "over_box" in extra else (0, 0, w, h)
        if roi is None:
            return out
        x0, y0, x1, y1 = roi
        top = self._warp_roi(over, Mo, roi, (0, 0, 0, 0))
        clear = cv2.subtract(255, top[:, :, 3])
        sub = cv2.multiply(out[y0:y1, x0:x1], cv2.merge([clear, clear, clear]), scale=1.0 / 255.0)
        out[y0:y1, x0:x1] = cv2.add(sub, top[:, :, :3])
        return out

    def composite(self, frame, H, mult, over, extra=None):
        return self.compose(frame, H, np.eye(3), (frame.shape[1], frame.shape[0]), mult, over, extra)

    def dof_strength(self, w, f):
        w0, w1, lo = self.dof_wide
        k = 1.0 - (1.0 - lo) * min(1.0, max(0.0, (w - w0) / max(1e-6, w1 - w0)))
        return self.dof * k * (1.0 - f)

    def _lens(self, out, t, H, M):
        if not self.lens.active:
            return out
        x, y, w, f = self.timeline.camera_full(t)
        fx, fy, defocus = self.timeline.focus(t)
        p = np.linalg.inv(M) @ H @ np.array([fx * self.tw, fy * self.th, 1.0])
        focus = (float(p[0] / p[2]), float(p[1] / p[2]))
        return self.lens.apply(out, focus, defocus * (1.0 - f), self.dof_strength(w, f))

    def frame(self, frame, H, t, virtual=True):
        if H is None:
            return self.fit(frame)
        M = self.view(t, H, frame.shape, virtual)
        hold = self.timeline.starts[self.timeline.ids["B21"]] if "B21" in self.timeline.ids else self.timeline.end
        tt = min(t, hold)
        work_w, work_h = self.work
        M = M @ np.diag([self.ow / work_w, self.oh / work_h, 1.0])
        layers = self.scene.layers(tt)
        extra = self.scene.extras(tt) if hasattr(self.scene, "extras") else None
        out = self.compose(frame, H, M, self.work, *layers, extra=extra)
        if virtual:
            out = self._lens(out, tt, H, M)
            out = self.motes.apply(out, tt, self.timeline.camera(tt))
        out = self.atmosphere.apply(out, t)
        return out if self.work == (self.ow, self.oh) else cv2.resize(out, (self.ow, self.oh), interpolation=cv2.INTER_LINEAR)
