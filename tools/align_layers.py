import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from config import CONFIG, MM_PER_IN, ROOT, default_beats, dump, load, read, resolve

PANELS = (("garden", "garden", 1), ("ghost", "battlement", 1), ("throne", "throne", 1),
          ("antic", "prince", 2), ("rg_letter", "court", 2), ("mousetrap", "players", 2),
          ("arras", "arras", 3), ("ophelia", "ophelia", 3), ("grave", "graveyard", 3),
          ("river", "river", 0),
          ("duel", "duel", 4), ("cup", "feast", 4), ("fortinbras", "topple", 4))
MIN_SIZE = (1650, 2550)
MOTIONS = {"translation": cv2.MOTION_TRANSLATION, "euclidean": cv2.MOTION_EUCLIDEAN, "affine": cv2.MOTION_AFFINE,
           "homography": cv2.MOTION_HOMOGRAPHY}
WARP = cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP
COLUMNS = (("ecc_before", 3, 7), ("ecc_after", 3, 9), ("shift_px", 2, 10), ("residual_global", 2, 13), ("residual_refined", 2, 9))


def gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def structure(img):
    g = gray(img).astype(np.float32)
    return cv2.GaussianBlur(g, (0, 0), 1.0) - cv2.GaussianBlur(g, (0, 0), 6.0)


def rounded(v, n=3):
    return None if v is None else round(float(v), n)


def translate(x0, y0):
    return np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float64)


def shrink(img, s):
    g = gray(img)
    if s >= 1.0:
        return g, np.eye(3)
    size = (max(8, round(g.shape[1] * s)), max(8, round(g.shape[0] * s)))
    sx, sy = size[0] / g.shape[1], size[1] / g.shape[0]
    return cv2.resize(g, size, interpolation=cv2.INTER_AREA), np.array([[sx, 0, 0.5 * sx - 0.5], [0, sy, 0.5 * sy - 0.5], [0, 0, 1]])


def sift_homography(src, dst, work=1400, ratio=0.75, thresh=3.0):
    s = min(1.0, work / max(src.shape[1], dst.shape[1]))
    a, Sa = shrink(src, s)
    b, Sb = shrink(dst, s)
    sift = cv2.SIFT_create()
    ka, da = sift.detectAndCompute(a, None)
    kb, db = sift.detectAndCompute(b, None)
    if da is None or db is None:
        raise SystemExit("SIFT found no features")
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
    good = [x[0] for x in pairs if len(x) == 2 and x[0].distance < ratio * x[1].distance]
    if len(good) < 8:
        raise SystemExit(f"Only {len(good)} SIFT matches; the images do not share enough structure")
    p = np.float32([ka[x.queryIdx].pt for x in good])
    q = np.float32([kb[x.trainIdx].pt for x in good])
    H, inliers = cv2.findHomography(p, q, cv2.RANSAC, thresh)
    if H is None:
        raise SystemExit("RANSAC could not fit a homography")
    return np.linalg.inv(Sb) @ H @ Sa, len(good), int(inliers.sum())


def box_of(pts, pad, w, h):
    x0, y0 = np.floor(pts.min(axis=0)).astype(int) - pad
    x1, y1 = np.ceil(pts.max(axis=0)).astype(int) + pad
    return max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1))


def panel_mask(pts, box, feather=0.0):
    x0, y0, x1, y1 = box
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    cv2.fillPoly(m, [np.round((pts - [x0, y0]) * 16).astype(np.int32)], 1.0, cv2.LINE_AA, 4)
    return cv2.GaussianBlur(m, (0, 0), feather) if feather > 0 else m


def corner_shift(R, box):
    x0, y0, x1, y1 = box
    c = np.float64([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]])
    return float(np.abs(cv2.perspectiveTransform(c, R) - c).max())


def rescale(W, s):
    full = np.vstack([W, [0, 0, 1]]) if W.shape[0] == 2 else W
    S = np.diag([s, s, 1.0])
    out = (S @ full.astype(np.float64) @ np.linalg.inv(S)).astype(np.float32)
    return out[:2] if W.shape[0] == 2 else out


