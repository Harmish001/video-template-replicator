#!/usr/bin/env python3
"""Deterministic renderer: template spec + user assignments -> MP4.

    render.py --spec spec.json --list-slots            # what content does this template need?
    render.py --spec spec.json --check                 # validate the spec
    render.py --spec spec.json --assign a.json --still 0.5,2,4 --still-dir stills/
    render.py --spec spec.json --assign a.json --out out.mp4 [--scale 0.5]

All geometry in the spec is normalized (x, w as fractions of canvas width,
y, h as fractions of canvas height) so previews at --scale 0.5 look identical.
Needs: ffmpeg, Pillow, numpy.
"""
import argparse, json, os, random, shutil, subprocess, sys, tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

ENTRY_TYPES = {"none", "fade", "scale_pop", "zoom_in", "rotate_in",
               "slide_from_left", "slide_from_right", "slide_from_top", "slide_from_bottom",
               "wipe_from_left", "wipe_from_right", "wipe_from_top", "wipe_from_bottom"}
TRANSITIONS = {"cut", "fade", "fade_black", "zoom_through",
               "slide_from_left", "slide_from_right", "slide_from_top", "slide_from_bottom",
               "wipe_from_left", "wipe_from_right", "wipe_from_top", "wipe_from_bottom"}
FITS = {"cover", "contain", "blur_fill"}

# ----------------------------------------------------------------- easing
def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

def _back(t):
    c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2

EASE = {
    "linear": lambda t: t,
    "ease_in": lambda t: t ** 3,
    "ease_out": lambda t: 1 - (1 - t) ** 3,
    "ease_in_out": lambda t: 4 * t ** 3 if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2,
    "ease_out_back": _back,
    "ease_out_quint": lambda t: 1 - (1 - t) ** 5,
}

def lerp(a, b, t):
    return a + (b - a) * t

def hex_rgb(s):
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))

# ----------------------------------------------------------------- spec
def load_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def spiral_order(rows, cols):
    out = []; top, bot, left, right = 0, rows - 1, 0, cols - 1
    while top <= bot and left <= right:
        out += [(top, c) for c in range(left, right + 1)]
        out += [(r, right) for r in range(top + 1, bot + 1)]
        if top < bot:
            out += [(bot, c) for c in range(right - 1, left - 1, -1)]
        if left < right:
            out += [(r, left) for r in range(bot - 1, top, -1)]
        top += 1; bot -= 1; left += 1; right -= 1
    return out

def grid_order(rows, cols, order, seed=0):
    idx = [(r, c) for r in range(rows) for c in range(cols)]
    if isinstance(order, list):
        return [idx[i] for i in order]
    if order == "row_major": return idx
    if order == "col_major": return [(r, c) for c in range(cols) for r in range(rows)]
    if order == "snake": return [(r, c if r % 2 == 0 else cols - 1 - c) for r in range(rows) for c in range(cols)]
    if order == "reverse": return idx[::-1]
    if order == "spiral": return spiral_order(rows, cols)
    if order == "random":
        l = idx[:]; random.Random(seed).shuffle(l); return l
    if order == "center_out":
        cr, cc = (rows - 1) / 2, (cols - 1) / 2
        return sorted(idx, key=lambda rc: (rc[0] - cr) ** 2 + (rc[1] - cc) ** 2)
    raise ValueError(f"unknown grid order {order!r}")

def expand_grid(L, canvas):
    """Turn a grid layer into one media layer per cell. Slot k = k-th cell to appear."""
    W, H = canvas["width"], canvas["height"]
    asp = H / W
    rows, cols = int(L.get("rows", 3)), int(L.get("cols", 3))
    rx, ry, rw, rh = L.get("rect", [0, 0, 1, 1])
    x0, y0, w, h = rx, ry * asp, rw, rh * asp          # unit = canvas width
    m, g = L.get("margin", 0.0), L.get("gap", 0.0)
    cw = (w - 2 * m - (cols - 1) * g) / cols
    ch = (h - 2 * m - (rows - 1) * g) / rows
    if L.get("cell_aspect", "square") == "square":
        cw = ch = min(cw, ch)
    tw, th = cols * cw + (cols - 1) * g, rows * ch + (rows - 1) * g
    ox, oy = x0 + (w - tw) / 2, y0 + (h - th) / 2
    order = grid_order(rows, cols, L.get("order", "row_major"), L.get("seed", 0))
    out = []
    for k, (r, c) in enumerate(order, 1):
        x, y = ox + c * (cw + g), oy + r * (ch + g)
        cell = {"type": "media", "slot": f"{L['id']}_{k}",
                "rect": [x, y / asp, cw, ch / asp],
                "start": L.get("start", 0) + (k - 1) * L.get("stagger", 0.2)}
        for key in ("fit", "radius", "border", "entry", "exit", "motion", "accepts", "prefer",
                    "duration", "opacity", "end", "note"):
            if key in L:
                cell[key] = L[key]
        out.append(cell)
    return out

