import cv2
import numpy as np

from ink import hex_bgr
from timeline import Timeline
from atmosphere import Atmosphere

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

    def crop_matrix(self, t):
        x, y, w, _ = self.timeline.camera_full(t)
        cw = w * self.tw
        ch = cw * self.oh / self.ow
        return np.array([[cw / self.ow, 0, x * self.tw - cw / 2],
                         [0, ch / self.oh, y * self.th - ch / 2],
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

    def compose(self, frame, H, M, size, mult, over):
        w, h = size
        if np.allclose(M, np.eye(3)) and frame.shape[1] == w and frame.shape[0] == h:
            out = frame.copy()
        else:
            out = cv2.warpPerspective(frame, M, (w, h), flags=INV, borderMode=cv2.BORDER_CONSTANT, borderValue=self.bg)
        Mt = np.linalg.inv(H) @ M
        ink = cv2.warpPerspective(mult, self.mult_inv @ Mt, (w, h), flags=INV,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        out = cv2.multiply(out, ink, scale=1.0 / 255.0)
        if over is None:
            return out
        top = cv2.warpPerspective(over, self.over_inv @ Mt, (w, h), flags=INV,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        clear = cv2.subtract(255, top[:, :, 3])
        out = cv2.multiply(out, cv2.merge([clear, clear, clear]), scale=1.0 / 255.0)
        return cv2.add(out, top[:, :, :3])

    def composite(self, frame, H, mult, over):
        return self.compose(frame, H, np.eye(3), (frame.shape[1], frame.shape[0]), mult, over)

    def frame(self, frame, H, t, virtual=True):
        if H is None:
            return self.fit(frame)
        M = self.view(t, H, frame.shape, virtual)
        hold = self.timeline.starts[self.timeline.ids["B21"]] if "B21" in self.timeline.ids else self.timeline.end
        work_w, work_h = self.work
        M = M @ np.diag([self.ow / work_w, self.oh / work_h, 1.0])
        out = self.atmosphere.apply(self.compose(frame, H, M, self.work, *self.scene.layers(min(t, hold))), t)
        return out if self.work == (self.ow, self.oh) else cv2.resize(out, (self.ow, self.oh), interpolation=cv2.INTER_LINEAR)
