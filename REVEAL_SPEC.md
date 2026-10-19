# hamlet-ar — "The Serpent Beneath" Reveal Spec


---

## 1. Concept

The camera sees the **official tapestry**, the bright and clean Denmark that the court wants seen. A single ink creature, **the Serpent**, is born at the garden poisoning and crawls panel to panel. Wherever it passes, the paper's surface "dissolves," exposing the **true tapestry** underneath: the same scenes, now showing poison, ghosts, bodies and rot.

Anchor line (Act 1, Scene 5): *"The serpent that did sting thy father's life / Now wears his crown."* The entity is that serpent made literal.

Every design choice below serves the group thesis: **corruption planted at the top grows wherever it is left untended, and a change of rulers only covers the rot rather than uprooting it.**

### Design laws (non-negotiable)

1. **The reveal is permanent.** The reveal mask never decreases. Nothing un-rots. Only the Fortinbras banner covers the rot at the end, and black still shows through it.
2. **No naked reveal edges.** No moving edge between clean and corrupt is ever visible without ink covering it. The ink front must lead the reveal front by at least 0.15 s.
3. **Ink is liquid, not a sticker.** Ink uses multiply blending over the paper so the paper grain shows through, and it carries a moving sheen. It is never a flat `#000` fill.
4. **The ghost is never touched by ink.** The ghost panel is revealed by light, not ink (see §6, panel 2). The ghost stays cool light grey, never black, consistent with the existing ghost cap.
5. **Everything is a pure function of the clock `t`.** Pause, beat-stepping, restart, scrubbing and the recorded backup must produce identical frames for the same `t`. No accumulated per-frame state.
6. **No music.** Sound is minimal and diegetic only, and there is silence at the end.

---

## 2. Layers and composite

All effect layers are rendered in **paper space**, a fixed rectified canvas matching the 11×17 sheet. Default resolution is 1100×1700 for the final composite; masks run at half resolution. The composite is warped into the camera frame once per frame with the existing tracking homography.

| Layer | Source | Purpose |
|---|---|---|
| `cam_paper` | camera frame warped to paper space | the real, physical tapestry |
| `clean_ref` | the image that was printed, or a scan of the drawing | lighting estimation only |
| `corrupt` | aligned corrupted art (§3) | the truth underneath |
| `R(p,t)` | reveal mask, 0..1, monotonic | where truth shows |
| `I(p,t)` | ink alpha, 0..1, transient | the creature and its floods |
| `D(p,t)` | damp halo, 0..1 | wet-paper darkening ahead of the ink |
| `S(p,t)` | sheen, 0..1 | liquid highlight along the serpent's spine |
| `V(p,t)` | residue veins, 0..1, monotonic | lasting stains after the ink recedes |

Composite order, in paper space:

```
lit      = corrupt * lighting_map(t)                     # §8
base     = lerp(cam_paper, lit, R)
base     = base * (1 - 0.15 * D)                         # damp halo, multiply
base     = base * lerp(1, ink_rgb, V * 0.55)             # residue veins, multiply
base     = base * lerp(1, ink_rgb, I)                    # ink body, multiply
base     = base + sheen_rgb * S * 0.12                   # additive
out_cam  = composite_over(camera_frame, warp(base), warp(paper_alpha))
```

### Palette

| Token | Value | Use |
|---|---|---|
| `ink_rgb` | `#0E0B10` | ink body: warm violet-black, never pure black |
| `ink_edge` | `#1C1620` | feathered edge, alpha falls off to 0.85 so grain shows |
| `sheen_rgb` | `#6B7A5A` | spine highlight with a faint poison-green cast |
| `poison_glint` | `#8FD14F` | tiny head glint and drip glint only, low alpha |
| `ghost_light` | `#DCE3EA` | ghost light-wipe (cool, light grey) |

Green appears only in the serpent's glints and in whatever the corrupt art already contains, which keeps the poison motif tied to the creature.

---

## 3. Asset pipeline (do this first — everything depends on it)

The phone scans **the physical paper**, so `corrupt` must be pixel-aligned to **what is actually on the paper**, not to a digital reference. Support two modes in `config.json`:

