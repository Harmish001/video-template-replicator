# Template spec reference

`spec.json` is the only thing Claude writes about a template. `scripts/render.py` turns it plus `assignments.json` into a video.
Working example: `assets/example-spec-grid-reveal.json` (3x3 grid reveal, full-bleed hero with title, two-photo slide-in).

## Contents
- Geometry rules
- Top level
- Scenes and transitions
- Layer types: grid, media, text, shape
- Animations (entry / exit) and easings
- Media motion (Ken Burns)
- assignments.json

## Geometry rules
Everything is normalized so previews at `--scale 0.5` match the final render:
- `rect: [x, y, w, h]` — x and w are fractions of canvas **width**; y and h are fractions of canvas **height**.
- Sizes that should look the same in both axes (`margin`, `gap`, `radius`, `size`, `stroke`, border `width`) are fractions of canvas **width**.
- Text `pos: [x, y]` is the **center** of the text block.
- Keep the template's aspect ratio. A 9:16 template rendered at 16:9 distorts every rect.

## Top level
```json
{ "canvas": {"width":1080, "height":1920, "fps":30, "background":"#000000"},
  "scenes": [ ... ] }
```
Use the template's own resolution and fps (from `analysis.json -> probe`). Cap at 1080 on the short side unless the user asks for more.

## Scenes and transitions
```json
{ "id":"grid_intro", "duration":4.0, "background":"#101018",      // or a two-color vertical gradient ["#101018","#202030"]
  "transition_out": {"type":"slide_from_right", "duration":0.5},   // how this scene hands over to the next one
  "layers": [ ... ] }
```
- `duration` is the scene's own length. When `transition_out` is not `cut`, the next scene starts `transition_out.duration` seconds **before** this one ends (they overlap), so total length = sum of durations − sum of overlaps.
- Transition types: `cut`, `fade`, `fade_black`, `zoom_through`, `slide_from_{left,right,top,bottom}` (incoming scene pushes in from that side), `wipe_from_{left,right,top,bottom}`.
- Layer times (`start`, `end`, `duration`, `delay`) are in seconds **relative to the scene start**.

## Layer types
Common to all layers: `start` (default 0), `duration` or `end` (default: until scene end), `entry`, `exit`, `opacity`.

### `grid`  (expands into one media layer per cell)
```json
{"type":"grid","id":"g1","rows":3,"cols":3,"rect":[0,0,1,1],"margin":0.04,"gap":0.015,
 "order":"row_major","stagger":0.25,"start":0.3,"cell_aspect":"square","radius":0.02,
 "entry":{"type":"scale_pop","duration":0.45,"easing":"ease_out_back"}, "fit":"cover", "accepts":"any"}
```
- `order`: `row_major`, `col_major`, `snake`, `spiral`, `reverse`, `center_out`, `random` (with `seed`), or an explicit list of 0-based cell indices (row-major numbering) giving the reveal order.
- `cell_aspect`: `square` (default; grid is centered in `rect`) or `fill` (cells stretch to fill `rect`).
- Slots are named `<id>_<k>` where **k = the k-th cell to appear** (not its screen position). `g1_1` is the first tile that appears.
- Any media-layer option (`fit`, `radius`, `border`, `motion`, `exit`, `accepts`, `prefer`) is copied to every cell.

### `media`
```json
{"type":"media","slot":"hero","rect":[0,0,1,1],"fit":"cover","radius":0,
 "accepts":"any","prefer":"portrait","motion":{"zoom":[1.0,1.15]},
 "border":{"width":0.004,"color":"#ffffff"},"loop":false,"note":"best shot of the trip"}
```
- `slot`: unique name the user's file is assigned to. Reusing the same slot name in two layers shows the same file twice.
- `fit`: `cover` (fill + crop, honours `focus`), `contain` (letterbox, transparent), `blur_fill` (contain over a blurred, darkened copy).
- `accepts`: `image` | `video` | `any`. `prefer`: `portrait` | `landscape` | `square` (a hint used by the auto-assigner and for questions to the user).
- Videos: play from `trim_start` (from assignments) for the layer's duration; `loop:true` repeats a short clip, otherwise the last frame holds. Source audio is not used.
- `note`: free text shown to the user when asking which file goes where.

### `text`
```json
{"type":"text","text_slot":"title","text":"My Story","pos":[0.5,0.82],"size":0.09,"color":"#ffffff",
 "stroke":0.004,"stroke_color":"#000000","align":"center","max_width":0.85,"font":"/path/to/font.ttf"}
```
`text_slot` is filled from `assignments.texts`; `text` is the fallback/default. No text at all -> layer is skipped. Font priority: layer `font`, `assignments.font`, Poppins Bold, DejaVu Sans Bold.

### `shape`
`{"type":"shape","rect":[0,0.9,1,0.1],"color":"#000000","opacity":0.6,"radius":0}` — bars, frames, overlays.

## Animations
`entry` / `exit`: `{"type": ..., "duration": 0.4, "easing": "ease_out", "delay": 0}`.
Exit plays the same vocabulary in reverse at the end of the layer (`exit.duration` before `end`).

| type | look |
|---|---|
| `none` | appears instantly at `start` |
| `fade` | opacity 0 -> 1 |
| `scale_pop` | grows from 50% with fade (use `ease_out_back` for the bounce) |
| `zoom_in` | starts 135% and settles to 100% with fade |
| `rotate_in` | rotates in from -12 degrees while growing |
| `slide_from_{left,right,top,bottom}` | slides in from fully off-screen |
| `wipe_from_{left,right,top,bottom}` | hard-edge reveal from that side |

Easings: `linear`, `ease_in`, `ease_out`, `ease_in_out`, `ease_out_back` (overshoot), `ease_out_quint` (very snappy). Defaults: entry `ease_out`, exit `ease_in`.

## Media motion (Ken Burns)
`"motion": {"zoom":[1.0,1.15], "pan_x":[0,0.5], "pan_y":[0,0], "easing":"ease_in_out"}` — values interpolate across the layer's lifetime. `pan_*` range -1..1 (fraction of the available slack; 0 = centered on `focus`). Zoom below 1 is ignored for `cover`.

## assignments.json
```json
{ "slots": { "g1_1": {"file":"../content/a.jpg", "focus":[0.5,0.35]},
             "hero":  {"file":"../content/b.mp4", "trim_start":2.5} },
  "texts": { "title": "Goa 2026" },
  "audio": { "file":"../content/song.mp3", "start": 12.0 },
  "font": null }
```
Paths are relative to the assignments file. `focus` = (x, y) fraction of the photo that must stay in frame when it is cropped (put it on the face / subject). Slots missing from `slots` are left empty.
`inventory_media.py` writes a draft of this file; edit it rather than writing from scratch.

## Not supported (approximate and tell the user)
Drop shadows and glows, 3D / perspective flips, particles and overlays with transparency (light leaks, confetti), per-letter or per-word text animation, speed ramps, masks other than rounded rectangles, color grading / LUTs, blend modes, using the user's clip audio.