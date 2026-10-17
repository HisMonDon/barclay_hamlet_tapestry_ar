import sys
import time
import tempfile
import threading
from pathlib import Path

import cv2
import numpy as np

from app import Renderer
from config import load, resolve
from scene import Scene
from timeline import Timeline
from tracking import marker_layout
from atmosphere import Atmosphere
from editor import EditorModel
from live import BackupPlayer, ResilientCamera
from sound import AudioPlayer, Soundtrack, backup_track, read_wav, wav_bytes

ROOT = Path(__file__).parent
OUT = ROOT / "test_out"
FRAME = (1920, 1080)
PAPER = (196, 223, 234)
DESK = (60, 80, 100)
FAILS = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        FAILS.append(name)


def scene(cfg):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    layout = marker_layout(cfg)
    pad = int(np.ceil(-np.concatenate(list(layout.values())).min())) + 60
    img = np.empty((th + 2 * pad, tw + 2 * pad, 3), np.uint8)
    img[:] = DESK
    img[pad:pad + th, pad:pad + tw] = PAPER
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for i, sq in layout.items():
        x0, y0 = sq[0] + pad
        side = int(round(sq[1][0] - sq[0][0]))
        q = side // 6
        cv2.rectangle(img, (int(x0) - q, int(y0) - q), (int(x0) + side + q, int(y0) + side + q), (255, 255, 255), -1)
        img[int(y0):int(y0) + side, int(x0):int(x0) + side] = cv2.cvtColor(
            cv2.aruco.generateImageMarker(d, i, side, borderBits=1), cv2.COLOR_GRAY2BGR)
    S = np.array([[1, 0, pad], [0, 1, pad], [0, 0, 1]], np.float64)
    return img, S


def truth(cfg):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    h = 620.0
    w = h * tw / th
    cx, cy = FRAME[0] / 2, FRAME[1] / 2
    quad = np.float32([(cx - w / 2 + 30, cy - h / 2), (cx + w / 2 - 10, cy - h / 2 + 15),
                       (cx + w / 2 + 25, cy + h / 2), (cx - w / 2 - 20, cy + h / 2 - 10)])
    rect = np.float32([(0, 0), (tw, 0), (tw, th), (0, th)])
    return cv2.getPerspectiveTransform(rect, quad)


def to_frame(H, cfg, x, y):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    p = cv2.perspectiveTransform(np.float32([[[x * tw, y * th]]]), H)[0, 0]
    return int(round(p[0])), int(round(p[1]))


def sample(img, xy, r=2):
    x, y = xy
    return img[y - r:y + r + 1, x - r:x + r + 1].reshape(-1, 3).mean(axis=0)


def beat(tl, bid):
    k = tl.ids[bid]
    return tl.beats[k], tl.starts[k]


def ghost_centre(cfg):
    poly = np.array(cfg["regions"][cfg["ghost"]["region"]]["polygon"])
    return float(poly[:, 0].mean()), float(poly[:, 1].mean())


