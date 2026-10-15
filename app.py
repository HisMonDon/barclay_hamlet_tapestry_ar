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
from controls import Controls, Rebuild

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
    ap.add_argument("--quality", default="live", choices=["live", "render"], help="starting quality preset (P switches)")
    ap.add_argument("--controls", action="store_true", help="open the operator controls window (C toggles)")
    args = ap.parse_args()

    beats_path = args.beats or default_beats(args.config)
    cfg = load(beats_path, args.config, quality=args.quality)
    cv2.setNumThreads(cfg.get("performance", {}).get("opencv_threads", 4))
    if args.no_audio:
        cfg.setdefault("sound", {})["on"] = False
    if args.tone:
        if args.tone not in cfg["ink"]["tones"]:
            raise SystemExit(f"Unknown tone {args.tone!r}; choose from {', '.join(cfg['ink']['tones'])}")
        cfg["ink"]["tone"] = args.tone
    renderer = Renderer(cfg)
    timeline = renderer.timeline
    tones = list(cfg["ink"]["tones"])
    quality = args.quality
    controls = Controls(timeline, tones, cfg["ink"]["tone"], quality)
    rebuild = Rebuild()

    def make_renderer(q, tone):
        c = load(beats_path, args.config, quality=q)
        c["ink"]["tone"] = tone
        if args.no_audio:
            c.setdefault("sound", {})["on"] = False
        return Renderer(c)
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
    if args.controls:
        controls.show()

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
            fresh = rebuild.take()
            if fresh is not None:
                fresh.tracker.H, fresh.tracker.quad = renderer.tracker.H, renderer.tracker.quad
                fresh.track_count = renderer.track_count
                if fresh.cfg["ink"]["tone"] != controls.tone:
                    fresh.scene.set_tone(controls.tone)
                renderer, quality = fresh, rebuild.name
                controls.quality, controls.status = quality, f"Quality preset: {quality}"
                final_frame = None
            elif rebuild.error:
                controls.status, rebuild.error = f"Quality switch failed: {rebuild.error}", None
            controls.draw(out, t, playing, [f"Building {rebuild.name} preset..."] if rebuild.busy else [])
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
                lines.append(f"{'BACKUP' if using_backup else 'LIVE'} {'PLAY' if playing else 'PAUSED'}  {t:5.2f}s  {controls.tone}  {quality}"
                             + ("  building " + rebuild.name if rebuild.busy else "") + ("  " + notice if notice else ""))
            if lines:
                hud(out, lines)
            cv2.imshow(WINDOW, out)
            key = cv2.waitKeyEx(1)
            if key in KEYS_QUIT:
                break
            force = False
            acts = controls.poll()
            if key in (ord("c"), ord("C")):
                controls.toggle()
            elif key in (ord("t"), ord("T")):
                acts.append(("tone", tones[(tones.index(controls.tone) + 1) % len(tones)]))
            elif key in (ord("p"), ord("P")):
                acts.append(("quality", "render" if quality == "live" else "live"))
            for act in acts:
                if act[0] == "seek":
                    t = min(timeline.end, max(0.0, act[1]))
                    force = True
                elif act[0] == "step":
                    t = timeline.step(t, act[1])
                    force = True
                elif act[0] == "toggle":
                    playing = not playing
                    force = True
                elif act[0] == "restart":
                    t, playing = 0.0, True
                    force = True
                elif act[0] == "tone" and act[1] != controls.tone:
                    renderer.scene.set_tone(act[1])
                    controls.tone = act[1]
                    controls.status = f"Ink tone: {act[1]}"
                    final_frame = None
                elif act[0] == "quality" and act[1] != quality and not using_backup:
                    if rebuild.start(act[1], lambda q=act[1], tn=controls.tone: make_renderer(q, tn)):
                        controls.status = f"Building the {act[1]} preset in the background; the show keeps running"
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
