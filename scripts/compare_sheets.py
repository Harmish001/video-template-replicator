#!/usr/bin/env python3
"""Stack template sheets (top) over render sheets (bottom) so differences are visible at a glance.

    compare_sheets.py analysis/sheets check/sheets --out compare/

Both folders must come from analyze_template.py with the same --fps, so tile N shows the same timestamp in both.
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template_sheets"); ap.add_argument("render_sheets"); ap.add_argument("--out", default="compare")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    t = sorted(Path(a.template_sheets).glob("sheet_*.jpg")); r = sorted(Path(a.render_sheets).glob("sheet_*.jpg"))
    for k, (tp, rp) in enumerate(zip(t, r), 1):
        A, B = Image.open(tp), Image.open(rp)
        w = max(A.width, B.width)
        im = Image.new("RGB", (w, A.height + B.height + 24), (200, 0, 0))
        im.paste(A, (0, 0)); im.paste(B.resize((B.width, B.height)), (0, A.height + 24))
        d = ImageDraw.Draw(im)
        d.text((6, A.height + 6), "^ TEMPLATE      v YOUR RENDER", fill=(255, 255, 255))
        p = out / f"compare_{k:02d}.jpg"; im.save(p, quality=85); print(p)
    if len(t) != len(r):
        print(f"warning: {len(t)} template sheets vs {len(r)} render sheets (durations differ)")

if __name__ == "__main__":
    main()