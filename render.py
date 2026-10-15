import argparse
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

import reference
from app import Renderer
from config import CONFIG, ROOT, default_beats, load, resolve

FONT = cv2.FONT_HERSHEY_SIMPLEX


def ffmpeg_path():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class VideoWriter:
    def __init__(self, path, fps, size, settings=None):
        settings = settings or {}
        self.process = None
        self.writer = None
        executable = ffmpeg_path()
        if executable:
            width, height = size
            command = [executable, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
                       "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264", "-crf", str(settings.get("video_crf", 16)),
                       "-preset", settings.get("video_preset", "medium"), "-pix_fmt", "yuv420p", str(path)]
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            self.writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)

    def isOpened(self):
        return self.process.poll() is None if self.process is not None else self.writer.isOpened()

    def write(self, frame):
        if self.process is None:
            self.writer.write(frame)
            return
        try:
            self.process.stdin.write(np.ascontiguousarray(frame).tobytes())
        except BrokenPipeError as error:
            message = self.process.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Video encoding failed: {message}") from error

    def release(self):
        if self.process is None:
            self.writer.release()
            return
        self.process.stdin.close()
        self.process.stdin = None
        output, error = self.process.communicate()
        if self.process.returncode:
            raise RuntimeError(f"Video encoding failed: {error.decode('utf-8', errors='replace')}")


def load_source(cfg, path):
    p = resolve(cfg, path or cfg.get("scan", "assets/reference_tapestry.png"))
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        if path is not None:
            raise ValueError(f"Could not read source image: {p}")
        img, _ = reference.render_tapestry(cfg)
    return img


class Source:
    def __init__(self, renderer, mode, path, size):
        cfg = renderer.cfg
        self.renderer = renderer
        self.mode = mode
        self.cap = None
        self.H = None
        self.video_index = -1
        if mode == "video":
            self.cap = cv2.VideoCapture(str(path))
            if not self.cap.isOpened():
                raise SystemExit(f"Could not open video {path}")
            self.frame = None
            return
        tapestry = load_source(cfg, path)
        if mode == "still":
            self.frame, self.H = reference.still_frame(cfg, tapestry)
        else:
            scene, S = reference.board_scene(cfg, tapestry)
            self.frame, truth = reference.camera_frame(cfg, scene, S, size)
            for x in range(3):
                self.H = renderer.tracker.update(self.frame)
            if self.H is None:
                self.H = truth
            quality = cfg.get("quality", {}).get(cfg.get("quality_name", "render"), {})
            if quality.get("direct_scan", True):
                renderer.comp.set_source_artwork(tapestry)

    def get(self, t=None):
        if self.cap is None:
            return self.frame, self.H
        if t is not None:
            fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
            target = max(0, int(t * fps))
            if target == self.video_index and self.frame is not None:
                return self.frame, self.H
            if target != self.video_index + 1:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, f = self.cap.read()
        if ok:
            self.frame = f
            self.video_index = target if t is not None else self.video_index + 1
        if self.frame is None:
            raise ValueError("Source video contains no readable camera frames")
        self.H = self.renderer.tracker.update(self.frame)
        return self.frame, self.H


def sheet_times(tl):
    out = []
    for k, (s, b) in enumerate(zip(tl.starts, tl.beats)):
        frac = 0.5 if k == len(tl.beats) - 1 else 0.8
        out.append((b.get("id", f"B{k + 1:02d}"), b["name"], s + frac * b["duration"]))
    return out


def label(img, text, sub=None):
    h, w = img.shape[:2]
    bar = np.zeros((34 if sub is None else 52, w, 3), np.uint8)
    bar[:] = (24, 20, 18)
    cv2.putText(bar, text, (10, 24), FONT, 0.62, (225, 220, 205), 1, cv2.LINE_AA)
    if sub:
        cv2.putText(bar, sub, (10, 44), FONT, 0.42, (160, 155, 145), 1, cv2.LINE_AA)
    return np.vstack([img, bar])


def contact_sheet(frames, cols=4, thumb=(560, 315)):
    tiles = []
    for (bid, name, t), img in frames:
        im = cv2.resize(img, thumb, interpolation=cv2.INTER_AREA)
        tiles.append(label(im, f"{bid} {name}", f"t = {t:5.2f} s"))
    th, tw = tiles[0].shape[:2]
    rows = (len(tiles) + cols - 1) // cols
    out = np.zeros((rows * (th + 8) + 8, cols * (tw + 8) + 8, 3), np.uint8)
    out[:] = (12, 10, 9)
    for k, tile in enumerate(tiles):
        r, c = divmod(k, cols)
        y, x = 8 + r * (th + 8), 8 + c * (tw + 8)
        out[y:y + th, x:x + tw] = tile
    return out


def soundtrack(renderer, path, start=0.0, end=None):
    from sound import Soundtrack
    st = Soundtrack(renderer.cfg, renderer.timeline, renderer.scene)
    st.write(path, start, end)
    return path


