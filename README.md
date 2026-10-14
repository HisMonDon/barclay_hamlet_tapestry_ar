# Hamlet's Tapestry

A camera tracks four ArUco markers around a hand-drawn Hamlet tapestry. Ink follows the corruption through the play, embroidered weeds emerge from soaked ink, and a gold banner covers the darkness without removing it. At the end, weeds cross onto the desk, the view opens to the whole camera frame, and the image holds in silence.

The default `beats_v2.json` contains B01-B21, 75 seconds and camera keys for every beat. The original `beats.json` is preserved. The included reference is a schematic stand-in; all scene coordinates remain provisional until mapped onto the real scan.

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
| R | Restart and play |
| V | Virtual camera / full composited camera frame |
| D | Operator debug display, including camera/audio status |
| B | Start the preloaded backup from its beginning, fullscreen |
| L | Return to live rendering |
| Esc / Q | Quit |

B21 freezes the image and silences audio from 65 seconds. It remains on screen after 75 seconds until a control is used. Operator text is hidden unless debug is enabled.

## Render the backup and review it

```powershell
.\.venv\Scripts\python.exe render.py --mode board --out renders/backup.mp4
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
.\.venv\Scripts\python.exe render.py --mode video --source assets/board_recording.mp4 --beats beats_mapped.json --out renders/backup.mp4
```

`--quality render` uses full-resolution composition; `--quality live` matches the optimized live path. `--no-audio`, `--no-atmosphere`, `--fps`, `--size`, `--start`, `--end`, `--sheet-only` and `--frames` support focused reviews. A clip's audio starts at the same timeline position as its picture.

## Remap the real drawing

Crop and straighten the scan to the drawing edges, with no board or desk inside it, then run:

```powershell
.\.venv\Scripts\python.exe editor.py assets/scan.png --beats beats_v2.json --out beats_mapped.json
.\.venv\Scripts\python.exe app.py --beats beats_mapped.json --camera 0 --debug --windowed
```

Select a beat in the right panel. Click the scan to add a seed; drag a handle to move it; right-drag a selected seed to set radius. The item list includes prop anchors, falling-petal destinations, wicking paths, weed origins and camera centers. Moving a seed also translates its wicking path. Enter seed time, speed, radius and a named clip region in the fields, then Apply.

Choose `ghost` or a bounds region, press **P**, click its polygon vertices, and press **Enter**. Drag existing polygon vertices to refine the outline. The region button assigns the selected region to a seed. Esc cancels an unfinished polygon. Delete removes a selected seed when the scan canvas has focus. Ctrl+Z undoes; Ctrl+S saves atomically and keeps the previous file as `.json.bak`. Adjusted objects are marked `provisional: false`.

Scrub the bottom slider for an effect preview or press Space with the canvas focused. Preview fields rebuild in the background after edits. It is intentionally lower resolution than final export. Polygon names and beat timing remain intact when saving; the editor preserves other beat data.

Before presenting, align the vial hand/ear, the joined hands, the ghost polygon and command thread, river/petal landings, blade path, cup, rolling crown and floor. Adjust camera widths/heights numerically in the beat file if the drawing's composition needs different close-ups. Check the gold-detection thresholds against the actual pencils or paint. Confirm the desk spill fits the real camera view and marker spacing.

## Configuration and sound

Board dimensions, paper size and overlay margin live in `config.json`. Story-specific seeds, props, camera keys, regions, ink, embroidery and banner settings live in the beat file. Positions are normalized 0-1 across the drawing; radii and speeds are measured in drawing widths. A camera key uses center `x,y` and width `w`, or full-drawing-height fraction `h`; `f: 1` opens to the full camera frame.

The live preset processes ink at 40% of tapestry resolution, embroidery at 70%, and composition at 1056x594, then outputs 1920x1080. These independent scales are editable under `quality.live`. Tracking runs every third rendered frame. The render preset retains full-resolution composition and finer effect canvases. `performance.opencv_threads` avoids oversubscribing the CPU.

Each atmosphere effect has an `on` switch. Grain is deterministic, candlelight evolves slowly and all atmospheric motion stops in the final hold. Weeds reveal along precomputed stitch lengths and root only after the configured soak delay. Banner threads sit above the original ink; later weeds sit above the weave.

Place original or licensed PCM WAV recordings in `assets/sfx/` named `crackle.wav`, `drip.wav`, `seep.wav`, `creak.wav` and `shuttle.wav`. Missing or unreadable files use deterministic synthesized placeholders. No audio is downloaded. Audio uses Windows' built-in [winsound file playback](https://docs.python.org/3/library/winsound.html), mixed into one timeline soundtrack so pause, seeking and restart keep the cues aligned. `--no-audio` disables it. Test speaker levels in the room; placeholders are a starting point for subtle diegetic sound.

## Validation and live-demo limits

```powershell
.\.venv\Scripts\python.exe test_pipeline.py
.\.venv\Scripts\python.exe benchmark.py --frames 90
```

Tests cover both paper sizes, tracking and freeze on loss, ghost protection, story order, permanent ink, banner gaps and row reveal, deterministic embroidery, soaked roots, desk spill, audio timing and silence, editor round trips, backup seeking and camera reconnect. Images and benchmark JSON are written under `test_out/`. `REVIEW.md` records the visual reviews; final sheets are under `review/`.

Performance is machine- and camera-dependent. Benchmarking uses a synthetic 1080p input and includes marker tracking and the rendering path, but excludes physical camera driver latency, GUI presentation and real audio hardware. Occasional frames can exceed 33 ms even when segment averages exceed 30 fps. A frozen homography cannot compensate for a board being moved while its markers are hidden. Camera drivers that block on a read cannot block the projector loop, but may delay reconnection. Keep a rehearsed backup MP4 and its WAV beside it, test B before class, and use the actual classroom laptop/projector for the final check.
