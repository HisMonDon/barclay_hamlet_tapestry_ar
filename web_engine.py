"""Rendering service behind the browser UI (server.py).

It wraps the same Renderer, Timeline, Soundtrack and BoardTracker as app.py. Every image is a pure function
of the timeline position t the browser asks for, so the browser's audio clock drives the show and pausing,
seeking and restarting stay in step with the sound.

Virtual Explore asks for a flat texture of the whole board: the printed drawing, the white board with its four
markers and a strip of desk for the spill, all composited at t without any camera. Scan Tapestry sends camera
frames (or uses a stream URL or the phone relay) and gets back the tracked composite that app.py would show.
"""

import threading
import time

import cv2
import numpy as np

from app import Renderer
from config import CONFIG, default_beats, load
from live import ResilientCamera
from reference import wood
from render import load_source
from sound import Soundtrack, wav_bytes
from tracking import marker_layout

BOARD_MARGIN_MM = 80.0
TEXTURE_MARGIN_MM = (86.0, 86.0, 86.0, 150.0)
DESK_MM = (1500.0, 950.0)
DESK_PX_PER_MM = 1.4
BOARD_BGR = (226, 231, 234)
DETAIL = {"standard": 0.8, "high": 1.4}
PATH_RATE = 60


def encode(img, quality=85):
    ok, data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return data.tobytes()


def decode(data):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if img is None:
        raise ValueError("Camera frame is not a readable JPEG or PNG image")
    return img


