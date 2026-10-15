"""Focused animation regressions: python -m unittest test_animation."""

import unittest

import numpy as np

from ink import Canvas
from props import Crown, Ctx
from timeline import Timeline
from weeds import Builder, WeedEngine


class CrownContinuityTests(unittest.TestCase):
    def crown(self, direction=1, pitch=16):
        return Crown(
            {"at": [0.4, 0.3], "floor": 0.8, "dir": direction, "view_pitch": pitch},
            2.0,
            Ctx(960, 1484, Canvas(960, 1484, 0.7, 0.8)),
        )

    @staticmethod
    def vertices(crown, t):
        matrix, anchor, reference = crown.pose(t)
        local = np.vstack([crown.top, crown.bottom])
        return (crown.view @ matrix @ ((local - reference) * crown.R).T).T[:, :2] + anchor

    def test_phase_boundaries_have_no_position_pop(self):
        for direction in (-1, 1):
            for pitch in (0, 16, 35):
                crown = self.crown(direction, pitch)
                boundaries = np.cumsum([crown.tA, crown.tB, crown.tC, crown.tD]) + crown.t0
                for phase, t in zip(("release", "landing", "settling", "rest"), boundaries):
                    with self.subTest(direction=direction, pitch=pitch, phase=phase):
                        before = self.vertices(crown, t - 1e-7)
                        after = self.vertices(crown, t + 1e-7)
                        self.assertLess(float(np.linalg.norm(after - before, axis=1).max()), 0.001)

    def test_settled_pose_stays_fixed(self):
        crown = self.crown()
        finish = crown.t0 + crown.tA + crown.tB + crown.tC + crown.tD
        np.testing.assert_array_equal(self.vertices(crown, finish), self.vertices(crown, finish + 10.0))


class WeedPlaybackTests(unittest.TestCase):
    def engine(self, fine=True):
        cfg = {
            "tapestry": {"width": 160, "height": 240},
            "board": {"drawing_mm": [160, 240]},
            "camera": {}, "beats": [], "overlay": {"margin": 0.15},
            "fx": {"stitch_detail": fine},
            "weeds": {"colours": {"olive": "#5a5a2a", "rust": "#98512b"}},
        }
        canvas = Canvas(160, 240, 1.0, 0.15)
        engine = WeedEngine(cfg, Timeline(cfg), np.zeros((240, 160)), canvas, canvas)
        # Intersecting stitches and knots straddle tile edges, including the final
        # incomplete edge tile. This catches stale AA caps and stale soft shadows.
        engine.S = np.array([
            [20, 60, 100, 80, 0, 2, 4, 0, 0],
            [130, 200, 180, 260, .1, 2.4, 1, 1, 1],
            [100, 60, 30, 100, .15, 1.5, 3, 1, 0],
            [70, 0, 75, 140, .6, 3, 2.5, 0, 2],
        ], dtype=float)
        engine.K = np.array([[72, 70, .25, 3.4, 1], [150, 245, .8, 2, 0]], dtype=float)
        engine.first = 0.0
        engine._xy()
        engine._init_redraw()
        return engine, np.zeros((canvas.h, canvas.w, 4), dtype=np.uint8)

    def test_equal_time_is_idempotent(self):
        engine, overlay = self.engine()
        engine.draw(overlay, .9)
        before, shadow = overlay.copy(), engine.soft_shade().copy()
        engine.draw(overlay, .9)
        np.testing.assert_array_equal(overlay, before)
        np.testing.assert_array_equal(engine.soft_shade(), shadow)

    def test_playback_seek_and_direct_frames_match(self):
        for fine in (False, True):
            engine, overlay = self.engine(fine)
            fresh, direct = self.engine(fine)
            for t in (.1, .5, .9, 1.8, 2.8, 3.2, .7, 2.0, 0.0):
                with self.subTest(fine=fine, t=t):
                    engine.draw(overlay, t)
                    shadow = engine.soft_shade()
                    fresh.clear(direct)
                    fresh.draw(direct, t)
                    np.testing.assert_array_equal(overlay, direct)
                    expected = fresh.soft_shade()
                    if expected is not None:
                        np.testing.assert_array_equal(shadow, expected)

    def test_colony_clipping_preserves_growth_timing_and_existing_geometry(self):
        engine, _ = self.engine()
        builder = Builder()
        builder.stroke((-20, 20), (-10, 30), 0, 1, 2, 0)
        builder.knot((-10, 20), 0, 2, 0)
        untouched_stroke, untouched_knot = builder.s[0], builder.k[0]
        builder.stroke((-10, 40), (30, 40), 1, 3, 2, 0)
        builder.stroke((150, 40), (170, 40), 4, 6, 2, 0)
        builder.stroke((-20, 50), (-10, 50), 0, 1, 2, 0)
        builder.knot((2, 40), 1, 3, 0)
        builder.knot((40, 40), 1, 3, 0)
        engine._clip_colony(builder, 1, 1)
        self.assertEqual(builder.s[0], untouched_stroke)
        self.assertEqual(builder.k[0], untouched_knot)
        self.assertEqual(len(builder.s), 3)
        np.testing.assert_allclose(builder.s[1][:6], [0, 40, 30, 40, 1.5, 3])
        np.testing.assert_allclose(builder.s[2][:6], [150, 40, 160, 40, 4, 5])
        self.assertEqual(builder.k[1:], [(40, 40, 1, 3, 0)])


if __name__ == "__main__":
    unittest.main()