### Mode A — printed tapestry (`"source_mode": "print"`)
The printed sheet is the AI "clean" image.
- `tools/align_layers.py`: SIFT + RANSAC global homography from `corrupt_hi` to `clean_hi`, then per-panel ECC refinement inside each panel rectangle. Write `assets/corrupt_aligned.png` at print resolution.
- Verified on the low-resolution previews: 406 matches, 308 RANSAC inliers, near-identity homography. Global alignment works; the per-panel ECC only cleans up residual ghosting.
- Print `clean_hi` at actual size with known margins. Store those margins in `config.json` so the paper-space canvas maps exactly to the printed image area.

### Mode B — hand-drawn tapestry (`"source_mode": "scan"`)
- Photograph the finished drawing flat, under diffuse light with no glare. Rectify it with the ArUco board to produce `assets/scan.png`, which becomes `clean_ref`.
- **B1 (preferred):** paint or inpaint the corrupted version directly over `scan.png`. This gives perfect alignment for free.
- **B2:** warp the AI `corrupt_hi` to `scan.png` **per panel**, using SIFT + RANSAC on each panel crop. If a panel has fewer than 25 inliers, fall back to `tools/panel_corners.py`, a click-4-corners tool, for that panel. Hand drawings drift panel by panel, so a single global warp will not work here.

### Resolution
The preview images are about 405 px wide, which is roughly 37 ppi at 11×17. That is unusable. Both the print and the corrupt layer need at least **1650×2550** (150 ppi). Upscale with Real-ESRGAN if no higher-resolution originals exist, and do it before alignment.

### Output contract
- `assets/corrupt_aligned.png` in paper space
- `assets/clean_ref.png` in paper space
- `assets/panels.json` (§4)
- Cache an asset hash so arrival maps (§5) recompute only when assets change

---

## 4. Panel map

Normalized paper-space rectangles `[x0, y0, x1, y1]`, measured from the clean preview. **Re-measure these on the real print or scan.** The rectangles sit just inside each drawn frame line, so the settled reveal seam lands on the frame, where both images have matching structure.

```json
{
  "panels": [
    {"id": "garden",     "row": 1, "rect": [0.062, 0.034, 0.343, 0.276]},
    {"id": "ghost",      "row": 1, "rect": [0.358, 0.034, 0.642, 0.276]},
    {"id": "throne",     "row": 1, "rect": [0.657, 0.034, 0.946, 0.276]},
    {"id": "antic",      "row": 2, "rect": [0.062, 0.293, 0.286, 0.491]},
    {"id": "rg_letter",  "row": 2, "rect": [0.304, 0.293, 0.585, 0.491]},
    {"id": "mousetrap",  "row": 2, "rect": [0.602, 0.293, 0.946, 0.491]},
    {"id": "arras",      "row": 3, "rect": [0.062, 0.509, 0.356, 0.711]},
    {"id": "ophelia",    "row": 3, "rect": [0.370, 0.509, 0.640, 0.711]},
    {"id": "grave",      "row": 3, "rect": [0.654, 0.509, 0.946, 0.711]},
    {"id": "river",      "row": 0, "rect": [0.062, 0.714, 0.946, 0.764]},
    {"id": "duel",       "row": 4, "rect": [0.062, 0.786, 0.351, 0.960]},
    {"id": "cup",        "row": 4, "rect": [0.365, 0.786, 0.644, 0.960]},
    {"id": "fortinbras", "row": 4, "rect": [0.659, 0.786, 0.946, 0.960]}
  ]
}
```

Each panel also gets an authored `entry` and `exit` point in normalized coordinates (§6). Rects may be upgraded to polygons later; the row 1 arches would benefit.

---

## 5. Reveal mechanics: arrival maps

Do not animate floods by hand. For each panel, **precompute an arrival-time map** `T_in(p)` once at startup, then cache it. Each frame just thresholds the map, which is fast.

