# Review log

Every round renders `render.py --mode board --sheet-only` (synthetic phone frame of the board, reference tapestry)
plus close-up peeks of the beats under work, and checks them against five questions:

1. Would a stranger understand the cause-and-effect chain without narration?
2. Does the ink look like ink in cloth, or like a digital blob?
3. Do the weeds look embroidered and alive, or like clip art?
4. Is anything distracting, flickery or cheap-looking on a projector?
5. Does every element serve the thesis?

Sheets are kept in `review/` (scaled JPEGs).

## Round 0: baseline (old engine running beat sheet v2)

`review/round0_sheet.jpg`

- **Seen.** Every stain is a smooth round blob with a crisp edge, and the bay clips are only bounding rectangles.
  There is no ghost cap, so the ghost is just untouched. There is no tide, so at the banner the ink is a single
  blob with paper showing through the weave. Nothing grows.
- **Verdict.** It reads as a tech demo. Q2 fails badly and Q1 fails from B11 onwards because nothing after the
  stains happens.

## Item reviews while building (before round 1)

- **Ink, pass 1.** Fronts became fibrous, but a `sin·sin` weave texture produced a visible checkerboard. That is
  cheap on a projector (Q4). I replaced it with irregular per-thread warp/weft variation.
- **Ink, pass 2.** The trees looked as if they resisted ink. Lines were 2.6x faster than blank cloth, so ink raced
  round the outlines and left the canopies white for seconds. I cut the contrast to 0.55/1.05, so the drawing still
  guides the ink without looking like masking.
- **Ink, pass 3.** Stalled fronts aliased into pixel stair-steps, very visible at 3x zoom. I added a 1 px apron per
  tile and a 0.7 px soften inside the tile shader. That needed the tile-activity windows dilated by the apron,
  otherwise static tiles went stale beside active ones. A test now checks the cache against a fresh compute.
- **Ink, pass 4.** The B10 burst came out a perfect diamond and the throne stain a smooth egg. The cause was too
  much anisotropy and noise averaging out along long geodesics. I cut anisotropy to 0.04 and added large-scale
  lobes. Fronts are now lobed and fingered, and the grain survives as fibre streaks.
- **Weeds, pass 1.** The highlight stripe was half the thread width, so the stems read as lime-green clip art.
  Knots read as orange berries. 132k strokes made the B20 spill a dense wreath hugging the board.
- **Weeds, pass 2.** Changes: darker palette, a 1 px sheen covering only the middle third of each stitch, a crease
  at each stitch start for the stem-stitch rhythm, capped branching, and fewer, longer spill runners. That made it
  read as crewel embroidery. The overlay margin went from 0.5 to 0.8 because the runners physically could not
  reach the desk visible in the full camera frame.
- **Banner.** The plain weave read as a brick wall, then as rows of pills. I switched to couched gold: long
  twisted floats, narrow staggered ties, and black between rows. It now reads as gold thread laid row by row
  over the dark.
- **Props.**
  - Vial: tilt, horizontal liquid surface, bead, stretch, detach, fall and splash all read. The drop's impact is
    now the seed for the ear bloom, so the ink can never start before the drop lands.
  - Crown, first try: it looked like a hoop. Once its axis turned to the viewer the spikes foreshortened away.
  - Crown, rebuilt: explicit axis tilt and yaw plus a spin, so it rolls in three-quarter view with the spikes
    visible.
  - Crown on the paper: gold resist kept the drawn crown gilded on the head, so there were two crowns. Gold resist
    is now removed under the crown's start. My reference drawing also floated the crown above the head, which I
    fixed.
  - Hops: drops were too small to read, so I made them about 60% bigger. R's stain was spreading onto G before the
    hop arrived, so I shrank its radius.

## Round 1: all ink, weeds, banner and props in

`review/round1_sheet.jpg`

