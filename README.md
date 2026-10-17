# Hamlet's Tapestry

A camera tracks four ArUco markers around a hand-drawn Hamlet tapestry. Ink follows the corruption through the play, embroidered weeds emerge from soaked ink, and a gold banner covers the darkness without removing it. At the end, weeds cross onto the desk, the view opens to the whole camera frame, and the image holds in silence.

The default show is now `beats_serpent.json`, "The Serpent Beneath" from `REVEAL_SPEC.md`: an ink creature crawls panel to panel and, wherever it passes, the printed tapestry gives way to the aligned corrupt art underneath. It runs S00-S16 over 77 seconds on `assets/clean_ref.png`; see **Reveal show** below. The previous show is still available with `--beats beats_tapestry.json`.

`beats_tapestry.json` uses the earlier supplied 1000x1573 artwork in `assets/hamlet_tapestry.png`, with B01-B21, 75 seconds and camera keys for every beat. Panel regions, effect anchors and close-ups are mapped to this image, including the clean red-and-blue throne scene. The previous supplied image is preserved in `assets/hamlet_tapestry_previous.png`; the original `beats.json`, schematic `beats_v2.json` and `assets/reference_tapestry.png` are also preserved.

## Install and run on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe make_markers_pdf.py
.\.venv\Scripts\python.exe app.py --camera 0
```

Use `--windowed --debug` while setting up. `--camera` also accepts a phone camera stream URL. Defaults for device, capture size, reconnect interval and backend are in `config.json`. Choose `dshow`, `msmf`, `ffmpeg` or `auto` to suit the device. Camera capture runs in a background thread, retains the last image on disconnect and retries automatically. The timeline waits for its first marker lock. After that, the last good homography survives marker loss.

Pick portrait `tabloid` or `letter` in `config.json` and regenerate `markers.pdf`. Print on US Letter at **100% / Actual size** and verify the 100 mm scale bar. Place marker 0 top-left, 1 top-right, 2 bottom-right and 3 bottom-left, upright with their inner corners facing the drawing. The black squares sit `gap_mm` beyond the drawing edges. At least three markers must be visible to acquire tracking.

| Key | Action |
| --- | --- |
| Space | Pause / resume with synchronized sound |
| Left / Right | Step one beat backward / forward |
| R | Reveal check: show the whole corrupt layer on the paper |
| Shift+R / Home | Restart and play |
| V | Virtual camera / full composited camera frame |
| D | Operator debug display: camera/audio status, fps, and in the reveal show the panel outlines, phases, the active arrival heatmap, the camera's follow target and its zoom (marked when the ceiling holds it back) |
| C | Open / close the operator controls window |
| T | Cycle the ink tone (Iron gall, Lamp black, Oxblood) |
| P | Switch quality preset (live / render), rebuilt in the background |
| B | Start the preloaded backup from its beginning, fullscreen |
| L | Return to live rendering |
| Esc / Q | Quit |

The controls window (`--controls`, or C) is a separate window for the laptop screen, so the projector output stays clean. It shows a preview, a scrubber with beat ticks (click or drag to seek), the beat list (click to jump), play/restart/step buttons, the three ink tones and the two quality presets. A preset switch builds the new renderer on a background thread while the show keeps running; the first build of `render` can take a while until its arrival field is cached in `.cache/`. `--quality render` starts in the render preset. Large timeline jumps rebuild the dense embroidery in its fixed drawing order and can take several seconds; the pre-rendered backup seeks immediately.

The beat flagged `"hold": true` (S16 in the reveal show, B21 in the earlier show) freezes the image; audio falls silent from the beat flagged `"silence": true` (the S14 banner), or from `sound.silence_from`. The image remains on screen at the end until a control is used. Operator text is hidden unless debug is enabled.

## Render the backup and review it

```powershell
.\.venv\Scripts\python.exe render.py --mode board --out renders/backup_tapestry.mp4
.\.venv\Scripts\python.exe app.py --play-backup
```

This writes a 1920x1080, 30 fps, 75-second MP4, a matching WAV and a PNG contact sheet with one frame per beat. `imageio-ffmpeg` supplies ffmpeg for audio muxing when a system ffmpeg is absent. The MP4 uses lossless ALAC audio to preserve exact silence in B21. The WAV remains available as the live player's audio companion. Keep the WAV beside the MP4. If muxing is unavailable, the renderer explicitly reports the separate video and audio files.

```powershell
.\.venv\Scripts\python.exe render.py --mode still --source assets/scan.png --beats beats_mapped.json --out renders/scan_backup.mp4
.\.venv\Scripts\python.exe render.py --mode board --source assets/scan.png --beats beats_mapped.json --sheet-only --sheet review/scan_sheet.png
.\.venv\Scripts\python.exe render.py --start 52 --end 65 --out renders/banner_spill.mp4
.\.venv\Scripts\python.exe render.py --times 5.1,5.32,18.8,43.5,57.4,65 --frames renders/details
```

`still` uses a flat scan; `board` places it on a synthetic tracked board and desk; `video` uses a recorded camera clip and its marker detections. The synthetic board is suitable for rehearsing the effect, but the best backup for the classroom uses a recorded view of the real board:

```powershell
.\.venv\Scripts\python.exe render.py --mode video --source assets/board_recording.mp4 --beats beats_mapped.json --out renders/backup_tapestry.mp4
```

`--quality render` uses full-resolution composition; `--quality live` matches the optimized live path. `--tone` picks the ink tone, `--no-lens` turns off depth of field, rack focus, bloom and dust motes. `--no-audio`, `--no-atmosphere`, `--fps`, `--size`, `--start`, `--end`, `--sheet-only` and `--frames` support focused reviews. A clip's audio starts at the same timeline position as its picture.

## Look and camera

- **Ink.** Absorbent warp and weft threads pull thin, connected fingers of ink ahead of each front (`ink.cost.wick`, sized in millimetres), and a ridged-noise channel network breaks fronts into branching capillary fingers (`capillary`). Both change the arrival field, so stains stall with fingered edges and weeds still root only in soaked cloth. Fresh ink carries an additive wet sheen: a thin meniscus glint on the light-facing side of a moving front, glints on thread tops while wet, a soft glow, and a slow candle sway between two light directions (`ink.sheen`). It fades as the ink dries to matte black. The sheen is a separate additive layer, so it never lightens the permanent ink.
- **Weeds.** From B11, a continuous layer grows across the tapestry as each area of ink soaks in. Evenly spaced, jittered roots and varied directions fill the cloth by the pullback; the ghost stays clear, and the banner area remains open until its later invasion. Large colonies can use `distribution: "even"`, `region`, `count`, `direction_jitter` (degrees) and `stagger` (seconds). Stems taper, stitches have a dark edge, a lit core and a highlight whose strength depends on how the thread lies against the light, plus twist striations and specular dots in the render preset. Leaves are fishbone satin (slanted stitches into a midrib, so the two halves catch light differently). Stem tips end in French knots, satin buds with a calyx, or small lazy-daisy flowers (`bud` and `petal` colours). Every stitch casts a soft drop shadow (`weeds.shadow_soft`), most visible where runners cross the white board and the desk.
- **Splashes.** Impacts throw ballistic droplets with motion streaks and highlights: the vial at the ear, the hops and arras spray, the pearl in the goblet (with a ring on the wine), petals on the river and the crown on the floor. Each landing leaves a small satellite stain that blooms a moment after impact (`splash`; set `stain: false` to keep droplets only). The `clip` on the vial, pearl and crown keeps their satellites inside the right bay.
- **Camera.** Handheld motion layers smooth noise into position, roll and zoom, scaled per beat with `"handheld"` (0 = locked, 1 = normal) and faded out as the view opens to the full camera frame. Camera keys may add `fx`, `fy`: the focus target, which trails the camera by `lens.lag`, so a key change racks focus from one subject to another. Fast camera moves soften the whole frame and resolve when the camera lands (`lens.rack`). Beats may add `"lens": [{"t", "dur", "amount"}]` for a deliberate focus pull. Depth of field falls off radially around the focus target (`lens.dof`), weaker on wide shots and gone in the full camera frame. Bright highlights bloom softly.
- **Dust.** Motes drift through the candlelight between lens and cloth as soft bokeh, brighter near the light (`motes`), with parallax against camera moves. They stop in the final hold.
- **Banner.** A slow sheen slides across the couched gold (`banner.shimmer`), beneath the glint that falters in B19.

Each preset has an `fx` block in `config.json`. `live` turns off the costlier passes (glow, stitch detail, radial depth of field, bloom) and keeps rack-focus softening only; `render` turns everything on. Layers are warped only over the screen area they cover, which keeps the live preset at roughly its previous frame time on synthetic input. Measure on the classroom laptop with `benchmark.py` and switch individual `fx` flags off if needed.

## Reveal assets (print mode)

`assets/clean_hi.png` is the image that gets printed; `assets/corrupt_hi.png` is the corrupted version of the same composition. Align them once, and again whenever either image changes:

```powershell
.\.venv\Scripts\python.exe -m tools.align_layers
```

The tool fits a SIFT + RANSAC homography from the corrupt art to the clean art, refines each panel with ECC, and keeps a panel's refinement only when it lowers that panel's measured line-structure residual. It writes `assets/corrupt_aligned.png`, `assets/clean_ref.png`, `assets/panels.json` (seeded from the beat file's regions on the first run; edit it or pass `--reseed`) and `assets/assets.hash`, plus `review/align_check.jpg` (checkerboard, aligned edges, unaligned edges) and `review/align_report.json`. Figures that were redrawn in the corrupt art read as a higher residual; check those panels in the review image.

Print `clean_ref.png` at 100% scale, centred, at the size the tool reports, and copy its `print_margin_mm` line into `config.json`. Markers still sit `gap_mm` beyond the sheet edges. Press **R** with the board tracked to show the whole corrupt layer on the paper as an alignment check; `render.py --reveal` renders the same view.

## Reveal show

`reveal.py` turns each panel beat into a reveal. Per panel it computes, once and cached in `.cache/reveal_*.npz`, a geodesic arrival map from the entry (or `entry_object` polylines) over a cost that is cheaper along the corrupt art's drawn lines, and a second map from the exit for draining. Each frame thresholds those maps through lookup tables, so every frame is a pure function of `t`: pause, stepping, restart, scrubbing and the backup render all agree.

A panel beat carries a `panel` block:

```json
"panel": {"id": "garden", "delay": 1.5, "phases": {"breach": 0.15, "flood": 0.4, "blink": 0.09, "recede": 0.29},
          "entry": [0.144, 0.202], "exit": [0.33, 0.11], "speed_mul": 1.0, "gravity": 0.0, "entry_object": [[[x, y], ...]]}
