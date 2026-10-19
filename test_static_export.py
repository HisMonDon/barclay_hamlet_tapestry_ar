"""Static export regressions: python -m unittest test_static_export -v"""

import json
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

import export_static as export


class StaticExportTests(unittest.TestCase):
    def test_exported_page_uses_relative_assets_for_github_project_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export.write_page(out)
            html = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn('name="tapestry-static"', html)
            self.assertNotIn('"/static/', html)
            self.assertIn('"three": "./static/vendor/three/three.module.min.js"', html)
            self.assertTrue((out / ".nojekyll").exists())
            self.assertTrue((out / "static/js/main.js").is_file())
            self.assertFalse((out / "static/phone.html").exists())

    def test_page_only_rejects_missing_stale_or_empty_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "data").mkdir()
            (out / "media").mkdir()
            meta = {"source": "old", "fps": 30, "frames": 1, "duration": 1 / 30}
            for data in (None, "invalid json", json.dumps(meta)):
                if data is not None:
                    (out / "data/video.json").write_text(data, encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, "Run without --page-only"):
                    export.cached_video(out, "current")
            meta["source"] = "current"
            (out / "data/video.json").write_text(json.dumps(meta), encoding="utf-8")
            (out / "media/board.mp4").touch()
            with self.assertRaises(SystemExit):
                export.cached_video(out, "current")
            (out / "media/board.mp4").write_bytes(b"video")
            self.assertEqual(export.cached_video(out, "current"), meta)

    def test_video_fingerprint_tracks_show_art_and_renderer_but_not_frontend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "scene.py"
            artwork = root / "art.png"
            frontend = root / "main.js"
            source.write_text("renderer = 1")
            artwork.write_bytes(b"first art")
            frontend.write_text("first UI")
            cfg = {"scan": str(artwork), "beats": [{"duration": 1}]}
            with patch.object(export, "ROOT", root):
                first = export.source_fingerprint(cfg)
                frontend.write_text("new UI")
                self.assertEqual(export.source_fingerprint(cfg), first)
                artwork.write_bytes(b"changed art")
                art_changed = export.source_fingerprint(cfg)
                self.assertNotEqual(art_changed, first)
                source.write_text("renderer = 2")
                self.assertNotEqual(export.source_fingerprint(cfg), art_changed)

    def test_invalid_export_settings_fail_before_engine_initialization(self):
        for options in (["--fps", "0"], ["--width", "1"], ["--crf", "0"], ["--crf", "52"], ["--out", str(export.WEB)]):
            with self.subTest(options=options), patch("sys.argv", ["export_static.py", *options]), \
                    patch("sys.stderr", new_callable=io.StringIO), patch.object(export, "WebEngine") as engine:
                with self.assertRaises(SystemExit):
                    export.main()
                engine.assert_not_called()

    @unittest.skipUnless(export.ffmpeg_path(), "ffmpeg is unavailable")
    def test_video_contains_exact_fractional_hold_and_preserves_previous_on_failure(self):
        calls = []
        hold = 0.35

        def texture(t, detail="standard"):
            calls.append(t)
            return np.full((16, 16, 3), round(255 * t / hold), np.uint8)

        engine = SimpleNamespace(timeline=SimpleNamespace(hold_t=hold), texture=texture)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.mp4"
            meta = export.encode_video(engine, path, fps=4, width=16, crf=18)
            self.assertEqual(calls, [0.0, 0.25, hold])
            self.assertEqual(meta["frames"], 3)
            cap = cv2.VideoCapture(str(path))
            frames = []
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    frames.append(frame)
            finally:
                cap.release()
            self.assertEqual(len(frames), 3)
            self.assertGreater(float(frames[-1].mean()), 250)
            original = path.read_bytes()

            def fail(t, detail="standard"):
                if t > 0:
                    raise RuntimeError("render failed")
                return np.zeros((16, 16, 3), np.uint8)

            engine.texture = fail
            with self.assertRaisesRegex(RuntimeError, "render failed"):
                export.encode_video(engine, path, fps=4, width=16, crf=18)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_name("board.tmp.mp4").exists())

    @unittest.skipUnless(export.ffmpeg_path(), "ffmpeg is unavailable")
    def test_wide_export_requests_high_detail_instead_of_upscaling_standard(self):
        calls = []

        def texture(t, detail="standard"):
            calls.append((t, detail))
            size = 32 if detail == "high" else 16
            return np.zeros((size, size, 3), np.uint8)

        engine = SimpleNamespace(timeline=SimpleNamespace(hold_t=0.25), texture=texture)
        with tempfile.TemporaryDirectory() as tmp:
            meta = export.encode_video(engine, Path(tmp) / "board.mp4", fps=4, width=32, crf=18)
        self.assertEqual(calls, [(0.0, "standard"), (0.0, "high"), (0.25, "high")])
        self.assertEqual(meta["width"], 32)

    @unittest.skipUnless(export.ffmpeg_path(), "ffmpeg is unavailable")
    def test_locked_destination_preserves_both_existing_and_completed_video(self):
        engine = SimpleNamespace(timeline=SimpleNamespace(hold_t=0),
                                 texture=lambda t, detail: np.zeros((16, 16, 3), np.uint8))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.mp4"
            path.write_bytes(b"existing video")
            with patch.object(Path, "replace", side_effect=PermissionError("video is in use")):
                with self.assertRaisesRegex(SystemExit, "completed video is saved"):
                    export.encode_video(engine, path, fps=4, width=16, crf=18)
            self.assertEqual(path.read_bytes(), b"existing video")
            self.assertGreater(path.with_name("board.tmp.mp4").stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