def run(cfg):
    renderer = Renderer(cfg)
    tl = renderer.timeline
    sc = renderer.scene
    img, S = scene(cfg)
    Ht = truth(cfg)
    frame = cv2.warpPerspective(img, Ht @ np.linalg.inv(S), FRAME, flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=DESK)
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    print(f"-- {cfg['board']['paper']}: tapestry {tw}x{th}, drawing {cfg['board']['drawing_mm']} mm")
    dw, dh = cfg["board"]["drawing_mm"]
    check("tapestry aspect follows paper", abs(th / tw - dh / dw) < 1.0 / tw, f"{th / tw:.4f} vs {dh / dw:.4f}")

    out, locked = renderer.render(frame, 0.0)
    check("tracker locks", locked, f"seen {renderer.tracker.seen}")
    H = renderer.tracker.H
    rect = np.float32([[(0, 0), (tw, 0), (tw, th), (0, th)]])
    err = float(np.abs(cv2.perspectiveTransform(rect, H) - cv2.perspectiveTransform(rect, Ht)).max())
    check("homography matches truth", err < 1.5, f"max corner error {err:.2f}px")
    check("virtual camera output size", out.shape == (FRAME[1], FRAME[0], 3), str(out.shape))

    centre = to_frame(Ht, cfg, 0.5, 0.55)
    c0 = renderer.composite(frame, H, *sc.layers(0.0))
    c1 = renderer.composite(frame, H, *sc.layers(tl.end))
    ink_only = renderer.composite(frame, H, sc.layers(tl.end)[0], None)
    v0, v1 = sample(c0, centre).mean(), sample(ink_only, centre).mean()
    check("paper untouched at t=0", v0 > 180, f"{v0:.0f}")
    check("paper black at end", v1 < 40, f"{v1:.0f}")
    desk = to_frame(Ht, cfg, -0.3, 0.5)
    dv = sample(ink_only, desk)
    check("desk unchanged by ink", np.abs(dv - np.array(DESK)).max() < 6, str(dv.round()))

    gx, gy = ghost_centre(cfg)
    ghost = sample(c1, to_frame(Ht, cfg, gx, gy))
    paper = sample(c0, to_frame(Ht, cfg, gx, gy))
    check("ghost capped, not blocked", 90 < ghost.mean() < paper.mean() - 30, f"ghost {ghost.round()} paper {paper.round()}")
    check("ghost tint is cool", ghost[0] > ghost[2] + 12 and paper[0] < paper[2], f"B-R {ghost[0] - ghost[2]:.0f}")
    b06, s06 = beat(tl, "B06")
    pre = sample(renderer.composite(frame, H, sc.layers(s06 + b06["duration"] - 0.1)[0], None), to_frame(Ht, cfg, gx, gy))
    check("ink parts around the ghost before his command", np.abs(pre - paper).max() < 8, str(pre.round()))
    check("ghost never fully black", ghost.mean() > 120, f"{ghost.mean():.0f}")

    bb, bstart = beat(tl, "B18")
    weave = bb["weaves"][0]
    bx0, by0, bx1, by1 = weave["bounds"]
    corners = [to_frame(Ht, cfg, x, y) for x, y in [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)]]
    region = np.zeros(c1.shape[:2], np.uint8)
    cv2.fillConvexPoly(region, np.int32(corners), 1)
    region = cv2.erode(region, np.ones((9, 9), np.uint8))
    banner_only = renderer.composite(frame, H, sc.layers(bstart + bb["duration"])[0], sc.over_banner_only(bstart + bb["duration"]))
    px = banner_only[region > 0].astype(np.float32)
    gold = (px[:, 2] > 110) & (px[:, 2] > px[:, 0] + 50)
    black = px.max(axis=1) < 45
    check("banner woven in gold", gold.mean() > 0.4, f"gold {gold.mean():.2f}")
    check("banner gaps show black ink", 0.05 < black.mean() < 0.45, f"black {black.mean():.2f}")
    under = sc.layers(tl.end)[0][sc.ink_canvas.span(bx0, by0, bx1, by1)].mean()
    check("banner does not erase ink", under < 40, f"ink under banner {under:.0f}")
    start = bstart + weave["t"]
    mid = sc.over_banner_only(start + 1.0)
    a = mid[..., 3]
    rows = np.nonzero(a.max(axis=1) > 0)[0]
    oy0, oy1 = sc.over_canvas.span(bx0, by0, bx1, by1)[0].start, sc.over_canvas.span(bx0, by0, bx1, by1)[0].stop
    check("banner weaves row by row", len(rows) and rows.min() < oy0 + 20 and rows.max() < oy1 - 40,
          f"rows {rows.min() if len(rows) else None}-{rows.max() if len(rows) else None} of {oy0}-{oy1}")
    check("no banner before its beat", sc.banner_layer(start - 0.5) == [])

    margin = cfg["overlay"]["margin"]
    for name, cv in (("ink", sc.ink_canvas), ("over", sc.over_canvas)):
        check(f"{name} canvas extends past paper", cv.pad == round(margin * cv.w0) and cv.w == cv.w0 + 2 * cv.pad
              and cv.h == cv.h0 + 2 * cv.pad, f"{cv.w0}x{cv.h0} -> {cv.w}x{cv.h}")
    over = np.zeros_like(sc.over)
    spots = [(-0.25, 0.5), (1.3, 0.2), (0.5, -0.3), (0.5, 1.0 + 0.3 * tw / th)]
    for x, y in spots:
        over[sc.over_canvas.span(x - 0.03, y - 0.02, x + 0.03, y + 0.02)] = (0, 0, 255, 255)
    cd = renderer.composite(frame, H, sc.layers(0.0)[0], over)
    hits = [sample(cd, to_frame(Ht, cfg, x, y)) for x, y in spots]
    check("desk margin draws through homography", all(h[2] > 240 and h[1] < 15 for h in hits),
          " ".join(str(h.round()) for h in hits))
    OUT.mkdir(exist_ok=True)
    cv2.imwrite(str(OUT / f"{cfg['board']['paper']}_desk.png"), cd)
    cv2.imwrite(str(OUT / f"{cfg['board']['paper']}_weaving.png"), renderer.composite(frame, H, *sc.layers(start + 1.2)))

    ink_checks(cfg, renderer)
    weed_checks(cfg, renderer, frame, H, Ht)
    camera_checks(cfg, renderer, frame, H)

    t0 = time.perf_counter()
    n = 0
    for k in np.arange(0.0, tl.end + 1e-6, 1.5):
        renderer.render(frame, float(k))
        n += 1
    check("full run renders", True, f"{1000 * (time.perf_counter() - t0) / n:.1f} ms/frame")
    cv2.imwrite(str(OUT / f"{cfg['board']['paper']}_end.png"), c1)


