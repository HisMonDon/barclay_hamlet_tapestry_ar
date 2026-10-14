import argparse
import base64
import copy
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from config import CONFIG, default_beats, dump, load, read
from timeline import Timeline


class EditorModel:
    def __init__(self, path, scan=None):
        self.path = Path(path).resolve()
        self.data = read(self.path)
        self.history = []
        self.revision = 0
        self.saved_revision = 0
        if scan is not None:
            source = str(Path(scan).resolve())
            if self.data.get("scan") != source:
                self.data["scan"] = source
                self.revision = 1

    def checkpoint(self):
        self.history.append(copy.deepcopy(self.data))
        self.history = self.history[-50:]

    def changed(self):
        self.revision += 1

    def undo(self):
        if self.history:
            self.data = self.history.pop()
            self.changed()

    def add_seed(self, beat, x, y, radius, speed):
        self.checkpoint()
        seed = {"x": x, "y": y, "t": 0.0, "speed": speed, "radius": radius, "provisional": False}
        self.data["beats"][beat].setdefault("seeds", []).append(seed)
        self.changed()
        return seed

    def move(self, item, point, position):
        x, y = position
        if point is None:
            dx, dy = x - item["x"], y - item["y"]
            item["x"], item["y"] = x, y
            if "path" in item:
                item["path"] = [[p[0] + dx, p[1] + dy] for p in item["path"]]
        elif isinstance(point, tuple):
            item[point[0]][point[1]] = [x, y]
        else:
            item[point] = [x, y]
        item["provisional"] = False
        self.changed()

    def set_region(self, name, polygon):
        if len(polygon) < 3:
            raise ValueError("A region needs at least three vertices")
        self.checkpoint()
        self.data.setdefault("regions", {})[name] = {"polygon": [list(x) for x in polygon], "provisional": False}
        self.changed()

    def save(self, path=None):
        destination = Path(path or self.path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            backup = destination.with_suffix(destination.suffix + ".bak")
            backup.write_bytes(destination.read_bytes())
        pending = destination.with_suffix(destination.suffix + ".tmp")
        dump(self.data, pending)
        pending.replace(destination)
        self.path = destination
        self.saved_revision = self.revision

    def handles(self, beat):
        handles = []
        for kind in ("seeds", "props", "weeds", "camera"):
            for index, item in enumerate(self.data["beats"][beat].get(kind, [])):
                label = f"{kind.rstrip('s')} {index + 1}"
                if "x" in item and "y" in item:
                    handles.append((label, kind, item, None, (item["x"], item["y"])))
                for key in ("hand", "ear", "at", "from", "to"):
                    if key in item:
                        handles.append((f"{label} {key}", kind, item, key, item[key]))
                for key in ("path", "land"):
                    for x, position in enumerate(item.get(key, [])):
                        handles.append((f"{label} {key} {x + 1}", kind, item, (key, x), position))
        return handles


def build_preview(data, cfg, scan):
    from app import Renderer
    import reference
    settings = copy.deepcopy(cfg)
    for key, value in data.items():
        if key not in ("tapestry", "board", "overlay", "quality", "output"):
            settings[key] = copy.deepcopy(value)
    settings["scan"] = str(scan)
    settings["ink"]["resolution"] = cfg.get("editor", {}).get("ink_resolution", 0.25)
    settings["overlay"]["scale"] = cfg.get("editor", {}).get("overlay_scale", 0.6)
    settings["output_scale"] = cfg.get("editor", {}).get("preview_width", 640) / settings["output"]["width"]
    renderer = Renderer(settings)
    image = cv2.imread(str(scan))
    frame, H = reference.still_frame(settings, image)
    return renderer, frame, H


class SeedEditor:
    def __init__(self, model, cfg, scan):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.model, self.cfg, self.scan = model, cfg, Path(scan).resolve()
        self.p = cfg.get("editor", {})
        self.image = cv2.imread(str(self.scan))
        if self.image is None:
            raise ValueError(f"Could not read scan: {scan}")
        self.window = tk.Tk()
        self.window.title("Hamlet tapestry - scan remapping")
        self.window.geometry(f"{self.p.get('width', 1440)}x{self.p.get('height', 900)}")
        self.beat = 0
        self.selected = None
        self.drag = None
        self.polygon = None
        self.preview = None
        self.future = None
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.built_revision = -1
        self.last_edit = 0
        self.playing = False
        self.last_tick = time.perf_counter()
        self.closed = False
        self.status = tk.StringVar(value="Click adds a seed. Drag moves it. Right-drag sets radius.")
        self.region = tk.StringVar(value=cfg.get("ghost", {}).get("region", "ghost"))
        self.timeline = Timeline(cfg)
        self.clock = tk.DoubleVar(value=0)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.window, background="#211d18", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        panel = ttk.Frame(self.window, padding=12, width=self.p.get("panel_width", 330))
        panel.grid(row=0, column=1, sticky="nsew")
        self.beats = ttk.Combobox(panel, state="readonly", width=38, values=[f"{x.get('id', k + 1)} {x['name']}" for k, x in enumerate(model.data["beats"])])
        self.beats.pack(fill="x")
        self.beats.current(0)
        self.beats.bind("<<ComboboxSelected>>", self.choose_beat)
        self.desc = ttk.Label(panel, wraplength=300)
        self.desc.pack(fill="x", pady=8)
        self.items = tk.Listbox(panel, height=12, exportselection=False)
        self.items.pack(fill="x")
        self.items.bind("<<ListboxSelect>>", self.select_item)
        self.fields = {}
        form = ttk.Frame(panel)
        form.pack(fill="x", pady=8)
        for x, key in enumerate(("t", "speed", "radius", "clip")):
            ttk.Label(form, text=key).grid(row=x, column=0, sticky="w")
            value = tk.StringVar()
            self.fields[key] = value
            entry = ttk.Entry(form, textvariable=value, width=25)
            entry.grid(row=x, column=1, sticky="ew")
            entry.bind("<Return>", self.apply_fields)
        ttk.Button(panel, text="Apply seed settings", command=self.apply_fields).pack(fill="x")
        ttk.Label(panel, text="Region polygon").pack(anchor="w", pady=(12, 2))
        self.regions = ttk.Combobox(panel, textvariable=self.region, values=list(model.data.get("regions", {})))
        self.regions.pack(fill="x")
        self.regions.bind("<<ComboboxSelected>>", lambda event: self.redraw())
        ttk.Button(panel, text="Draw / replace polygon (P)", command=self.start_polygon).pack(fill="x", pady=4)
        ttk.Button(panel, text="Use region as selected seed bounds", command=self.assign_region).pack(fill="x")
        ttk.Button(panel, text="Save (Ctrl+S)", command=self.save).pack(fill="x", pady=(14, 4))
        ttk.Button(panel, text="Undo (Ctrl+Z)", command=self.undo).pack(fill="x")
        ttk.Button(panel, text="Play / pause preview (Space)", command=self.toggle_play).pack(fill="x", pady=4)
        self.preview_label = ttk.Label(panel, text="Building preview...")
        self.preview_label.pack(fill="x", pady=8)
        ttk.Label(panel, wraplength=300, text="Seeds: click / drag. Radius: right-drag.\nPolygon: P, click vertices, Enter finishes.\nDrag polygon vertices to refine the ghost or bounds.\nProps, paths, weeds and camera points also have handles.\nDelete removes the selected seed. Esc cancels a polygon.").pack(fill="x", pady=6)
        self.slider = ttk.Scale(self.window, variable=self.clock, from_=0, to=self.timeline.end, command=lambda value: self.scrub())
        self.slider.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        ttk.Label(self.window, textvariable=self.status).grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        self.canvas.bind("<Configure>", lambda event: self.redraw())
        self.canvas.bind("<Button-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Button-3>", self.radius_press)
        self.canvas.bind("<B3-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-3>", self.release)
        self.window.bind("<Control-s>", lambda event: self.save())
        self.window.bind("<Control-z>", lambda event: self.undo())
        self.window.bind("<Delete>", self.delete)
        self.window.bind("<Return>", self.finish_polygon)
        self.window.bind("<Escape>", self.cancel_polygon)
        self.window.bind("<Key-p>", lambda event: self.start_polygon() if event.widget == self.canvas else None)
        self.canvas.bind("<space>", lambda event: self.toggle_play())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.window.after(50, self.tick)

    def photo(self, image):
        return self.tk.PhotoImage(data=base64.b64encode(cv2.imencode(".png", image)[1]).decode("ascii"))

    def choose_beat(self, event=None):
        self.beat = self.beats.current()
        self.selected = None
        self.clock.set(self.timeline.starts[self.beat])
        self.refresh()

    def refresh(self):
        self.handles = self.model.handles(self.beat)
        self.items.delete(0, "end")
        for x in self.handles:
            self.items.insert("end", x[0])
        self.desc.configure(text=self.model.data["beats"][self.beat].get("desc", ""))
        self.redraw()

    def select_item(self, event=None):
        pick = self.items.curselection()
        if pick:
            self.selected = self.handles[pick[0]]
            item = self.selected[2]
            for key, value in self.fields.items():
                value.set(str(item.get(key, "")))
            self.redraw()

    def xy(self, point):
        return self.origin[0] + point[0] * self.display_size[0], self.origin[1] + point[1] * self.display_size[1]

    def uv(self, event):
        return ((event.x - self.origin[0]) / self.display_size[0], (event.y - self.origin[1]) / self.display_size[1])

    def redraw(self):
        w, h = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        ratio = min((w - 40) / self.image.shape[1], (h - 40) / self.image.shape[0])
        self.display_size = (max(1, round(self.image.shape[1] * ratio)), max(1, round(self.image.shape[0] * ratio)))
        self.origin = ((w - self.display_size[0]) / 2, (h - self.display_size[1]) / 2)
        self.scan_photo = self.photo(cv2.resize(self.image, self.display_size, interpolation=cv2.INTER_AREA))
        self.canvas.delete("all")
        self.canvas.create_image(*self.origin, image=self.scan_photo, anchor="nw")
        region = self.model.data.get("regions", {}).get(self.region.get(), {})
        polygon = region.get("polygon", [])
        if polygon:
            self.canvas.create_polygon(*[v for x in polygon for v in self.xy(x)], fill="", outline="#b6d5ed", width=2)
            for x in polygon:
                px, py = self.xy(x)
                self.canvas.create_rectangle(px - 3, py - 3, px + 3, py + 3, fill="#b6d5ed")
        self.handles = self.model.handles(self.beat)
        for x in self.handles:
            px, py = self.xy(x[4])
            chosen = self.selected is not None and x[2] is self.selected[2] and x[3] == self.selected[3]
            colour = "#ffdb74" if chosen else "#8be0cd" if x[1] == "seeds" else "#c1a8df"
            r = 6 if chosen else 4
            self.canvas.create_oval(px - r, py - r, px + r, py + r, fill=colour, outline="#251e17")
            if chosen:
                self.canvas.create_text(px + 10, py - 12, text=x[0], fill=colour, anchor="w")
                if "radius" in x[2] and x[3] is None:
                    radius = x[2]["radius"] * self.display_size[0]
                    self.canvas.create_oval(px - radius, py - radius, px + radius, py + radius, outline=colour)
        if self.polygon:
            points = [v for x in self.polygon for v in self.xy(x)]
            if len(points) >= 4:
                self.canvas.create_line(*points, fill="#ffdb74", width=2)

    def press(self, event):
        self.canvas.focus_set()
        uv = self.uv(event)
        if not 0 <= uv[0] <= 1 or not 0 <= uv[1] <= 1:
            return
        if self.polygon is not None:
            self.polygon.append(list(uv))
            self.redraw()
            return
        handles = self.model.handles(self.beat)
        distance = lambda position: math.hypot(self.xy(position)[0] - event.x, self.xy(position)[1] - event.y)
        nearest = min(handles, key=lambda x: distance(x[4]), default=None)
        if nearest and distance(nearest[4]) <= self.p.get("pick_px", 14):
            self.model.checkpoint()
            self.selected = nearest
            self.drag = ("point", nearest[2], nearest[3])
            self.items.selection_clear(0, "end")
            self.items.selection_set(handles.index(nearest))
            self.select_item()
        else:
            polygon = self.model.data.get("regions", {}).get(self.region.get(), {}).get("polygon", [])
            index = min(range(len(polygon)), key=lambda x: distance(polygon[x]), default=None)
            if index is not None and distance(polygon[index]) <= self.p.get("pick_px", 14):
                self.model.checkpoint()
                self.drag = ("region", self.region.get(), index)
            else:
                seed = self.model.add_seed(self.beat, *uv, self.p.get("seed_radius", 0.06), self.p.get("seed_speed", 0.035))
                self.selected = ("seed", "seeds", seed, None, uv)
                self.drag = ("point", seed, None)
                self.refresh()
        self.redraw()

    def radius_press(self, event):
        if self.selected and self.selected[1] == "seeds" and self.selected[3] is None:
            self.model.checkpoint()
            self.drag = ("radius", self.selected[2], None)
            self.motion(event)

    def motion(self, event):
        if self.drag is None:
            return
        kind, item, point = self.drag
        uv = self.uv(event)
        uv = tuple(min(1, max(0, x)) for x in uv)
        if kind == "point":
            self.model.move(item, point, uv)
        elif kind == "region":
            region = self.model.data["regions"][item]
            region["polygon"][point] = list(uv)
            region["provisional"] = False
            self.model.changed()
        else:
            aspect = self.image.shape[0] / self.image.shape[1]
            item["radius"] = round(max(0.002, math.hypot(uv[0] - item["x"], (uv[1] - item["y"]) * aspect)), 5)
            item["provisional"] = False
            self.fields["radius"].set(str(item["radius"]))
            self.model.changed()
        self.last_edit = time.perf_counter()
        self.redraw()

    def release(self, event):
        self.drag = None
        self.last_edit = time.perf_counter()

    def apply_fields(self, event=None):
        if not self.selected:
            return
        item = self.selected[2]
        try:
            values = {key: float(value.get()) for key, value in self.fields.items() if key != "clip" and value.get().strip()}
            if any(values.get(key, 1) <= 0 for key in ("speed", "radius")) or values.get("t", 0) < 0:
                raise ValueError("time must be nonnegative; speed and radius must be positive")
            clip = self.fields["clip"].get().strip()
            if clip and clip not in self.model.data.get("regions", {}):
                raise ValueError("Choose a named region for clip")
            self.model.checkpoint()
            item.update(values)
            if clip:
                item["clip"] = clip
            else:
                item.pop("clip", None)
            item["provisional"] = False
            self.model.changed()
            self.last_edit = time.perf_counter()
            self.redraw()
        except ValueError as error:
            self.status.set(str(error))
        return "break"

    def start_polygon(self):
        self.polygon = []
        self.canvas.focus_set()
        self.status.set(f"Drawing {self.region.get()}: click vertices, Enter finishes, Esc cancels")

    def finish_polygon(self, event=None):
        if self.polygon is None:
            return
        try:
            self.model.set_region(self.region.get().strip() or "bounds", self.polygon)
            self.polygon = None
            self.regions.configure(values=list(self.model.data["regions"]))
            self.status.set("Region updated. Assign it as seed bounds if needed.")
            self.redraw()
        except ValueError as error:
            self.status.set(str(error))

    def cancel_polygon(self, event=None):
        self.polygon = None
        self.redraw()

    def assign_region(self):
        if self.selected and self.region.get() in self.model.data.get("regions", {}):
            self.model.checkpoint()
            self.selected[2]["clip"] = self.region.get()
            self.selected[2].pop("bounds", None)
            self.selected[2]["provisional"] = False
            self.fields["clip"].set(self.region.get())
            self.model.changed()

    def delete(self, event=None):
        if self.window.focus_get() != self.canvas or self.selected is None or self.selected[1] != "seeds":
            return
        self.model.checkpoint()
        seeds = self.model.data["beats"][self.beat].get("seeds", [])
        if self.selected[2] in seeds:
            seeds.remove(self.selected[2])
        self.selected = None
        self.model.changed()
        self.refresh()

    def undo(self):
        self.model.undo()
        self.selected = None
        self.refresh()

    def save(self):
        try:
            self.model.save()
            self.status.set(f"Saved {self.model.path.name}; previous version in .json.bak")
        except OSError as error:
            self.status.set(f"Save failed: {error}")

    def scrub(self):
        if self.preview is not None:
            renderer, frame, H = self.preview
            image = renderer.frame(frame, H, self.clock.get())
            image = cv2.resize(image, (300, 169), interpolation=cv2.INTER_AREA)
            self.preview_photo = self.photo(image)
            self.preview_label.configure(image=self.preview_photo, text="")
        self.status.set(f"{self.clock.get():05.2f}s | preview {'updating' if self.built_revision != self.model.revision else 'current'} | {'unsaved' if self.model.saved_revision != self.model.revision else 'saved'}")

    def toggle_play(self):
        self.playing = not self.playing

    def tick(self):
        if self.closed:
            return
        now = time.perf_counter()
        if self.playing:
            self.clock.set(min(self.timeline.end, self.clock.get() + now - self.last_tick))
            if self.clock.get() >= self.timeline.end:
                self.playing = False
            self.scrub()
        self.last_tick = now
        if self.future is not None and self.future.done():
            try:
                self.preview = self.future.result()
                self.built_revision = self.future_revision
                self.scrub()
            except Exception as error:
                self.built_revision = self.future_revision
                self.status.set(f"Preview failed: {error}")
            self.future = None
        if self.future is None and self.built_revision != self.model.revision and self.drag is None and now - self.last_edit >= self.p.get("debounce_s", 0.25):
            self.future_revision = self.model.revision
            self.future = self.worker.submit(build_preview, copy.deepcopy(self.model.data), self.cfg, self.scan)
        self.window.after(50, self.tick)

    def close(self):
        if self.model.saved_revision != self.model.revision:
            from tkinter import messagebox
            answer = messagebox.askyesnocancel("Unsaved remapping", "Save your changes before closing?")
            if answer is None:
                return
            if answer:
                self.save()
                if self.model.saved_revision != self.model.revision:
                    return
        self.closed = True
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()

    def run(self):
        self.window.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Remap tapestry seeds, prop anchors, camera keys and region polygons onto a scan")
    parser.add_argument("scan")
    parser.add_argument("--beats", default=None)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    beats = args.beats or default_beats(args.config)
    model = EditorModel(beats, args.scan)
    if args.out:
        model.path = Path(args.out).resolve()
    cfg = load(beats, args.config)
    SeedEditor(model, cfg, args.scan).run()


if __name__ == "__main__":
    main()
