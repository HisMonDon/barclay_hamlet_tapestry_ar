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