def refine(tpl, inp, pts, box, motion, iters, levels=3):
    x0, y0, x1, y1 = box
    mask = (panel_mask(pts, box) > 0.5).astype(np.uint8) * 255
    a, b = tpl[y0:y1, x0:x1], inp[y0:y1, x0:x1]
    before = float(cv2.computeECC(a, b, mask))
    W = np.eye(3, dtype=np.float32) if motion == cv2.MOTION_HOMOGRAPHY else np.eye(2, 3, dtype=np.float32)
    soft = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), 4.0)
    (dx, dy), response = cv2.phaseCorrelate(a * soft, b * soft, cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F))
    if response > 0.05:
        W[0, 2], W[1, 2] = dx, dy
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-6)
    cc = None
    for x in range(levels - 1, -1, -1):
        s = 0.5 ** x
        size = (round(a.shape[1] * s), round(a.shape[0] * s))
        if x and min(size) < 32:
            continue
        A = cv2.resize(a, size, interpolation=cv2.INTER_AREA) if x else a
        B = cv2.resize(b, size, interpolation=cv2.INTER_AREA) if x else b
        m = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST) if x else mask
        try:
            cc, Ws = cv2.findTransformECC(A, B, rescale(W, s), motion, criteria, m, 5)
        except cv2.error:
            cc = None
            continue
        W = rescale(Ws, 1.0 / s)
    if cc is None:
        return None, before, None
    W = np.vstack([W, [0, 0, 1]]) if W.shape[0] == 2 else W
    T = translate(x0, y0)
    return T @ W.astype(np.float64) @ np.linalg.inv(T), before, float(cc)


def peak(r, search):
    _, v, _, (px, py) = cv2.minMaxLoc(r)
    d = [0.0, 0.0]
    for x in ((0, px, py, r[py, max(0, px - 1)], r[py, min(r.shape[1] - 1, px + 1)]),
              (1, py, px, r[max(0, py - 1), px], r[min(r.shape[0] - 1, py + 1), px])):
        k, i, j, a, c = x
        if 0 < i < r.shape[1 - k] - 1:
            d[k] = 0.5 * (a - c) / (a - 2 * r[py, px] + c - 1e-9)
    return v, px + d[0] - search, py + d[1] - search


def residual(tpl, img, mask, size=48, search=12, step=24, floor=6.0, accept=0.5):
    h, w = tpl.shape
    ys, xs = np.mgrid[search:h - size - search:step, search:w - size - search:step]
    out = []
    for x in zip(ys.ravel(), xs.ravel()):
        yy, xx = x
        t = tpl[yy:yy + size, xx:xx + size]
        if mask[yy:yy + size, xx:xx + size].mean() < 0.95 or t.std() < floor:
            continue
        v, dx, dy = peak(cv2.matchTemplate(img[yy - search:yy + size + search, xx - search:xx + size + search], t, cv2.TM_CCOEFF_NORMED), search)
        if v > accept:
            out.append(float(np.hypot(dx, dy)))
    return (float(np.median(out)), len(out)) if out else (None, 0)


