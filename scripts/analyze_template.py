#!/usr/bin/env python3
"""Turn a template video into things Claude can actually look at.

    analyze_template.py TEMPLATE.mp4 --out analysis/                 # overview
    analyze_template.py TEMPLATE.mp4 --out analysis/ --zoom 0.8 3.2  # dense frames for one window
    analyze_template.py RENDER.mp4   --out check/                    # same tool checks your own output

Writes:
  analysis.json         probe info, hard cuts, activity bursts (+ where on screen each changed), still holds
  sheets/sheet_NN.jpg   labelled contact sheets (timestamp on every tile)  -> open these with `view`
  zoom/zoom_NN.jpg      (only with --zoom) dense contact sheets of one time window

Claude cannot watch video. Timings here are measured from frame differences (+-1/15 s);
easing, rotation and motion direction must be judged by looking at consecutive tiles.
"""
import argparse, json, shutil, subprocess, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)

def probe(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], text=True)
    if r.returncode:
        sys.exit(f"ffprobe failed: {r.stderr}")
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        rot = int(sd.get("rotation", rot))
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {"width": w, "height": h, "fps": round(fps, 3), "duration": round(float(j["format"].get("duration", v.get("duration", 0))), 3),
            "has_audio": any(s["codec_type"] == "audio" for s in j["streams"]), "codec": v.get("codec_name")}

def label_font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()

