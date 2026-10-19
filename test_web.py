"""Browser UI back end: python -m unittest test_web -v"""

import http.client
import io
import json
import ssl
import tempfile
import threading
import unittest
import wave
from pathlib import Path

import cv2
import numpy as np

import reference
import server
from tracking import marker_layout
from web_engine import PhoneRelay, WebEngine, decode, encode


def camera_view(engine, cover=()):
    """A synthetic 1280x720 camera view of the board, optionally with some markers covered."""
    cfg = engine.cfg
    scene, S = reference.board_scene(cfg, engine.board.scan)
    frame, Ht = reference.camera_frame(cfg, scene, S, (1280, 720))
    for i in cover:
        q = cv2.perspectiveTransform(marker_layout(cfg)[i][None].astype(np.float64), Ht)[0]
        cv2.fillConvexPoly(frame, np.round(q).astype(np.int32), (40, 30, 20))
    return frame


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = WebEngine()

    def setUp(self):
        self.engine.set_source("none")

    def test_info_mirrors_the_timeline(self):
        info = self.engine.info()
        tl = self.engine.timeline
        self.assertEqual(len(info["beats"]), len(tl.beats))
        self.assertEqual(info["end"], tl.end)
        self.assertEqual(info["hold"], tl.hold_t)
        self.assertEqual([b["start"] for b in info["beats"]], [round(s, 4) for s in tl.starts])
        silence = [b for b in info["beats"] if b["silence"]]
        self.assertTrue(silence and info["silence"] == silence[0]["start"])
        x0, y0, x1, y1 = info["board"]["texture_mm"]
        bx0, by0, bx1, by1 = info["board"]["board_mm"]
        self.assertTrue(x0 <= bx0 and y0 <= by0 and x1 >= bx1 and y1 > by1, "texture covers the board and the spill strip")

    def test_soundtrack_matches_timeline_and_keeps_the_silence(self):
        with wave.open(io.BytesIO(self.engine.wav)) as w:
            rate, n = w.getframerate(), w.getnframes()
            data = np.frombuffer(w.readframes(n), "<i2")
        self.assertAlmostEqual(n / rate, self.engine.timeline.end, places=2)
        silence = self.engine.soundtrack.silence
        self.assertEqual(int(np.abs(data[int(silence * rate):]).max()), 0)
        self.assertGreater(int(np.abs(data[:int(silence * rate)]).max()), 0)

    def test_camera_path_samples_the_show_camera(self):
        path = self.engine.camera_path()
        tl = self.engine.timeline
        self.assertEqual(len(path["poses"]), int(np.ceil(tl.end * path["rate"])) + 1)
        k = 10 * path["rate"]
        np.testing.assert_allclose(path["poses"][k], tl.camera_pose(10.0), atol=1e-4)

    def test_virtual_texture_is_a_pure_function_of_t(self):
        a, meta = self.engine.virtual(30.0)
        self.engine.virtual(60.0)
        b, _ = self.engine.virtual(30.0)
        self.assertEqual(a, b)
        img = decode(a)
        self.assertEqual([img.shape[1], img.shape[0]], meta["size"])
        self.assertFalse(meta["frozen"])
        early = decode(self.engine.virtual(0.5)[0]).astype(np.int16)
        self.assertGreater(float(np.abs(img.astype(np.int16) - early).mean()), 1.0, "the reveal changes the board")

    def test_virtual_texture_holds_still_at_the_end(self):
        hold = self.engine.timeline.hold_t
        a, meta = self.engine.virtual(hold + 0.5)
        b, _ = self.engine.virtual(self.engine.timeline.end)
        self.assertTrue(meta["frozen"])
        self.assertEqual(a, b)

    def test_high_detail_texture_is_larger(self):
        _, std = self.engine.virtual(5.0, "standard")
        _, high = self.engine.virtual(5.0, "high")
        self.assertGreater(high["size"][0], std["size"][0])

    def test_scan_tracks_markers_and_reports_loss(self):
        e = self.engine
        e.set_source("browser")
        _, meta = e.scan(camera_view(e), 20.0)
        self.assertTrue(meta["locked"])
        self.assertEqual(meta["tracking"], "locked")
        self.assertEqual(meta["seen"], [0, 1, 2, 3])
        self.assertEqual(meta["size"], [e.comp.ow, e.comp.oh])
        covered = camera_view(e, cover=(1, 2))
        for x in range(e.renderer.track_interval + 1):
            _, meta = e.scan(covered, 20.1)
        self.assertTrue(meta["locked"], "the last good homography is kept")
        self.assertEqual(meta["tracking"], "lost")
        e.set_source("browser")
        _, meta = e.scan(np.full((720, 1280, 3), 90, np.uint8), 20.2)
        self.assertFalse(meta["locked"])
        self.assertEqual(meta["tracking"], "searching")

    def test_source_validation(self):
        with self.assertRaises(ValueError):
            self.engine.set_source("stream", "")
        with self.assertRaises(ValueError):
            self.engine.set_source("stream", "not a url")
        with self.assertRaises(ValueError):
            self.engine.set_source("teleport")

    def test_scan_replaces_camera_paper_but_keeps_the_live_surroundings(self):
        e = self.engine
        frame = camera_view(e)
        e.set_source("browser")
        original, meta = e.scan(frame, 0.0, follow=False)
        self.assertTrue(meta["locked"])
        H = e.renderer.tracker.H.copy()
        tw, th = e.cfg["tapestry"]["width"], e.cfg["tapestry"]["height"]
        paper = cv2.perspectiveTransform(np.float64([[[0, 0], [tw, 0], [tw, th], [0, th]]]), H)[0]
        changed = frame.copy()
        cv2.fillConvexPoly(changed, np.round(paper).astype(np.int32), (0, 255, 0))
        # A changing camera background must remain visible outside the digital paper.
        changed[:80, :100] = (255, 0, 255)
        replaced, _ = e.scan(changed, 0.0, follow=False)
        before, after = decode(original), decode(replaced)
        M = e.scan_comp.view(0.0, H, frame.shape, virtual=False)
        projected = cv2.perspectiveTransform(paper[None], np.linalg.inv(M))[0]
        mask = np.zeros(before.shape[:2], np.uint8)
        cv2.fillConvexPoly(mask, np.round(projected).astype(np.int32), 255)
        mask = cv2.erode(mask, np.ones((17, 17), np.uint8)) > 0
        self.assertLess(float(np.abs(before.astype(float) - after)[mask].mean()), 1.0,
                        "camera colours underneath the paper must not leak into the artwork")
        self.assertGreater(float(np.abs(before[:40, :60].astype(float) - after[:40, :60]).mean()), 20.0)
        animated, _ = e.scan(changed, 20.0, follow=False)
        self.assertGreater(float(np.abs(decode(animated).astype(float) - after)[mask].mean()), 2.0,
                           "the show must still animate over the replacement")

    def test_scan_digital_art_follows_a_moving_board_without_changing_virtual_explore(self):
        e = self.engine
        virtual_before, _ = e.virtual(0.0)
        frame = camera_view(e)
        e.set_source("browser")
        e.scan(frame, 0.0, follow=False)
        H = e.renderer.tracker.H.copy()
        move = np.float64([[1, 0, 100], [0, 1, 0]])
        moved = cv2.warpAffine(frame, move, (frame.shape[1], frame.shape[0]))
        for _ in range(e.renderer.track_interval + 1):
            data, meta = e.scan(moved, 0.0, follow=False)
        self.assertEqual(meta["tracking"], "locked")
        origin = np.float64([[[0, 0]]])
        old_pos = cv2.perspectiveTransform(origin, H)[0, 0]
        new_pos = cv2.perspectiveTransform(origin, e.renderer.tracker.H)[0, 0]
        np.testing.assert_allclose(new_pos - old_pos, [100, 0], atol=2.0)
        # A patch of the digital base at the tracked centre matches the requested art.
        art = cv2.imread(str(Path(__file__).parent / "assets/hamlet_tapestry.png"))
        tw, th = e.cfg["tapestry"]["width"], e.cfg["tapestry"]["height"]
        M = e.scan_comp.view(0.0, e.renderer.tracker.H, moved.shape, virtual=False)
        to_output = np.linalg.inv(M) @ e.renderer.tracker.H
        q = cv2.perspectiveTransform(np.float64([[[tw * 0.5, th * 0.5]]]), to_output)[0, 0]
        x, y = np.round(q).astype(int)
        expected = art[art.shape[0] // 2 - 12:art.shape[0] // 2 + 13,
                       art.shape[1] // 2 - 12:art.shape[1] // 2 + 13].mean(axis=(0, 1))
        actual = decode(data)[y - 2:y + 3, x - 2:x + 3].mean(axis=(0, 1))
        np.testing.assert_allclose(actual, expected, atol=35)
        self.assertEqual(e.virtual(0.0)[0], virtual_before)

    def test_phone_relay_only_keeps_frames_while_in_use(self):
        relay = PhoneRelay()
        jpeg = encode(np.full((40, 60, 3), 120, np.uint8))
        self.assertFalse(relay.push(jpeg))
        self.assertEqual(relay.frame(), (None, False))
        self.assertEqual(relay.status()[0], "connecting")
        relay.set_active(True)
        self.assertTrue(relay.push(jpeg))
        frame, fresh = relay.frame()
        self.assertEqual(frame.shape, (40, 60, 3))
        self.assertTrue(fresh)
        self.assertEqual(relay.status()[0], "live")
        relay.set_active(False)
        self.assertEqual(relay.frame(), (None, False))

    def test_phone_source_feeds_scan(self):
        e = self.engine
        e.set_source("phone")
        self.assertEqual(e.source_status()["state"], "waiting")
        e.phone.push(encode(camera_view(e)), "test phone")
        frame, fresh = e.source_frame()
        self.assertTrue(fresh)
        _, meta = e.scan(frame, 5.0)
        self.assertTrue(meta["locked"])
        e.set_source("none")
        self.assertEqual(e.source_frame(), (None, False))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = WebEngine()
        cls.app = server.App(cls.engine, 0, 0)
        cls.desk = server.Server(("127.0.0.1", 0), cls.app)
        cls.port = cls.desk.server_address[1]
        threading.Thread(target=cls.desk.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.desk.shutdown()
        cls.desk.server_close()
        cls.engine.close()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": host or f"localhost:{self.port}", **(headers or {})}
        conn.request(method, path, body=body, headers=h)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def test_pages_and_static_files(self):
        res, data = self.request("GET", "/")
        self.assertEqual(res.status, 200)
        self.assertIn(b"Virtual Explore", data)
        self.assertIn(b"Scan Tapestry", data)
        for path in ("/static/js/main.js", "/static/vendor/three/three.module.min.js", "/phone"):
            self.assertEqual(self.request("GET", path)[0].status, 200, path)
        self.assertEqual(self.request("GET", "/static/../server.py")[0].status, 404)

    def test_ping_tells_the_static_site_the_backend_is_up(self):
        res, data = self.request("GET", "/api/ping")
        self.assertEqual(json.loads(data), {"ok": True})
        self.assertEqual(res.getheader("Access-Control-Allow-Origin"), "*")
        self.assertEqual(self.request("GET", "/api/ping", host="evil.example")[0].status, 403)

    def test_info_audio_and_texture(self):
        res, data = self.request("GET", "/api/info")
        info = json.loads(data)
        self.assertEqual(info["end"], self.engine.timeline.end)
        self.assertIn("phone", info)
        res, data = self.request("GET", "/api/audio.wav")
        self.assertEqual(res.getheader("Content-Type"), "audio/wav")
        self.assertEqual(data[:4], b"RIFF")
        res, data = self.request("GET", "/api/virtual?t=12.5")
        self.assertEqual(res.getheader("Content-Type"), "image/jpeg")
        self.assertEqual(json.loads(res.getheader("X-Meta"))["t"], 12.5)

    def test_scan_upload(self):
        self.request("POST", "/api/source", json.dumps({"kind": "browser"}), {"X-Tapestry": "1"})
        jpeg = encode(camera_view(self.engine))
        res, data = self.request("POST", "/api/scan?t=3&source=browser", jpeg, {"X-Tapestry": "1", "Content-Type": "image/jpeg"})
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Content-Type"), "image/jpeg")
        meta = json.loads(res.getheader("X-Meta"))
        self.assertTrue(meta["locked"])
        self.assertEqual(decode(data).shape[:2], (self.engine.comp.oh, self.engine.comp.ow))
        res, data = self.request("POST", "/api/scan?t=3&source=browser", b"not a jpeg", {"X-Tapestry": "1"})
        self.assertEqual(res.status, 400)

    def test_server_source_without_frames_reports_state(self):
        self.request("POST", "/api/source", json.dumps({"kind": "phone"}), {"X-Tapestry": "1"})
        res, data = self.request("POST", "/api/scan?t=0&source=server", b"", {"X-Tapestry": "1"})
        body = json.loads(data)
        self.assertFalse(body["frame"])
        self.assertEqual(body["kind"], "phone")
        self.assertIn(body["state"], ("waiting", "connecting"))
        self.request("POST", "/api/source", json.dumps({"kind": "none"}), {"X-Tapestry": "1"})

    def test_guards(self):
        self.assertEqual(self.request("GET", "/api/info", host="evil.example")[0].status, 403)
        self.assertEqual(self.request("POST", "/api/source", json.dumps({"kind": "none"}))[0].status, 403)
        self.assertEqual(self.request("POST", "/api/phone/frame?k=wrong", b"x")[0].status, 403)
        res, data = self.request("POST", f"/api/phone/frame?k={self.app.token}", encode(np.zeros((8, 8, 3), np.uint8)))
        self.assertEqual(res.status, 200)
        self.assertIn("active", json.loads(data))


class PhoneListenerTests(unittest.TestCase):
    def test_tls_listener_serves_only_the_phone_page(self):
        engine = type("E", (), {"phone": PhoneRelay()})()
        app = server.App(engine, 0, 0)
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = server.ensure_certificate(["127.0.0.1"], Path(tmp))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
        srv = server.Server(("127.0.0.1", 0), app, public=True, tls=ctx)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            client = ssl.create_default_context()
            client.check_hostname = False
            client.verify_mode = ssl.CERT_NONE

            def get(path, method="GET", body=None):
                conn = http.client.HTTPSConnection("127.0.0.1", srv.server_address[1], context=client, timeout=10)
                conn.request(method, path, body=body)
                res = conn.getresponse()
                data = res.read()
                conn.close()
                return res.status, data

            self.assertEqual(get("/phone")[0], 200)
            self.assertEqual(get("/static/phone.js")[0], 200)
            self.assertEqual(get("/api/info")[0], 404)
            self.assertEqual(get("/static/js/main.js")[0], 404)
            self.assertEqual(get("/api/phone/status?k=bad")[0], 403)
            status, data = get(f"/api/phone/status?k={app.token}")
            self.assertEqual((status, json.loads(data)), (200, {"active": False}))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
