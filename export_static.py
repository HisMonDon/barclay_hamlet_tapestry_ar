"""Export Virtual Explore as a static website (no camera, no Python on the host).

    .venv\\Scripts\\python.exe export_static.py            # writes site/

The engine renders the board texture for every frame up to the hold and encodes it as an H.264 video. The page
plays that video on the 3D board with the same controls as the local app. The site is silent unless
--audio adds the soundtrack. Upload the
site/ folder to any static host (Vercel, GitHub Pages, Netlify).
"""

import argparse
import json
import math
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from config import CONFIG, ROOT
from render import ffmpeg_path
from web_engine import WebEngine

WEB = ROOT / "web"
SKIP = {"phone.html", "phone.css", "phone.js"}


def even(v):
    return int(v) // 2 * 2


def encode_video(engine, path, fps, width, crf):
    """Render the board texture from 0 to the hold and pipe it to ffmpeg. Frames after the hold are identical,
    so the video stops there and the page holds its last frame."""
    exe = ffmpeg_path()
    if exe is None:
        raise SystemExit("ffmpeg is unavailable: pip install imageio-ffmpeg")
    first = engine.texture(0.0)
    h, w = first.shape[:2]
    W = even(width)
    H = even(round(W * h / w))
    hold = engine.timeline.hold_t
    n = int(math.floor(hold * fps)) + 1
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-level", "4.1", "-g", str(max(1, fps // 2)), "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    start = time.perf_counter()
    try:
        for k in range(n):
            img = engine.texture(k / fps)
            proc.stdin.write(np.ascontiguousarray(cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)).tobytes())
            if k % (fps * 10) == 0:
                print(f"  {k / fps:5.1f} s  {k + 1}/{n} frames", flush=True)
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", errors="replace")
        proc.wait()
    if proc.returncode:
        raise SystemExit(f"Video encoding failed: {err}")
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

    out = Path(args.out)
    (out / "media").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    print("Loading the show...", flush=True)
    engine = WebEngine(args.beats, args.config, args.quality)
    info = engine.info()
    info.pop("detail", None)
    info["static"] = True
    info["audio"] = bool(args.audio and info["audio"])

    video_meta = out / "data" / "video.json"
    if args.page_only and video_meta.exists() and (out / "media" / "board.mp4").exists():
        info["video"] = json.loads(video_meta.read_text())
    else:
        print("Rendering the board animation...", flush=True)
        info["video"] = encode_video(engine, out / "media" / "board.mp4", args.fps, args.width, args.crf)
        video_meta.write_text(json.dumps(info["video"]))

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
    print(f"Preview it with:  .venv\\Scripts\\python.exe -m http.server 8080 --directory {out}")


if __name__ == "__main__":
    main()
