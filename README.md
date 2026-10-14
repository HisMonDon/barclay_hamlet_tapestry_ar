# Hamlet's Tapestry — live AR ink

A phone camera looks down at the drawing. Four ArUco markers at its corners give a homography to a flat
"tapestry space". Ink is computed in tapestry space and composited back onto the live frame. Weaves are drawn in a second layer composited over the ink. Both layers extend past the drawing
by `overlay_margin`, so content can later be placed on the desk around the board. A virtual camera then crops an animated rectangle of tapestry space and renders it fullscreen.

## Setup

```bash
python -m venv .venv
.venv\Scripts\python -m pip install opencv-python numpy
.venv\Scripts\python make_markers_pdf.py
```

Pick the paper in `config.json` (`"paper": "tabloid"` or `"letter"`, always portrait), then regenerate the PDF.
Print `markers.pdf` on US Letter at **100% / Actual size** and check the 100 mm scale bar. Cut out the four markers and mount them
upright around the drawing: 0 top-left, 1 top-right, 2 bottom-right, 3 bottom-left. The dot marks each marker's inner
corner. Its black square sits `gap_mm` outside both edges of the drawing.

## Run

```bash
.venv\Scripts\python app.py --camera 0
```

`--camera` takes a device index, or a stream URL from a phone-webcam app (for example `http://192.168.1.20:4747/video`).
Other flags: `--capture-size 1920x1080`, `--tone "Oxblood"`, `--windowed`, `--beats other.json`, `--config other.json`.

## config.json

The board lives here and nowhere else. Changing `paper` resizes the drawing, the tapestry aspect, the marker layout
and the PDF text together.

| Key | Meaning |
| --- | --- |
| `paper` | `tabloid` (11 x 17 in, default) or `letter` (8.5 x 11 in). Always portrait |
| `papers_in` | paper sizes in inches |
| `marker_mm`, `gap_mm` | marker size and its gap outside the drawing edge |
| `tapestry_width` | width of tapestry space in px. Height follows the paper aspect |
| `overlay_margin` | how far the overlay canvas reaches past each edge of the drawing, in drawing widths (default 0.5) |

`tapestry` and `board` inside a beat sheet are ignored.

## Test

```bash
.venv/bin/python test_pipeline.py
```

Renders a synthetic camera frame of the board for both paper sizes, then checks tracking, ink, the ghost cap, the
woven banner and drawing in the desk margin. Frames are written to `test_out/`.

| Key | Action |
| --- | --- |
| Space | pause / resume |
| ← / → | step back / forward one beat |
| R | restart |
| V | switch between the virtual camera and the full composited camera frame (useful for aiming the phone) |
| Esc / Q | quit |

At least 3 of the 4 markers must be visible to lock on. Until the first lock, the raw camera image is shown. After
that, the last good homography is kept whenever detection fails, so a hand over a marker never makes the ink flicker.

## beats.json

| Key | Meaning |
| --- | --- |
| `ink.tone`, `ink.tones` | ink palettes (`stain`, `front`, `deep`) taken from the reference |
| `ink.ramp` | `[darkness, colour, alpha]` stops: stain halo, then a sharp front, then dried deep ink |
| `ink.alpha`, `ink.noise` | strength and shape of the fractal noise that makes uneven soaking |
| `ink.edge` | width of the soft front, in tapestry-width units. `1.0` gives the bare `clamp((t − t0)·v − d + α·noise)` |
| `ink.caps` | ellipses where ink tops out at a pale tint instead of going black (the ghost). `ink.resist` is read the same way |
| `ink.cap` | defaults for every cap: `max` (strength of the tint at full ink, 0-1, default 1) and `tint` (default `#9fb4dc`, cool blue-grey) |
| `camera` | easing, zoom dip on long moves, and idle drift |
| `beats[]` | `name`, `duration`, `desc`, `camera` keys, `seeds` and `weaves` |

All positions are normalised 0–1 (`x` across the width, `y` down the height). Distances and speeds are measured in
tapestry widths. Times inside a beat are seconds from that beat's start.

- **Camera key:** `{t, x, y, w}`. `(x, y)` is the crop centre and `w` is the crop width. The crop height follows the
  output aspect.
- **Seed:** `{x, y, t, speed}`. Optional fields:
  - `radius` stops growth.
  - `bounds` `[x0, y0, x1, y1]` clips the seed to one bay.
  - `alpha` overrides the noise strength.
  - `lift: true` turns the seed into a weave over its `bounds`, at its `speed`.
- **Cap:** `{x, y, rx, ry}` ellipse. Optional `angle`, `feather`, `max`, `tint`.
- **Weave:** `{t, bounds}`. Woven over the ink, never erasing it: rows of thread go in top to bottom, the shuttle
  alternates direction, and the black stays visible between rows and where the weft dips under the warp. Optional
  `speed` (rows advance in tapestry widths per second), `colour`, `pitch` (row height), `fill` (thread share of a row),
  `warp` (over/under period) and `duty` (share of the period where the weft is on top).

`beats.json` was extracted from the reference animation. It has 20 three-second beats, its 40 camera keyframes, every
ink source (including the 170 meadow flowers, reproduced exactly from its seeded RNG), a top-down "tide" that ends with
the whole tapestry black, and the colour palette.
