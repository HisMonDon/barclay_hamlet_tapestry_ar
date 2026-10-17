# Supplied tapestry artwork

- Replaced the art with the latest supplied 1000x1573 PNG, retaining its exact bytes and clean throne scene. The previous supplied art is preserved as `assets/hamlet_tapestry_previous.png`.
- The supplied PNG is preserved unchanged in `assets/hamlet_tapestry.png` and is now the default art through `beats_tapestry.json`.
- Panel regions, ghost protection, prop anchors, ink paths, camera centres and focus targets are mapped to its layout. Original beat files and schematic art remain available.
- The matching backup is `renders/backup_tapestry.mp4`. Schematic generation keeps its own output path, and missing configured art reports an error.

# Graphics upgrade

Drop these files over the project. Every new effect has a config switch, and old beat files still load.

## Whole-tapestry overgrowth

- `beats_v2.json` now starts a dense, evenly distributed colony in B11. Roots wait for soaked ink, so the overgrowth follows the corruption and fills the tapestry by the pullback. Varied directions connect the plants across the cloth; the ghost and banner remain protected.
- `weeds.py` supports `distribution: "even"` and `direction_jitter` for large colonies.
- Finished stitches are cached in a fixed order; growing tips and their shadows redraw cleanly, so playback, pause and seeking agree without accumulating old highlights. Non-spill leaf and stitch geometry stays inside the cloth.
- `props.py` fixes crown position jumps when the crown leaves its pivot and finishes rolling.
- `test_pipeline.py` checks broad coverage and every story panel. `test_animation.py` checks crown motion at each phase boundary, weed playback/seek parity, shadows and edge clipping.

## New files
- `lens.py`: depth of field around a focus target, rack-focus softening on camera moves, highlight bloom.
- `particles.py`: splash droplets with satellite stains (`Splash`), dust motes in the candlelight (`Motes`).
- `controls.py`: operator window with preview, scrubber, beat list, ink tone and quality preset; background preset rebuild.

## Changed files
- `fields.py`: thread wicking (directional erosion of arrival distance) and capillary channels in the cost field. Cache key bumped, so the first run recomputes arrival fields.
- `ink.py`: additive wet sheen layer (meniscus glint, wet thread glints, candle sway), smoothed front normals, `spec_layer` / `spec_box`.
- `weeds.py`: tapered stems, edge/core/highlight shading by thread direction, twist striations, fishbone satin leaves, buds, lazy-daisy flowers, better French knots, quarter-resolution soft drop shadows.
- `props.py`: `impacts()` on vial, hops/spray, pearl, petals and crown; splashes built automatically; old built-in splash drawing removed.
- `banner.py`: slow metallic shimmer on the couched gold; cached coverage after weaving.
- `timeline.py`: handheld camera (position, roll, zoom; per-beat level), focus targets `fx`/`fy`, rack-focus defocus, focus-pull events, `camera_pose`.
- `compositor.py`: roll in the virtual camera, sheen and shadow layers, region-limited warps, lens and motes in the frame path.
- `scene.py`: `extras(t)` for sheen/shadow layers and overlay bounds; splash settings passed to props.
- `config.py` / `config.json`: `lens` and `motes` sections, per-preset `fx` flags.
- `beats_v2.json`: wick/capillary/sheen ink settings, bud and petal colours, soft shadows, splash, handheld camera, per-beat handheld levels, focus targets, focus pulls (B07, B10, B19), banner shimmer, clips for vial/pearl/crown splashes.
- `app.py`: `--controls`, `--quality`, keys C / T / P, tone validation.
- `render.py`: `--tone`, `--no-lens`.
- `README.md`, `REVIEW.md`: documentation and the round 5 review.

## Turning things off
Per preset in `config.json` → `quality.<preset>.fx`: `sheen`, `sheen_glow`, `weed_shadow`, `stitch_detail`, `dof`, `dof_radial`, `dof_levels`, `bloom`, `motes`, `splash`. Globally: `lens.dof.on`, `lens.bloom.on`, `lens.rack` (0 = no rack focus), `motes.on`, `splash.on`, `ink.sheen.on`, `ink.cost.wick.on`, `weeds.shadow_soft.on`, `camera.handheld` (remove for a locked camera), `banner.shimmer` (0).