- Compute a geodesic distance from the panel's `entry` point over a cost image, using `skimage.graph.MCP_Geometric`. Fallback: Euclidean distance × (1 + 0.35·fBm).
- Cost image: `cost = 1 + 0.6 * fbm(p) - 0.45 * edge_strength(corrupt)`, clamped to a minimum of 0.25. Ink therefore runs faster **along the drawn lines** of the corrupt art, as real ink wicks along paper fibers and strokes. The flood traces the figures before it fills the gaps, which reads as intentional rather than random.
- Normalize `T_in` to 0..1 within the panel, and clip to the panel rect.
- Also precompute `T_out(p)`, the geodesic distance from the `exit` point, used for draining.

Per panel, with local progress `u` through each phase:

| Phase | What happens | Masks |
|---|---|---|
| **DORMANT** | nothing | R=0, I=0 |
| **BREACH** | Serpent head reaches the frame and pauses 0.3 s. It swells to 1.3× and 3–5 capillary tendrils probe under the frame line. A drip sound plays. | tendrils only |
| **FLOOD** | Ink spreads by `T_in`. Ink front: `I = smoothstep(u - T_in, 0, 0.08)`. Reveal trails behind: `R = smoothstep(u - T_in - 0.15, 0, 0.1)`. | ink leads reveal (Law 2) |
| **BLINK** | The panel is almost fully ink for 0.3–0.6 s. This is the held breath before the truth. | I≈0.95 |
| **RECEDE** | Ink drains toward the exit: farthest-from-exit pixels clear first, `I = 1 - smoothstep(u, T_out_inv - 0.1, T_out_inv)`. The drain gathers into the Serpent's body as it leaves. | R stays 1 |
| **SETTLED** | The corrupt panel shows. Residue veins `V` remain along the strongest drain channels and creep 4–8% inward from the panel corners. Veins "breathe" with a 4 s alpha pulse of ±0.06. Existing weeds sprout from vein endpoints. | R=1, V persistent |

Easing: FLOOD uses ease-in-out cubic, RECEDE uses ease-out quad, and BLINK holds linear.

---

## 6. Choreography

The Serpent moves **boustrophedon**, reading the tapestry like a page, which also gives it its S-shaped body: row 1 left→right, row 2 right→left, row 3 left→right, river right→left, row 4 left→right.

It travels **along the gold pillars and frame bands** between panels. Corruption moves through the structures of the state.

**Rhythm:** the pace accelerates through the court (contagion), slows hard at Ophelia (the one gentle beat), accelerates again toward the crown, then stops dead.

| # | Panel | t (s) | Entry → what the ink does | Camera |
|---|---|---|---|---|
| 0 | — | 0.0–2.0 | Clean tapestry, untouched, silence | full, 1.0× |
| 1 | garden | 2.0–8.0 | **Birth.** One black drop falls from the vial in Claudius's hand (0.8 s) and pools at the sleeping king's ear (0.7 s). The Serpent rises out of the pool (1.0 s) with its glint flaring once. The panel floods and drains. Exit is at the top-right pillar. | push to 2.2× on the vial, ease out to 1.6× |
| 2 | ghost | 8.0–11.0 | **Ink cannot enter.** The Serpent skirts along the bottom frame band, recoiling 6 px away from the panel edge. Reveal is a cool `ghost_light` wipe falling from the moon downward (2.5 s), with no ink. Truth arrives as light. | 1.6× framing row 1 |
| 3 | throne | 11.0–17.0 | Climbs the right pillar and coils once around the throne base. Ink pools under the crown first, then floods. Reveals the shadow behind the throne. | 2.0× |
| 4 | mousetrap | 17.0–21.5 | Drops down the right column into the bed and spreads across the sheets from the sleeper's ear, mirroring panel 1. | 1.5× tracking |
| 5 | rg_letter | 21.5–25.0 | Enters **through the letter**: the page blackens first, then spreads to both men. | 1.5× tracking |
| 6 | antic | 25.0–28.5 | Rises out of Hamlet's own shadow. Even the watcher is touched. | 1.5× tracking |
| 7 | arras | 28.5–33.0 | Seeps out from **behind the curtain**, along its bottom hem where the feet show, then floods. | 1.8× |
| 8 | ophelia | 33.0–39.0 | **Deceleration.** Half speed and narrow tendrils. The flood follows her flowers before it reaches her. The drain is slow, like water. | slow push 1.6×→2.4× |
| 9 | grave | 39.0–43.5 | Ink **pours down** into the open grave: apply a +y gravity bias to `T_in`. It rises again around the skull. | 1.8× |
| 10 | river | 43.5–47.5 | The Serpent drops into the water and **swims** right→left. This is its fastest, most sinuous run: wavelength ×0.7, amplitude ×1.4. The reveal follows its wake like a disturbed current. | pull to 1.2×, pan R→L |
| 11 | duel | 47.5–51.5 | Runs **along both blades** first (poisoned rapiers), then floods outward from the crossing point. | 1.8× |
| 12 | cup | 51.5–56.0 | The head **rises out of the cup** like a drink being poured, then floods down over the table and Gertrude. | push to 2.5× on cup |
| 13 | fortinbras | 56.0–61.5 | Coils around the throne. Crown topple. Floods and reveals the bodies. The Serpent settles, coiled, and goes still except for the sheen. | 2.0× → pull to 1.0× |
| 14 | banner | 61.5–67.0 | Existing gold banner weave covers the whole tapestry. Black shows through the gaps, and the Serpent's sheen still **moves beneath the threads**. Rot covered, not uprooted. | full, 1.0× |
| 15 | spill | 67.0–71.0 | Weeds and the Serpent's tail slide off the bottom paper edge onto the desk. | pull to 0.85× to show desk |
| 16 | hold | 71.0–77.0 | Total stillness. Silence. Nothing moves. | locked |