def align(clean, corrupt, panels, motion="affine", feather=2.0, max_shift=0.02, iters=200, min_patches=8):
    h, w = clean.shape[:2]
    H, matches, inliers = sift_homography(corrupt, clean)
    G = np.linalg.inv(H)
    base = cv2.warpPerspective(corrupt, G, (w, h), flags=WARP, borderMode=cv2.BORDER_REPLICATE)
    tpl, inp = structure(clean), structure(base)
    acc = np.zeros((h, w, 3), np.float32)
    weight = np.zeros((h, w), np.float32)
    pad = max(int(round(3 * feather)) + 2, 14)
    maps, rows = {"global": G}, []
    for x in panels:
        pts = np.array(x["polygon"], np.float64) * [w, h]
        box = box_of(pts, pad, w, h)
        x0, y0, x1, y1 = box
        size = (x1 - x0, y1 - y0)
        hard = panel_mask(pts, box)
        t = structure(clean[y0:y1, x0:x1])
        glob = residual(t, structure(base[y0:y1, x0:x1]), hard)
        R, before, after = refine(tpl, inp, pts, box, MOTIONS[motion], iters)
        shift = None if R is None else corner_shift(R, box)
        piece = cv2.warpPerspective(corrupt, G @ translate(x0, y0), size, flags=WARP, borderMode=cv2.BORDER_REPLICATE)
        ref, keep = (None, 0), False
        if R is not None and shift <= max_shift * w:
            cand = cv2.warpPerspective(corrupt, G @ R @ translate(x0, y0), size, flags=WARP, borderMode=cv2.BORDER_REPLICATE)
            ref = residual(t, structure(cand), hard)
            enough = glob[1] >= min_patches and ref[1] >= min_patches
            keep = ref[0] < glob[0] if enough else after > before
            if keep:
                piece = cand
        maps[x["id"]] = G @ R if keep else G
        m = panel_mask(pts, box, feather)
        acc[y0:y1, x0:x1] += piece.astype(np.float32) * m[..., None]
        weight[y0:y1, x0:x1] += m
        rows.append({"id": x["id"], "ecc_before": rounded(before), "ecc_after": rounded(after), "shift_px": rounded(shift),
                     "residual_global": rounded(glob[0]), "residual_refined": rounded(ref[0]), "patches": glob[1], "refined": bool(keep)})
    cover = np.minimum(weight, 1.0)[..., None]
    out = acc / np.maximum(weight, 1.0)[..., None] + base.astype(np.float32) * (1.0 - cover)
    report = {"matches": matches, "inliers": inliers, "homography": np.round(H, 6).tolist(), "panels": rows}
    return np.clip(out + 0.5, 0, 255).astype(np.uint8), report, maps


def seed_panels(regions, scan, clean):
    h, w = clean.shape[:2]
    sh, sw = scan.shape[:2]
    H = np.eye(3) if scan.shape == clean.shape and np.array_equal(scan, clean) else sift_homography(scan, clean)[0]
    out = []
    for x in PANELS:
        pid, region, row = x
        pts = np.array(regions[region]["polygon"], np.float64) * [sw, sh]
        q = np.clip(cv2.perspectiveTransform(pts[None], H)[0] / [w, h], 0.0, 1.0)
        rect = [round(float(v), 4) for v in (*q.min(axis=0), *q.max(axis=0))]
        out.append({"id": pid, "row": row, "region": region, "rect": rect, "polygon": np.round(q, 5).tolist()})
    return {"panels": out}


def fit_margins(raw, aspect, min_mm):
    w, h = sorted(round(x * MM_PER_IN, 2) for x in raw["papers_in"][raw["paper"]])
    aw, ah = w - 2 * min_mm, h - 2 * min_mm
    pw, ph = (aw, aw * aspect) if aw * aspect <= ah else (ah / aspect, ah)
    side, top = round((w - pw) / 2, 2), round((h - ph) / 2, 2)
    return [side, top, side, top], [round(pw, 2), round(ph, 2)]


def edges(img):
    return cv2.Canny(cv2.GaussianBlur(gray(img), (0, 0), 1.2), 40, 110) > 0


def overlay(clean, other):
    out = cv2.cvtColor(gray(clean), cv2.COLOR_GRAY2BGR) // 3
    a, b = edges(clean), edges(other)
    out[a] = (60, 220, 60)
    out[b] = (220, 60, 220)
    out[a & b] = (255, 255, 255)
    return out


def checker(clean, other, cell=64):
    yy, xx = np.indices(clean.shape[:2])
    out = clean.copy()
    sel = ((yy // cell + xx // cell) % 2).astype(bool)
    out[sel] = other[sel]
    return out


def review(clean, corrupt, aligned, path, width=700):
    raw = cv2.resize(corrupt, (clean.shape[1], clean.shape[0]), interpolation=cv2.INTER_AREA)
    tiles = [checker(clean, aligned), overlay(clean, aligned), overlay(clean, raw)]
    size = (width, int(round(width * clean.shape[0] / clean.shape[1])))
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.hstack([cv2.resize(x, size, interpolation=cv2.INTER_AREA) for x in tiles]), [cv2.IMWRITE_JPEG_QUALITY, 90])


def asset_hash(paths):
    h = hashlib.sha1()
    for x in paths:
        h.update(Path(x).read_bytes())
    return h.hexdigest()[:16]


def row_text(row):
    cells = []
    for x in COLUMNS:
        key, places, width = x
        cells.append(("-" if row[key] is None else f"{row[key]:.{places}f}").rjust(width))
    return f"{row['id']:<12}{''.join(cells)}{row['patches']:>9}  {'ECC' if row['refined'] else 'global'}"


def imread(path, label):
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"Could not read the {label} art: {path}")
    h, w = img.shape[:2]
    if w < MIN_SIZE[0] or h < MIN_SIZE[1]:
        print(f"warning: {label} art is {w}x{h}; {MIN_SIZE[0]}x{MIN_SIZE[1]} (150 ppi at 11x17) is the target")
    return img