def extract_frames(video, out_dir, fps, tile_w, start=None, end=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-i", str(video), "-vf", f"fps={fps},scale={tile_w}:-2", "-q:v", "3", str(out_dir / "f_%05d.jpg")]
    r = run(cmd, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg failed: {r.stderr}")
    return sorted(out_dir.glob("f_*.jpg"))

def make_sheets(frames, t0, fps, cols, rows, out_dir, prefix):
    out_dir.mkdir(parents=True, exist_ok=True)
    if not frames:
        return []
    tw, th = Image.open(frames[0]).size
    font = label_font(max(12, tw // 14))
    per = cols * rows
    paths = []
    for s in range(0, len(frames), per):
        chunk = frames[s:s + per]
        sheet = Image.new("RGB", (cols * tw, rows * th), (30, 30, 30))
        for k, f in enumerate(chunk):
            im = Image.open(f).convert("RGB")
            t = t0 + (s + k) / fps
            d = ImageDraw.Draw(im)
            txt = f"{t:6.2f}s"
            d.rectangle([0, 0, d.textlength(txt, font=font) + 8, font.size + 6], fill=(0, 0, 0))
            d.text((4, 2), txt, font=font, fill=(255, 255, 0))
            sheet.paste(im, ((k % cols) * tw, (k // cols) * th))
        p = out_dir / f"{prefix}_{s // per + 1:02d}.jpg"
        sheet.save(p, quality=85)
        paths.append(str(p))
    return paths

def activity(video, afps=15):
    """Per-frame mean abs diff on a tiny grayscale proxy + the proxy frames themselves."""
    pw = 144
    info = probe(video)
    ph = max(2, int(round(pw * info["height"] / info["width"] / 2)) * 2)
    r = run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={afps},scale={pw}:{ph},format=gray",
             "-f", "rawvideo", "-"])
    arr = np.frombuffer(r.stdout, np.uint8)
    n = len(arr) // (pw * ph)
    F = arr[:n * pw * ph].reshape(n, ph, pw).astype(np.float32)
    d = np.zeros(n, np.float32)
    d[1:] = np.abs(F[1:] - F[:-1]).mean(axis=(1, 2))
    return F, d, afps

def label_components(mask, min_area=30):
    """4-connected components of a boolean array -> list of (ys, xs) index arrays."""
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    comps = []
    for y0 in range(h):
        for x0 in range(w):
            if mask[y0, x0] and not seen[y0, x0]:
                stack, ys, xs = [(y0, x0)], [], []
                seen[y0, x0] = True
                while stack:
                    y, x = stack.pop()
                    ys.append(y); xs.append(x)
                    for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True; stack.append((ny, nx))
                if len(ys) >= min_area:
                    comps.append((np.array(ys), np.array(xs)))
    return comps

def appearances(F, a, b, afps, thr=25.0):
    """Which screen regions changed between frame a and b, and when each one started changing.
    For a grid reveal this returns roughly one entry per tile, ordered by onset time."""
    final = np.abs(F[b] - F[a]) > thr
    h, w = final.shape
    out = []
    for ys, xs in label_components(final):
        series = [(np.abs(F[i] - F[a])[ys, xs] > thr).mean() for i in range(a, b + 1)]
        onset = next((a + k for k, v in enumerate(series) if v >= 0.2), b)
        out.append({"onset_s": round(onset / afps, 2),
                    "bbox_norm": [round(xs.min() / w, 3), round(ys.min() / h, 3),
                                  round((xs.max() + 1) / w, 3), round((ys.max() + 1) / h, 3)],
                    "area_frac": round(len(ys) / (h * w), 4)})
    return sorted(out, key=lambda d: (d["onset_s"], d["bbox_norm"][1], d["bbox_norm"][0]))

def find_events(F, d, afps, active_thr=0.35, cut_thr=28.0, gap_frames=2, still_min=0.4):
    n = len(d)
    cuts = [round(i / afps, 2) for i in range(1, n) if d[i] >= cut_thr]
    active = d >= active_thr
    bursts, i = [], 1
    while i < n:
        if active[i]:
            j = i
            gap = 0
            while j + 1 < n and (active[j + 1] or gap < gap_frames):
                gap = 0 if active[j + 1] else gap + 1
                j += 1
            while j > i and not active[j]:
                j -= 1
            a, b = max(0, i - 1), min(n - 1, j + 1)
            diff = np.abs(F[b] - F[a]) > 18
            bbox = None
            if diff.any():
                ys, xs = np.where(diff)
                h, w = diff.shape
                bbox = [round(xs.min() / w, 3), round(ys.min() / h, 3), round((xs.max() + 1) / w, 3), round((ys.max() + 1) / h, 3)]
            seg = d[i:j + 1]
            kind = "full_frame_change" if seg.max() >= cut_thr else "motion"
            burst = {"start": round(i / afps, 2), "end": round((j + 1) / afps, 2), "peak": round(float(seg.max()), 2),
                     "mean": round(float(seg.mean()), 2), "changed_bbox_norm": bbox, "kind": kind}
            if kind == "motion" and (j + 1 - i) / afps >= 0.3:
                burst["appearances"] = appearances(F, a, b, afps)
            bursts.append(burst)
            i = j + 1
        else:
            i += 1
    stills, i = [], 1
    while i < n:
        if not active[i]:
            j = i
            while j + 1 < n and not active[j + 1]:
                j += 1
            if (j - i + 1) / afps >= still_min:
                stills.append([round(i / afps, 2), round((j + 1) / afps, 2)])
            i = j + 1
        else:
            i += 1
    return cuts, bursts, stills

def border_color(frame_path):
    im = Image.open(frame_path).convert("RGB")
    w, h = im.size
    px = [im.getpixel((x, y)) for x in range(0, w, max(1, w // 10)) for y in (1, h - 2)]
    px += [im.getpixel((x, y)) for y in range(0, h, max(1, h // 10)) for x in (1, w - 2)]
    arr = np.array(px)
    return "#%02x%02x%02x" % tuple(int(v) for v in np.median(arr, axis=0))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", default="analysis")
    ap.add_argument("--fps", type=float, default=4.0, help="overview sampling rate (default 4)")
    ap.add_argument("--zoom", nargs=2, type=float, metavar=("START", "END"))
    ap.add_argument("--zoom-fps", type=float, default=12.0)
    ap.add_argument("--cols", type=int)
    ap.add_argument("--rows", type=int)
    a = ap.parse_args()

    video, out = Path(a.video), Path(a.out)
    info = probe(video)
    portrait = info["height"] >= info["width"]
    tile_w = 240 if portrait else 400
    cols = a.cols or (6 if portrait else 4)
    rows = a.rows or (2 if portrait else 3)

    if a.zoom:
        s, e = a.zoom
        zdir = out / "zoom_frames"
        if zdir.exists():
            shutil.rmtree(zdir)
        frames = extract_frames(video, zdir, a.zoom_fps, tile_w + 60, s, e)
        sheets = make_sheets(frames, s, a.zoom_fps, cols, rows, out / "zoom", "zoom")
        print(json.dumps({"window": [s, e], "fps": a.zoom_fps, "sheets": sheets}, indent=2))
        return

    out.mkdir(parents=True, exist_ok=True)
    fdir = out / "frames"
    if fdir.exists():
        shutil.rmtree(fdir)
    frames = extract_frames(video, fdir, a.fps, tile_w)
    sheets = make_sheets(frames, 0.0, a.fps, cols, rows, out / "sheets", "sheet")
    F, d, afps = activity(video)
    cuts, bursts, stills = find_events(F, d, afps)
    bg = border_color(frames[0]) if frames else None
    report = {"video": str(video), "probe": info, "overview_fps": a.fps, "sheets": sheets,
              "first_frame_border_color": bg, "hard_cuts_s": cuts, "motion_bursts": bursts, "still_holds_s": stills,
              "notes": ["bursts[].appearances = separate screen regions that changed inside a burst, with the time each started changing "
                        "(for a grid reveal: one entry per tile, in reveal order, with its box). Tiny or low-contrast tiles can be missed or merged - confirm on the contact sheets.",
                        "kind=full_frame_change means a cut or a full-screen transition (slide/wipe/fade); zoom into it to identify which. "
                        "bursts[].changed_bbox_norm = bounding box (x0,y0,x1,y1 as 0-1 fractions) of pixels that differ between burst start and end; "
                        "for a grid reveal each tile appearance shows up as its own burst, so the boxes give you the tile geometry.",
                        "timings are measured at 15 fps (+-0.07 s). Use --zoom START END for denser frames."]}
    (out / "analysis.json").write_text(json.dumps(report, indent=2))
    shutil.rmtree(fdir, ignore_errors=True)
    print(json.dumps({k: report[k] for k in ("probe", "hard_cuts_s", "still_holds_s", "first_frame_border_color")}, indent=2))
    print(f"{len(bursts)} motion bursts, {len(sheets)} contact sheets -> {out}/ (see analysis.json)")
    if info["duration"] > 75:
        print("warning: template longer than 75 s; templates this long usually need splitting into sections", file=sys.stderr)

if __name__ == "__main__":
    main()