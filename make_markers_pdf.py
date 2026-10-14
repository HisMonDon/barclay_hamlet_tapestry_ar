import argparse
import math
from pathlib import Path

import cv2

from config import CONFIG, board

PT = 72.0 / 25.4
LETTER = (215.9, 279.4)
MARGIN = 15.0
LABEL_H = 14.0
HEADER = 27.0
CORNERS = {0: "TOP-LEFT", 1: "TOP-RIGHT", 2: "BOTTOM-RIGHT", 3: "BOTTOM-LEFT"}
INNER = {0: (1, 1), 1: (0, 1), 2: (0, 0), 3: (1, 0)}


def marker_bits(marker_id):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(d, marker_id, 6, borderBits=1)
    return img < 128


def esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Page:
    def __init__(self, w_mm, h_mm):
        self.w, self.h = w_mm, h_mm
        self.ops = []

    def y(self, y_mm):
        return (self.h - y_mm) * PT

    def rect_path(self, x, y, w, h):
        self.ops.append(f"{x * PT:.3f} {self.y(y + h):.3f} {w * PT:.3f} {h * PT:.3f} re")

    def fill(self, gray=0.0):
        self.ops.append(f"{gray:.3f} g f")

    def stroke_rect(self, x, y, w, h, gray=0.5, width=0.25, dash=None):
        self.ops.append("q")
        if dash:
            self.ops.append(f"[{dash * PT:.2f}] 0 d")
        self.ops.append(f"{gray:.3f} G {width * PT:.3f} w")
        self.rect_path(x, y, w, h)
        self.ops.append("S Q")

    def line(self, x0, y0, x1, y1, width=0.3):
        self.ops.append(f"0 G {width * PT:.3f} w {x0 * PT:.3f} {self.y(y0):.3f} m {x1 * PT:.3f} {self.y(y1):.3f} l S")

    def dot(self, cx, cy, r):
        k = 0.5523 * r
        pts = [(cx + r, cy), (cx + r, cy + k, cx + k, cy + r, cx, cy + r), (cx - k, cy + r, cx - r, cy + k, cx - r, cy),
               (cx - r, cy - k, cx - k, cy - r, cx, cy - r), (cx + k, cy - r, cx + r, cy - k, cx + r, cy)]
        s = f"{pts[0][0] * PT:.3f} {self.y(pts[0][1]):.3f} m"
        for p in pts[1:]:
            s += " " + " ".join(f"{p[i] * PT:.3f} {self.y(p[i + 1]):.3f}" for i in range(0, 6, 2)) + " c"
        self.ops.append(s + " 0 g f")

    def text(self, x, y, size, s):
        self.ops.append(f"BT /F1 {size:.1f} Tf 0 g {x * PT:.3f} {self.y(y):.3f} Td ({esc(s)}) Tj ET")

    def stream(self):
        return "\n".join(self.ops).encode("latin-1")


def draw_marker(page, marker_id, x, y, m, quiet, gap):
    box = m + 2 * quiet
    page.stroke_rect(x, y, box, box, gray=0.6, width=0.2, dash=1.5)
    bits = marker_bits(marker_id)
    cell = m / bits.shape[0]
    for r in range(bits.shape[0]):
        for c in range(bits.shape[1]):
            if bits[r, c]:
                page.rect_path(x + quiet + c * cell, y + quiet + r * cell, cell, cell)
    page.fill(0.0)
    ix, iy = INNER[marker_id]
    page.dot(x + quiet * 0.5 + ix * (m + quiet), y + quiet * 0.5 + iy * (m + quiet), min(1.2, quiet * 0.25))
    page.text(x, y + box + 5.0, 9, f"ID {marker_id}  -  {CORNERS[marker_id]} corner of drawing")
    page.text(x, y + box + 9.5, 7, f"Dot marks the inner corner. Black square sits {gap:g} mm outside both drawing edges.")


def write_pdf(path, pages):
    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, p in enumerate(pages):
        content = p.stream()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {p.w * PT:.3f} {p.h * PT:.3f}] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    Path(path).write_bytes(out)


def build(marker_mm, gap_mm, drawing_mm, paper):
    quiet = max(marker_mm / 6, 4.0)
    box = marker_mm + 2 * quiet
    slot_w, slot_h = box + 10.0, box + LABEL_H + 6.0
    pw = max(LETTER[0], slot_w + 2 * MARGIN)
    ph = max(LETTER[1], slot_h + 2 * MARGIN + HEADER)
    cols = max(1, int((pw - 2 * MARGIN) // slot_w))
    rows = max(1, int((ph - 2 * MARGIN - HEADER) // slot_h))
    per = min(4, cols * rows)
    cols = min(cols, per)
    order = [0, 1, 3, 2] if per == 4 and cols == 2 else [0, 1, 2, 3]
    pages = []
    for start in range(0, 4, per):
        ids = order[start:start + per]
        p = Page(pw, ph)
        p.text(MARGIN, MARGIN, 11, f"ArUco DICT_4X4_50  -  marker IDs {', '.join(map(str, sorted(ids)))} of 0-3  -  {marker_mm:g} mm")
        p.text(MARGIN, MARGIN + 6.0, 10, "Print at 100% / Actual size.")
        p.text(MARGIN, MARGIN + 10.5, 7.5, f"US Letter. Drawing area {drawing_mm[0]:g} x {drawing_mm[1]:g} mm ({paper}, portrait). Cut on the dashed lines.")
        p.line(MARGIN, MARGIN + 15.0, MARGIN + 100.0, MARGIN + 15.0, 0.4)
        for k in range(0, 101, 10):
            p.line(MARGIN + k, MARGIN + 14.0, MARGIN + k, MARGIN + (16.0 if k % 50 == 0 else 15.0), 0.25)
        p.text(MARGIN + 102.0, MARGIN + 16.0, 7, "100 mm check")
        for j, mid in enumerate(ids):
            cx, cy = j % cols, j // cols
            draw_marker(p, mid, MARGIN + cx * slot_w, MARGIN + HEADER + cy * slot_h, marker_mm, quiet, gap_mm)
        pages.append(p)
    return pages


def main():
    ap = argparse.ArgumentParser(description="Printable PDF of the four board markers")
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--out", default=str(Path(__file__).with_name("markers.pdf")))
    args = ap.parse_args()
    b = board(args.config)["board"]
    m = b["marker_mm"]
    pages = build(m, b["gap_mm"], b["drawing_mm"], b["paper"])
    write_pdf(args.out, pages)
    print(f"Wrote {args.out}: {len(pages)} page(s), {m:g} mm markers")


if __name__ == "__main__":
    main()