def ink_checks(cfg, renderer):
    sc = renderer.scene
    ink = sc.ink
    tl = renderer.timeline
    T = ink.T
    gx, gy = ghost_centre(cfg)
    gi, gj = int(gy * ink.ih), int(gx * ink.iw)
    check("ghost interior never reached by ink", not np.isfinite(T[gi, gj]))
    b02, s02 = beat(tl, "B02")
    b03, s03 = beat(tl, "B03")
    ear = b03["seeds"][0]
    ei, ej = int(ear["y"] * ink.ih), int(ear["x"] * ink.iw)
    impact = next(p for p in sc.props if p.spec.get("kind") == "vial").t_impact
    check("the vial drop inks the king's ear on impact", s02 < impact < s03 + 0.2 and abs(T[ei, ej] - impact) < 0.3,
          f"impact {impact:.2f}, ear T={T[ei, ej]:.2f}")
    order = []
    for bid in ("B03", "B04", "B05", "B08", "B10", "B13"):
        b, s = beat(tl, bid)
        sd = b["seeds"][0]
        order.append(float(T[int(sd["y"] * ink.ih), int(sd["x"] * ink.iw)]))
    check("ink reaches each person in story order", all(a < b for a, b in zip(order, order[1:])),
          " ".join(f"{v:.1f}" for v in order))
    fin = np.isfinite(T)
    check("saturation tide covers the tapestry", fin.mean() > 0.97, f"{fin.mean():.3f}")
    ok = ink.fields.cost < 100
    line = (ink.fields.line > 0.6) & ok
    blank = (ink.fields.line < 0.05) & ok
    check("drawn lines wick faster than blank cloth", ink.fields.cost[line].mean() < 0.8 * ink.fields.cost[blank].mean(),
          f"{ink.fields.cost[line].mean():.2f} vs {ink.fields.cost[blank].mean():.2f}")
    rng = np.random.default_rng(1)
    times = np.sort(rng.uniform(0, tl.end, 40))
    ys, xs = rng.integers(0, ink.ih, 300), rng.integers(0, ink.iw, 300)
    prev = None
    lit = np.zeros(300, bool)
    cleansed = 0
    for t in times:
        d = 255 - ink.layer(float(t))[ink.ink_canvas.inner][ys, xs].mean(axis=1)
        if prev is not None:
            cleansed += int(np.sum(lit & (d < 120)))
        lit |= d > 160
        prev = d
    check("nothing is ever cleansed", cleansed == 0, f"{cleansed} pixels lightened after soaking")
    a = ink.layer(41.7).copy()
    fresh = Scene(cfg, tl)
    b = fresh.ink.layer(41.7)
    diff = int(np.abs(a.astype(int) - b.astype(int)).max())
    check("tile cache matches a fresh compute", diff <= 2, f"max diff {diff}")


def weed_checks(cfg, renderer, frame, H, Ht):
    sc = renderer.scene
    tl = renderer.timeline
    we = sc.weeds
    T = sc.ink.T
    soak = cfg["weeds"]["soak_s"]
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    b11, s11 = beat(tl, "B11")
    check("weeds appear only from B11", we.first_plant >= s11 - 1e-6, f"first {we.first_plant:.2f} vs {s11:.2f}")
    bad = 0
    for x, y, tr, kind in we.roots:
        u, v = x / tw, y / th
        if 0 <= u < 1 and 0 <= v < 1:
            bad += int(not T[int(v * sc.ink.ih), int(u * sc.ink.iw)] + soak <= tr + 1e-6)
    check("weeds root only in soaked ink", bad == 0 and len(we.roots) > 50, f"{len(we.roots)} roots, {bad} unsoaked")
    S = we.S[we.S[:, 8] != 2]
    u, v = S[:, 0] / tw, S[:, 1] / th
    tol = 4.0 / tw
    inside = (u >= -tol) & (u < 1 + tol) & (v >= -tol) & (v < 1 + tol)
    mu, mv = (S[:, 0] + S[:, 2]) / 2 / tw, (S[:, 1] + S[:, 3]) / 2 / th
    strict = (mu >= 0) & (mu < 1) & (mv >= 0) & (mv < 1) & (S[:, 8] == 0)
    Ti = T[(mv[strict] * sc.ink.ih).astype(int), (mu[strict] * sc.ink.iw).astype(int)]
    late = np.sum(Ti > (S[strict, 4] + S[strict, 5]) / 2 + 0.25)
    check("stitches grow only over ink that has arrived", late <= 0.002 * strict.sum(), f"{late} of {strict.sum()}")
    b20, s20 = beat(tl, "B20")
    out = ~inside
    check("weeds leave the tapestry only in B20", out.sum() > 200 and S[out, 4].min() >= s20 - 1e-6,
          f"{int(out.sum())} desk stitches from {S[out, 4].min() if out.any() else 0:.2f}")
    t = s11 + 2.0
    growing = np.sum((S[:, 4] < t) & (S[:, 5] > t))
    done = np.sum(S[:, 5] <= t)
    check("weeds reveal tip by tip", growing > 0 and done > 0 and growing < 0.5 * (growing + done),
          f"{growing} growing, {done} complete")
    again = type(we)(cfg, tl, T, sc.ink_canvas, sc.over_canvas)
    check("weed growth is deterministic", np.array_equal(again.S, we.S) and np.array_equal(again.K, we.K))

    # Inspect the weed overlay by itself: camera crops, props and the banner must
    # not make a few local colonies look like coverage of the entire cloth.
    canvas = sc.over_canvas
    weed_layer = np.zeros_like(sc.over)
    early_time, broad_time = s11 + 1.5, tl.at("B18")
    again.draw(weed_layer, early_time)
    early_alpha = weed_layer[canvas.inner][..., 3] > 64
    again.draw(weed_layer, broad_time)
    broad_alpha = weed_layer[canvas.inner][..., 3] > 64
    eligible = np.ones(broad_alpha.shape, np.uint8)
    for name in set(cfg["weeds"].get("avoid_regions", [])) | {cfg["ghost"]["region"]}:
        polygon = np.rint(np.array(cfg["regions"][name]["polygon"]) * [canvas.w0, canvas.h0]).astype(np.int32)
        cv2.fillPoly(eligible, [polygon], 0)
    cloth = eligible > 0
    early_coverage, broad_coverage = early_alpha[cloth].mean(), broad_alpha[cloth].mean()
    check("weeds visibly cover the cloth before the banner", broad_coverage > 0.50, f"{broad_coverage:.2f} of unprotected cloth")
    check("weed coverage grows with the arriving ink", 0 < early_coverage < 0.5 * broad_coverage,
          f"{early_coverage:.2f} at {early_time:.1f}s -> {broad_coverage:.2f} at {broad_time:.1f}s")
    cell_coverage = []
    rows, cols = np.linspace(0, canvas.h0, 16, dtype=int), np.linspace(0, canvas.w0, 11, dtype=int)
    for y0, y1 in zip(rows, rows[1:]):
        for x0, x1 in zip(cols, cols[1:]):
            cell = cloth[y0:y1, x0:x1]
            if cell.mean() >= 0.5:
                cell_coverage.append(broad_alpha[y0:y1, x0:x1][cell].mean())
    occupied = np.array(cell_coverage) > 0.10
    check("weeds spread across the whole tapestry grid", np.mean(occupied) > 0.90,
          f"{sum(occupied)} of {len(occupied)} eligible cells, minimum density {min(cell_coverage):.2f}")
    for name in ("garden", "battlement", "throne", "prince", "court", "players", "arras", "ophelia", "graveyard", "duel", "feast", "topple"):
        panel = np.zeros_like(eligible)
        polygon = np.rint(np.array(cfg["regions"][name]["polygon"]) * [canvas.w0, canvas.h0]).astype(np.int32)
        cv2.fillPoly(panel, [polygon], 1)
        coverage = broad_alpha[(panel > 0) & cloth].mean()
        check(f"weeds reach the {name} panel before the banner", coverage > 0.20, f"{coverage:.2f} of panel")

    end = renderer.composite(frame, H, *sc.layers(tl.end))
    pts = [to_frame(Ht, cfg, x, y) for x in (-0.4, -0.2, 1.2, 1.4) for y in np.linspace(0.05, 0.95, 12)]
    pts = [p for p in pts if 0 <= p[0] < FRAME[0] and 0 <= p[1] < FRAME[1]]
    changed = np.mean([np.abs(sample(end, p, 6) - np.array(DESK)).max() > 12 for p in pts])
    check("weeds spread onto the desk at the end", changed > 0.15, f"{changed:.2f} of desk probes covered")
    a = sc.layers(s11 + 1.5)[1].copy()
    sc.layers(s11 + 2.5)
    b = sc.layers(s11 + 1.5)[1]
    check("stepping back redraws weeds exactly", int(np.abs(a.astype(int) - b.astype(int)).max()) <= 1)


