import argparse
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np

from app import Renderer
from config import CONFIG, load
from render import Source


def main():
    parser = argparse.ArgumentParser(description="Measure sequential live rendering at the configured output size")
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--beats", default=None)
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--out", default="test_out/performance.json")
    args = parser.parse_args()
    cfg = load(args.beats, args.config)
    cv2.setNumThreads(cfg.get("performance", {}).get("opencv_threads", 4))
    start = time.perf_counter()
    renderer = Renderer(cfg)
    source = Source(renderer, "board", None, (1920, 1080))
    frame, H = source.get()
    results = {"platform": platform.platform(), "opencv": cv2.__version__, "threads": cv2.getNumThreads(),
               "output": [renderer.ow, renderer.oh], "composition": renderer.comp.work,
               "startup_s": round(time.perf_counter() - start, 3), "segments": []}
    for x in (4.0, 10.0, 27.0, 45.0, 53.0, 60.0, 66.0):
        segment = x
        samples = []
        for x in range(args.frames + 5):
            start = time.perf_counter()
            renderer.render(frame, segment + x / 30)
            elapsed = (time.perf_counter() - start) * 1000
            if x >= 5:
                samples.append(elapsed)
        item = {"start_s": segment, "mean_ms": round(float(np.mean(samples)), 2), "p95_ms": round(float(np.percentile(samples, 95)), 2),
                "fps": round(1000 / float(np.mean(samples)), 2)}
        results["segments"].append(item)
        print(json.dumps(item), flush=True)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