```

The Serpent (`serpent.py`) is born at the first panel's entry, travels along the gold pillars and frame bands on the `serpent.route` of each panel beat, pauses and swells at each BREACH, pours itself into the FLOOD, and re-forms at the exit as the ink drains into it. Its path is a centripetal Catmull-Rom spline through the waypoints with the lateral S-wave baked in (`amplitude_px`, `wavelength_px`, per-beat `wave` multipliers), so the body follows the head's own track. The body is a variable-width SDF with a scrolling-noise edge, drop shadow, damp halo, a tailward sheen, a poison glint at breaches and birth, capillaries bleeding into the fibres, and droplets that stay behind as residue. The last panel beat's `settle` waypoints coil it up at rest. All of it is a function of `t`.

`delay` is seconds from the beat start; `phases` are fractions of the rest of the beat, and whatever remains is SETTLED. During FLOOD the ink front leads the reveal by at least 0.15 s and the reveal finishes under the ink before RECEDE drains toward the exit, so no moving edge between clean and corrupt is ever uncovered. Residue veins stay along the drain channel and creep in from the corners. `"mode": "light"` (the ghost) reveals with a cool light wipe and no ink, and `"mode": "swim"` (the river) floods along the Serpent's own track as it swims through. Panel outlines come from `assets/panels.json`. Ink colours, widths, vein and cost settings live in the beat file's `reveal` block; `quality.<preset>.reveal_width` sets the mask resolution (550 live, 1100 render).

### Special beats

- **Ghost (S02).** The Serpent never touches a light panel. Near one, its path loses its sideways wave and is pushed out until its ink edge clears the panel by `serpent.recoil_px` (6 px on 1100-wide paper); capillaries that would reach in are dropped. The light-wipe leaves the ghost figure (the `ghost` region) lifted toward `ghost_light` by `ghost_lift`, applied after the lighting match, so it stays a cool light grey.
- **River (S10).** In a `swim` panel the Serpent swims from entry to exit during FLOOD, its head kept just ahead of the ink front. The arrival map is the wake: each point of the track floods `swim_lag` after the head passes, and the ink spreads to the banks over the cost image (`swim_spread`). `swim_reach` is the flood level at which the head reaches the exit. The flood paces steadily instead of on the usual cubic ease.
- **Weeds.** A `veins` colony roots weeds at the ends of each flood panel's residue veins (both ends of the drain vein and the tip of each corner creep), up to `count` per panel, once the vein reaches them. The weeds' cloth opens one panel at a time as each panel settles, and the whole sheet opens once a covering banner has finished.
- **Banner (S14).** A weave with `"cover": true` is drawn over the weeds as well as the rot. `gap_shade` darkens the gaps between the couched rows so black shows through, `body_through` lets the Serpent's body darken the threads above it, and `sheen_through` lets its moving sheen catch on them. A weave with no shimmer or glint redraws only the row on the shuttle.
- **Spill (S15).** A `spill` colony takes `sides` and a `span` along the edge. The S15 beat's `serpent.spill` route uncoils the Serpent off the bottom edge over `dur` seconds, until its tail rests `tail_px` past the paper. The reveal canvas carries a `margin` so the Serpent can be drawn on the desk.
- **Hold (S16).** Everything is frozen and silent.

### Following the Serpent

The virtual camera follows the Serpent. Its target is the head 0.35 s ahead along the path, or the centre of the panel while that panel floods and blinks. Each beat's `"follow"` weight mixes the target with the keyed centre (0 keeps the keyed framing, 1 follows fully, `[x, y]` sets the axes separately), and a change of weight eases in over `blend_s`. A critically damped spring (ω 3.5 rad/s) carries the centre and the zoom, so there are no cuts. The spring is integrated once at startup on a fixed 120 Hz grid, which keeps the camera a pure function of `t`. The centre is held on the paper: a crop narrower than the sheet stays inside it, and a wider crop keeps the whole sheet in view. Per-beat zoom stays in the `camera` keys.

The zoom ceiling keeps the crop at least `ceiling_px` (640) camera pixels wide. It is measured through the tracked homography every frame and eases in over `ceiling_knee` (15%) instead of stopping a push dead. It applies to the live feed, recorded video and the live-preset board. A render that paints the scan directly has no camera pixels to protect and keeps the full push. A 2.5× push needs the paper at least about 580 px wide in a 1080p feed, so frame the board tightly; on the synthetic board (paper about 390 px wide) the deepest pushes stop near 1.7-1.9×. Settings live under `camera.follow`, `camera.ceiling_px` and `camera.ceiling_knee` in the beat file.

### Matching the camera

`realism.py` keeps the revealed art from looking pasted on. Each frame it measures the paper as the camera sees it:

- **Lighting:** a low-resolution map of the camera's view of the paper divided by `clean_ref`, blurred and clamped to 0.5-1.6, carries shadows, falloff and white balance onto the revealed art.
- **Grain:** camera noise is measured on the flattest patch of paper (minus the print's own texture) and matched with noise from a fixed bank indexed by frame time.
- **Focus:** the camera's sharpness on a textured patch is compared with the clean art at several blur levels, and the corrupt art is drawn from the matching pre-blurred level.

In the live app the measurements ease in with a temporal EMA and take turns across frames; offline and backup renders measure every frame, so a given frame and `t` always render identically. Renders that paint the scan directly (`direct_scan`) skip realism, since their paper is unlit. Settings live under `reveal.realism` in the beat file; `fx.realism` turns it off per preset.

`benchmark.py` reports `reveal_ms` and `reveal_p95_ms` per segment: the reveal layers, the realism measurements and the reveal composite together. The budget is 12 ms on the presentation laptop.

## Remap the real drawing

Crop and straighten the scan to the drawing edges, with no board or desk inside it, then run:

```powershell
.\.venv\Scripts\python.exe editor.py assets/scan.png --beats beats_tapestry.json --out beats_mapped.json
.\.venv\Scripts\python.exe app.py --beats beats_mapped.json --camera 0 --debug --windowed
```

Select a beat in the right panel. Click the scan to add a seed; drag a handle to move it; right-drag a selected seed to set radius. The item list includes prop anchors, falling-petal destinations, wicking paths, weed origins and camera centers. Moving a seed also translates its wicking path. Enter seed time, speed, radius and a named clip region in the fields, then Apply.

Choose `ghost` or a bounds region, press **P**, click its polygon vertices, and press **Enter**. Drag existing polygon vertices to refine the outline. The region button assigns the selected region to a seed. Esc cancels an unfinished polygon. Delete removes a selected seed when the scan canvas has focus. Ctrl+Z undoes; Ctrl+S saves atomically and keeps the previous file as `.json.bak`. Adjusted objects are marked `provisional: false`.

Scrub the bottom slider for an effect preview or press Space with the canvas focused. Preview fields rebuild in the background after edits. It is intentionally lower resolution than final export. Polygon names and beat timing remain intact when saving; the editor preserves other beat data.

The supplied artwork already has mapped vial hand/ear, joined hands, ghost polygon and command thread, river/petal landings, blade path, cup, crown and floor. For a different drawing or crop, realign these anchors before presenting. Adjust camera widths/heights numerically in the beat file if the drawing's composition needs different close-ups. Focus targets (`fx`, `fy`) are separate from camera centres and the editor does not move them; update them by hand after remapping. Check the gold-detection thresholds against the actual pencils or paint. Confirm the desk spill fits the real camera view and marker spacing.

`reference.py` generates only the old schematic at `assets/reference_tapestry.png`; it does not overwrite the supplied artwork. Missing configured artwork raises an error so an export cannot silently substitute the schematic.

## Configuration and sound

Board dimensions, paper size and overlay margin live in `config.json`. Story-specific seeds, props, camera keys, regions, ink, embroidery and banner settings live in the beat file. Positions are normalized 0-1 across the drawing; radii and speeds are measured in drawing widths. A camera key uses center `x,y` and width `w`, or full-drawing-height fraction `h`; `f: 1` opens to the full camera frame.

The live preset processes ink at 40% of tapestry resolution, embroidery at 70%, and composition at 1056x594, then outputs 1920x1080. These independent scales are editable under `quality.live`. Tracking runs every third rendered frame. The render preset retains full-resolution composition and finer effect canvases. Synthetic board exports project the original scan directly into each final view, preserving close-up detail instead of enlarging a downsampled fake camera image. Video is encoded once to H.264; adding audio copies that picture stream without recompression. `quality.render.direct_scan`, `video_crf` and `video_preset` control this path. `performance.opencv_threads` avoids oversubscribing the CPU.

Each atmosphere effect has an `on` switch. Grain is deterministic, candlelight evolves slowly and all atmospheric motion stops in the final hold. Weeds reveal along precomputed stitch lengths and root only after the configured soak delay. Banner threads sit above the original ink; later weeds sit above the weave.

Place original or licensed PCM WAV recordings in `assets/sfx/` named `crackle.wav`, `drip.wav`, `seep.wav`, `creak.wav` and `shuttle.wav`. Missing or unreadable files use deterministic synthesized placeholders. No audio is downloaded. Audio uses Windows' built-in [winsound file playback](https://docs.python.org/3/library/winsound.html), mixed into one timeline soundtrack so pause, seeking and restart keep the cues aligned. `--no-audio` disables it. Test speaker levels in the room; placeholders are a starting point for subtle diegetic sound.

## Validation and live-demo limits

```powershell
.\.venv\Scripts\python.exe test_pipeline.py
.\.venv\Scripts\python.exe -m unittest test_animation -v
.\.venv\Scripts\python.exe benchmark.py --frames 90
```

Tests cover both paper sizes, tracking and freeze on loss, ghost protection, story order, permanent ink, banner gaps and row reveal, deterministic embroidery, coverage across every story panel, soaked roots, desk spill, animation continuity, audio timing and silence, editor round trips, backup seeking and camera reconnect. Images and benchmark JSON are written under `test_out/`. `REVIEW.md` records the visual reviews; final sheets are under `review/`.

Performance is machine- and camera-dependent. Benchmarking uses a synthetic 1080p input and includes marker tracking and the rendering path, but excludes physical camera driver latency, GUI presentation and real audio hardware. Occasional frames can exceed 33 ms even when segment averages exceed 30 fps. A frozen homography cannot compensate for a board being moved while its markers are hidden. Camera drivers that block on a read cannot block the projector loop, but may delay reconnection. Keep a rehearsed backup MP4 and its WAV beside it, test B before class, and use the actual classroom laptop/projector for the final check.