def camera_checks(cfg, renderer, frame, H):
    tl = renderer.timeline
    for k, b in enumerate(tl.beats):
        if not b.get("camera"):
            check(f"camera keys for {b['id']}", False)
    check("every beat has camera keys", all(b.get("camera") for b in tl.beats))
    b21, s21 = beat(tl, "B21")
    f = tl.camera_full(s21 + 1.0)[3]
    check("ending pulls back to the full camera frame", f > 0.999, f"f={f:.3f}")
    M = renderer.comp.view(s21 + 1.0, H, frame.shape)
    fit = renderer.comp.view(s21 + 1.0, H, frame.shape, virtual=False)
    check("full camera frame matches the V view", np.allclose(M, fit))
    renderer.tracker.update(frame)
    before = renderer.tracker.H.copy()
    renderer.tracker.update(np.zeros_like(frame))
    check("tracking freezes on marker loss", np.array_equal(before, renderer.tracker.H))


def artwork_checks(cfg):
    import copy
    from render import load_source
    from scene import load_scan

    expected_path = ROOT / "assets" / "hamlet_tapestry.png"
    selected_path = resolve(cfg, cfg.get("scan"))
    check("default scan selects the supplied tapestry", selected_path == expected_path, str(selected_path))
    check("supplied tapestry exists", expected_path.is_file())
    expected = cv2.imread(str(expected_path), cv2.IMREAD_COLOR)
    check("supplied tapestry retains its original dimensions", expected is not None and expected.shape == (1573, 1000, 3),
          str(None if expected is None else expected.shape))
    source, scan = load_source(cfg, None), load_scan(cfg)
    check("offline source loads the supplied pixels exactly", expected is not None and np.array_equal(source, expected))
    check("scene fields use the supplied pixels exactly", expected is not None and np.array_equal(scan, expected))

    with tempfile.TemporaryDirectory() as folder:
        missing = Path(folder) / "missing_tapestry.png"
        broken = copy.deepcopy(cfg)
        broken["scan"] = str(missing)
        for name, load_missing in (
                ("missing configured scan cannot substitute schematic art", lambda: load_source(broken, None)),
                ("missing explicit source cannot substitute configured art", lambda: load_source(cfg, str(missing))),
                ("scene refuses missing configured art", lambda: load_scan(broken))):
            try:
                load_missing()
            except ValueError as error:
                check(name, str(missing) in str(error), str(error))
            else:
                check(name, False, "missing artwork was accepted")