Total ≈77 s, matching the existing ~75 s beat sheet. Fold these into the **existing beats JSON** schema rather than creating a parallel format. Each panel beat needs `id`, `t0`, `t1`, phase splits (`breach`, `flood`, `blink`, `recede` as fractions), `entry`, `exit`, `speed_mul`, and optional `gravity` and `entry_object` hints.

---

## 7. The Serpent

### Locomotion — make it move like a real snake
Real snakes use **lateral undulation**: every point on the body passes through the same positions the head did. Wiggling a body in place looks fake. So:
- Bake the S-curves **into the path**: Catmull-Rom spline through authored waypoints, plus a normal offset `A·sin(2π·s/λ)`, where `s` is arc length. Defaults: `A = 10 px`, `λ = 90 px` at 1100-wide paper space.
- Head position is `path(s_head(t))`. The body is the path from `s_head - L` to `s_head`. It costs no simulation and is deterministic (Law 5).
- `s_head(t)` comes from the beats, with ease-in-out between waypoints and short **pauses at breaches** (snakes stop to taste the air).

### Anatomy
| Part | Spec |
|---|---|
| Length `L` | 260 px (most of one panel width), shorter on fast runs |
| Width profile | head 18 px, neck 14 px, peak body 22 px at 30% from head, tapering to 2 px at the tail. Teardrop head. |
| Edge | SDF to the polyline, perturbed by scrolling fBm (amplitude 2.5 px), feathered 3 px |
| Capillaries | thin fingers that bleed perpendicular to the body into paper fibers, 1–2 px wide, 8–20 px long, spawned at random arc positions, grow over 0.4 s, fade over 1.5 s. Densest near the head. |
| Sheen | 2 px highlight offset 30% toward the upper-left of the spine, moving tailward at 1.2× body speed, so it reads as wet and alive |
| Glint | single `poison_glint` point on the head, 3 px, pulses at breaches and at birth only |
| Drop shadow | soft, offset (3, 4) px, alpha 0.22. Offset doubles while rearing at breaches. This sells "on the paper, not in the screen." |
| Droplets | every 2–4 s the tail sheds a drop that blooms into a 6–12 px blot and becomes a residue vein seed |

### Damp halo `D`
A soft field 12–20 px ahead of and around the body that slightly darkens and desaturates the paper, like wet paper just before ink arrives.

---

## 8. Making the composite look physically real

Without this, the corrupt layer will look pasted on top of the paper.

