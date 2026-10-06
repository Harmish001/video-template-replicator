# Analysis playbook: from contact sheets to spec.json

Claude cannot watch video. It reads (a) measured numbers from `analysis.json` and (b) still frames on labelled contact sheets. Timing and geometry come from the numbers; the *look* of each animation comes from comparing consecutive tiles.

## Contents
- Pass 1: structure
- Pass 2: per-scene detail (use --zoom)
- Reading animations from frames
- Common template patterns
- Confidence and what to tell the user

## Pass 1: structure
1. Open every `sheets/sheet_NN.jpg` with `view`. Each tile has its timestamp in yellow. Write down scene boundaries (a new background, a new layout, a cut).
2. Cross-check against `analysis.json`:
   - `hard_cuts_s`: single-frame jumps = `cut` transitions.
   - `motion_bursts[].kind == "full_frame_change"`: a cut or a full-screen transition. Zoom in to tell fade vs slide vs wipe.
   - `still_holds_s`: nothing moves. Good scene-end candidates.
   - `motion_bursts[].appearances`: separate regions that changed, with onset times. For a grid reveal this is one entry per tile in reveal order, with bounding boxes.
3. Draft the scene list with durations. Boundaries come from the middle of transition bursts: the outgoing scene's `duration` runs until the burst's end, and `transition_out.duration` is the burst's length.

## Pass 2: per-scene detail
Run `analyze_template.py TEMPLATE --out analysis --zoom START END` (12 fps by default; use `--zoom-fps 20` for very fast moves) and view `analysis/zoom/zoom_01.jpg`.

**Grid reveal (the 3x3 case)** — from `appearances`:
- `rows`, `cols`: cluster the bboxes by y then x.
- `margin`: left edge of the leftmost box (x0). `gap`: spacing between neighbouring boxes. Measured values are 0.5-1% too tight because faded edges fall under the pixel threshold, so round up to the nearest "design-looking" number (0.04, 0.02, 0.015).
- `stagger`: median difference between consecutive `onset_s`. `start`: first onset minus about 0.05.
- `order`: sort boxes by onset and compare to `row_major`, `snake`, `spiral`, `center_out`; else pass the explicit index list.
- Tiles that split into two entries or were missed (very low contrast against the background) are normal. Confirm the count on the sheet.
- Corner `radius`: look at a zoomed tile; round to 0 / 0.01 / 0.02 / 0.04.

**Timing**: all measured times have about +-0.07 s error. Prefer round numbers (0.25, 0.4, 0.5) when the measurement is within error of one.

**Background**: `first_frame_border_color` is a hint. Check gradients by viewing the top and bottom of a tile.

## Reading animations from frames
Look at 3-6 consecutive tiles around the layer's first appearance:

| You see | Spec |
|---|---|
| Same position, brightening | `fade` |
| Small and growing from the center, possibly overshooting then settling | `scale_pop` + `ease_out_back` |
| Larger than final and shrinking | `zoom_in` |
| Tilted and straightening | `rotate_in` |
| Edge of the picture enters from the side while the rest is off-screen | `slide_from_<side>` |
| Picture is fully sharp but only part is visible, hard edge moving | `wipe_from_<side>` |
| Fast start then slow arrival | `ease_out`; slow-fast-slow = `ease_in_out`; overshoot = `ease_out_back` |
| Image slowly growing/drifting during a hold | `motion.zoom` / `pan` (estimate start and end scale by comparing first and last tile) |

If two spec options both fit, choose the simpler and mention the alternative in the report.

## Common template patterns
- **Grid intro -> full-bleed hero -> collage**: the example spec. Reveal order and stagger define the rhythm.
- **Photo stack / slideshow**: one `media` layer per scene, `fade` or slide transitions, Ken Burns on each.
- **Split screens**: two or three `media` layers with different `rect`s and staggered `slide_from_*` entries.
- **Title cards**: `text` layers over a flat or gradient background; match `size` by comparing text height with canvas height.
- **Polaroid / frame**: `border` plus `radius`; shadows are unsupported, so say so.

## Confidence and what to tell the user
After building the spec, state plainly:
- Which parts were **measured** (scene boundaries, tile geometry, stagger).
- Which parts were **judged by eye** (easing, rotation, zoom amounts).
- Which template features are **unsupported** and what replaced them (see `spec-reference.md`, "Not supported").
Never claim a pixel-perfect match. A clean replica of structure, layout, order, and timing is realistic; replicating custom effects is not.
Do not copy the template's music, fonts, or stock footage into the output. Reproduce the structure and let the user's own audio and media fill it. If the template contains a visible logo or watermark, leave it out and mention that.