def expand_spec(spec):
    canvas = spec.setdefault("canvas", {})
    canvas.setdefault("width", 1080); canvas.setdefault("height", 1920)
    canvas.setdefault("fps", 30); canvas.setdefault("background", "#000000")
    for sc in spec["scenes"]:
        layers = []
        for L in sc.get("layers", []):
            layers += expand_grid(L, canvas) if L["type"] == "grid" else [L]
        sc["_layers"] = layers
    return spec

def scene_timeline(spec):
    starts, durs, t = [], [], 0.0
    scenes = spec["scenes"]
    for i, sc in enumerate(scenes):
        starts.append(t); durs.append(float(sc["duration"]))
        tr = sc.get("transition_out") or {}
        overlap = float(tr.get("duration", 0.4)) if (i < len(scenes) - 1 and tr.get("type", "cut") != "cut") else 0.0
        t += durs[-1] - overlap
    return starts, durs, starts[-1] + durs[-1]

def list_slots(spec):
    seen = {}
    for sc in spec["scenes"]:
        for L in sc["_layers"]:
            if L["type"] == "media":
                seen.setdefault(L["slot"], {"slot": L["slot"], "kind": "media", "scene": sc.get("id"),
                                            "accepts": L.get("accepts", "any"), "prefer": L.get("prefer"),
                                            "note": L.get("note")})
            elif L["type"] == "text" and L.get("text_slot"):
                seen.setdefault(L["text_slot"], {"slot": L["text_slot"], "kind": "text", "scene": sc.get("id"),
                                                 "default": L.get("text"), "note": L.get("note")})
    return list(seen.values())

def check_spec(spec):
    errs = []
    if not spec.get("scenes"):
        errs.append("spec has no scenes")
    for sc in spec["scenes"]:
        sid = sc.get("id", "?")
        if sc.get("duration", 0) <= 0: errs.append(f"scene {sid}: duration must be > 0")
        tr = (sc.get("transition_out") or {}).get("type", "cut")
        if tr not in TRANSITIONS: errs.append(f"scene {sid}: unknown transition {tr!r}")
        for L in sc["_layers"]:
            lt = L.get("type")
            if lt not in ("media", "text", "shape"):
                errs.append(f"scene {sid}: unknown layer type {lt!r}"); continue
            for key in ("entry", "exit"):
                t = (L.get(key) or {}).get("type", "none")
                if t not in ENTRY_TYPES: errs.append(f"scene {sid}: unknown {key} type {t!r}")
                e = (L.get(key) or {}).get("easing")
                if e and e not in EASE: errs.append(f"scene {sid}: unknown easing {e!r}")
            if lt == "media":
                if "slot" not in L: errs.append(f"scene {sid}: media layer without slot")
                if L.get("fit", "cover") not in FITS: errs.append(f"scene {sid}: unknown fit {L.get('fit')!r}")
                r = L.get("rect", [0, 0, 1, 1])
                if len(r) != 4 or r[2] <= 0 or r[3] <= 0: errs.append(f"scene {sid}: bad rect {r}")
            if lt == "text" and not (L.get("text") or L.get("text_slot")):
                errs.append(f"scene {sid}: text layer needs text or text_slot")
            if L.get("start", 0) >= sc["duration"]:
                errs.append(f"scene {sid}: layer starts after scene ends ({L.get('slot') or L.get('text_slot') or lt})")
    return errs

# ----------------------------------------------------------------- media
@lru_cache(maxsize=96)
def _open_rgb(path):
    return Image.open(path).convert("RGB")