def main():
    ap = argparse.ArgumentParser(description="Align the corrupt art to the printed clean art and write the reveal assets")
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--beats", default=None, help="beat file whose regions seed panels.json")
    ap.add_argument("--clean", default=None)
    ap.add_argument("--corrupt", default=None)
    ap.add_argument("--motion", default="affine", choices=sorted(MOTIONS))
    ap.add_argument("--feather", type=float, default=2.0, help="panel seam feather, in art pixels")
    ap.add_argument("--max-shift", type=float, default=0.02, help="largest per-panel refinement, in art widths")
    ap.add_argument("--min-margin-mm", type=float, default=6.35, help="smallest printer margin")
    ap.add_argument("--reseed", action="store_true", help="rebuild panels.json from the beat file regions")
    args = ap.parse_args()

    raw = read(args.config)
    if raw.get("source_mode", "print") != "print":
        raise SystemExit("Only source_mode 'print' is built")
    cfg = load(args.beats or default_beats(args.config), args.config)
    paths = cfg["assets"]
    clean = imread(Path(args.clean) if args.clean else resolve(cfg, paths["clean_hi"]), "clean")
    corrupt = imread(Path(args.corrupt) if args.corrupt else resolve(cfg, paths["corrupt_hi"]), "corrupt")
    panels_path = resolve(cfg, paths["panels"])
    if args.reseed or not panels_path.exists():
        scan = cv2.imread(str(resolve(cfg, cfg["scan"]))) if cfg.get("scan") else None
        dump(seed_panels(cfg["regions"], clean if scan is None else scan, clean), panels_path)
        print(f"seeded {panels_path} from {Path(cfg['beats_path']).name} regions")
    panels = read(panels_path)["panels"]

    aligned, report, maps = align(clean, corrupt, panels, args.motion, args.feather, args.max_shift)
    corrupt_path, clean_path = resolve(cfg, paths["corrupt"]), resolve(cfg, paths["clean_ref"])
    cv2.imwrite(str(corrupt_path), aligned)
    cv2.imwrite(str(clean_path), clean)
    h, w = clean.shape[:2]
    margins, printed = fit_margins(raw, h / w, args.min_margin_mm)
    report["print_margin_mm"], report["printed_mm"] = margins, printed
    report["hash"] = asset_hash([corrupt_path, clean_path, panels_path])
    resolve(cfg, paths["hash"]).write_text(report["hash"] + "\n", encoding="utf-8")
    review(clean, corrupt, aligned, ROOT / "review" / "align_check.jpg")
    (ROOT / "review" / "align_report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")

    H = np.array(report["homography"])
    print(f"SIFT: {report['matches']} matches, {report['inliers']} RANSAC inliers")
    print(f"global homography: scale {H[0, 0]:.4f} x {H[1, 1]:.4f}, shift ({H[0, 2]:.1f}, {H[1, 2]:.1f}) px")
    print(f"{'panel':<12}{'ecc':>7}{'->':>3}{'ecc':>6}{'shift px':>10}{'residual px':>13}{'->':>3}{'px':>6}{'patches':>9}  used")
    for x in report["panels"]:
        print(row_text(x))
    final = [x["residual_refined"] if x["refined"] else x["residual_global"] for x in report["panels"]]
    final = [x for x in final if x is not None]
    if final:
        print(f"median panel residual {np.median(final):.2f} px (NCC on line structure; redrawn figures read high)")
    print(f"print clean_ref at {printed[0]} x {printed[1]} mm, centred, 100% scale")
    print(f'config.json: "print_margin_mm": {json.dumps(margins)}')
    print(f"wrote {corrupt_path.name}, {clean_path.name}, {panels_path.name}, hash {report['hash']}, review/align_check.jpg")


if __name__ == "__main__":
    main()
