import cv2
import numpy as np

MARKER_IDS = (0, 1, 2, 3)


def marker_layout(cfg):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    dw, dh = cfg["board"]["drawing_mm"]
    m, g = cfg["board"]["marker_mm"], cfg["board"]["gap_mm"]
    sx, sy = tw / dw, th / dh
    origins = {0: (-g - m, -g - m), 1: (dw + g, -g - m), 2: (dw + g, dh + g), 3: (-g - m, dh + g)}
    layout = {}
    for i, (ox, oy) in origins.items():
        sq = [(ox, oy), (ox + m, oy), (ox + m, oy + m), (ox, oy + m)]
        layout[i] = np.array([(x * sx, y * sy) for x, y in sq], np.float32)
    return layout


class BoardTracker:
    def __init__(self, cfg, min_markers=3, smoothing=0.35, snap_px=30.0, max_rms=0.006, detect_width=960):
        self.layout = marker_layout(cfg)
        tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        self.rect = np.array([(0, 0), (tw, 0), (tw, th), (0, th)], np.float32)
        params = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), params)
        self.min_markers = min_markers
        self.smoothing = smoothing
        self.snap_px = snap_px
        self.max_rms = max_rms
        self.detect_width = detect_width
        self.criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
        self.quad = None
        self.H = None
        self.seen = ()

    def _detect(self, gray, scale):
        img = gray if scale == 1.0 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        corners, ids, _ = self.detector.detectMarkers(img)
        found = {}
        if ids is not None:
            for c, i in zip(corners, ids.ravel()):
                i = int(i)
                if i in self.layout and i not in found:
                    found[i] = c.reshape(4, 2) / scale
        return found

    def _measure(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        scale = min(1.0, self.detect_width / gray.shape[1])
        found = self._detect(gray, scale)
        if len(found) < len(MARKER_IDS) and scale < 1.0:
            full = self._detect(gray, 1.0)
            if len(full) > len(found):
                found = full
        self.seen = tuple(sorted(found))
        if found:
            pts = np.concatenate([found[i] for i in self.seen]).astype(np.float32).reshape(-1, 1, 2)
            cv2.cornerSubPix(gray, pts, (5, 5), (-1, -1), self.criteria)
            for k, i in enumerate(self.seen):
                found[i] = pts[4 * k:4 * k + 4].reshape(4, 2)
        if len(found) < self.min_markers:
            return None
        src = np.concatenate([self.layout[i] for i in self.seen])
        dst = np.concatenate([found[i] for i in self.seen]).astype(np.float32)
        H, _ = cv2.findHomography(src, dst, 0)
        if H is None:
            return None
        proj = cv2.perspectiveTransform(src[None], H)[0]
        rms = float(np.sqrt(np.mean(np.sum((proj - dst) ** 2, axis=1))))
        if rms > self.max_rms * max(frame.shape[:2]):
            return None
        quad = cv2.perspectiveTransform(self.rect[None], H)[0]
        if not cv2.isContourConvex(quad.astype(np.float32)) or cv2.contourArea(quad) < 400:
            return None
        return quad

    def update(self, frame):
        quad = self._measure(frame)
        if quad is None:
            return self.H
        if self.quad is None or float(np.abs(quad - self.quad).max()) > self.snap_px:
            self.quad = quad
        else:
            self.quad = self.quad + self.smoothing * (quad - self.quad)
        self.H = cv2.getPerspectiveTransform(self.rect, self.quad.astype(np.float32))
        return self.H
