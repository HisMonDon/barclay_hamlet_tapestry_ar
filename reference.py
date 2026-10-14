import argparse
import math
from pathlib import Path

import cv2
import numpy as np

from config import ROOT, load, resolve
from tracking import marker_layout

PAL = {
    "parch": "#eadfc4", "line": "#3a2517", "gold": "#c99b45", "goldHi": "#ebc978", "goldLo": "#8c6726",
    "red": "#9a3324", "redLo": "#67211a", "blue": "#2e4868", "blueHi": "#5a7a9c", "green": "#66733b",
    "greenLo": "#414c26", "greenHi": "#8b9551", "flesh": "#e8c9a5", "black": "#2b2422", "silver": "#c9c8bd",
    "brown": "#6d4b2f", "stone": "#cdbfa1", "stoneLo": "#a59579", "white": "#f3ecda", "pale": "#dde3df",
    "paleLo": "#a9b4b2", "ochre": "#b37f34", "pink": "#c9786a", "auburn": "#b06a34", "sky": "#9fb0b8",
    "water": "#6f8fa6", "waterLo": "#4c6a82", "grey": "#9a948a",
}
COLS = [(0.030, 0.336), (0.352, 0.648), (0.664, 0.970)]
ROWS = [(0.0185, 0.2593), (0.2741, 0.4852), (0.5000, 0.6748), (0.7111, 0.9148)]
RIVER = (0.0312, 0.6748, 0.9688, 0.7111)
MEADOW = (0.030, 0.9148, 0.970, 0.982)
ARCH = 0.07
NAMES = [["garden", "battlement", "throne"], ["prince", "court", "players"],
         ["arras", "ophelia", "graveyard"], ["duel", "feast", "topple"]]
GHOST = [(0.0, -0.062), (0.016, -0.056), (0.021, -0.040), (0.017, -0.030), (0.034, -0.018), (0.040, 0.010),
         (0.036, 0.044), (0.028, 0.072), (-0.028, 0.072), (-0.036, 0.044), (-0.040, 0.010), (-0.034, -0.018),
         (-0.017, -0.030), (-0.021, -0.040), (-0.016, -0.056)]
GHOST_AT = (0.5125, 0.1278)


def bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def arch_polygon(x0, x1, y0, y1, aspect, n=9, rise=ARCH, sharp=1.6):
    a = (x1 - x0) / 2
    R = sharp * a
    unit = math.sqrt(2 * a * R - a * a)
    k = rise * aspect / unit
    end = math.atan2(-unit, a - R) % (2 * math.pi)
    pts = []
    for x in np.linspace(0.0, 1.0, n):
        th = math.pi + (end - math.pi) * x
        px, py = x0 + R + R * math.cos(th), unit + R * math.sin(th)
        pts.append((px, y0 + py * k / aspect))
    right = [(2 * (x0 + a) - px, py) for px, py in reversed(pts[:-1])]
    return [(x0, y1)] + pts + right + [(x1, y1)]


def bay_polygons(aspect):
    out = {}
    for r, (y0, y1) in enumerate(ROWS):
        for c, (x0, x1) in enumerate(COLS):
            out[NAMES[r][c]] = [[round(px, 4), round(py, 4)] for px, py in arch_polygon(x0, x1, y0, y1, aspect)]
    return out


def ghost_polygon(aspect):
    gx, gy = GHOST_AT
    return [[round(gx + dx, 4), round(gy + dy / aspect, 4)] for dx, dy in GHOST]


