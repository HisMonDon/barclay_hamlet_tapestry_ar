import os
import threading
import time
from pathlib import Path

import cv2


def open_camera(source, width, height, settings=None):
    settings = settings or {}
    source = int(source) if str(source).isdigit() else str(source)
    backends = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "ffmpeg": cv2.CAP_FFMPEG}
    backend = backends.get(settings.get("backend", "auto"), cv2.CAP_ANY)
    if isinstance(source, str) and "://" in source:
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(settings.get("open_timeout_ms", 2000)),
                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(settings.get("read_timeout_ms", 1500))]
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG if backend == cv2.CAP_ANY else backend, params)
    else:
        cap = cv2.VideoCapture(source, backend)
    if width and height and cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class ResilientCamera:
    def __init__(self, source, size, settings, opener=open_camera, clock=time.perf_counter):
        self.source, self.size, self.p = source, size, settings
        self.opener, self.clock = opener, clock
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.frame = None
        self.stamp = None
        self.connected = False
        self.error = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="tapestry-camera", daemon=True)
        self.thread.start()
        return self

    def _run(self):
        while not self.stop_event.is_set():
            cap = None
            try:
                cap = self.opener(self.source, *self.size, self.p)
                if not cap.isOpened():
                    raise OSError("camera unavailable")
                while not self.stop_event.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise OSError("camera disconnected")
                    with self.lock:
                        self.frame, self.stamp, self.connected = frame, self.clock(), True
                        self.error = None
            except (OSError, cv2.error) as error:
                with self.lock:
                    self.connected, self.error = False, str(error)
            finally:
                if cap is not None:
                    cap.release()
            self.stop_event.wait(self.p.get("reconnect_s", 1.5))

    def latest(self):
        with self.lock:
            stale = self.stamp is None or self.clock() - self.stamp > self.p.get("stale_s", 0.75)
            return self.frame, self.connected and not stale

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=0.5)


class BackupPlayer:
    def __init__(self, path):
        self.path = Path(path)
        self.cap = cv2.VideoCapture(str(path)) if self.path.exists() else None
        self.ready = self.cap is not None and self.cap.isOpened()
        self.first = None
        self.last = None
        self.index = -1
        self.fps = 30.0
        self.duration = 0.0
        if self.ready:
            self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
            self.duration = self.cap.get(cv2.CAP_PROP_FRAME_COUNT) / self.fps
            ok, self.first = self.cap.read()
            self.ready = ok
            self.last, self.index = self.first, 0

    def frame(self, t):
        if not self.ready:
            return None
        target = max(0, min(round(self.duration * self.fps) - 1, int(t * self.fps)))
        if target == 0 and self.index != 0:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 1)
            self.index, self.last = 0, self.first
        elif target < self.index or target > self.index + 5:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = self.cap.read()
            if ok:
                self.last, self.index = frame, target
        else:
            while self.index < target:
                ok, frame = self.cap.read()
                if not ok:
                    break
                self.last, self.index = frame, self.index + 1
        return self.last

    def close(self):
        if self.cap is not None:
            self.cap.release()
