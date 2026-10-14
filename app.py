import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from scene import Scene
from compositor import Compositor
from config import CONFIG, default_beats, load
from timeline import Timeline
from tracking import BoardTracker
from live import BackupPlayer, ResilientCamera, open_camera
from sound import AudioPlayer, Soundtrack, backup_track
from config import resolve

KEYS_LEFT = {2424832, 65361, 63234, 81}
KEYS_RIGHT = {2555904, 65363, 63235, 83}
KEYS_QUIT = {27, ord("q"), ord("Q")}
WINDOW = "Tapestry"


class Renderer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.timeline = Timeline(cfg)
        self.scene = Scene(cfg, self.timeline)
        self.ink = self.scene
        settings = dict(cfg.get("tracking", {}))
        self.track_interval = max(1, settings.pop("interval_frames", 1))
        self.track_count = 0
        self.tracker = BoardTracker(cfg, **settings)
        self.comp = Compositor(cfg, self.scene, self.timeline)
        self.ow, self.oh = self.comp.ow, self.comp.oh

    def composite(self, frame, H, mult, over):
        return self.comp.composite(frame, H, mult, over)

    def fit(self, frame):
        return self.comp.fit(frame)

    def frame(self, frame, H, t, virtual=True):
        return self.comp.frame(frame, H, t, virtual)

    def render(self, frame, t, virtual=True):
        H = self.tracker.update(frame) if self.tracker.H is None or self.track_count % self.track_interval == 0 else self.tracker.H
        self.track_count += 1
        if H is None:
            return self.fit(frame), False
        return self.frame(frame, H, t, virtual), True


def hud(img, lines):
    y = img.shape[0] - 24 - 30 * (len(lines) - 1)
    for line in lines:
        cv2.putText(img, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(img, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 225, 200), 1, cv2.LINE_AA)
        y += 30


def main():
    ap = argparse.ArgumentParser(description="Live AR ink tapestry")
    ap.add_argument("--beats", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--camera", default=None, help="device index or stream URL")
    ap.add_argument("--capture-size", default=None)
    ap.add_argument("--tone", default=None)
    ap.add_argument("--windowed", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--backup", default=None)
    ap.add_argument("--play-backup", action="store_true")
    args = ap.parse_args()

    cfg = load(args.beats or default_beats(args.config), args.config)
    cv2.setNumThreads(cfg.get("performance", {}).get("opencv_threads", 4))
    if args.no_audio:
        cfg.setdefault("sound", {})["on"] = False
    if args.tone:
        cfg["ink"]["tone"] = args.tone
    renderer = Renderer(cfg)
    timeline = renderer.timeline
    camera_settings = cfg.get("camera_input", {})
    cw, ch = (int(v) for v in (args.capture_size or camera_settings.get("capture_size", "1920x1080")).lower().split("x"))
    camera = ResilientCamera(args.camera or camera_settings.get("source", "0"), (cw, ch), camera_settings)
    backup = BackupPlayer(resolve(cfg, args.backup or cfg.get("backup_video", "renders/backup.mp4")))
    using_backup = args.play_backup and backup.ready
    if args.play_backup and not backup.ready:
        raise SystemExit("Backup video is unavailable; render it first with render.py")
    if not args.play_backup:
        camera.start()
    track = Soundtrack(cfg, timeline, renderer.scene)
    live_audio = AudioPlayer(track)
    backup_audio = AudioPlayer(backup_track(track, backup.path)) if backup.ready else live_audio
    audio = backup_audio if using_backup else live_audio

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    if not args.windowed:
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    frame, final_frame = None, None
    debug = args.debug or cfg.get("debug", {}).get("hud", False)
    t, playing, virtual = 0.0, True, True
    hold = timeline.at("B21") if "B21" in timeline.ids else timeline.end
    last = time.perf_counter()
    notice = ""
    try:
        while True:
            now = time.perf_counter()
            ready = using_backup or renderer.tracker.H is not None
            if playing and ready:
                t = min(timeline.end, t + now - last)
            last = now
            frame, connected = camera.latest()
            locked = False
            if using_backup:
                out = backup.frame(t).copy()
                ready = True
            elif t >= hold and final_frame is not None:
                out = final_frame.copy()
                locked = True
            elif frame is not None:
                out, locked = renderer.render(frame, t, virtual)
                if t >= hold:
                    final_frame = out.copy()
            else:
                out = np.full((renderer.oh, renderer.ow, 3), renderer.comp.bg, np.uint8)
            audio.sync(t, playing and ready, now)
            lines = []
            if debug:
                if not connected and not using_backup:
                    lines.append("Camera reconnecting; retaining last frame")
                if not locked and not using_backup:
                    lines.append("Looking for markers 0-3: " + (", ".join(map(str, renderer.tracker.seen)) or "none"))
                if audio.error:
                    lines.append("Audio: " + audio.error)
                if audio.soundtrack.warnings:
                    lines.append(audio.soundtrack.warnings[-1])
                lines.append(f"{'BACKUP' if using_backup else 'LIVE'} {'PLAY' if playing else 'PAUSED'}  {t:5.2f}s" + ("  " + notice if notice else ""))
            if lines:
                hud(out, lines)
            cv2.imshow(WINDOW, out)
            key = cv2.waitKeyEx(1)
            if key in KEYS_QUIT:
                break
            force = False
            if key == 32:
                playing = not playing
                force = True
            elif key in KEYS_LEFT:
                t = timeline.step(t, -1)
                force = True
            elif key in KEYS_RIGHT:
                t = timeline.step(t, +1)
                force = True
            elif key in (ord("r"), ord("R")):
                t, playing = 0.0, True
                force = True
            elif key in (ord("v"), ord("V")):
                virtual = not virtual
                final_frame = None
            elif key in (ord("d"), ord("D")):
                debug = not debug
            elif key in (ord("b"), ord("B")):
                if backup.ready:
                    audio.stop()
                    audio = backup_audio
                    using_backup, t, playing = True, 0.0, True
                    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                    force = True
                else:
                    notice = "Backup unavailable"
            elif key in (ord("l"), ord("L")):
                audio.stop()
                audio = live_audio
                using_backup = False
                if camera.thread is None:
                    camera.start()
                force = True
            if force:
                final_frame = None
                audio.sync(t, playing and (using_backup or locked), time.perf_counter(), force=True)
                last = time.perf_counter()
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        live_audio.close()
        if backup_audio is not live_audio:
            backup_audio.close()
        camera.close()
        backup.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
