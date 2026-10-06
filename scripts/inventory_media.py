#!/usr/bin/env python3
"""Scan the user's content folder, make a labelled contact sheet, and draft slot assignments.

    inventory_media.py CONTENT_DIR --spec spec.json --out work/ [--sort name|date] [--exclude PATH ...]

Writes into --out:
  media.json         every usable file: kind, size, orientation, duration
  media_sheet_NN.jpg numbered thumbnails (view these to pick focus points / hero shots)
  assignments.json   DRAFT mapping of template slots -> files (edit it, then render)
Prints a report: shortages, surplus, orientation mismatches, unreadable files.
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, expand_spec, list_slots, load_json  # noqa: E402

def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]

def orient(w, h):
    r = w / h
    return "square" if 0.93 <= r <= 1.07 else ("landscape" if r > 1 else "portrait")

def probe_video(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(p)],
                       capture_output=True, text=True)
    if r.returncode:
        return None
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    if not v:
        return None
    w, h = int(v["width"]), int(v["height"])
    for sd in v.get("side_data_list", []) or []:
        if abs(int(sd.get("rotation", 0))) in (90, 270):
            w, h = h, w
    return w, h, float(j["format"].get("duration") or v.get("duration") or 0)

def exif_date(p):
    try:
        ex = Image.open(p).getexif()
        return ex.get(36867) or ex.get(306) or ex.get(0x9003)
    except Exception:
        return None

def scan(root, exclude):
    items, bad = [], []
    for p in sorted(root.rglob("*"), key=natural_key):
        if not p.is_file() or p.name.startswith(".") or str(p.resolve()) in exclude:
            continue
        ext = p.suffix.lower()
        try:
            if ext in IMAGE_EXT:
                with Image.open(p) as im:
                    w, h = im.size
                    o = im.getexif().get(274)
                if o in (5, 6, 7, 8):
                    w, h = h, w
                items.append({"path": str(p), "kind": "image", "width": w, "height": h, "orientation": orient(w, h),
                              "taken": exif_date(p), "mtime": p.stat().st_mtime})
            elif ext in VIDEO_EXT:
                pv = probe_video(p)
                if not pv:
                    bad.append(str(p)); continue
                w, h, dur = pv
                items.append({"path": str(p), "kind": "video", "width": w, "height": h, "orientation": orient(w, h),
                              "duration": round(dur, 2), "mtime": p.stat().st_mtime})
            elif ext in AUDIO_EXT:
                items.append({"path": str(p), "kind": "audio", "mtime": p.stat().st_mtime})
        except Exception:
            bad.append(str(p))
    return items, bad

def thumb(item, size=220):
    p = item["path"]
    if item["kind"] == "image":
        im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
    else:
        tmp = "/tmp/_thumb.jpg"
        t = max(0.0, min(1.0, item.get("duration", 1) / 2))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", p, "-frames:v", "1", tmp], capture_output=True)
        im = Image.open(tmp).convert("RGB")
    im.thumbnail((size, size))
    return im

def contact_sheets(items, out, cols=6, size=220):
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    visual = [it for it in items if it["kind"] in ("image", "video")]
    per = cols * 4
    paths = []
    for s in range(0, len(visual), per):
        chunk = visual[s:s + per]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * size, rows * (size + 22)), (25, 25, 25))
        d = ImageDraw.Draw(sheet)
        for k, it in enumerate(chunk):
            x, y = (k % cols) * size, (k // cols) * (size + 22)
            try:
                im = thumb(it, size)
                sheet.paste(im, (x + (size - im.width) // 2, y + 22 + (size - im.height) // 2))
            except Exception:
                pass
            tag = f"#{it['index']} {'VID ' if it['kind'] == 'video' else ''}{Path(it['path']).name}"
            d.text((x + 3, y + 3), tag[:30], font=font, fill=(255, 255, 0))
        p = out / f"media_sheet_{s // per + 1:02d}.jpg"
        sheet.save(p, quality=85); paths.append(str(p))
    return paths

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("content_dir")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default="work")
    ap.add_argument("--sort", choices=["name", "date"], default="name")
    ap.add_argument("--exclude", nargs="*", default=[])
    a = ap.parse_args()

    root, out = Path(a.content_dir), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    excl = {str(Path(x).resolve()) for x in a.exclude}
    items, bad = scan(root, excl)
    visual = [i for i in items if i["kind"] in ("image", "video")]
    audio = [i for i in items if i["kind"] == "audio"]
    if a.sort == "date":
        visual.sort(key=lambda i: (i.get("taken") or "", i["mtime"]))
    for k, it in enumerate(visual, 1):
        it["index"] = k
    (out / "media.json").write_text(json.dumps({"visual": visual, "audio": audio, "unreadable": bad}, indent=2))
    sheets = contact_sheets(visual, out)

    spec = expand_spec(load_json(a.spec))
    slots = list_slots(spec)
    media_slots = [s for s in slots if s["kind"] == "media"]
    unused = list(visual)
    assign, warnings = {}, []

    def ok(it, accepts):
        return accepts == "any" or it["kind"] == accepts

    for s in media_slots:
        cands = [it for it in unused if ok(it, s["accepts"])]
        if not cands:
            warnings.append(f"no {s['accepts']} left for slot {s['slot']} (scene {s['scene']})"); continue
        pick = cands[0]
        if s.get("prefer"):
            pick = next((c for c in cands[:6] if c["orientation"] == s["prefer"]), cands[0])
            if pick["orientation"] != s["prefer"]:
                warnings.append(f"slot {s['slot']} prefers {s['prefer']} but got {pick['orientation']} ({Path(pick['path']).name})")
        unused.remove(pick)
        rel = os.path.relpath(pick["path"], out)
        entry = {"file": rel, "focus": [0.5, 0.5]}
        if pick["kind"] == "video":
            entry["trim_start"] = 0
        assign[s["slot"]] = entry

    texts = {s["slot"]: s["default"] for s in slots if s["kind"] == "text" and s.get("default")}
    result = {"fill_policy": "empty", "slots": assign, "texts": texts}
    if audio:
        result["audio"] = {"file": os.path.relpath(audio[0]["path"], out), "start": 0}
    (out / "assignments.json").write_text(json.dumps(result, indent=2))

    unfilled = [s["slot"] for s in media_slots if s["slot"] not in assign]
    print(json.dumps({
        "images": sum(i["kind"] == "image" for i in visual), "videos": sum(i["kind"] == "video" for i in visual),
        "audio_files": [Path(x["path"]).name for x in audio], "unreadable": bad,
        "media_slots_in_template": len(media_slots), "slots_filled": len(assign), "unfilled_slots": unfilled,
        "unused_files": [Path(i["path"]).name for i in unused],
        "text_slots_needing_input": [s["slot"] for s in slots if s["kind"] == "text" and s["slot"] not in texts],
        "warnings": warnings, "contact_sheets": sheets, "draft_assignments": str(out / "assignments.json"),
    }, indent=2))

if __name__ == "__main__":
    main()