class Media:
    def __init__(self, assign, base_dir, fps, maxdim, tmp):
        self.slots = assign.get("slots", {})
        self.texts = assign.get("texts", {})
        self.font = assign.get("font")
        self.base = Path(base_dir); self.fps = fps; self.maxdim = maxdim; self.tmp = Path(tmp)
        self.images = {}; self.videos = {}

    def entry(self, slot):
        e = self.slots.get(slot)
        if not e or not e.get("file"):
            return None
        e = dict(e); p = Path(e["file"])
        e["_path"] = str(p if p.is_absolute() else (self.base / p))
        e["_video"] = Path(e["_path"]).suffix.lower() in VIDEO_EXT
        return e

    def image(self, path, scale=1.0):
        key = (path, round(scale, 3))
        if key not in self.images:
            if (path, 1.0) not in self.images:
                try:
                    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
                except Exception as ex:
                    sys.exit(f"cannot open image {path}: {ex}")
                self.images[(path, 1.0)] = im
            im = self.images[(path, 1.0)]
            if scale < 1:
                im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
            self.images[key] = im
        return self.images[key]

    def video_frame(self, e, lt, dur, loop):
        key = (e["_path"], float(e.get("trim_start", 0)), round(dur, 2))
        if key not in self.videos:
            d = self.tmp / f"v{len(self.videos):03d}"; d.mkdir(parents=True)
            M = self.maxdim
            vf = f"fps={self.fps},scale=w='if(gt(iw,ih),min(iw,{M}),-2)':h='if(gt(iw,ih),-2,min(ih,{M}))'"
            cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(key[1]), "-t", str(dur + 0.2), "-i", e["_path"],
                   "-an", "-vf", vf, "-q:v", "3", str(d / "%05d.jpg")]
            r = subprocess.run(cmd, capture_output=True, text=True)
            n = len(list(d.glob("*.jpg")))
            if r.returncode != 0 or n == 0:
                sys.exit(f"ffmpeg could not decode {e['_path']}: {r.stderr[-300:]}")
            self.videos[key] = (d, n)
        d, n = self.videos[key]
        i = int(lt * self.fps)
        i = (i % n) if (loop and i >= n) else min(i, n - 1)
        return _open_rgb(str(d / f"{i + 1:05d}.jpg"))

# ----------------------------------------------------------------- drawing
@lru_cache(maxsize=256)
def round_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