def mux(video, audio, out, codec="alac"):
    exe = ffmpeg_path()
    if not exe or audio is None:
        return False
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio), "-c:v", "copy",
           "-c:a", codec, "-shortest", str(out)]
    return subprocess.run(cmd).returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Render the timeline offline to MP4 and a contact sheet")
    ap.add_argument("--mode", choices=["still", "board", "video"], default="board",
                    help="still: over the scan image; board: synthetic camera frame of the board; video: a recorded camera clip")
    ap.add_argument("--source", default=None, help="scan image (still/board) or camera video (video)")
    ap.add_argument("--beats", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--quality", default="render", choices=["render", "live"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--sheet", default=None, help="contact sheet path (default next to --out)")
    ap.add_argument("--frames", default=None, help="also save the contact-sheet frames full size into this folder")
    ap.add_argument("--sheet-only", action="store_true", help="render only the contact sheet")
    ap.add_argument("--times", default=None, help="comma separated times to render as single frames into --frames")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--size", default="1920x1080", help="synthetic camera frame size for board mode")
    ap.add_argument("--no-atmosphere", action="store_true")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--tone", default=None, help="ink tone name from the beat file (e.g. 'Iron gall', 'Lamp black', 'Oxblood')")
    ap.add_argument("--no-lens", action="store_true", help="disable depth of field, rack focus, bloom and dust motes")
    args = ap.parse_args()

    cfg = load(args.beats or default_beats(args.config), args.config, quality=args.quality)
    if args.tone:
        if args.tone not in cfg["ink"]["tones"]:
            ap.error(f"unknown tone {args.tone!r}; choose from {', '.join(cfg['ink']['tones'])}")
        cfg["ink"]["tone"] = args.tone
    if args.no_lens:
        cfg["fx"].update({"dof": False, "bloom": False, "motes": False})
    cv2.setNumThreads(cfg.get("performance", {}).get("opencv_threads", 4))
    if args.source and args.mode != "video":
        cfg["scan"] = str(Path(args.source).resolve())
    if args.fps <= 0:
        ap.error("--fps must be positive")
    if args.no_atmosphere:
        for k in cfg.get("atmosphere", {}).values():
            k["on"] = False
    renderer = Renderer(cfg)
    tl = renderer.timeline
    size = tuple(int(v) for v in args.size.lower().split("x"))
    src = Source(renderer, args.mode, args.source, size)
    out = Path(args.out) if args.out else resolve(cfg, cfg.get("backup_video", "renders/backup.mp4"))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet_path = Path(args.sheet) if args.sheet else out.with_name(out.stem + "_sheet.png")
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(args.frames) if args.frames else None
    if frames_dir:
        frames_dir.mkdir(parents=True, exist_ok=True)

    if args.times:
        for t in (float(v) for v in args.times.split(",")):
            frame, H = src.get(t)
            img = renderer.frame(frame, H, t)
            p = (frames_dir or out.parent) / f"t{t:06.2f}.png"
            cv2.imwrite(str(p), img)
            print(f"wrote {p}")
        return

    picks = sheet_times(tl)
    shots = []
    t0 = time.perf_counter()
    for bid, name, t in picks:
        frame, H = src.get(t)
        img = renderer.frame(frame, H, t)
        shots.append(((bid, name, t), img))
        if frames_dir:
            cv2.imwrite(str(frames_dir / f"{bid}_{name}.png"), img)
    cv2.imwrite(str(sheet_path), contact_sheet(shots))
    print(f"wrote {sheet_path} ({(time.perf_counter() - t0) / len(picks) * 1000:.0f} ms/frame)")
    if args.sheet_only:
        return

    end = tl.end if args.end is None else min(args.end, tl.end)
    if not 0 <= args.start < end:
        ap.error("render range must satisfy 0 <= --start < --end <= timeline duration")
    n = int(np.ceil((end - args.start) * args.fps))
    raw = out.with_name(out.stem + "_video.mp4")
    writer = VideoWriter(raw, args.fps, (renderer.ow, renderer.oh), cfg.get("quality", {}).get(args.quality, {}))
    if not writer.isOpened():
        raise SystemExit("Could not open the MP4 writer")
    if args.mode == "video":
        src.cap.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000.0)
    t0 = time.perf_counter()
    held = None
    hold = tl.at("B21") if "B21" in tl.ids else tl.end
    for k in range(n):
        t = args.start + k / args.fps
        frame, H = src.get(t)
        if t < hold or held is None:
            image = renderer.frame(frame, H, t)
            if t >= hold:
                held = image.copy()
        else:
            image = held
        writer.write(image)
        if k % 150 == 0:
            print(f"  {t:6.2f} s  {k + 1}/{n}")
    writer.release()
    dt = time.perf_counter() - t0
    print(f"rendered {n} frames in {dt:.1f} s ({dt / n * 1000:.1f} ms/frame)")
    wav = None if args.no_audio else soundtrack(renderer, out.with_suffix(".wav"), args.start, end)
    if wav and mux(raw, wav, out, cfg.get("sound", {}).get("export_codec", "alac")):
        raw.unlink()
        print(f"wrote {out} with audio")
    else:
        raw.replace(out)
        print(f"wrote {out}" + (f" and {wav} (ffmpeg unavailable or mux failed; audio not muxed)" if wav else ""))
    if src.cap is not None:
        src.cap.release()


if __name__ == "__main__":
    main()
