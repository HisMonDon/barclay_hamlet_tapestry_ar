"""Export Virtual Explore as a static website (no camera, no Python on the host).

    .venv\\Scripts\\python.exe export_static.py            # writes site/

The engine renders the board texture for every frame up to the hold and encodes it as an H.264 video. The page
plays that video on the 3D board with the same controls as the local app. The site is silent unless
--audio adds the soundtrack. Upload the
site/ folder to any static host (Vercel, GitHub Pages, Netlify).
"""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from config import CONFIG, ROOT, resolve
from render import ffmpeg_path
from web_engine import WebEngine

WEB = ROOT / "web"
SKIP = {"phone.html", "phone.css", "phone.js"}


def even(v):
    return int(v) // 2 * 2


def source_fingerprint(cfg):
    """Identify the show and renderer, without invalidating the video for HTML/CSS/JS edits."""
    digest = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode("utf-8"))
    paths = list(ROOT.glob("*.py"))
    assets = list(cfg.get("assets", {}).values()) + [cfg.get("scan")]
    paths += [resolve(cfg, value) for value in assets if value]
    for path in sorted(set(paths)):
        if path.is_file() and not path.name.startswith("test_"):
            digest.update(str(path).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def cached_video(out, fingerprint):
    """A page refresh must never silently pair new show data with an old animation."""
    try:
        meta = json.loads((out / "data" / "video.json").read_text(encoding="utf-8"))
        valid = (meta.get("source") == fingerprint and (out / "media" / "board.mp4").stat().st_size > 0)
        valid = valid and meta["fps"] > 0 and meta["frames"] > 0 and meta["duration"] > 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise SystemExit("--page-only needs a complete export of the current show. Run without --page-only first.")
    return meta


def encode_video(engine, path, fps, width, crf):
    """Render the board texture from 0 to the hold and pipe it to ffmpeg. Frames after the hold are identical,
    so the video stops there and the page holds its last frame."""
    exe = ffmpeg_path()
    if exe is None:
        raise SystemExit("ffmpeg is unavailable: pip install imageio-ffmpeg")
    detail = "standard"
    first = engine.texture(0.0, detail)
    if width > first.shape[1]:
        detail = "high"
        first = engine.texture(0.0, detail)
    h, w = first.shape[:2]
    W = even(width)
    H = max(2, even(round(W * h / w)))
    hold = engine.timeline.hold_t
    # Include the exact hold even when it falls between video frame timestamps.
    n = int(math.ceil(hold * fps)) + 1
    pending = path.with_name(path.stem + ".tmp" + path.suffix)
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-g", str(max(1, fps // 2)), "-movflags", "+faststart", str(pending)]
    start = time.perf_counter()
    encoded = False
    try:
        # A file avoids blocking if ffmpeg fills its stderr pipe before the frame loop completes.
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errors,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                for k in range(n):
                    img = first if k == 0 else engine.texture(min(k / fps, hold), detail)
                    proc.stdin.write(np.ascontiguousarray(cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)).tobytes())
                    if k % (fps * 10) == 0:
                        print(f"  {k / fps:5.1f} s  {k + 1}/{n} frames", flush=True)
            except BrokenPipeError:
                pass  # Report ffmpeg's actual error below.
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
                proc.wait()
            if proc.returncode:
                errors.seek(0)
                raise SystemExit(f"Video encoding failed: {errors.read().decode('utf-8', errors='replace')}")
        encoded = True
        try:
            pending.replace(path)
        except PermissionError as error:
            raise SystemExit(f"The completed video is saved at {pending}, but {path} could not be replaced. "
                             "Close any preview using it and rerun the full export to rebuild the video, "
                             "show data and page together.") from error
    finally:
        if not encoded:
            pending.unlink(missing_ok=True)
    print(f"  rendered {n} frames in {time.perf_counter() - start:.0f} s")
    return {"url": "media/board.mp4", "fps": fps, "frames": n, "duration": n / fps, "width": W, "height": H}


def write_page(out):
    static = out / "static"
    if static.exists():
        shutil.rmtree(static)
    shutil.copytree(WEB, static, ignore=lambda d, names: [x for x in names if x in SKIP or x == "index.html"])
    html = (WEB / "index.html").read_text(encoding="utf-8")
    # relative URLs, so the site also works from a sub-path such as user.github.io/repo/; import maps need the ./
    html = html.replace('"/static/', '"./static/').replace("'/static/", "'./static/")
    html = html.replace('<meta name="color-scheme" content="dark">',
                        '<meta name="color-scheme" content="dark">\n  <meta name="tapestry-static" content="1">')
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("")


def main():
    ap = argparse.ArgumentParser(description="Export Virtual Explore as a static website")
    ap.add_argument("--out", default="site")
    ap.add_argument("--beats", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--quality", default="live", choices=["live", "render"])
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1120, help="video width in pixels (height follows the board)")
    ap.add_argument("--crf", type=int, default=22, help="H.264 quality, lower is sharper and larger")
    ap.add_argument("--page-only", action="store_true", help="rebuild the page and data but keep the existing video")
    ap.add_argument("--audio", action="store_true", help="include the soundtrack (the site is silent by default)")
    args = ap.parse_args()

    if args.fps < 1 or args.width < 2 or not 1 <= args.crf <= 51:
        ap.error("--fps must be positive, --width must be at least 2, and --crf must be between 1 and 51")
    out = Path(args.out).resolve()
    web = WEB.resolve()
    if out == web or out in web.parents or web in out.parents:
        ap.error("--out must be separate from the web source directory and its parent directories")
    (out / "media").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    print("Loading the show...", flush=True)
    engine = WebEngine(args.beats, args.config, args.quality)
    info = engine.info()
    info.pop("detail", None)
    info["static"] = True
    info["audio"] = bool(args.audio and info["audio"])
    fingerprint = source_fingerprint(engine.cfg)

    video_meta = out / "data" / "video.json"
    if args.page_only:
        info["video"] = cached_video(out, fingerprint)
    else:
        print("Rendering the board animation...", flush=True)
        info["video"] = encode_video(engine, out / "media" / "board.mp4", args.fps, args.width, args.crf)
        info["video"]["source"] = fingerprint
        video_meta.write_text(json.dumps(info["video"]), encoding="utf-8")

    (out / "data" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    (out / "data" / "camera-path.json").write_text(json.dumps(engine.camera_path()), encoding="utf-8")
    wav = out / "media" / "soundtrack.wav"
    if info["audio"]:
        wav.write_bytes(engine.wav)
    elif wav.exists():
        wav.unlink()
    (out / "media" / "desk.jpg").write_bytes(engine.desk_jpeg)
    write_page(out)

    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and ".git" not in p.parts)
    video = (out / "media" / "board.mp4").stat().st_size
    print(f"\nWrote {out}/  ({total / 1e6:.1f} MB, video {video / 1e6:.1f} MB)")
    print(f'Preview it with:  .venv\\Scripts\\python.exe -m http.server 8080 --directory "{out}"')


if __name__ == "__main__":
    main()