1. **Lighting map:** `L = blur(cam_paper, σ=40) / (blur(clean_ref, σ=40) + ε)`, clamped to 0.5–1.6, with a temporal EMA (α = 0.1). Multiply `corrupt` by `L`. This matches shadows, white balance and the falloff from the phone's position.
2. **Grain match:** estimate camera noise σ from a high-pass of `cam_paper` on a flat paper region, and add matched Gaussian noise to `lit` each frame.
3. **Focus match:** compare Laplacian variance of `cam_paper` against `clean_ref`, and blur `lit` to match. The phone's softness is the target, not the asset's crispness.
4. **Seam hiding:** settled reveal boundaries sit on frame lines (§4). Moving boundaries are always under ink (Law 2).

---

## 9. Virtual camera

Extend the existing virtual camera with a **follow mode**:
- The target is the Serpent's head position 0.35 s ahead along the path (look-ahead).
- Use a critically damped spring with ω ≈ 3.5 rad/s for both position and zoom, and no cuts.
- Per-beat zoom comes from the §6 table, stored in the beats JSON.
- **Zoom ceiling:** never zoom so far that the source crop is under 640 px wide in camera pixels. The phone feed falls apart past that point, so the zoom value is clamped silently.

---

## 10. Sound (optional hooks, diegetic only)

- Low, wet slither with gain tied to head speed, very quiet
- One soft drip at each BREACH
- A faint inhale at BLINK
- Nothing at all during the ghost light-wipe
- Total silence from the banner beat onward

---

## 11. Controls and debug

- Existing pause, step-beat and restart keep working, and stepping to beat *n* shows every prior panel already settled (Law 5).
- `d`: debug overlay showing panel rects, the path with waypoints, the active arrival-map heatmap, the Serpent skeleton, phase names and fps.
- `r`: toggle `R = 1` everywhere, for an instant alignment check against the paper.
- The recorded backup video renders from the same clock.

**Performance:** run masks at half resolution, and precompute or scroll noise textures rather than evaluating fBm per frame. Use cv2 operations and uint8 lookup tables for smoothstep. Budget: ≤12 ms per frame for the reveal stage on the presentation laptop.

---

## 12. Build order (each phase is a shippable fallback)

The deadline is Oct 21. Every phase must end with a working demo.

| Phase | Deliverable | Ship-able as |
|---|---|---|
| 1 | Asset pipeline (§3), `panels.json`, `r` toggle showing the full corrupt layer aligned on the paper | proof of alignment |
| 2 | Arrival maps plus per-panel FLOOD/BLINK/RECEDE/SETTLED driven by beats, with simple ink and no Serpent yet | full reveal sequence |
| 3 | The Serpent: path, body, edge, sheen, shadow, travel between panels | the real concept |
| 4 | Physical realism (§8): lighting, grain, focus | polish |
| 5 | Camera follow mode and per-beat zoom (§9) | presentation-grade |
| 6 | Special beats: ghost light-wipe, river swim, banner with Serpent beneath, spill, hold | finished |

If time runs out, cut from the bottom of the list. Phases 1–3 alone already communicate the thesis.

---

## 13. Acceptance criteria

- [ ] With `r` on, the corrupt layer sits on the paper with under 3 px misalignment at panel figures
- [ ] No frame ever shows a moving clean/corrupt edge without ink over it
- [ ] Revealed panels never revert, except under the banner
- [ ] The ghost panel never contains ink, and the ghost is never darker than `ghost_light` minus 15%
- [ ] The Serpent's body visibly follows its head's track rather than wiggling in place
- [ ] Ink shows paper grain through it (multiply blending, not an opaque fill)
- [ ] The same `t` produces the same frame after pause, step, restart or backup render
- [ ] Stable at the target fps on the presentation laptop under classroom lighting
- [ ] A classmate who has not been briefed can say "something evil is spreading through the court and exposing what was hidden" after one viewing

---

## 14. Open decisions for the group

1. **Print or hand-draw?** Mode A is far less work and gives a cleaner reveal. Mode B is more authentic but costs alignment time, and that time comes out of the remaining days before the deadline.
2. **Epigraph?** Optionally, the anchor line could write itself in ink across the top border at t = 0–2 s before the drop falls, then soak into the paper. It helps an audience that hasn't been briefed, but it breaks the "no text" restraint. Default: off (`"epigraph": false`).
