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


def load_source(cfg, path):
    p = resolve(cfg, path or cfg.get("scan", "assets/reference_tapestry.png"))
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        img, _ = reference.render_tapestry(cfg)
    return img


class Source:
    def __init__(self, renderer, mode, path, size):
        cfg = renderer.cfg
        self.renderer = renderer
        self.mode = mode
        self.cap = None
        self.H = None
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

    def get(self):
        if self.cap is None:
            return self.frame, self.H
        ok, f = self.cap.read()
        if ok:
            self.frame = f
        if self.frame is None:
            return None, None
        return self.frame, self.renderer.tracker.update(self.frame)


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


def soundtrack(renderer, path):
    try:
        from sound import Soundtrack
    except ImportError:
        return None
    st = Soundtrack(renderer.cfg, renderer.timeline, renderer.scene)
    st.write(path)
    return path


def mux(video, audio, out):
    exe = ffmpeg_path()
    if not exe or audio is None:
        return False
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio), "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    return subprocess.run(cmd).returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Render the timeline offline to MP4 and a contact sheet")
    ap.add_argument("--mode", choices=["still", "board", "video"], default="board",
                    help="still: over the scan image; board: synthetic camera frame of the board; video: a recorded camera clip")
    ap.add_argument("--source", default=None, help="scan image (still/board) or camera video (video)")
    ap.add_argument("--beats", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--quality", default="render", choices=["render", "live"])
    ap.add_argument("--out", default=str(ROOT / "renders" / "backup.mp4"))
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
    args = ap.parse_args()

    cfg = load(args.beats or default_beats(args.config), args.config, quality=args.quality)
    if args.no_atmosphere:
        for k in cfg.get("atmosphere", {}).values():
            k["on"] = False
    renderer = Renderer(cfg)
    tl = renderer.timeline
    size = tuple(int(v) for v in args.size.lower().split("x"))
    src = Source(renderer, args.mode, args.source, size)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet_path = Path(args.sheet) if args.sheet else out.with_name(out.stem + "_sheet.png")
    frames_dir = Path(args.frames) if args.frames else None
    if frames_dir:
        frames_dir.mkdir(parents=True, exist_ok=True)

    if args.times:
        for t in (float(v) for v in args.times.split(",")):
            frame, H = src.get()
            img = renderer.frame(frame, H, t)
            p = (frames_dir or out.parent) / f"t{t:06.2f}.png"
            cv2.imwrite(str(p), img)
            print(f"wrote {p}")
        return

    picks = sheet_times(tl)
    shots = []
    t0 = time.perf_counter()
    for bid, name, t in picks:
        frame, H = src.get()
        img = renderer.frame(frame, H, t)
        shots.append(((bid, name, t), img))
        if frames_dir:
            cv2.imwrite(str(frames_dir / f"{bid}_{name}.png"), img)
    cv2.imwrite(str(sheet_path), contact_sheet(shots))
    print(f"wrote {sheet_path} ({(time.perf_counter() - t0) / len(picks) * 1000:.0f} ms/frame)")
    if args.sheet_only:
        return

    end = tl.end if args.end is None else args.end
    n = int(round((end - args.start) * args.fps)) + 1
    raw = out.with_name(out.stem + "_video.mp4")
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (renderer.ow, renderer.oh))
    if not writer.isOpened():
        raise SystemExit("Could not open the MP4 writer")
    if args.mode == "video":
        src.cap.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000.0)
    t0 = time.perf_counter()
    for k in range(n):
        t = args.start + k / args.fps
        frame, H = src.get()
        writer.write(renderer.frame(frame, H, t))
        if k % 150 == 0:
            print(f"  {t:6.2f} s  {k + 1}/{n}")
    writer.release()
    dt = time.perf_counter() - t0
    print(f"rendered {n} frames in {dt:.1f} s ({dt / n * 1000:.1f} ms/frame)")
    wav = None if args.no_audio else soundtrack(renderer, out.with_suffix(".wav"))
    if wav and mux(raw, wav, out):
        raw.unlink()
        print(f"wrote {out} with audio")
    else:
        raw.replace(out)
        print(f"wrote {out}" + (f" and {wav} (ffmpeg not found, audio not muxed)" if wav else ""))


if __name__ == "__main__":
    main()