def fractal(w, h, cells, octaves, persistence, rng):
    out = np.zeros((h, w), np.float32)
    amp = 1.0
    for o in range(octaves):
        nx = max(2, int(round(cells * 2 ** o)))
        ny = max(2, int(round(nx * h / w)))
        out += amp * cv2.resize(rng.random((ny + 1, nx + 1), dtype=np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
        amp *= persistence
    out -= out.mean()
    return out / max(1e-6, float(np.abs(out).max()))


class Sheet:
    def __init__(self, tw, th, scale=2.0, seed=11):
        self.W, self.H = int(round(tw * scale)), int(round(th * scale))
        self.s = scale
        self.tw = tw
        self.aspect = th / tw
        self.rng = np.random.default_rng(seed)
        self.img = np.zeros((self.H, self.W, 3), np.float32)
        self.gold = np.zeros((self.H, self.W), np.float32)
        self.wob = fractal(self.W // 4, self.H // 4, 24, 3, 0.5, self.rng)

    def P(self, x, y):
        return np.array([x * self.W, y * self.H], np.float64)

    def px(self, v):
        return v * self.tw * self.s

    def poly(self, pts):
        return np.array([self.P(x, y) for x, y in pts], np.float64)

    def jitter(self, pts, amp=1.2):
        pts = np.asarray(pts, np.float64)
        if len(pts) < 2:
            return pts
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        L = float(seg.sum())
        n = max(2, int(L / 6))
        s = np.concatenate([[0], np.cumsum(seg)])
        u = np.linspace(0, L, n)
        x = np.interp(u, s, pts[:, 0])
        y = np.interp(u, s, pts[:, 1])
        ix = np.clip((x / 4).astype(int), 0, self.wob.shape[1] - 1)
        iy = np.clip((y / 4).astype(int), 0, self.wob.shape[0] - 1)
        d = self.wob[iy, ix] * amp * self.s
        e = self.wob[np.clip(iy + 7, 0, self.wob.shape[0] - 1), ix] * amp * self.s
        return np.stack([x + d, y + e], 1)

    def box(self, pts, pad):
        pts = np.asarray(pts)
        x0 = max(0, int(pts[:, 0].min() - pad))
        y0 = max(0, int(pts[:, 1].min() - pad))
        x1 = min(self.W, int(pts[:, 0].max() + pad + 2))
        y1 = min(self.H, int(pts[:, 1].max() + pad + 2))
        return x0, y0, x1, y1

    def blend(self, m, box, colour, alpha, gold):
        x0, y0, x1, y1 = box
        a = m.astype(np.float32) / 255.0 * alpha
        col = np.array(bgr(colour), np.float32) if isinstance(colour, str) else np.array(colour, np.float32)
        img = self.img[y0:y1, x0:x1]
        img += (col - img) * a[..., None]
        g = self.gold[y0:y1, x0:x1]
        if gold:
            np.maximum(g, a, out=g)
        else:
            g *= 1.0 - a

    def fill(self, pts, colour, alpha=1.0, gold=False):
        b = self.box(pts, 4)
        if b[2] <= b[0] or b[3] <= b[1]:
            return
        m = np.zeros((b[3] - b[1], b[2] - b[0]), np.uint8)
        q = np.asarray(pts) - (b[0], b[1])
        cv2.fillPoly(m, [np.round(q * 16).astype(np.int32)], 255, cv2.LINE_AA, 4)
        self.blend(m, b, colour, alpha, gold)

    def line(self, pts, width=1.6, colour="line", closed=False, jit=1.0, gold=False):
        pts = np.asarray(pts, np.float64)
        if closed:
            pts = np.vstack([pts, pts[:1]])
        q = self.jitter(pts, jit)
        wpx = max(1, int(round(width * self.s)))
        b = self.box(q, wpx + 4)
        if b[2] <= b[0] or b[3] <= b[1]:
            return
        m = np.zeros((b[3] - b[1], b[2] - b[0]), np.uint8)
        cv2.polylines(m, [np.round((q - (b[0], b[1])) * 16).astype(np.int32)], False, 255, wpx, cv2.LINE_AA, 4)
        self.blend(m, b, PAL.get(colour, colour), 1.0, gold)

    def shape(self, pts, colour, width=1.6, alpha=1.0, gold=False, outline=True):
        self.fill(pts, PAL.get(colour, colour), alpha, gold)
        if outline:
            self.line(pts, width, closed=True)

    def ellipse(self, x, y, rx, ry, colour, angle=0.0, width=1.6, gold=False, n=40, outline=True):
        c = self.P(x, y)
        a = math.radians(angle)
        th = np.linspace(0, 2 * np.pi, n, endpoint=False)
        ex, ey = self.px(rx) * np.cos(th), self.px(ry) * np.sin(th)
        pts = np.stack([c[0] + ex * math.cos(a) - ey * math.sin(a), c[1] + ex * math.sin(a) + ey * math.cos(a)], 1)
        self.shape(pts, colour, width, gold=gold, outline=outline)
        return pts

    def rel(self, x, y, pts):
        c = self.P(x, y)
        return np.array([(c[0] + self.px(dx), c[1] + self.px(dy)) for dx, dy in pts], np.float64)


def figure(S, x, foot, height, robe, head="flesh", crown=False, hat=None, arm=None, hair="brown", lying=False):
    h = height
    hy = foot - h * 0.86 / S.aspect
    r = h * 0.075
    body = [(-0.34 * h * 0.5, 0.16 * h), (0.34 * h * 0.5, 0.16 * h), (0.24 * h, 0.86 * h), (-0.24 * h, 0.86 * h)]
    pts = S.rel(x, hy, [(dx, dy) for dx, dy in body])
    S.shape(pts, robe)
    S.line(S.rel(x, hy, [(-0.05 * h, 0.2 * h), (0.0, 0.5 * h), (0.05 * h, 0.2 * h)]), 1.0)
    S.ellipse(x, hy + r * 0.35 / S.aspect, r * 1.05, r * 0.9, hair, width=1.0)
    S.ellipse(x, hy + r * 0.55 / S.aspect, r * 0.85, r, head)
    if crown:
        c = S.rel(x, hy, [(-r, -0.2 * r), (-r, -1.3 * r), (-0.5 * r, -0.8 * r), (0, -1.5 * r), (0.5 * r, -0.8 * r),
                          (r, -1.3 * r), (r, -0.2 * r)])
        S.shape(c, "gold", 1.2, gold=True)
    if hat:
        S.shape(S.rel(x, hy, [(-1.4 * r, -0.4 * r), (1.4 * r, -0.4 * r), (0.8 * r, -1.3 * r), (-0.9 * r, -1.2 * r)]), hat, 1.2)
    if arm:
        S.line(np.vstack([S.rel(x, hy, [(arm[0][0] * h, arm[0][1] * h)]), S.P(*arm[1])]), 2.6, robe)
        S.line(np.vstack([S.rel(x, hy, [(arm[0][0] * h, arm[0][1] * h)]), S.P(*arm[1])]), 0.9)
        S.ellipse(arm[1][0], arm[1][1], r * 0.35, r * 0.35, head, width=0.9)
    return hy


def tree(S, x, ground, h, canopy="green", fruit=None):
    t = S.rel(x, ground, [(-0.012, 0), (-0.007, -h * 0.55), (0.007, -h * 0.55), (0.012, 0)])
    S.shape(t, "brown")
    cy = ground - h * 0.68 / S.aspect
    for dx, dy, r in [(-0.03, 0.01, 0.034), (0.03, 0.012, 0.032), (0.0, -0.018, 0.04)]:
        S.ellipse(x + dx, cy + dy / S.aspect, r, r * 0.85, canopy, width=1.2)
    if fruit:
        for k in range(7):
            a = k * 2.4
            S.ellipse(x + 0.035 * math.cos(a) * (0.4 + 0.08 * k), cy + 0.025 * math.sin(a) / S.aspect, 0.004, 0.004, fruit, width=0.6)


def draw_architecture(S, bays):
    S.fill(S.poly([(0, 0), (1, 0), (1, 1), (0, 1)]), PAL["parch"])
    border = [(0.008, 0.006), (0.992, 0.006), (0.992, 0.994), (0.008, 0.994)]
    S.line(S.poly(border), 2.2, "goldLo", closed=True, gold=True)
    S.line(S.poly([(0.016, 0.011), (0.984, 0.011), (0.984, 0.989), (0.016, 0.989)]), 1.2, closed=True)
    for name, poly in bays.items():
        S.fill(S.poly(poly), "#efe6cf", 0.55)
    for name, poly in bays.items():
        S.line(S.poly(poly), 3.2, "gold", closed=True, gold=True, jit=0.4)
        S.line(S.poly(poly), 1.0, closed=True, jit=0.4)
    for r, (y0, y1) in enumerate(ROWS):
        for cx in (0.344, 0.656):
            col = [(cx - 0.006, y0 + 0.01), (cx + 0.006, y0 + 0.01), (cx + 0.006, y1), (cx - 0.006, y1)]
            S.shape(S.poly(col), "stone", 1.0)
            S.ellipse(cx, y0 + 0.012, 0.009, 0.006, "gold", gold=True, width=1.0)
        for c, (x0, x1) in enumerate(COLS):
            xm = (x0 + x1) / 2
            S.ellipse(xm, y0 + 0.004, 0.006, 0.004, "gold", gold=True, width=0.8)


def draw_scenes(S):
    A = S.aspect
    S.fill(S.poly([(0.03, 0.235), (0.336, 0.235), (0.336, 0.2593), (0.03, 0.2593)]), PAL["greenHi"], 0.5)
    tree(S, 0.09, 0.236, 0.15, "green", "red")
    tree(S, 0.31, 0.236, 0.12, "greenLo", "ochre")
    body = S.poly([(0.07, 0.212), (0.232, 0.205), (0.236, 0.226), (0.075, 0.234)])
    S.shape(body, "blue")
    S.line(S.poly([(0.09, 0.219), (0.22, 0.214)]), 0.9)
    S.ellipse(0.246, 0.2095, 0.016, 0.0125, "flesh")
    S.ellipse(0.2525, 0.2122, 0.0035, 0.0045, "fleshLo" if "fleshLo" in PAL else "pink", width=0.8)
    S.shape(S.poly([(0.236, 0.196), (0.239, 0.187), (0.243, 0.193), (0.247, 0.185), (0.251, 0.193), (0.255, 0.187), (0.257, 0.198)]), "gold", 1.0, gold=True)
    S.ellipse(0.06, 0.226, 0.012, 0.008, "red")
    figure(S, 0.295, 0.236, 0.115, "red", arm=((-0.05, 0.25), (0.268, 0.162)), hair="black")
    vial = S.rel(0.264, 0.158, [(-0.004, -0.012), (0.004, -0.012), (0.004, -0.004), (0.009, 0.004), (0.006, 0.012), (-0.006, 0.012), (-0.009, 0.004), (-0.004, -0.004)])
    S.shape(vial, "#9fb4a4", 1.0)

    S.fill(S.poly([(0.352, 0.03), (0.648, 0.03), (0.648, 0.16), (0.352, 0.16)]), PAL["sky"], 0.35)
    for x in np.linspace(0.37, 0.63, 11):
        S.ellipse(x, 0.045 + 0.02 * math.sin(x * 40), 0.002, 0.002, "goldHi", gold=True, width=0.4, outline=False)
    wall = [(0.352, 0.185), (0.648, 0.185), (0.648, 0.2593), (0.352, 0.2593)]
    S.shape(S.poly(wall), "stone", 1.2)
    for x in np.arange(0.36, 0.64, 0.032):
        S.shape(S.poly([(x, 0.172), (x + 0.018, 0.172), (x + 0.018, 0.186), (x, 0.186)]), "stone", 1.0)
    for y in np.arange(0.2, 0.26, 0.016):
        S.line(S.poly([(0.354, y), (0.646, y)]), 0.6, "stoneLo")
    for tx in (0.375, 0.625):
        S.shape(S.poly([(tx - 0.022, 0.09), (tx + 0.022, 0.09), (tx + 0.022, 0.2593), (tx - 0.022, 0.2593)]), "stoneLo", 1.2)
        S.shape(S.poly([(tx - 0.026, 0.09), (tx, 0.06), (tx + 0.026, 0.09)]), "redLo", 1.2)
    gpoly = S.poly(ghost_polygon(A))
    S.shape(gpoly, "pale", 1.8)
    gx, gy = GHOST_AT
    S.ellipse(gx, gy - 0.044 / A, 0.013, 0.012, "paleLo", width=1.2)
    S.line(S.poly([(gx - 0.008, gy - 0.044 / A), (gx + 0.008, gy - 0.044 / A)]), 1.4)
    S.line(S.poly([(gx - 0.03, gy - 0.0), (gx + 0.03, gy - 0.0)]), 0.9, "paleLo")
    S.line(S.poly([(gx - 0.026, gy + 0.03 / A), (gx + 0.026, gy + 0.03 / A)]), 0.9, "paleLo")
    S.line(S.poly([(gx, gy - 0.02 / A), (gx, gy + 0.07 / A)]), 0.8, "paleLo")
    S.line(S.poly([(0.462, 0.15), (0.505, 0.06)]), 1.6, "grey")
    S.shape(S.poly([(0.503, 0.064), (0.51, 0.05), (0.508, 0.067)]), "silver", 0.8)

    S.shape(S.poly([(0.69, 0.05), (0.85, 0.05), (0.85, 0.2593), (0.69, 0.2593)]), "red", 1.2, alpha=0.75)
    for y in np.arange(0.07, 0.25, 0.03):
        for x in np.arange(0.705, 0.84, 0.03):
            S.ellipse(x, y, 0.004, 0.004, "gold", gold=True, width=0.5)
    S.shape(S.poly([(0.735, 0.13), (0.802, 0.13), (0.808, 0.252), (0.729, 0.252)]), "brown", 1.4)
    S.shape(S.poly([(0.733, 0.125), (0.804, 0.125), (0.804, 0.133), (0.733, 0.133)]), "gold", 1.0, gold=True)
    figure(S, 0.7688, 0.25, 0.135, "red", arm=((0.05, 0.25), (0.838, 0.174)))
    S.shape(S.poly([(0.7524, 0.140), (0.7524, 0.124), (0.7544, 0.1289 - 0.012), (0.7616, 0.130), (0.7688, 0.115),
                    (0.7759, 0.130), (0.7831, 0.1289 - 0.012), (0.7852, 0.124), (0.7852, 0.140)]), "gold", 1.2, gold=True)
    figure(S, 0.898, 0.25, 0.125, "blue", arm=((-0.05, 0.25), (0.845, 0.172)), hair="auburn", crown=True)

    S.fill(S.poly([(0.03, 0.455), (0.336, 0.455), (0.336, 0.4852), (0.03, 0.4852)]), PAL["stoneLo"], 0.45)
    S.shape(S.poly([(0.25, 0.32), (0.31, 0.32), (0.31, 0.455), (0.25, 0.455)]), "brown", 1.2)
    S.shape(S.poly([(0.26, 0.33), (0.30, 0.33), (0.30, 0.45), (0.26, 0.45)]), "#3b2a20", 1.0, alpha=0.6)
    hy = figure(S, 0.1875, 0.462, 0.16, "black", arm=((0.04, 0.22), (0.24, 0.37)), hair="black")
    S.ellipse(0.243, 0.366, 0.009, 0.008, "white", width=1.0)
    for fx in (0.1688, 0.2062):
        S.ellipse(fx, 0.4593, 0.008, 0.004, "black", width=0.9)

    for y in np.arange(0.42, 0.485, 0.014):
        S.line(S.poly([(0.354, y), (0.646, y)]), 0.6, "stoneLo")
    for x in np.arange(0.36, 0.65, 0.03):
        S.line(S.poly([(x, 0.42), (x - 0.01, 0.485)]), 0.6, "stoneLo")
    figure(S, 0.43, 0.425, 0.13, "ochre", hat="green", arm=((0.05, 0.24), (0.48, 0.355)))
    figure(S, 0.57, 0.425, 0.13, "green", hat="ochre", arm=((-0.05, 0.24), (0.52, 0.355)))

    S.shape(S.poly([(0.69, 0.425), (0.95, 0.425), (0.95, 0.45), (0.69, 0.45)]), "brown", 1.2)
    S.shape(S.poly([(0.672, 0.3), (0.71, 0.3), (0.70, 0.425), (0.672, 0.425)]), "red", 1.2)
    S.shape(S.poly([(0.93, 0.3), (0.965, 0.3), (0.965, 0.425), (0.94, 0.425)]), "red", 1.2)
    S.line(S.poly([(0.672, 0.3), (0.965, 0.3)]), 2.0, "gold", gold=True)
    figure(S, 0.79, 0.425, 0.1, "blue", crown=True)
    figure(S, 0.85, 0.425, 0.1, "redLo", arm=((-0.04, 0.2), (0.81, 0.375)))

    S.fill(S.poly([(0.03, 0.655), (0.336, 0.655), (0.336, 0.6748), (0.03, 0.6748)]), PAL["stoneLo"], 0.4)
    cur = [(0.125, 0.535), (0.205, 0.535), (0.212, 0.655), (0.118, 0.655)]
    S.shape(S.poly(cur), "blue", 1.4)
    for x in np.arange(0.13, 0.21, 0.016):
        S.line(S.poly([(x, 0.537), (x + 0.003, 0.653)]), 0.7, "blueHi")
    S.line(S.poly([(0.118, 0.533), (0.212, 0.533)]), 2.2, "gold", gold=True)
    for fx in (0.15, 0.18):
        S.ellipse(fx, 0.659, 0.008, 0.004, "brown", width=0.8)
    figure(S, 0.075, 0.67, 0.14, "black", arm=((0.04, 0.22), (0.11, 0.595)), hair="black")
    S.line(S.poly([(0.11, 0.595), (0.1638, 0.5859)]), 1.6, "silver")
    S.line(S.poly([(0.11, 0.595), (0.1638, 0.5859)]), 0.6)

    tree(S, 0.6, 0.672, 0.14, "greenHi")
    figure(S, 0.5, 0.672, 0.15, "white", hair="auburn", arm=((0.05, 0.25), (0.5288, 0.5926)))
    for k in range(9):
        a = k * 2.4
        S.ellipse(0.5288 + 0.008 * math.cos(a), 0.5926 + 0.006 * math.sin(a) / A - 0.006, 0.004, 0.004,
                  ["pink", "red", "goldHi", "white", "blueHi"][k % 5], width=0.6)

    S.shape(S.poly([(0.69, 0.655), (0.80, 0.63), (0.92, 0.655), (0.92, 0.6748), (0.69, 0.6748)]), "brown", 1.2)
    figure(S, 0.86, 0.66, 0.13, "brown", hat="brown", arm=((0.04, 0.22), (0.905, 0.62)))
    S.line(S.poly([(0.905, 0.58), (0.912, 0.66)]), 1.4, "brown")
    S.ellipse(0.74, 0.641, 0.009, 0.008, "white", width=1.0)

    S.shape(S.poly([RIVER[:2], (RIVER[2], RIVER[1]), RIVER[2:], (RIVER[0], RIVER[3])]), "water", 1.2, alpha=0.55)
    for k in range(3):
        y = RIVER[1] + (k + 1) * (RIVER[3] - RIVER[1]) / 4
        xs = np.linspace(RIVER[0] + 0.01, RIVER[2] - 0.01, 60)
        S.line(np.array([S.P(x, y + 0.003 * math.sin(x * 70 + k)) for x in xs]), 0.8, "waterLo")

    S.fill(S.poly([(0.03, 0.885), (0.336, 0.885), (0.336, 0.9148), (0.03, 0.9148)]), PAL["greenHi"], 0.4)
    figure(S, 0.11, 0.905, 0.14, "red", hair="auburn", arm=((0.04, 0.24), (0.1475, 0.8137)))
    S.line(S.poly([(0.1475, 0.8137), (0.2437, 0.7926)]), 1.6, "silver")
    S.line(S.poly([(0.1475, 0.8137), (0.2437, 0.7926)]), 0.6)
    figure(S, 0.29, 0.905, 0.14, "black", hair="black", arm=((-0.04, 0.24), (0.255, 0.805)))

    S.shape(S.poly([(0.47, 0.83), (0.625, 0.83), (0.632, 0.875), (0.463, 0.875)]), "white", 1.2)
    S.line(S.poly([(0.47, 0.875), (0.475, 0.905)]), 1.4, "brown")
    S.line(S.poly([(0.622, 0.875), (0.618, 0.905)]), 1.4, "brown")
    gob = S.rel(0.5463, 0.7889, [(-0.012, -0.012), (0.012, -0.012), (0.009, 0.006), (0.002, 0.012), (0.002, 0.03), (0.01, 0.036), (-0.01, 0.036), (-0.002, 0.03), (-0.002, 0.012), (-0.009, 0.006)])
    S.shape(gob, "gold", 1.2, gold=True)
    S.ellipse(0.5463, 0.7889 - 0.012 / A, 0.012, 0.003, "goldLo", width=0.8, gold=True)
    figure(S, 0.42, 0.905, 0.15, "blue", hair="auburn", crown=True)
    S.ellipse(0.6, 0.822, 0.004, 0.009, "white", width=0.8)

    S.fill(S.poly([(0.664, 0.895), (0.97, 0.895), (0.97, 0.9148), (0.664, 0.9148)]), PAL["stoneLo"], 0.5)
    figure(S, 0.8125, 0.905, 0.15, "red", arm=((0.05, 0.24), (0.86, 0.85)))
    S.shape(S.poly([(0.799, 0.812), (0.799, 0.798), (0.8025, 0.802), (0.8065, 0.792), (0.8125, 0.799), (0.8185, 0.792),
                    (0.8225, 0.802), (0.826, 0.798), (0.826, 0.812)]), "gold", 1.2, gold=True)
    figure(S, 0.91, 0.905, 0.12, "silver", hat="silver")

    S.fill(S.poly([(MEADOW[0], MEADOW[1]), (MEADOW[2], MEADOW[1]), (MEADOW[2], MEADOW[3]), (MEADOW[0], MEADOW[3])]), PAL["green"], 0.55)
    rng = np.random.default_rng(170)
    for k in range(120):
        x = rng.uniform(0.04, 0.96)
        y = rng.uniform(0.922, 0.976)
        col = ["pink", "white", "goldHi", "red", "blueHi"][k % 5]
        S.line(S.poly([(x, y), (x + rng.uniform(-0.003, 0.003), y + 0.008)]), 0.6, "greenLo")
        S.ellipse(x, y, 0.0028, 0.0028, col, width=0.5)


def texture(S):
    rng = np.random.default_rng(5)
    H, W = S.H, S.W
    mott = fractal(W, H, 5, 5, 0.55, rng)
    fib = cv2.resize(rng.random((H // 2, W // 16), dtype=np.float32), (W, H), interpolation=cv2.INTER_CUBIC)
    fib2 = cv2.resize(rng.random((H // 16, W // 2), dtype=np.float32), (W, H), interpolation=cv2.INTER_CUBIC)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    p = 3.0 * S.s
    weave = (np.sin(xx * np.pi / p) * np.sin(yy * np.pi / p))
    f = 1.0 + 0.045 * mott + 0.025 * (fib - 0.5) + 0.02 * (fib2 - 0.5) + 0.018 * weave
    d = np.minimum(np.minimum(xx, W - xx), np.minimum(yy, H - yy)) / (0.05 * W)
    f *= 0.93 + 0.07 * np.clip(d, 0, 1)
    S.img *= f[..., None]


def render_tapestry(cfg, scale=2.0):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    S = Sheet(tw, th, scale)
    draw_architecture(S, bay_polygons(th / tw))
    draw_scenes(S)
    texture(S)
    img = np.clip(S.img, 0, 255).astype(np.uint8)
    return img, (S.gold * 255).astype(np.uint8)


def wood(w, h, seed=3):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    n = fractal(w // 2, h // 2, 4, 4, 0.5, rng)
    n = cv2.resize(n, (w, h))
    rings = np.sin((yy * 0.035 + 7.0 * n + 0.004 * xx) * 2.2)
    grain = cv2.resize(rng.random((h // 3, max(2, w // 60)), dtype=np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    base = np.array([42, 64, 92], np.float32)
    f = 1.0 + 0.10 * rings + 0.10 * (grain - 0.5) + 0.12 * n
    return np.clip(base * f[..., None], 0, 255).astype(np.uint8)


def board_scene(cfg, tapestry, pad_frac=0.75):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    layout = marker_layout(cfg)
    pad = int(round(pad_frac * tw))
    W, H = tw + 2 * pad, th + 2 * pad
    img = wood(W, H)
    bx = int(round(0.12 * tw))
    cv2.rectangle(img, (pad - bx, pad - bx), (pad + tw + bx, pad + th + bx), (214, 226, 232), -1)
    img[pad:pad + th, pad:pad + tw] = cv2.resize(tapestry, (tw, th), interpolation=cv2.INTER_AREA)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for i, sq in layout.items():
        x0, y0 = sq[0] + pad
        side = int(round(sq[1][0] - sq[0][0]))
        q = side // 6
        cv2.rectangle(img, (int(x0) - q, int(y0) - q), (int(x0) + side + q, int(y0) + side + q), (250, 250, 250), -1)
        img[int(y0):int(y0) + side, int(x0):int(x0) + side] = cv2.cvtColor(
            cv2.aruco.generateImageMarker(d, i, side, borderBits=1), cv2.COLOR_GRAY2BGR)
    S = np.array([[1, 0, pad], [0, 1, pad], [0, 0, 1]], np.float64)
    return img, S


def camera_frame(cfg, scene, S, size=(1920, 1080), fill=0.62, skew=(30, -10, 25, -20, 15, 10)):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    h = fill * size[1]
    w = h * tw / th
    cx, cy = size[0] / 2, size[1] / 2
    a, b, c, d, e, f = skew
    quad = np.float32([(cx - w / 2 + a, cy - h / 2), (cx + w / 2 + b, cy - h / 2 + e),
                       (cx + w / 2 + c, cy + h / 2), (cx - w / 2 + d, cy + h / 2 - f)])
    rect = np.float32([(0, 0), (tw, 0), (tw, th), (0, th)])
    Ht = cv2.getPerspectiveTransform(rect, quad)
    frame = cv2.warpPerspective(scene, Ht @ np.linalg.inv(S), size, flags=cv2.INTER_AREA,
                                borderMode=cv2.BORDER_REPLICATE)
    yy, xx = np.mgrid[0:size[1], 0:size[0]].astype(np.float32)
    r = ((xx - cx) / size[0]) ** 2 + ((yy - cy) / size[1]) ** 2
    light = (1.04 - 0.55 * r - 0.06 * (xx / size[0]))[..., None]
    frame = np.clip(frame.astype(np.float32) * light, 0, 255).astype(np.uint8)
    return frame, Ht


def still_frame(cfg, tapestry, pad_frac=0.18):
    tw, th = cfg["tapestry"]["width"], cfg["tapestry"]["height"]
    scale = tapestry.shape[1] / tw
    pad = int(round(pad_frac * tapestry.shape[1]))
    H, W = tapestry.shape[0] + 2 * pad, tapestry.shape[1] + 2 * pad
    frame = wood(W, H)
    frame[pad:pad + tapestry.shape[0], pad:pad + tapestry.shape[1]] = tapestry
    Ht = np.array([[scale, 0, pad], [0, scale * tapestry.shape[0] / (th * scale), pad], [0, 0, 1]], np.float64)
    return frame, Ht


def main():
    ap = argparse.ArgumentParser(description="Render the reference tapestry image")
    ap.add_argument("--beats", default=None)
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cfg = load(args.beats)
    out = Path(args.out) if args.out else resolve(cfg, cfg.get("scan", "assets/reference_tapestry.png"))
    out.parent.mkdir(parents=True, exist_ok=True)
    img, gold = render_tapestry(cfg, args.scale)
    cv2.imwrite(str(out), img)
    print(f"wrote {out} {img.shape[1]}x{img.shape[0]}")


if __name__ == "__main__":
    main()