- **Q1.** The chain reads from B02 to B15 without narration. Each stain starts at the person it should and reaches
  the next in play order (garden → crown → queen → walls → ghost's command → prince → courtiers → stage → arras →
  river → blade → cup → crown). The B07 grey thread is the clearest moment.
- **Q2.** Pass. Lobed, fibrous fronts, a wet front darker than dry ink behind, and the linen texture is visible in
  the black. Drawn lines stay faintly readable through the multiply.
- **Q3.** Borderline. Up close they are embroidered, but from projector distance the olive highlights read
  yellow-lime and the rust knots read as berries.
- **Q4.** The B16 framing dropped below the tapestry onto the white board and a marker, which looks like a
  mistake.
- **Q5.** B17 should look nearly black. It read as warm grey-brown mud, which weakens "rotting underneath".

Changes:

- Weed palette darkened and desaturated (olive #4b4a24, black-green #1c2a1b, rust #6e3518, highlights only about
  25% lighter).
- Knot probability cut by a third.
- Dry ink density 0.93 to 0.95, dry tone darker (#1a1410).
- B16 camera recentred on the tapestry (y 0.70 to 0.74).

## Round 2: sound, atmosphere and remapping

`review/round2_sheet.png`, rendered in synthetic board mode at all 21 beats and visually inspected.

- The chain remains readable: crown to queen, the protected ghost, the command thread, and the black river each have distinct framing. The fine line work survives in the ink.
- The first embroidered shoots read clearly against old garden ink. The final runners visibly cross the white board onto the brown desk. The banner still shows black through its gaps.
- Warmth, vignette and grain were kept restrained: flicker amplitude 0.018, grain 0.7 intensity levels, vignette strength 0.14. None should compete with the drop or ghost.
- Audio is generated deterministically from the same cue times, with the drip tied to actual vial impact and the seep ending at the banner. Every sample from 65 seconds onward is zero. Pause and seek are checked through a fake native backend.
- Found an antialiasing leak at the left edge of unreached banner rows when changing overlay resolution. Gated each row by its start time. This preserves row-by-row weaving at every quality setting.
- Added an editor with background preview rebuilding. The model round-trip tests cover path translation, region polygons, undo and backups; a real Tk window smoke test also passed.
- Performance profiling found repeated rendering of the settled crown and finished banner. Cached those states. Live composition uses a configurable 0.67 working scale and resizes to the requested 1920x1080 output; export quality retains full-resolution composition.

## Round 3: final render quality and live preset
`review/round3_sheet.png` and `review/round3_live_sheet.png`, both 21-beat synthetic board contact sheets, visually inspected side by side.
- The full-quality image preserves the line drawing beneath the black ink; the ghost remains a small, cool light shape. The river turns black before the meadow is overrun. The gold remains visibly separated by dark gaps, and later plants cover parts of it.
- The live image softens tiny stitch and ink-front details. This is acceptable at the wide desk reveal: the corruption, protected ghost, gold cover and spreading runners remain clear. The live and offline arrival grids produce slightly different small branch shapes, while preserving event order.
- The reference figures are deliberately schematic. Their flatness is a limitation of the stand-in image; the actual hand drawing will supply the intended richness. No effect can substitute for precise remapping onto it.
- Optimized premultiplied-alpha blending with OpenCV arithmetic. Final live scales: ink 0.40, embroidery 0.70, composition 0.55, then 1920x1080 output. Tracking every third frame. Full export keeps composition at 1920x1080.
- Measured sequential rendering with synthetic 1920x1080 camera input, four OpenCV threads, and tracking included; no competing render job was running. Startup with a warm arrival cache: 2.445 s.

| Segment start | Mean ms | p95 ms | Mean fps |
| --- | --- | --- | --- |
| 4s | 24.41 | 32.73 | 40.97 |
| 10s | 13.31 | 19.34 | 75.13 |
| 27s | 21.05 | 26.91 | 47.51 |
| 45s | 26.98 | 36.46 | 37.07 |
| 53s | 20.08 | 25.85 | 49.81 |
| 60s | 29.79 | 39.12 | 33.57 |
| 66s | 18.13 | 25.38 | 55.16 |

These numbers exclude camera-driver latency, window presentation and actual sound-device latency. They are not a guarantee for the classroom laptop; the heaviest sampled segment exceeds 33 ms in its slowest frames. The preloaded MP4 is the practical fallback.

## Round 4: encoded backup and motion audit

Rendered and inspected `review/round4_sheet.png`, then decoded the actual MP4 to `review/encoded_sheet.png` and inspected that too. Inspected temporal montages `vial_motion.png`, `crown_motion.png` and `ending_motion.png`; focused MP4 clips are in `renders/review_vial.mp4`, `renders/review_crown.mp4` and `renders/review_banner_spill.mp4`.

- Vial: the bead detaches, falls to the ear, and the first bloom follows impact. The reference drawing and tiny vial need careful positioning on the real scan.
- Crown: its pivot, falling edge-on phase, roll and final ring shape are readable. The old drawn crown remains faintly visible through permanent ink, consistent with the drawing surviving beneath the rot.
- Ending: the glint progresses across the gold, fails as plants invade, then the view opens to reveal runners beyond all four board edges. The physical desk contrast is legible in the synthetic camera view.
- Found an export-specific problem: AAC introduced 51 tiny nonzero samples (peak 2/32768) after the silence boundary. Switched the default MP4 audio codec to lossless ALAC and remuxed the backup. Decoded verification now finds exactly 441,000 zero-valued samples in B21. Native Windows playback also passed a silent-device smoke test without an error.
- The backup is 2,250 frames at 30 fps, 1920x1080, exactly 75 seconds. Full-resolution picture rendering took 186.1 seconds (82.7 ms/frame); compression/muxing adds time.
- The complete pipeline passes, including both paper sizes and the original beat-file compatibility check. Recorded-camera sampling and matching backup WAV tests pass.

Remaining real-world checks: crop and rectify the real scan, remap all provisional anchors/regions, verify dark-line and gold detection on the actual materials, rehearse the edge spill with the actual camera placement, set speaker levels, and check projector blacks. The laptop benchmark uses synthetic capture; no physical camera disconnect or classroom projector was available for a hardware rehearsal.

## Follow-up: ghost command thread and blurry close-ups

The user's screenshot exposed a quality failure missed in the earlier full contact sheets: the synthetic camera reduced the drawing to roughly 400 pixels wide before the virtual camera enlarged its bays. A 1080p output file therefore contained soft, enlarged source pixels. The stitched command thread also read as a heavy dashed annotation.

- Kept the creative meaning of the thread (the ghost's command reaching Hamlet), reduced its width from 0.65 to 0.28, dimmed its cool grey and narrowed the gaps. It now reads as a fine thread.
- Synthetic board exports now project the original scan directly into the final view before ink and embroidery, preserving its native detail while keeping the desk scene. Real live-camera rendering is unaffected by that offline-only replacement.
- Removed the lossy intermediate MP4 encoding pass. ffmpeg now receives original BGR frames and encodes H.264 once at configurable CRF 16; sound muxing copies the encoded picture.
- Visually inspected `review/quality_comparison.png`: previous encoded frame on the left, corrected render on the right. Faces, arch outlines, cloth grain and the ghost's outline are visibly sharper. The included schematic reference art is still a placeholder for the real scan.
- Regression checks confirm that close-up contrast survives even when the synthetic camera contains no corresponding fine detail, desk pixels outside the drawing stay intact, and adding audio does not change decoded picture pixels. Full pipeline passes.

The regenerated `renders/backup_sharp.mp4` is now the configured default for no-camera playback. Decoded the actual new video for the final before/after comparison; verified 2,250 frames at 30 fps and exactly 441,000 zero-valued audio samples in B21. Picture rendering and single-pass encoding took 224.4 seconds (99.7 ms/frame).

## Round 5: graphics upgrade

Rendered every beat in both presets on the synthetic board, plus close-up crops of the vial impact, crown drips, Gertrude's bloom, the arras burst, first sprouts, goblet, banner and desk spill. The pipeline test passes for both paper sizes and the original beat file still loads.

- **Ink.** Fronts now grow thin fingers along absorbent threads and branch through a capillary channel network; stalled edges stay fingered instead of smooth. A first sheen pass read as grey haze over every fresh stain and a soft white halo on the paper. It now sits just behind the front as a thin glint plus sparse thread-top glints, and fades within about a second. It is additive and separate from the multiply layer, so "nothing is ever cleansed" still holds.
- **Weeds.** Fishbone satin leaves, tapered stems, buds, small flowers and French knots with a lit crescent read as crewel work at close range. The new soft shadows are what make the runners look raised on the white board.
- **Splashes.** Droplets read at every impact. Satellite stains first appeared before their droplet landed, because the stain halo leads the front; satellite seeds are now delayed by the halo lead.
- **Camera.** The first depth-of-field setting blurred the edges of a 0.8-width banner shot; the falloff now starts further out and fades on wide shots. The B11 to B12 whip pan softens the whole frame for about 0.2 s after landing, which reads as a focus pull rather than a cut.
- **Dust.** Motes at the first brightness were invisible on a projector-scale frame; they are now sparse soft bokeh, brighter near the candle side.
- **Performance.** Two extra full-frame warps and per-tile Python work raised live frame time by about half. Warping each layer only over its on-screen bounds, drawing shadows straight at quarter resolution, re-blurring only the changed region and moving the sheen sway to two precomputed light directions brought the live preset back to within about 5% of the previous build, measured in the same session on synthetic input. The previous build's numbers above remain the hardware reference; re-run `benchmark.py` on the classroom laptop.

## Whole-tapestry weeds and motion repair

- The sparse original colonies left most panels bare. B11 now adds 640 potential roots, distributed one per jittered grid cell across the cloth. Ink arrival plus the soak delay controls when each root starts. Three seconds of stagger and varied stem directions build interlocking growth instead of isolated upright clumps.
- The ghost remains protected, the banner area stays clear until its later invasion, and non-spill leaves, loops and stitches are clipped to the cloth. The clipping also preserves the reveal time of the remaining part of each stitch. Desk growth still begins in B20.
- Coverage checks inspect weeds alone rather than mistaking ink or props for plants. By B18, both paper sizes cover every eligible cell of a 10-by-15 grid, with dense growth in all twelve story panels. The tests also check that coverage builds gradually from B11.
- Crown release now uses the same projection as the visible crown. Settling starts and finishes at zero wobble, removing position jumps at both boundaries. Focused tests compare every projected vertex across all motion phases in both directions and at several viewing angles.
- The previous weed renderer repainted partial stitches into the persistent image, leaving old highlights and darker antialiased edges. Finished stitches now cache in fixed 30 Hz completion batches and active tips redraw over a clean backing. Focused checks compare overlay and soft-shadow pixels exactly across playback, repeated frames and backward/forward seeks. Large live seeks rebuild this ordering and can take several seconds; the encoded backup remains the immediate-seek option.
- Final validation: the full pipeline passes for tabloid, letter and the original beat file; all five focused animation tests pass. Isolated weed alpha covers 83% of eligible tabloid cloth and 88% of letter cloth by B18. Both fill 142/142 eligible grid cells; minimum cell coverage is 34% and 35% respectively.
- Visually inspected `review/weeds_coverage_comparison.jpg` and `review/weeds_growth_sheet.jpg`, including the early sprouts, tide, full canopy, readable gold banner and four-sided desk spill. `review/weeds_performance.json` records the final synthetic live benchmark (30 measured frames per phase, four OpenCV threads). This session's host is slower than the historical benchmark, including phases before any weeds appear: 54–79 ms/frame before growth and 110–138 ms/frame in the dense phases. Use the regenerated 30 fps backup for smooth presentation on this host.
- Regenerated `renders/backup_sharp.mp4` and its companion WAV. Rendering and encoding took 1,058.2 seconds on this host. The MP4 contains 2,250 frames at 1920×1080 and 30 fps (75 seconds). Decoded audio has exactly 441,000 zero-valued samples from 65 seconds onward. Visually inspected the actual encoded frames in `review/weeds_encoded_sheet.jpg`, including the unchanged composition through the final hold. H.264 reconstruction differs slightly across the held frames (mean absolute channel difference 1.04/255); the source image is held fixed. Export measurements are in `review/weeds_export_validation.json`.

## Supplied tapestry artwork (2026-10-17)

- Preserved the supplied 1009x1559 PNG unchanged as `assets/hamlet_tapestry.png`. The default `beats_tapestry.json` maps effects to that image; original art and beat files remain available.
- Traced the twelve story bays using each row's pillar positions, the raised-hand ghost silhouette and the river. The old meadow event now grows across the lower floral border, since this artwork has no separate meadow panel.
- Aligned the vial and ear, crown drips, joined hands, command thread, prince, courtier hops, arras strike, bouquet, river landings, poisoned blade, goblet, tablecloth and final crown. Camera centres and focus targets follow these anchors.
- Visually inspected `review/tapestry_sheet.png` and full-size Garden, Walls, Arras and Goblet frames. Major impacts and ghost protection align with the artwork. The supplied painted vial remains visible underneath the animated vial.
- Full pipeline checks pass for tabloid, letter and legacy beats, with all existing ink, story-order and weed-coverage thresholds retained. Final mapping ink checks and all five focused animation regressions pass. Missing configured art reports an error, and schematic generation retains a separate output path.
- Regenerated the default `renders/backup_tapestry.mp4` and matching WAV with full render quality. Rendering and encoding took 420.7 seconds for 2,250 frames; prior backup files remain available.
- Decoded the MP4: 1920x1080, 30 fps, exactly 75 seconds, all 21 sampled beats and the final frame readable. The last 441,000 audio samples (65-75 seconds) are exactly zero, with audible content before the hold. Visually inspected `review/tapestry_encoded_sheet.jpg`; measurements are in `review/tapestry_export_validation.json`.

## Replacement tapestry artwork (2026-10-17)

- Installed the latest 1000x1573 PNG directly from the attachment without resizing or re-encoding. SHA-256: `a2f7c67e06da7fc01a5bd6114d16e59ccfa13e099eabcf5d2f5c3daace9289ba`. The clean throne scene replaces the previous purple ghost treatment; the previous supplied image is retained in `assets/hamlet_tapestry_previous.png`.
- Remapped the twelve story bays, ghost silhouette, ink and prop anchors, paths, camera centres and focus targets. The new throne scene was traced directly; matching panels were aligned against their previous details. Crown landing and river/border bounds follow the replacement image.
- The protected banner leaves a narrow strip above it in the feast panel. Added five local creepers during B16 to keep this strip visibly overgrown; they still wait for soaked ink and respect the banner mask. Feast coverage before the banner is 58% on tabloid and 62% on letter, without changing the coverage thresholds.
- Full pipeline checks pass for tabloid, letter and legacy beats; all five animation tests pass. Visually inspected the updated `review/tapestry_sheet.png` and full-size Garden, Crown and Goblet frames. The timeline remains B01-B21 and 75 seconds.
- Regenerated `renders/backup_tapestry.mp4`, its matching WAV and contact sheet. Rendering and encoding took 637.6 seconds. Decoded validation confirms 2,250 frames at 1920x1080 and 30 fps, exactly 75 seconds, all 21 beat samples and the final frame readable, and exactly 441,000 zero-valued audio samples during the final hold. Visually inspected `review/tapestry_encoded_sheet.jpg`; updated measurements and source checksum are in `review/tapestry_export_validation.json`.
