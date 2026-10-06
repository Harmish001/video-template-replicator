---
name: video-template-replicator
description: Recreate a reference video template (grid reveals, photo/video slideshows, split screens, title cards, slide/zoom/fade animations) using the user's own photos and videos. The user puts a template video in one folder and their media in another; Claude studies the template frame by frame, writes a structured spec, asks which media go where when unclear, and renders a new MP4 that follows the template's layout, order, and timing. Use whenever the user mentions a video template, "make a video like this one", a reel/story/slideshow template, photo-grid or collage intro videos, or wants to turn their photos and clips into a video that copies the structure of an example video, even if they never say the word "template".
compatibility: Needs ffmpeg/ffprobe on PATH, Python 3.9+, Pillow, numpy. Works in Claude Code, Cowork, or any agent with a shell and file access.
---

# Video Template Replicator

Turns **one template video + a folder of the user's media** into **a new video with the same structure**.
Two stages: Claude *analyzes* the template into `spec.json` (judgment), then a fixed script *renders* it (deterministic). Never improvise the render with ad-hoc ffmpeg commands; fix the spec instead.

Claude cannot watch video. It works from measured frame differences and labelled still frames. Be honest about this in the final report (see "Report").

## Folder convention
Ask for (or detect) two folders. Do not write into them.
```
<project>/
  template/     the reference video (exactly one; if several, ask which)
  content/      user's photos, videos, optional music, optional font
  vtr_work/     temporary work directory (analysis, spec.json, assignments.json, previews) - deleted during cleanup
  video-result/ final output directory containing ONLY the rendered MP4 and thumbnail image
```
Scripts live in this skill's `scripts/`. Below, `S` = that directory.

## Workflow

### 1. Preflight
```bash
ffmpeg -version | head -1; python3 -c "import PIL, numpy"
```
If something is missing, `pip install pillow numpy --break-system-packages` (or ask the user to install ffmpeg). Make `vtr_work/`.

### 2. Analyze the template
```bash
python3 S/analyze_template.py template/<file> --out vtr_work/analysis
```
Then **view every `vtr_work/analysis/sheets/sheet_NN.jpg`** and read `analysis.json`. Follow `references/analysis-playbook.md` for how to read them, including `--zoom START END` for dense frames of fast sections (reveals, transitions). Templates longer than ~60 s: tell the user and ask whether to replicate the whole thing.

### 3. Write `vtr_work/spec.json`
Use only the primitives in `references/spec-reference.md` (grid, media, text, shape; the listed entry/exit animations, easings, transitions). Start from `assets/example-spec-grid-reveal.json` when the template is a grid reveal. Match the template's resolution, fps, and **aspect ratio**.
```bash
python3 S/render.py --spec vtr_work/spec.json --check        # validate
python3 S/render.py --spec vtr_work/spec.json --list-slots   # what the user must provide
```
Give media layers helpful `note`s ("the hero shot", "ending photo") and `prefer` orientations where the template clearly wants portrait or landscape.

### 4. Inventory the user's content and draft assignments
```bash
python3 S/inventory_media.py content --spec vtr_work/spec.json --out vtr_work --exclude template/<file>
```
View `vtr_work/media_sheet_*.jpg`. Read the printed report. Use `--sort date` if the user wants chronological order; the default is filename order.

### 5. Ask the user only what you need
Ask **one batched message**, not a stream of questions. Ask when:
- **Count mismatch**: fewer files than media slots (offer: repeat photos, leave cells empty, or shorten the grid) or many more (which to use? first N, evenly spaced, or let me choose?).
- **Order matters and is unknowable**: first/last slot, hero slot, grid reveal order, when the file names carry no order.
- **Orientation mismatch** reported by the inventory for a prominent slot (hero, first tile).
- **Text slots** (titles, captions, names, dates) with no default. Always ask for these.
- **Audio**: none found in `content/` -> ask whether they want music (the template's own audio is not reused).
- **Videos**: which part of a long clip to use (`trim_start`), if not obvious.
If the user says "you decide", choose using the media sheets (best portrait shot for hero, varied shots across the grid) and say what you chose. If an interactive question tool is available, use it for pick-lists; otherwise ask in prose. Don't ask about anything the template or file names already answer.

Set `focus` ([x, y] 0-1) in assignments for photos where the subject is off-center (faces near the top: `[0.5, 0.3]`).

### 6. Preview, compare, fix
```bash
python3 S/render.py --spec vtr_work/spec.json --assign vtr_work/assignments.json \
   --scale 0.4 --still 0.5,1.5,2.5 --still-dir vtr_work/stills        # quick look at chosen moments
python3 S/render.py --spec ... --assign ... --scale 0.5 --out vtr_work/preview.mp4
python3 S/analyze_template.py vtr_work/preview.mp4 --out vtr_work/check
python3 S/compare_sheets.py vtr_work/analysis/sheets vtr_work/check/sheets --out vtr_work/compare
```
View `vtr_work/compare/compare_NN.jpg` (template on top, your render below). Check: scene count and boundaries, reveal order and stagger, tile geometry, background, text placement, animation direction. Compare `analysis.json` of both (`motion_bursts` times) for timing drift. Fix the **spec**, re-run. Two or three iterations is normal; stop when remaining differences are inside the noted limits.

### 7. Final render & thumbnail
```bash
python3 S/render.py --spec vtr_work/spec.json --assign vtr_work/assignments.json \
   --out video-result/final.mp4 --thumbnail video-result/thumbnail.jpg
```
Full-resolution rendering is CPU-bound (roughly 2-6 frames/s at 1080x1920). Warn the user for long videos.

### 8. Cleanup extra files
Immediately after the final video and thumbnail are generated in `video-result/`, clean up and delete all temporary files, JSON specs/assignments, intermediate work folders (`vtr_work/`, `vtr_work1/`, etc.), and temporary caches.
Ensure that **only** `video-result/` (containing the final MP4 and the thumbnail image) remains alongside the user's `template/` and `content/` folders.
```bash
# Clean up temporary work folder and any extra intermediate files
rm -rf vtr_work
```

## Report (keep short, honest)
- Scenes and duration, how many slots were filled, what you chose by yourself.
- **Measured**: scene timing, grid geometry, stagger. **Judged by eye**: easing, zoom amounts, rotations.
- Anything approximated or unsupported (shadows, 3D, particles, per-letter text effects, the template's audio/fonts).
- Inform the user that the final video and thumbnail are available in `video-result/`.

## Rules
- The template's music, fonts, logos, and footage are the template author's. Replicate structure and motion, not assets. Don't ship the template video's pixels inside the output.
- Never overwrite the user's originals (`template/` and `content/`).
- The final output folder must be named `video-result/` and contain **only** the rendered video and its thumbnail image (`thumbnail.jpg` or `thumbnail.png`).
- Always delete extra intermediate files (like `spec.json`, `assignments.json`, `analysis.json`, and the `vtr_work/` directory) once the video and thumbnail have been created.
- If the template needs something the primitives can't express, use the nearest primitive and say so. Don't extend `render.py` mid-task unless the user asks for a new capability.
- HEIC photos are not readable by default; ask the user to convert them (or `pip install pillow-heif` and register it) when the inventory lists them as unreadable.