def system_checks(cfg):
    import copy
    from types import SimpleNamespace
    tl = Timeline(cfg)
    sync_time = tl.at("B02", 2.32)
    scene = SimpleNamespace(cues=lambda: [("vial", sync_time)], weaves=[])
    track = Soundtrack(cfg, tl, scene)
    check("drip stays synchronized to the vial impact", any(name == "drip" and abs(start - sync_time) < 1e-6 for name, start, end in track.events))
    seep = next(x for x in track.events if x[0] == "seep")
    check("seep stops abruptly at the banner", abs(seep[2] - tl.at("B18")) < 1 / track.rate)
    check("final hold is sample-exact silence", np.count_nonzero(track.samples[round(tl.at("B21") * track.rate):]) == 0)
    check("soundtrack contains sound without clipping", 0.01 < abs(track.samples).max() < 1)
    silent = copy.deepcopy(cfg)
    silent["sound"]["on"] = False
    check("sound switch produces silence", not Soundtrack(silent, tl, scene).samples.any())

    class Backend:
        SND_ASYNC, SND_FILENAME, SND_NODEFAULT = 1, 2, 4

        def __init__(self):
            self.calls = []

        def PlaySound(self, path, flags):
            self.calls.append((path, flags))

    backend = Backend()
    player = AudioPlayer(track, backend)
    try:
        player.sync(5, True, 10)
        first = len(backend.calls)
        player.sync(5.03, True, 10.03)
        check("audio does not restart each video frame", len(backend.calls) == first)
        player.sync(5.03, False, 10.04)
        check("pause stops audio immediately", backend.calls[-1][0] is None)
        player.sync(30, True, 11, force=True)
        tail = read_wav(backend.calls[-1][0], track.rate)
        check("step and resume use the matching audio position", len(tail) == len(track.samples) - 30 * track.rate and np.max(abs(tail[:1000] - track.samples[30 * track.rate:30 * track.rate + 1000])) < 1e-4)
        player.sync(65, True, 12)
        check("B21 stops native playback", backend.calls[-1][0] is None)
    finally:
        player.close()

    off = copy.deepcopy(cfg)
    for x in off["atmosphere"].values():
        x["on"] = False
    image = np.full((90, 160, 3), 150, np.uint8)
    check("atmosphere switches leave pixels unchanged", np.array_equal(Atmosphere(off, (160, 90)).apply(image, 8), image))
    atmosphere = Atmosphere(cfg, (160, 90))
    a, b = atmosphere.apply(image, 8), atmosphere.apply(image, 8)
    check("atmosphere is deterministic on seek", np.array_equal(a, b))
    check("atmosphere stops evolving during final hold", np.array_equal(atmosphere.apply(image, 65), atmosphere.apply(image, 75)))
    check("candlelight changes gently between frames", max(abs(atmosphere.level(x + 1 / 30) - atmosphere.level(x)) for x in np.arange(0, 65, 0.1)) < 0.06)

    with tempfile.TemporaryDirectory() as folder:
        destination = Path(folder) / "mapped.json"
        model = EditorModel(cfg["beats_path"])
        seed = model.add_seed(0, 0.2, 0.3, 0.1, 0.04)
        seed["path"] = [[0.2, 0.3], [0.3, 0.4]]
        model.move(seed, None, (0.4, 0.5))
        check("moving a seed also moves its wicking path", np.allclose(seed["path"], [[0.4, 0.5], [0.5, 0.6]]))
        model.set_region("ghost", [[0.4, 0.1], [0.6, 0.1], [0.5, 0.3]])
        model.save(destination)
        mapped = load(destination)
        check("editor saves seeds and ghost polygons without losing props", mapped["beats"][0]["seeds"][-1]["x"] == 0.4 and len(mapped["regions"]["ghost"]["polygon"]) == 3 and mapped["beats"][1]["props"] == cfg["beats"][1]["props"])
        model.save()
        check("editor preserves the previous save as a backup", destination.with_suffix(".json.bak").exists())
        model.undo()
        check("editor undo restores region geometry", len(model.data["regions"]["ghost"]["polygon"]) > 3)

        video = Path(folder) / "backup.mp4"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 90))
        for x in range(30):
            writer.write(np.full((90, 160, 3), x * 6, np.uint8))
        writer.release()
        backup = BackupPlayer(video)
        check("backup is preloaded for an instant switch", backup.ready and backup.first is not None)
        check("backup can seek and hold the final frame", backup.frame(0.5).mean() > 60 and backup.frame(5).mean() > 140 and backup.frame(0).mean() < 5)
        backup.close()
        from render import Source
        source = Source(SimpleNamespace(cfg=cfg, tracker=SimpleNamespace(update=lambda frame: np.eye(3))), "video", video, (160, 90))
        selected, H = source.get(0.5)
        first, H = source.get(0)
        check("recorded-camera reviews sample the requested timeline time", 70 < selected.mean() < 100 and first.mean() < 5)
        source.cap.release()
        from compositor import Compositor
        flat_scene = SimpleNamespace(mult_to_tapestry=lambda: np.eye(3), over_to_tapestry=lambda: np.eye(3))
        comp = Compositor(cfg, flat_scene)
        tapestry_width, tapestry_height = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
        stripes = np.tile(np.repeat(np.array([40, 220], np.uint8), 2), tapestry_width // 2)
        artwork = np.tile(stripes[None, :, None], (120, 1, 3))
        coarse = np.full((180, 320, 3), 130, np.uint8)
        H = np.array([[0.1, 0, 100], [0, 0.1, 0], [0, 0, 1]], np.float64)
        crop = np.array([[0.2 * tapestry_width / 320, 0, 0.4 * tapestry_width],
                         [0, 0.2 * tapestry_width / 320, 0.1 * tapestry_height], [0, 0, 1]], np.float64)
        clear = np.full((1, 1, 3), 255, np.uint8)
        blurred = comp.compose(coarse, H, H @ crop, (320, 180), clear, None)
        comp.set_source_artwork(artwork)
        sharp = comp.compose(coarse, H, H @ crop, (320, 180), clear, None)
        check("offline close-ups retain scan detail beyond the fake camera resolution", blurred.std() < 1 and sharp.std() > 50,
              f"contrast {blurred.std():.1f} -> {sharp.std():.1f}")
        outside = crop.copy()
        outside[0, 2] = -tapestry_width
        unchanged = comp.compose(coarse, H, H @ outside, (320, 180), clear, None)
        check("direct scan rendering leaves the surrounding desk intact", np.array_equal(unchanged, np.full_like(unchanged, 130)))
        audio_path = video.with_suffix(".wav")
        audio_path.write_bytes(wav_bytes(np.full(track.rate, 0.1, np.float32), track.rate))
        companion = backup_track(track, video)
        check("backup playback uses its own matching WAV", len(companion.samples) == track.rate and abs(companion.samples.mean() - 0.1) < 1e-4)
        from render import VideoWriter, ffmpeg_path, mux
        encoded = Path(folder) / "encoded.mp4"
        writer = VideoWriter(encoded, 30, (160, 90), cfg.get("quality", {}).get("render", {}))
        check("offline video encoder opens", writer.isOpened())
        for x in range(12):
            image = np.tile(np.arange(160, dtype=np.uint8)[None, :, None], (90, 1, 3))
            writer.write(cv2.add(image, x))
        writer.release()
        capture = cv2.VideoCapture(str(encoded))
        count, fps = capture.get(cv2.CAP_PROP_FRAME_COUNT), capture.get(cv2.CAP_PROP_FPS)
        ok, picture = capture.read()
        capture.release()
        check("offline encoder preserves frame count, rate and dimensions", ok and count == 12 and fps == 30 and picture.shape == (90, 160, 3))
        if ffmpeg_path():
            soundtrack = Path(folder) / "encoded.wav"
            soundtrack.write_bytes(wav_bytes(np.zeros(round(0.4 * track.rate), np.float32), track.rate))
            combined = Path(folder) / "combined.mp4"
            check("video and lossless audio mux successfully", mux(encoded, soundtrack, combined))
            capture = cv2.VideoCapture(str(combined))
            ok, remuxed = capture.read()
            capture.release()
            check("audio muxing does not recompress the picture", ok and np.array_equal(picture, remuxed))

    disconnected = threading.Event()
    recovered = threading.Event()
    released = []
    attempts = []

    class Capture:
        def __init__(self, number):
            self.number, self.reads = number, 0

        def isOpened(self):
            return True

        def read(self):
            self.reads += 1
            if self.number == 0 and self.reads > 1:
                disconnected.set()
                return False, None
            if self.number > 0:
                recovered.set()
            time.sleep(0.003)
            return True, np.full((10, 10, 3), 60 + self.number, np.uint8)

        def release(self):
            released.append(self.number)

    def opener(source, width, height, settings):
        attempts.append(len(attempts))
        return Capture(attempts[-1])

    camera = ResilientCamera("0", (10, 10), {"reconnect_s": 0.15, "stale_s": 1}, opener=opener).start()
    try:
        lost = disconnected.wait(2)
        frame, connected = camera.latest()
        check("camera loss retains the last good image", lost and frame is not None and frame.mean() == 60)
        ok = recovered.wait(2)
        time.sleep(0.02)
        frame, connected = camera.latest()
        check("camera reconnects without restarting the app", ok and connected and frame.mean() == 61 and 0 in released)
    finally:
        camera.close()


def alignment_checks(cfg):
    from fields import fractal_noise, raster
    from tools.align_layers import align, seed_panels
    clean = cv2.imread(str(ROOT / "assets" / "hamlet_tapestry.png"))
    h, w = clean.shape[:2]
    panels = seed_panels(cfg["regions"], clean, clean)["panels"]
    rng = np.random.default_rng(4)
    drift = {x["id"]: rng.uniform(2.0, 3.0, 2) * rng.choice([-1.0, 1.0], 2) for x in panels}
    drifted = clean.copy()
    for x in panels:
        dx, dy = drift[x["id"]]
        moved = cv2.warpAffine(clean, np.float64([[1, 0, dx], [0, 1, dy]]), (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        inside = raster(x["polygon"], w, h) > 0.5
        drifted[inside] = moved[inside]
    hsv = cv2.cvtColor(drifted, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= 0.55
    hsv[..., 2] *= 0.8
    rot = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    rot = cv2.addWeighted(rot, 0.85, np.full_like(rot, (40, 90, 30)), 0.15, 0)
    rot[fractal_noise(w, h, 12, 3, 0.5, 5) > 0.55] //= 2
    c, s = 1.012 * np.cos(np.radians(0.5)), 1.012 * np.sin(np.radians(0.5))
    G = np.array([[c, -s, 9.0], [s, c, -6.0], [0, 0, 1]])
    corrupt = cv2.warpPerspective(rot, G, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    aligned, report, maps = align(clean, corrupt, panels)
    Gi = np.linalg.inv(G)
    refined, coarse = [], []
    for x in panels:
        x0, y0, x1, y1 = np.array(x["rect"]) * [w, h, w, h]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        pts = np.float64([[cx, cy], [cx - (cx - x0) / 2, cy - (cy - y0) / 2], [cx + (x1 - cx) / 2, cy - (cy - y0) / 2],
                          [cx + (x1 - cx) / 2, cy + (y1 - cy) / 2], [cx - (cx - x0) / 2, cy + (y1 - cy) / 2]])
        miss = lambda M: cv2.perspectiveTransform(cv2.perspectiveTransform(pts[None], M), Gi)[0] - drift[x["id"]] - pts
        refined.append(miss(maps[x["id"]]))
        coarse.append(miss(maps["global"]))
    rms = lambda e: float(np.sqrt(np.mean(np.sum(np.concatenate(e) ** 2, axis=1))))
    check("alignment recovers per-panel drift under 1 px", rms(refined) < 1.0,
          f"RMS {rms(refined):.2f} px (global only {rms(coarse):.2f} px), {sum(x['refined'] for x in report['panels'])}/{len(panels)} panels refined")
    check("aligned corrupt art keeps the clean art's size", aligned.shape == clean.shape)
    big = cv2.copyMakeBorder(cv2.resize(clean, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_CUBIC), 30, 30, 10, 10, cv2.BORDER_REPLICATE)
    moved = seed_panels(cfg["regions"], clean, big)["panels"]
    bh, bw = big.shape[:2]
    expect = [np.array(x["rect"]) * [w, h, w, h] * 1.25 + [10, 30, 10, 30] for x in panels]
    got = [np.array(x["rect"]) * [bw, bh, bw, bh] for x in moved]
    err = max(float(np.abs(expect[x] - got[x]).max()) for x in range(len(panels)))
    check("panel regions transfer onto rescaled art", err < 1.5, f"max rect error {err:.2f} px")


def margin_checks():
    import json
    from config import board, read
    raw = read(ROOT / "config.json")
    raw["print_margin_mm"] = [6.35, 9.79, 6.35, 9.79]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "config.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        cfg = board(path)
    sw, sh = sorted(round(x * 25.4, 2) for x in raw["papers_in"][raw["paper"]])
    ml, mt, mr, mb = raw["print_margin_mm"]
    dw, dh = cfg["board"]["drawing_mm"]
    check("print margins shrink the drawing to the printed area", abs(dw - (sw - ml - mr)) < 0.01 and abs(dh - (sh - mt - mb)) < 0.01, f"{dw} x {dh} mm")
    layout = marker_layout(cfg)
    sx, sy = cfg["tapestry"]["width"] / dw, cfg["tapestry"]["height"] / dh
    sheet = {x: layout[x] / [sx, sy] + [ml, mt] for x in layout}
    g, m = raw["gap_mm"], raw["marker_mm"]
    want = {0: (-g - m, -g - m), 1: (sw + g, -g - m), 2: (sw + g, sh + g), 3: (-g - m, sh + g)}
    err = max(float(np.abs(sheet[x][0] - want[x]).max()) for x in want)
    check("markers stay gap_mm beyond the sheet edges", err < 0.01, f"max error {err:.3f} mm")


def reveal_checks(cfg):
    import copy
    from types import SimpleNamespace
    from compositor import Compositor
    from reveal import load_art
    img, S = scene(cfg)
    Ht = truth(cfg)
    frame = cv2.warpPerspective(img, Ht @ np.linalg.inv(S), FRAME, flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=DESK)
    comp = Compositor(cfg, SimpleNamespace(mult_to_tapestry=lambda: np.eye(3), over_to_tapestry=lambda: np.eye(3)))
    art = np.empty((300, 200, 3), np.uint8)
    art[:, :100] = (200, 40, 180)
    art[:, 100:] = (30, 160, 60)
    comp.set_reveal_art(art)
    clear = np.full((1, 1, 3), 255, np.uint8)
    off = comp.compose(frame, Ht, np.eye(3), FRAME, clear, None)
    comp.force_reveal = True
    on = comp.compose(frame, Ht, np.eye(3), FRAME, clear, None)
    left, right, desk = to_frame(Ht, cfg, 0.25, 0.5), to_frame(Ht, cfg, 0.75, 0.5), to_frame(Ht, cfg, -0.3, 0.5)
    check("paper shows through with the reveal check off", np.abs(sample(off, left) - PAPER).max() < 6, str(sample(off, left).round()))
    check("reveal check shows the corrupt layer on the paper", np.abs(sample(on, left) - (200, 40, 180)).max() < 6
          and np.abs(sample(on, right) - (30, 160, 60)).max() < 6, f"{sample(on, left).round()} {sample(on, right).round()}")
    check("reveal check leaves the desk alone", np.abs(sample(on, desk) - DESK).max() < 6, str(sample(on, desk).round()))
    missing = copy.deepcopy(cfg)
    missing["assets"]["corrupt"] = "assets/missing_corrupt_layer.png"
    check("missing reveal art disables the check instead of failing", load_art(missing) is None)


def serpent_checks(cfg):
    tl = Timeline(cfg)
    check("serpent sheet runs 77 s over 17 beats", abs(tl.end - 77.0) < 1e-6 and len(tl.beats) == 17, f"{tl.end} s, {len(tl.beats)} beats")
    check("the hold comes from its beat flag", abs(tl.hold_t - tl.at("S16")) < 1e-9, f"{tl.hold_t}")
    renderer = Renderer(cfg)
    sc = renderer.scene
    rv = sc.reveal
    order = ["garden", "ghost", "throne", "mousetrap", "rg_letter", "antic", "arras", "ophelia", "grave", "river", "duel", "cup", "fortinbras"]
    check("panels are visited boustrophedon", [x["id"] for x in rv.panels] == order, " ".join(x["id"] for x in rv.panels))
    interior = np.zeros((rv.h, rv.w), bool)
    ghost = np.zeros_like(interior)
    for x in range(len(rv.panels)):
        g = rv.geo[x]
        r0, r1, c0, c1 = g["roi"]
        interior[r0:r1, c0:c1] |= g["own"] & (g["mask8"] > 250)
        if rv.panels[x]["light"]:
            ghost[r0:r1, c0:c1] |= g["own"]
    full = int(0.9 * rv.alpha * 255)
    prev, dropped, naked, inked = None, 0, 0, 0
    for x in np.arange(0.0, tl.end + 1e-6, 0.05):
        L = rv.layers(float(x))
        R = L["R"]
        G = L["glow"] if L["glow"] is not None else np.zeros_like(R)
        naked += int(np.sum(interior & (R > 5) & (R < 250) & (rv.I8 < full) & (G < 229)))
        inked += int(np.sum(ghost & ((rv.I8 > 0) | (rv.D8 > 0))))
        if prev is not None:
            dropped += int(np.sum(R.astype(np.int16) < prev.astype(np.int16) - 1))
        prev = R.copy()
    check("revealed pixels never revert", dropped == 0, f"{dropped} pixel drops")
    check("every moving reveal edge is under ink or light", naked == 0, f"{naked} uncovered pixels")
    check("ink never touches the ghost panel", inked == 0, f"{inked} inked pixels")
    check("every panel is revealed by the hold", bool(np.all(rv.layers(tl.hold_t)["R"][interior] >= 250)))
    late = [x["id"] for x in rv.panels if rv.phase(x, tl.starts[tl.ids[x["beat"]] + 1])[0] != "settled"]
    check("stepping to the next beat shows each finished panel settled", not late, " ".join(late))

    img, S = scene(cfg)
    Ht = truth(cfg)
    frame = cv2.warpPerspective(img, Ht @ np.linalg.inv(S), FRAME, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=DESK)
    a = renderer.frame(frame, Ht, 41.7)
    renderer.frame(frame, Ht, 70.0)
    renderer.frame(frame, Ht, 3.0)
    b = renderer.frame(frame, Ht, 41.7)
    c = Renderer(cfg).frame(frame, Ht, 41.7)
    check("the same t gives the same frame after seeking or a fresh start", np.array_equal(a, b) and np.array_equal(a, c))
    dbg = renderer.comp.debug(renderer.frame(frame, Ht, 4.9).copy())
    check("the debug overlay draws panels and the arrival heatmap", dbg.shape == a.shape and rv.heat(4.9) is not None)

    comp = renderer.comp
    garden = rv.panels[0]
    x0, y0, x1, y1 = garden["geo"]["rect"]
    pts = [to_frame(Ht, cfg, x0 + (x1 - x0) * x[0], y0 + (y1 - y0) * x[1]) for x in ((0.35, 0.4), (0.5, 0.55), (0.65, 0.7), (0.45, 0.8))]
    art = cv2.warpPerspective(comp.reveal_bgr, Ht @ np.linalg.inv(comp.reveal_inv), FRAME, flags=cv2.INTER_LINEAR)
    before = comp.compose(frame, Ht, np.eye(3), FRAME, *sc.layers(1.0), extra=sc.extras(1.0))
    t_after = garden["times"][4] + 3.0
    after = comp.compose(frame, Ht, np.eye(3), FRAME, *sc.layers(t_after), extra=sc.extras(t_after))
    clean = max(float(np.abs(sample(before, x) - PAPER).max()) for x in pts)
    shown = float(np.median([np.abs(sample(after, x) - sample(art, x)).max() for x in pts]))
    check("panels show the paper before their reveal", clean < 6, f"max diff {clean:.1f}")
    check("revealed panels show the aligned corrupt art", shown < 10, f"median diff {shown:.1f}")

    track = Soundtrack(cfg, tl, sc)
    drips = sorted(x[1] for x in track.events if x[0] == "drip")
    inhales = sorted(x[1] for x in track.events if x[0] == "inhale")
    floods = [x for x in rv.panels if not x["light"]]
    check("a drip sounds at every breach", np.allclose(drips, sorted(x["times"][0] for x in floods)), f"{len(drips)} drips")
    check("an inhale sounds at every blink", np.allclose(inhales, sorted(x["times"][2] for x in floods)), f"{len(inhales)} inhales")
    banner = tl.at("S14")
    check("silence from the banner beat on", abs(track.silence - banner) < 1e-9 and np.count_nonzero(track.samples[round(banner * track.rate):]) == 0)
    wipe = rv.panels[1]["times"]
    check("nothing sounds during the ghost light-wipe", np.count_nonzero(track.samples[round(wipe[0] * track.rate):round(wipe[4] * track.rate)]) == 0)


def main():
    default = load()
    check("default paper is tabloid", default["board"]["paper"] == "tabloid", default["board"]["paper"])
    check("app defaults to the serpent reveal", Path(default["beats_path"]).name == "beats_serpent.json", default["beats_path"])
    tapestry = load(str(ROOT / "beats_tapestry.json"))
    tl = Timeline(tapestry)
    check("tapestry beat sheet runs 75 s", abs(tl.end - 75.0) < 1e-6, f"{tl.end}")
    check("beat sheet has B01..B21", list(tl.ids) == [f"B{k:02d}" for k in range(1, 22)])
    mapped = [s for b in tapestry["beats"] for k in ("seeds", "camera") for s in b.get(k, [])]
    mapped.extend(tapestry["regions"].values())
    check("mapped coordinates retain their calibration status", all(isinstance(s.get("provisional"), bool) for s in mapped))
    artwork_checks(tapestry)
    system_checks(tapestry)
    margin_checks()
    reveal_checks(tapestry)
    alignment_checks(tapestry)
    serpent_checks(default)
    run(tapestry)
    run(load(str(ROOT / "beats_tapestry.json"), paper="letter"))
    old = load(str(ROOT / "beats.json"))
    r = Renderer(old)
    check("old beats.json still loads", r.timeline.end > 0)
    if FAILS:
        print(f"{len(FAILS)} failed: {', '.join(FAILS)}")
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