class Board:
    """The virtual board, measured in millimetres from the drawing's top-left corner.

    The texture covers the board and a strip of desk below it, so weeds and the Serpent's tail can leave the
    board in S15. The desk strip is cut from the same wood image the browser lays on the 3D desk, so the two meet
    without a seam.
    """

    def __init__(self, cfg, scan):
        self.cfg = cfg
        self.tw, self.th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        self.dw, self.dh = cfg["board"]["drawing_mm"]
        self.k = self.tw / self.dw
        l, t, r, b = TEXTURE_MARGIN_MM
        self.region = (-l, -t, self.dw + r, self.dh + b)
        m = BOARD_MARGIN_MM
        self.board = (-m, -m, self.dw + m, self.dh + m)
        self.desk = ((self.dw - DESK_MM[0]) / 2, (self.dh - DESK_MM[1]) / 2,
                     (self.dw + DESK_MM[0]) / 2, (self.dh + DESK_MM[1]) / 2)
        self.desk_img = wood(int(round(DESK_MM[0] * DESK_PX_PER_MM)), int(round(DESK_MM[1] * DESK_PX_PER_MM)))
        self.scan = scan
        self.frames = {}

    def describe(self):
        markers = {str(i): [[round(float(x) / self.k, 2), round(float(y) / self.k, 2)] for x, y in sq]
                   for i, sq in marker_layout(self.cfg).items()}
        return {"drawing_mm": [self.dw, self.dh], "board_mm": list(self.board), "texture_mm": list(self.region),
                "desk_mm": list(self.desk), "markers_mm": markers, "marker_mm": self.cfg["board"]["marker_mm"],
                "gap_mm": self.cfg["board"]["gap_mm"], "paper": self.cfg["board"]["paper"]}

    def frame(self, detail):
        """Static background for one detail level and H, the map from tapestry pixels to texture pixels."""
        if detail in self.frames:
            return self.frames[detail]
        s = DETAIL[detail] * self.k
        x0, y0, x1, y1 = self.region
        W, H = int(round((x1 - x0) * s)), int(round((y1 - y0) * s))
        dx0, dy0 = self.desk[:2]
        A = np.float64([[DESK_PX_PER_MM / s, 0, (x0 - dx0) * DESK_PX_PER_MM], [0, DESK_PX_PER_MM / s, (y0 - dy0) * DESK_PX_PER_MM]])
        img = cv2.warpAffine(self.desk_img, A, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
        px = lambda x, y: (int(round((x - x0) * s)), int(round((y - y0) * s)))
        shadow = np.zeros((H, W), np.float32)
        bx0, by0, bx1, by1 = self.board
        cv2.rectangle(shadow, px(bx0 + 1.5, by0 + 3), px(bx1 + 1.5, by1 + 3), 1.0, -1)
        shadow = cv2.GaussianBlur(shadow, (0, 0), 2.5 * s)
        img = (img * (1.0 - 0.45 * shadow[..., None])).astype(np.uint8)
        cv2.rectangle(img, px(bx0, by0), px(bx1, by1), BOARD_BGR, -1)
        paper = np.zeros((H, W), np.float32)
        cv2.rectangle(paper, px(0.4, 0.8), px(self.dw + 0.4, self.dh + 0.8), 1.0, -1)
        paper = cv2.GaussianBlur(paper, (0, 0), 0.9 * s)
        img = (img * (1.0 - 0.12 * paper[..., None])).astype(np.uint8)
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        for i, sq in marker_layout(self.cfg).items():
            (mx0, my0), (mx1, my1) = px(*(sq[0] / self.k)), px(*(sq[2] / self.k))
            side = max(6, min(mx1 - mx0, my1 - my0))
            img[my0:my0 + side, mx0:mx0 + side] = cv2.cvtColor(cv2.aruco.generateImageMarker(d, i, side, borderBits=1), cv2.COLOR_GRAY2BGR)
        ax0, ay0 = px(0, 0)
        ax1, ay1 = px(self.dw, self.dh)
        img[ay0:ay1, ax0:ax1] = cv2.resize(self.scan, (ax1 - ax0, ay1 - ay0), interpolation=cv2.INTER_AREA)
        Ht = np.float64([[(ax1 - ax0) / self.tw, 0, ax0], [0, (ay1 - ay0) / self.th, ay0], [0, 0, 1]])
        self.frames[detail] = (img, Ht)
        return self.frames[detail]


class StreamSource:
    """A camera opened on the server: a stream URL (phone app, IP camera) or a device index, as in app.py."""

    def __init__(self, source, cfg):
        settings = cfg.get("camera_input", {})
        w, h = (int(v) for v in settings.get("capture_size", "1920x1080").lower().split("x"))
        self.source = source
        self.camera = ResilientCamera(source, (w, h), settings).start()

    def frame(self):
        return self.camera.latest()

    def status(self):
        frame, connected = self.camera.latest()
        error = self.camera.error
        if connected:
            return "live", None
        if frame is None:
            return ("unavailable", f"Could not open {self.source}: {error}. Retrying.") if error else ("connecting", None)
        return "reconnecting", "The stream stopped sending frames. Showing the last frame and retrying."

    def close(self):
        self.camera.close()


class PhoneRelay:
    """Latest frame posted by the phone page. Frames are kept only while Scan mode uses the phone."""

    STALE_S = 2.5

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.active = False
        self.data = None
        self.stamp = None
        self.ping = None
        self.decoded = None
        self.device = ""

    def push(self, data, device=""):
        with self.lock:
            self.ping = self.clock()
            self.device = device[:80]
            if self.active:
                self.data, self.stamp, self.decoded = data, self.ping, None
            return self.active

    def touch(self, device=""):
        with self.lock:
            self.ping = self.clock()
            self.device = device[:80] or self.device
            return self.active

    def set_active(self, on):
        with self.lock:
            self.active = on
            self.data = self.stamp = self.decoded = None

    def frame(self):
        with self.lock:
            data, stamp, decoded = self.data, self.stamp, self.decoded
        if data is None:
            return None, False
        if decoded is None:
            decoded = decode(data)
            with self.lock:
                if self.stamp == stamp:
                    self.decoded = decoded
        return decoded, self.clock() - stamp < self.STALE_S

    def status(self):
        with self.lock:
            now = self.clock()
            seen = self.ping is not None and now - self.ping < self.STALE_S
            fresh = self.stamp is not None and now - self.stamp < self.STALE_S
            if fresh:
                return "live", None
            if self.stamp is not None:
                return "reconnecting", "The phone stopped sending frames. Check that its page is still open and awake."
            if seen:
                return "connecting", "The phone page is open. Tap Start camera on the phone."
            return "waiting", "Scan the QR code with the phone, or open the link shown below on it."

    def connected(self):
        with self.lock:
            return self.ping is not None and self.clock() - self.ping < self.STALE_S


class WebEngine:
    def __init__(self, beats=None, config=str(CONFIG), quality="live"):
        cfg = load(beats or default_beats(config), config, quality=quality)
        cv2.setNumThreads(cfg.get("performance", {}).get("opencv_threads", 4))
        self.cfg = cfg
        self.quality = quality
        self.renderer = Renderer(cfg)
        self.renderer.set_live(True)
        self.timeline = self.renderer.timeline
        self.scene = self.renderer.scene
        self.comp = self.renderer.comp
        # The browser scales the picture to the screen itself, so the scan composite is sent at the
        # compositor's working size rather than resized to the output size and encoded at twice the pixels.
        self.comp.ow, self.comp.oh = self.comp.work
        self.lock = threading.RLock()
        self.board = Board(cfg, self.scene.scan if self.scene.scan is not None else load_source(cfg, None))
        self.soundtrack = Soundtrack(cfg, self.timeline, self.scene)
        self.wav = wav_bytes(self.soundtrack.samples, self.soundtrack.rate)
        self.desk_jpeg = encode(self.board.desk_img, 88)
        self.phone = PhoneRelay()
        self.source_lock = threading.Lock()
        self.source_kind = "none"
        self.stream = None
        self._path = None

    # ---- timeline ---------------------------------------------------------------------------------------
    def info(self):
        tl = self.timeline
        beats = [{"id": b.get("id", f"B{k + 1:02d}"), "name": b.get("name", ""), "desc": b.get("desc", ""),
                  "start": round(tl.starts[k], 4), "duration": b["duration"], "hold": bool(b.get("hold")),
                  "silence": bool(b.get("silence"))} for k, b in enumerate(tl.beats)]
        out = self.cfg.get("output", {"width": 1920, "height": 1080})
        return {"title": self.cfg.get("title", "Hamlet's Tapestry"), "beats": beats, "end": tl.end, "hold": tl.hold_t,
                "silence": round(float(self.soundtrack.silence), 4), "audio": bool(self.cfg.get("sound", {}).get("on", True)),
                "quality": self.quality, "output": [out["width"], out["height"]], "board": self.board.describe(),
                "detail": list(DETAIL), "min_markers": self.renderer.tracker.min_markers}

    def camera_path(self):
        """The show's virtual camera (centre, crop width, full-frame blend, roll) sampled for the 3D follow camera."""
        if self._path is None:
            tl = self.timeline
            n = int(np.ceil(tl.end * PATH_RATE)) + 1
            poses = [[round(float(v), 5) for v in tl.camera_pose(k / PATH_RATE)] for k in range(n)]
            self._path = {"rate": PATH_RATE, "poses": poses, "fields": ["x", "y", "w", "f", "roll"]}
        return self._path

    def clamp(self, t):
        return min(self.timeline.end, max(0.0, float(t)))

    # ---- Virtual Explore -----------------------------------------------------------------------------------
    def virtual(self, t, detail="standard"):
        """The board texture at t: the show without a camera. Frozen from the hold onwards."""
        detail = detail if detail in DETAIL else "standard"
        t = self.clamp(t)
        tt = min(t, self.timeline.hold_t)
        frame, Ht = self.board.frame(detail)
        start = time.perf_counter()
        with self.lock:
            layers = self.scene.layers(tt)
            extra = self.scene.extras(tt)
            realism, self.comp.realism = self.comp.realism, None
            try:
                img = self.comp.compose(frame, Ht, np.eye(3), (frame.shape[1], frame.shape[0]), *layers, extra=extra)
            finally:
                self.comp.realism = realism
        data = encode(img, 90 if detail == "high" else 84)
        return data, {"t": t, "frozen": t >= self.timeline.hold_t, "detail": detail, "size": [img.shape[1], img.shape[0]],
                      "ms": round((time.perf_counter() - start) * 1000, 1)}

    # ---- Scan Tapestry -----------------------------------------------------------------------------------
    def reset_tracking(self):
        with self.lock:
            tr = self.renderer.tracker
            tr.H = tr.quad = None
            tr.seen = ()
            tr.found = False
            self.renderer.track_count = 0
            self.renderer.set_live(True)

    def scan(self, frame, t, follow=True, overlay=False):
        t = self.clamp(t)
        start = time.perf_counter()
        with self.lock:
            out, locked = self.renderer.render(frame, t, follow)
            tr = self.renderer.tracker
            if overlay and locked:
                out = self.comp.debug(out)
            tracking = "searching" if tr.H is None else "locked" if tr.found else "lost"
            seen = [int(x) for x in tr.seen]
        data = encode(out, 82)
        return data, {"t": t, "locked": locked, "tracking": tracking, "seen": seen, "frozen": t >= self.timeline.hold_t,
                      "size": [out.shape[1], out.shape[0]], "camera": [frame.shape[1], frame.shape[0]],
                      "ms": round((time.perf_counter() - start) * 1000, 1)}

    def set_source(self, kind, source=None):
        """Switch Scan mode's camera. Every switch releases the old camera and waits for a fresh marker lock."""
        if kind not in ("none", "browser", "phone", "stream"):
            raise ValueError(f"Unknown camera source {kind!r}")
        source = (source or "").strip()
        if kind == "stream" and not source:
            raise ValueError("Enter a stream URL such as http://192.168.1.20:8080/video, or a camera number such as 0")
        if kind == "stream" and not (source.isdigit() or "://" in source):
            raise ValueError("A stream URL starts with http://, https:// or rtsp://; a camera number is a whole number such as 0")
        with self.source_lock:
            old, self.stream = self.stream, None
            self.phone.set_active(kind == "phone")
            self.source_kind = kind
            if kind == "stream":
                self.stream = StreamSource(source, self.cfg)
        if old is not None:
            old.close()
        self.reset_tracking()
        return self.source_status()

    def source_frame(self):
        """Latest server-side frame (stream or phone) and whether it is fresh."""
        with self.source_lock:
            kind, stream = self.source_kind, self.stream
        if kind == "stream" and stream is not None:
            return stream.frame()
        if kind == "phone":
            return self.phone.frame()
        return None, False

    def source_status(self):
        with self.source_lock:
            kind, stream = self.source_kind, self.stream
        state, message = "idle", None
        if kind == "stream" and stream is not None:
            state, message = stream.status()
        elif kind == "phone":
            state, message = self.phone.status()
        elif kind == "browser":
            state = "browser"
        return {"kind": kind, "state": state, "message": message, "source": stream.source if stream else None,
                "phone_connected": self.phone.connected(), "phone_device": self.phone.device}

    def close(self):
        self.set_source("none")
