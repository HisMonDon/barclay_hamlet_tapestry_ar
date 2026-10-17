import threading

import cv2
import numpy as np

WINDOW = "Tapestry controls"
FONT = cv2.FONT_HERSHEY_SIMPLEX
BG = (20, 17, 15)
PANEL = (32, 28, 25)
LINE = (62, 56, 50)
FG = (222, 216, 202)
DIM = (138, 131, 120)
ACCENT = (84, 168, 214)
ACCENT_DIM = (40, 78, 100)


class Rebuild:
    """Builds a Renderer for another quality preset in the background so the show keeps running."""

    def __init__(self):
        self.thread = None
        self.result = None
        self.error = None
        self.name = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, name, factory):
        if self.busy:
            return False
        self.name, self.result, self.error = name, None, None

        def work():
            try:
                self.result = factory()
            except Exception as error:
                self.error = f"{type(error).__name__}: {error}"

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return True

    def take(self):
        if self.thread is None or self.busy:
            return None
        out, self.thread = self.result, None
        return out


class Controls:
    """Operator window: preview, scrubber with beat ticks, beat list, ink tone and quality preset.

    It opens as its own window so the projector output stays clean. All keyboard shortcuts keep working
    while it has focus.
    """

    def __init__(self, timeline, tones, tone, quality, qualities=("live", "render"), size=(1200, 720)):
        self.tl = timeline
        self.tones = list(tones)
        self.tone = tone
        self.quality = quality
        self.qualities = list(qualities)
        self.W, self.H = size
        self.open = False
        self.shown = False
        self.actions = []
        self.drag = False
        self.status = ""
        m = 16
        self.prev = (m, m, 820, m + int(round((820 - m) * 9 / 16)))
        self.list = (836, m, self.W - m, self.prev[3])
        y = self.prev[3] + 22
        self.scrub = (m, y + 18, self.W - m, y + 50)
        by = self.scrub[3] + 24
        self.buttons = []
        x = m
        for label, act, w in (("Play / Pause", ("toggle",), 130), ("Restart", ("restart",), 96),
                              ("< Beat", ("step", -1), 84), ("Beat >", ("step", 1), 84)):
            self.buttons.append((label, (x, by, x + w, by + 34), act))
            x += w + 8
        x += 18
        self.tone_x = x
        for name in self.tones:
            w = 18 + 11 * len(name)
            self.buttons.append((name, (x, by, x + w, by + 34), ("tone", name)))
            x += w + 8
        x += 18
        for name in self.qualities:
            w = 24 + 11 * len(name)
            self.buttons.append((name.capitalize(), (x, by, x + w, by + 34), ("quality", name)))
            x += w + 8
        self.status_y = by + 70

    def show(self):
        if not self.open:
            cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback(WINDOW, self._mouse)
            self.open = True
            self.shown = False

    def hide(self):
        if self.open:
            cv2.destroyWindow(WINDOW)
            self.open = self.shown = False

    def toggle(self):
        self.hide() if self.open else self.show()

    def poll(self):
        out, self.actions = self.actions, []
        return out

    def _t_at(self, x):
        x0, _, x1, _ = self.scrub
        return float(np.clip((x - x0) / max(1, x1 - x0), 0.0, 1.0)) * self.tl.end

    @staticmethod
    def _inside(r, x, y):
        return r[0] <= x < r[2] and r[1] <= y < r[3]

    def _mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            sx0, sy0, sx1, sy1 = self.scrub
            if self._inside((sx0 - 6, sy0 - 18, sx1 + 6, sy1 + 6), x, y):
                self.drag = True
                self.actions.append(("seek", self._t_at(x)))
                return
            lx0, ly0, lx1, ly1 = self.list
            if self._inside(self.list, x, y):
                k = int((y - ly0 - 8) // self._row())
                if 0 <= k < len(self.tl.beats):
                    self.actions.append(("seek", self.tl.starts[k]))
                return
            for label, r, act in self.buttons:
                if self._inside(r, x, y):
                    self.actions.append(act)
                    return
        elif event == cv2.EVENT_MOUSEMOVE and self.drag:
            self.actions.append(("seek", self._t_at(x)))
        elif event == cv2.EVENT_LBUTTONUP:
            self.drag = False

    def _row(self):
        y0, y1 = self.list[1], self.list[3]
        return max(14, (y1 - y0 - 16) / max(1, len(self.tl.beats)))

    def _text(self, img, s, org, scale=0.48, col=FG, thick=1):
        cv2.putText(img, s, (int(org[0]), int(org[1])), FONT, scale, col, thick, cv2.LINE_AA)

    def draw(self, frame, t, playing, notes=()):
        if not self.open:
            return
        if self.shown:
            try:
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    self.open = self.shown = False
                    return
            except cv2.error:
                self.open = self.shown = False
                return
        self.shown = True
        img = np.full((self.H, self.W, 3), BG, np.uint8)
        x0, y0, x1, y1 = self.prev
        if frame is not None:
            img[y0:y1, x0:x1] = cv2.resize(frame, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
        cv2.rectangle(img, (x0 - 1, y0 - 1), (x1, y1), LINE, 1)
        cur = self.tl.beat_at(t)
        lx0, ly0, lx1, ly1 = self.list
        cv2.rectangle(img, (lx0, ly0), (lx1, ly1), PANEL, -1)
        row = self._row()
        for k, b in enumerate(self.tl.beats):
            ry = ly0 + 8 + k * row
            if k == cur:
                cv2.rectangle(img, (lx0 + 4, int(ry)), (lx1 - 4, int(ry + row - 2)), ACCENT_DIM, -1)
                p = (t - self.tl.starts[k]) / max(1e-6, b["duration"])
                cv2.rectangle(img, (lx0 + 4, int(ry + row - 4)), (lx0 + 4 + int((lx1 - lx0 - 8) * min(1.0, p)), int(ry + row - 2)),
                              ACCENT, -1)
            col = FG if k == cur else DIM
            base = int(ry + row * 0.68)
            self._text(img, b.get("id", f"B{k + 1:02d}"), (lx0 + 12, base), 0.45, col)
            self._text(img, b.get("name", ""), (lx0 + 62, base), 0.45, col)
            self._text(img, f"{self.tl.starts[k]:5.1f}s", (lx1 - 66, base), 0.42, col)
        sx0, sy0, sx1, sy1 = self.scrub
        cv2.rectangle(img, (sx0, sy0), (sx1, sy1), PANEL, -1)
        end = max(1e-6, self.tl.end)
        px = lambda tt: int(sx0 + (sx1 - sx0) * tt / end)
        cv2.rectangle(img, (sx0, sy0), (px(min(t, end)), sy1), ACCENT_DIM, -1)
        for k, s in enumerate(self.tl.starts):
            x = px(s)
            cv2.line(img, (x, sy0), (x, sy1), LINE, 1)
            if k % 2 == 0 or (px(self.tl.starts[min(k + 1, len(self.tl.starts) - 1)]) - x) > 36:
                self._text(img, self.tl.beats[k].get("id", ""), (x + 3, sy0 - 5), 0.36, FG if k == cur else DIM)
        x = px(min(t, end))
        cv2.line(img, (x, sy0 - 4), (x, sy1 + 4), ACCENT, 2)
        for label, r, act in self.buttons:
            on = (act[0] == "tone" and act[1] == self.tone) or (act[0] == "quality" and act[1] == self.quality) \
                or (act[0] == "toggle" and playing)
            cv2.rectangle(img, r[:2], r[2:], ACCENT_DIM if on else PANEL, -1)
            cv2.rectangle(img, r[:2], r[2:], ACCENT if on else LINE, 1)
            self._text(img, label, (r[0] + 10, r[1] + 22), 0.47, FG)
        ty = self.buttons[0][1][1] - 8
        self._text(img, "INK TONE", (self.tone_x, ty), 0.36, DIM)
        qx = next(r[0] for label, r, act in self.buttons if act[0] == "quality")
        self._text(img, "QUALITY", (qx, ty), 0.36, DIM)
        b = self.tl.beats[cur]
        line = f"{t:6.2f} s / {self.tl.end:.0f} s   {'PLAYING' if playing else 'PAUSED'}   {b.get('id', '')} {b.get('name', '')}"
        self._text(img, line, (16, self.status_y), 0.55, FG)
        self._text(img, b.get("desc", ""), (16, self.status_y + 26), 0.45, DIM)
        y = self.status_y + 52
        for n in list(notes) + ([self.status] if self.status else []):
            self._text(img, n, (16, y), 0.45, ACCENT)
            y += 22
        self._text(img, "Space play   Left/Right beat   Shift+R restart   R reveal check   T tone   P quality   C close panel   V camera view   D debug",
                   (16, self.H - 14), 0.4, DIM)
        cv2.imshow(WINDOW, img)