def load_font(path, size):
    for p in ([path] if path else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def make_text_image(text, font_path, size_px, color, align, max_w, stroke_px, stroke_color):
    font = load_font(font_path, size_px)
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for w in para.split(" "):
            if not w:
                continue
            t = (cur + " " + w).strip()
            if cur and font.getlength(t) > max_w:
                lines.append(cur); cur = w
            else:
                cur = t
        if cur:
            lines.append(cur)
    if not lines:
        lines = [""]
    lh = int(size_px * 1.25); pad = stroke_px + 4
    wmax = int(max(font.getlength(l) for l in lines)) + 2 * pad
    img = Image.new("RGBA", (max(1, wmax), lh * len(lines) + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = hex_rgb(color) + (255,)
    for i, l in enumerate(lines):
        lw = font.getlength(l)
        x = pad if align == "left" else (wmax - pad - lw if align == "right" else (wmax - lw) / 2)
        d.text((x, pad + i * lh), l, font=font, fill=fill, stroke_width=stroke_px,
               stroke_fill=hex_rgb(stroke_color) + (255,))
    return img

def pixel_rect(rect, W, H):
    x, y, w, h = rect
    return int(round(x * W)), int(round(y * H)), max(1, int(round(w * W))), max(1, int(round(h * H)))

def fit_media(src, rw, rh, fit, zoom, pan_x, pan_y, focus, radius):
    sw, sh = src.size
    if fit == "cover":
        zoom = max(1.0, zoom)
        s = max(rw / sw, rh / sh) * zoom
        cw, ch = rw / s, rh / s
        cx = (focus[0] + pan_x * 0.5 * (1 - cw / sw)) * sw if cw < sw else sw / 2
        cy = (focus[1] + pan_y * 0.5 * (1 - ch / sh)) * sh if ch < sh else sh / 2
        x0 = max(0.0, min(sw - cw, cx - cw / 2)) if cw < sw else 0.0
        y0 = max(0.0, min(sh - ch, cy - ch / 2)) if ch < sh else 0.0
        x1 = min(float(sw), x0 + cw)
        y1 = min(float(sh), y0 + ch)
        box = (max(0, int(round(x0))), max(0, int(round(y0))), min(sw, int(round(x1))), min(sh, int(round(y1))))
        out = src.resize((rw, rh), Image.BILINEAR, box=box).convert("RGBA")
    else:
        s = min(rw / sw, rh / sh) * max(0.2, zoom)
        fw, fh = max(1, int(sw * s)), max(1, int(sh * s))
        fg = src.resize((fw, fh), Image.BILINEAR)
        if fit == "blur_fill":
            sb = max(rw / sw, rh / sh)
            small = src.resize((max(1, int(sw * sb / 10)), max(1, int(sh * sb / 10))), Image.BILINEAR)
            bg = small.resize((max(rw, int(sw * sb)), max(rh, int(sh * sb))), Image.BICUBIC)
            bx, by = (bg.width - rw) // 2, (bg.height - rh) // 2
            bg = bg.crop((bx, by, bx + rw, by + rh)).point(lambda v: int(v * 0.7)).convert("RGBA")
        else:
            bg = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        bg.paste(fg, ((rw - fw) // 2, (rh - fh) // 2))
        out = bg
    if radius > 0:
        out.putalpha(ImageChops.multiply(out.getchannel("A"), round_mask(rw, rh, radius)))
    return out

def make_polaroid_card(src, rw, rh, fit, zoom, px, py, focus, border_color="#ffffff", shadow=True, W=1080):
    pad_x = max(6, int(0.048 * rw))
    pad_top = max(6, int(0.048 * rw))
    pad_bottom = max(20, int(0.20 * rh))
    pw = max(1, rw - 2 * pad_x)
    ph = max(1, rh - pad_top - pad_bottom)
    
    photo = fit_media(src, pw, ph, fit, zoom, px, py, focus, 0)
    card = Image.new("RGBA", (rw, rh), hex_rgb(border_color) + (255,))
    card.paste(photo, (pad_x, pad_top))
    ImageDraw.Draw(card).rectangle([0, 0, rw - 1, rh - 1], outline=(215, 215, 215, 255), width=1)
    
    if shadow:
        blur = max(2, int(0.010 * W))
        offset_y = max(1, int(0.005 * W))
        margin = blur * 2 + offset_y
        sw, sh = rw + 2 * margin, rh + 2 * margin
        shadow_img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        shadow_mask = Image.new("L", (sw, sh), 0)
        shadow_mask.paste(Image.new("L", (rw, rh), 255), (margin, margin + offset_y))
        shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(blur))
        shadow_color = Image.new("RGBA", (sw, sh), (0, 0, 0, 120))
        shadow_img.paste(shadow_color, (0, 0), shadow_mask)
        shadow_img.paste(card, (margin, margin), card)
        return shadow_img, margin
    return card, 0

# ----------------------------------------------------------------- animation
def transform_for(kind, e, rx, ry, rw, rh, W, H):
    """Return dict(alpha, scale, dx, dy, rot, wipe) for animation `kind` at eased progress e."""
    t = dict(alpha=1.0, scale=1.0, dx=0.0, dy=0.0, rot=0.0, wipe=None)
    ec = clamp(e)
    if kind == "none": return t
    if kind == "fade": t["alpha"] = ec
    elif kind == "scale_pop": t["scale"] = 0.5 + 0.5 * e; t["alpha"] = clamp(e * 2.5)
    elif kind == "zoom_in": t["scale"] = 1.35 - 0.35 * e; t["alpha"] = clamp(e * 1.6)
    elif kind == "rotate_in": t["rot"] = -12 * (1 - e); t["scale"] = 0.8 + 0.2 * e; t["alpha"] = clamp(e * 2)
    elif kind == "slide_from_left": t["dx"] = -(rx + rw) * (1 - e)
    elif kind == "slide_from_right": t["dx"] = (W - rx) * (1 - e)
    elif kind == "slide_from_top": t["dy"] = -(ry + rh) * (1 - e)
    elif kind == "slide_from_bottom": t["dy"] = (H - ry) * (1 - e)
    elif kind.startswith("wipe_from_"): t["wipe"] = (kind[10:], ec)
    return t

def combine(a, b):
    return dict(alpha=a["alpha"] * b["alpha"], scale=a["scale"] * b["scale"], dx=a["dx"] + b["dx"],
                dy=a["dy"] + b["dy"], rot=a["rot"] + b["rot"],
                wipe=[w for w in (a["wipe"], b["wipe"]) if w])

def wipe_mask(size, wipes):
    w, h = size
    m = Image.new("L", size, 255)
    for d, p in wipes:
        r = Image.new("L", size, 0); dr = ImageDraw.Draw(r)
        if d == "left": dr.rectangle([0, 0, int(w * p), h], fill=255)
        elif d == "right": dr.rectangle([int(w * (1 - p)), 0, w, h], fill=255)
        elif d == "top": dr.rectangle([0, 0, w, int(h * p)], fill=255)
        else: dr.rectangle([0, int(h * (1 - p)), w, h], fill=255)
        m = ImageChops.multiply(m, r)
    return m

def paste_layer(canvas, content, x, y, tf):
    img = content
    w, h = img.size
    if tf["scale"] != 1.0:
        nw, nh = max(1, int(w * tf["scale"])), max(1, int(h * tf["scale"]))
        img = img.resize((nw, nh), Image.BILINEAR)
        x += (w - nw) / 2; y += (h - nh) / 2; w, h = nw, nh
    if tf["rot"]:
        r = img.rotate(tf["rot"], expand=True, resample=Image.BILINEAR)
        x -= (r.width - w) / 2; y -= (r.height - h) / 2; img = r
    a = img.getchannel("A")
    if tf["wipe"]:
        a = ImageChops.multiply(a, wipe_mask(a.size, tf["wipe"]))
    if tf["alpha"] < 1.0:
        a = a.point(lambda v, k=tf["alpha"]: int(v * k))
    canvas.paste(img.convert("RGB"), (int(round(x + tf["dx"])), int(round(y + tf["dy"]))), a)

def anim_progress(anims, lt, start, end):
    """Return (entry_kind, e_in, exit_kind, e_out) for a layer at local scene time lt."""
    en = anims[0] or {}; ex = anims[1] or {}
    ek = en.get("type", "none"); xk = ex.get("type", "none")
    ed = max(1e-3, float(en.get("duration", 0.4))); xd = max(1e-3, float(ex.get("duration", 0.3)))
    e_in = 1.0 if ek == "none" else EASE[en.get("easing", "ease_out")](clamp((lt - start - float(en.get("delay", 0))) / ed))
    e_out = 1.0 if xk == "none" else 1 - EASE[ex.get("easing", "ease_in")](clamp((lt - (end - xd)) / xd))
    return ek, e_in, xk, e_out

# ----------------------------------------------------------------- scene rendering
class Renderer:
    def __init__(self, spec, media, W, H):
        self.spec, self.media, self.W, self.H = spec, media, W, H
        self.starts, self.durs, self.total = scene_timeline(spec)
        self.text_cache = {}
        self.bg_cache = {}

    def background(self, sc):
        bg = sc.get("background", self.spec["canvas"]["background"])
        key = json.dumps(bg)
        if key not in self.bg_cache:
            if isinstance(bg, list):
                c1, c2 = np.array(hex_rgb(bg[0]), float), np.array(hex_rgb(bg[1]), float)
                t = np.linspace(0, 1, self.H)[:, None, None]
                arr = (c1 * (1 - t) + c2 * t) * np.ones((1, self.W, 1))
                self.bg_cache[key] = Image.fromarray(arr.astype("uint8"), "RGB")
            else:
                self.bg_cache[key] = Image.new("RGB", (self.W, self.H), hex_rgb(bg))
        return self.bg_cache[key].copy()

    def layer_content(self, L, lt, lstart, lend):
        """Return (RGBA image, x, y) or None if the layer has nothing to draw."""
        W, H = self.W, self.H
        if L["type"] == "text":
            txt = L.get("text") or self.media.texts.get(L.get("text_slot"))
            if not txt:
                return None
            key = (L.get("text_slot"), txt, id(L))
            if key not in self.text_cache:
                self.text_cache[key] = make_text_image(
                    txt, L.get("font") or self.media.font, int(L.get("size", 0.06) * W),
                    L.get("color", "#ffffff"), L.get("align", "center"), int(L.get("max_width", 0.85) * W),
                    int(L.get("stroke", 0) * W), L.get("stroke_color", "#000000"))
            img = self.text_cache[key]
            px, py = L.get("pos", [0.5, 0.5])
            return img, int(px * W - img.width / 2), int(py * H - img.height / 2)
        rx, ry, rw, rh = pixel_rect(L.get("rect", [0, 0, 1, 1]), W, H)
        radius = int(L.get("radius", 0) * W)
        if L["type"] == "shape":
            img = Image.new("RGBA", (rw, rh), hex_rgb(L.get("color", "#ffffff")) + (int(255 * L.get("opacity", 1)),))
            if radius:
                img.putalpha(ImageChops.multiply(img.getchannel("A"), round_mask(rw, rh, radius)))
            return img, rx, ry
        e = self.media.entry(L["slot"])
        if e is None:
            return None
        mo = L.get("motion") or {}
        zoom0, zoom1 = mo.get("zoom", [1.0, 1.0])
        px0, px1 = mo.get("pan_x", [0, 0]); py0, py1 = mo.get("pan_y", [0, 0])
        ldur = max(1e-3, lend - lstart)
        u = EASE[mo.get("easing", "ease_in_out")](clamp((lt - lstart) / ldur))
        zoom = lerp(zoom0, zoom1, u)
        focus = e.get("focus") or L.get("focus") or [0.5, 0.5]
        if e["_video"]:
            src = self.media.video_frame(e, max(0.0, lt - lstart), ldur, L.get("loop", False))
        else:
            sw0, sh0 = self.media.image(e["_path"], 1.0).size
            need = max(rw / sw0, rh / sh0) * max(zoom0, zoom1, 1.0)
            src = self.media.image(e["_path"], min(1.0, need))
        if L.get("polaroid") or L.get("card") == "polaroid":
            img, shadow_margin = make_polaroid_card(
                src, rw, rh, L.get("fit", "cover"), zoom, lerp(px0, px1, u), lerp(py0, py1, u),
                focus, (L.get("border") or {}).get("color", "#ffffff") if isinstance(L.get("border"), dict) else "#ffffff",
                L.get("shadow", True), W
            )
            rx -= shadow_margin
            ry -= shadow_margin
        else:
            img = fit_media(src, rw, rh, L.get("fit", "cover"), zoom, lerp(px0, px1, u), lerp(py0, py1, u), focus, radius)
            b = L.get("border")
            if b:
                bw = max(1, int(b.get("width", 0.004) * W))
                ImageDraw.Draw(img).rounded_rectangle([0, 0, rw - 1, rh - 1], radius=radius,
                                                      outline=hex_rgb(b.get("color", "#ffffff")) + (255,), width=bw)
        if L.get("opacity", 1) < 1:
            img.putalpha(img.getchannel("A").point(lambda v, k=L["opacity"]: int(v * k)))
        return img, rx, ry

    def scene_frame(self, i, lt):
        sc = self.spec["scenes"][i]
        canvas = self.background(sc)
        for L in sc["_layers"]:
            lstart = float(L.get("start", 0))
            lend = float(L["end"]) if "end" in L else (lstart + float(L["duration"]) if "duration" in L else float(sc["duration"]))
            if lt < lstart or lt > lend:
                continue
            ek, e_in, xk, e_out = anim_progress((L.get("entry"), L.get("exit")), lt, lstart, lend)
            if (ek != "none" and e_in <= 0) or (xk != "none" and e_out <= 0):
                continue
            got = self.layer_content(L, lt, lstart, lend)
            if got is None:
                continue
            img, x, y = got
            tf = combine(transform_for(ek, e_in, x, y, img.width, img.height, self.W, self.H),
                         transform_for(xk, e_out, x, y, img.width, img.height, self.W, self.H))
            paste_layer(canvas, img, x, y, tf)
        return canvas

    def blend(self, a, b, kind, p):
        W, H = self.W, self.H
        e = EASE["ease_in_out"](clamp(p))
        if kind == "fade":
            return Image.blend(a, b, e)
        if kind == "fade_black":
            black = Image.new("RGB", a.size)
            return Image.blend(a, black, e * 2) if e < 0.5 else Image.blend(black, b, (e - 0.5) * 2)
        if kind == "zoom_through":
            s = 1 + 0.35 * e
            z = a.resize((int(W * s), int(H * s)), Image.BILINEAR)
            ox, oy = (z.width - W) // 2, (z.height - H) // 2
            return Image.blend(z.crop((ox, oy, ox + W, oy + H)), b, e)
        if kind.startswith("slide_from_"):
            d = kind[11:]; out = Image.new("RGB", (W, H))
            dx, dy = {"left": (W, 0), "right": (-W, 0), "top": (0, H), "bottom": (0, -H)}[d]
            out.paste(a, (int(dx * e), int(dy * e)))
            out.paste(b, (int(dx * e - dx), int(dy * e - dy)))
            return out
        if kind.startswith("wipe_from_"):
            return Image.composite(b, a, wipe_mask((W, H), [(kind[10:], e)]))
        return b

    def frame(self, T):
        T = min(T, self.total - 1e-4)
        active = [(i, T - self.starts[i]) for i in range(len(self.starts))
                  if self.starts[i] <= T < self.starts[i] + self.durs[i]]
        if len(active) == 1:
            return self.scene_frame(*active[0])
        (i, lt_a), (j, lt_b) = active[0], active[-1]
        tr = self.spec["scenes"][i].get("transition_out") or {}
        a, b = self.scene_frame(i, lt_a), self.scene_frame(j, lt_b)
        return self.blend(a, b, tr.get("type", "cut"), lt_b / max(1e-3, float(tr.get("duration", 0.4))))

# ----------------------------------------------------------------- main
def even(n):
    return int(n) // 2 * 2

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--assign")
    ap.add_argument("--out")
    ap.add_argument("--scale", type=float, default=1.0, help="0.5 = half-size preview")
    ap.add_argument("--still", help="comma-separated times (s); writes PNG stills instead of a video")
    ap.add_argument("--still-dir", default="stills")
    ap.add_argument("--list-slots", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--thumbnail", help="path to save a thumbnail image (e.g. video-result/thumbnail.jpg)")
    ap.add_argument("--thumbnail-time", type=float, help="timestamp in seconds for thumbnail (default: middle of video or 1.0s)")
    a = ap.parse_args()

    spec = expand_spec(load_json(a.spec))
    errs = check_spec(spec)
    if errs:
        print("SPEC ERRORS:\n  " + "\n  ".join(errs)); sys.exit(1)
    if a.list_slots:
        print(json.dumps({"duration": round(scene_timeline(spec)[2], 2), "slots": list_slots(spec)}, indent=2)); return
    if a.check:
        print(f"spec OK: {len(spec['scenes'])} scenes, {len(list_slots(spec))} slots, {scene_timeline(spec)[2]:.2f}s"); return
    if not a.assign:
        sys.exit("--assign is required to render")

    assign = load_json(a.assign)
    cv = spec["canvas"]; W, H, fps = even(cv["width"] * a.scale), even(cv["height"] * a.scale), cv["fps"]
    missing = [s["slot"] for s in list_slots(spec) if s["kind"] == "media" and s["slot"] not in assign.get("slots", {})]
    if missing:
        print(f"note: {len(missing)} unfilled media slots will be left empty: {', '.join(missing[:12])}", file=sys.stderr)

    tmp = tempfile.mkdtemp(prefix="vtr_")
    try:
        base = Path(a.assign).resolve().parent
        media = Media(assign, base, fps, max(W, H), tmp)
        R = Renderer(spec, media, W, H)
        if a.still:
            Path(a.still_dir).mkdir(parents=True, exist_ok=True)
            for t in [float(x) for x in a.still.split(",")]:
                R.frame(t).save(Path(a.still_dir) / f"still_{t:07.3f}.png")
            print(f"wrote stills to {a.still_dir}"); return
        if not a.out:
            sys.exit("--out is required to render a video")
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        if a.thumbnail:
            Path(a.thumbnail).parent.mkdir(parents=True, exist_ok=True)
            t_thumb = a.thumbnail_time if a.thumbnail_time is not None else min(1.0, R.total / 2.0)
            R.frame(t_thumb).convert("RGB").save(a.thumbnail)
            print(f"wrote thumbnail to {a.thumbnail}")
        n = int(round(R.total * fps))
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(fps), "-i", "-"]
        au = assign.get("audio") or {}
        if au.get("file"):
            p = Path(au["file"]); p = p if p.is_absolute() else base / p
            cmd += ["-ss", str(au.get("start", 0)), "-i", str(p)]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(a.crf), "-preset", "medium", "-movflags", "+faststart"]
        if au.get("file"):
            cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-t", f"{R.total:.3f}",
                    "-af", f"afade=t=out:st={max(0, R.total - 1):.3f}:d=1"]
        else:
            cmd += ["-an"]
        cmd.append(a.out)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for k in range(n):
            proc.stdin.write(R.frame(k / fps).tobytes())
            if k % max(1, n // 10) == 0:
                print(f"  {100 * k // n}%", file=sys.stderr)
        proc.stdin.close()
        if proc.wait() != 0:
            sys.exit("ffmpeg failed while encoding")
        print(f"wrote {a.out}  ({W}x{H}, {fps}fps, {R.total:.2f}s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()