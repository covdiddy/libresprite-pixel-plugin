"""MCP tool implementations (LibreSprite port of pixel-mcp).

Every tool takes a dict of arguments and returns a JSON-serialisable dict. Tools are stateless:
they load the .aseprite file, edit it in memory and write it back.
"""
import base64
import json
import os
import subprocess
import tempfile
import zlib

import ase
import analysis
import draw
import pngio
from ase import AseError, Cel, Layer, Tag
from config import load_config

TOOLS = {}  # name -> (description, schema, handler)


def tool(name, description, props, required=()):
    def deco(fn):
        TOOLS[name] = (description, {"type": "object", "properties": props, "required": list(required)}, fn)
        return fn
    return deco


def S(type_, desc, **extra):
    d = {"type": type_, "description": desc}
    d.update(extra)
    return d


SP = S("string", "Path to the .aseprite sprite file")
LN = S("string", "Layer name")
FN = S("integer", "Frame number (1-based)", minimum=1)
COLOR = S("string", "Hex colour #RRGGBB or #RRGGBBAA")
USEPAL = S("boolean", "Snap colours to the sprite palette")
REGION = {"type": "object", "properties": {k: {"type": "integer"} for k in ("x", "y", "width", "height")},
          "required": ["x", "y", "width", "height"]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def need(a, *keys):
    for k in keys:
        if k not in a or a[k] is None or a[k] == "":
            raise AseError("Missing required parameter: %s" % k)


def path_of(a, key="sprite_path"):
    need(a, key)
    return os.path.abspath(os.path.expanduser(a[key]))


def open_sprite(a, key="sprite_path"):
    p = path_of(a, key)
    if not os.path.isfile(p):
        raise AseError("Sprite file not found: %s" % p)
    return ase.load(p)


def commit(spr):
    ase.save(spr)


def target(spr, a, create=True):
    """(layer_index, frame_index, image) for layer_name/frame_number arguments."""
    need(a, "layer_name", "frame_number")
    li = spr.layer_index(a["layer_name"])
    if spr.layers[li].kind == 1:
        raise AseError("'%s' is a group, not an image layer" % a["layer_name"])
    fi = spr.check_frame(int(a["frame_number"]))
    return li, fi, spr.get_image(li, fi, create)


def temp_dir():
    d = load_config()["temp_dir"]
    os.makedirs(d, exist_ok=True)
    return d


def run_ls(args, timeout=None):
    cfg = load_config()
    exe = cfg.get("libresprite_path")
    if not exe:
        raise AseError("LibreSprite not found. Run /pixel-setup <path-to-libresprite>.")
    try:
        r = subprocess.run([exe, "--batch"] + args, capture_output=True, text=True, timeout=timeout or cfg["timeout"])
    except subprocess.TimeoutExpired:
        raise AseError("LibreSprite timed out")
    if r.returncode != 0:
        raise AseError("LibreSprite failed: %s" % (r.stderr or r.stdout).strip()[-500:])
    return r.stdout


def rgba_hex(c):
    return draw.to_hex(c, c[3] != 255)


def check_palette_color(a, key):
    need(a, key)
    return draw.parse_color(a[key])


# ---------------------------------------------------------------------------
# canvas
# ---------------------------------------------------------------------------

@tool("create_canvas", "Create a new sprite (.aseprite) with the given size and colour mode. Returns the file path. "
      "Note: LibreSprite port stores sprites as RGBA; indexed/grayscale are accepted but saved as RGB.",
      {"width": S("integer", "Width in pixels (1-4096)", minimum=1, maximum=4096),
       "height": S("integer", "Height in pixels (1-4096)", minimum=1, maximum=4096),
       "color_mode": S("string", "rgb, grayscale or indexed", enum=["rgb", "grayscale", "indexed"])},
      ["width", "height", "color_mode"])
def create_canvas(a):
    need(a, "width", "height", "color_mode")
    w, h = int(a["width"]), int(a["height"])
    if not (1 <= w <= 4096 and 1 <= h <= 4096):
        raise AseError("Canvas size must be between 1 and 4096 pixels")
    if a["color_mode"] not in ("rgb", "grayscale", "indexed"):
        raise AseError("color_mode must be rgb, grayscale or indexed")
    spr = ase.Sprite(w, h)
    out = os.path.join(temp_dir(), "sprite-%s.aseprite" % os.urandom(4).hex())
    ase.save(spr, out)
    res = {"file_path": out}
    if a["color_mode"] != "rgb":
        res["note"] = "Stored as RGB (LibreSprite port edits files natively as RGBA)."
    return res


@tool("add_layer", "Add a new layer on top of the sprite.", {"sprite_path": SP, "layer_name": LN}, ["sprite_path", "layer_name"])
def add_layer(a):
    spr = open_sprite(a)
    need(a, "layer_name")
    spr.layers.append(Layer(a["layer_name"]))
    commit(spr)
    return {"success": True}


@tool("add_frame", "Append a new empty frame with the given duration.",
      {"sprite_path": SP, "duration_ms": S("integer", "Frame duration in ms (1-65535)", minimum=1, maximum=65535)},
      ["sprite_path", "duration_ms"])
def add_frame(a):
    spr = open_sprite(a)
    need(a, "duration_ms")
    d = int(a["duration_ms"])
    if not 1 <= d <= 65535:
        raise AseError("duration_ms must be between 1 and 65535")
    spr.durations.append(d)
    commit(spr)
    return {"frame_number": spr.frame_count}


@tool("get_sprite_info", "Sprite metadata: size, colour mode, frame/layer counts, layer names (bottom to top).",
      {"sprite_path": SP}, ["sprite_path"])
def get_sprite_info(a):
    spr = open_sprite(a)
    return {"width": spr.width, "height": spr.height, "color_mode": spr.source_mode, "frame_count": spr.frame_count,
            "layer_count": len(spr.layers), "layers": [l.name for l in spr.layers],
            "frame_durations_ms": spr.durations,
            "tags": [{"name": t.name, "from_frame": t.from_frame + 1, "to_frame": t.to_frame + 1, "direction": t.direction} for t in spr.tags]}


@tool("delete_layer", "Delete a layer (the last remaining layer cannot be deleted).", {"sprite_path": SP, "layer_name": LN},
      ["sprite_path", "layer_name"])
def delete_layer(a):
    spr = open_sprite(a)
    need(a, "layer_name")
    if len(spr.layers) <= 1:
        raise AseError("Cannot delete the last remaining layer")
    li = spr.layer_index(a["layer_name"])
    del spr.layers[li]
    spr.cels = {(l - 1 if l > li else l, f): c for (l, f), c in spr.cels.items() if l != li}
    commit(spr)
    return {"success": True}


def _remap_frames(spr, mapping, new_count):
    """mapping: old frame index -> new frame index (None = deleted). Linked cels are re-pointed or materialised."""
    new_cels = {}
    for (li, fi), cel in spr.cels.items():
        nf = mapping.get(fi)
        if nf is None:
            continue
        if cel.link is not None:
            nl = mapping.get(cel.link)
            if nl is None:
                src = spr.resolve(li, fi)
                new_cels[(li, nf)] = Cel(bytearray(src.data) if src else spr.blank(), 0, 0, cel.opacity)
                continue
            cel = Cel(None, cel.x, cel.y, cel.opacity, link=nl)
        new_cels[(li, nf)] = cel
    spr.cels = new_cels


@tool("delete_frame", "Delete a frame (the last remaining frame cannot be deleted).", {"sprite_path": SP, "frame_number": FN},
      ["sprite_path", "frame_number"])
def delete_frame(a):
    spr = open_sprite(a)
    need(a, "frame_number")
    if spr.frame_count <= 1:
        raise AseError("Cannot delete the last remaining frame")
    fi = spr.check_frame(int(a["frame_number"]))
    # materialise cels linked *to* the deleted frame before dropping it
    mapping = {i: (i if i < fi else (None if i == fi else i - 1)) for i in range(spr.frame_count)}
    _remap_frames(spr, mapping, spr.frame_count - 1)
    del spr.durations[fi]
    tags = []
    for t in spr.tags:
        f0 = t.from_frame - (1 if t.from_frame > fi else 0)
        f1 = t.to_frame - (1 if t.to_frame >= fi else 0)
        if t.from_frame == fi and t.to_frame == fi:
            continue
        if f1 < f0:
            f1 = f0
        t.from_frame, t.to_frame = f0, min(f1, spr.frame_count - 1)
        tags.append(t)
    spr.tags = tags
    commit(spr)
    return {"success": True}


@tool("flatten_layers", "Flatten all visible layers into one layer (keeps the bottom layer's name).", {"sprite_path": SP}, ["sprite_path"])
def flatten_layers(a):
    spr = open_sprite(a)
    name = spr.layers[0].name
    flat = {}
    for fi in range(spr.frame_count):
        flat[(0, fi)] = Cel(spr.flatten_frame(fi))
    spr.layers = [Layer(name)]
    spr.cels = flat
    commit(spr)
    return {"success": True}


# ---------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------

PIXEL = {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "color": COLOR},
         "required": ["x", "y", "color"]}
BASE = {"sprite_path": SP, "layer_name": LN, "frame_number": FN}


@tool("draw_pixels", "Draw individual pixels (batch).",
      dict(BASE, pixels={"type": "array", "items": PIXEL, "description": "Pixels to draw"}, use_palette=USEPAL),
      ["sprite_path", "layer_name", "frame_number", "pixels"])
def draw_pixels(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "pixels")
    n = 0
    for p in a["pixels"]:
        c = draw.maybe_snap(spr, draw.parse_color(p["color"]), a.get("use_palette"))
        draw.put(img, spr.width, spr.height, int(p["x"]), int(p["y"]), c)
        n += 1
    commit(spr)
    return {"pixels_drawn": n}


@tool("draw_line", "Draw a line with the given colour and thickness.",
      dict(BASE, x1={"type": "integer"}, y1={"type": "integer"}, x2={"type": "integer"}, y2={"type": "integer"},
           color=COLOR, thickness=S("integer", "Line thickness in pixels (default 1)", minimum=1), use_palette=USEPAL),
      ["sprite_path", "layer_name", "frame_number", "x1", "y1", "x2", "y2", "color"])
def draw_line(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "x1", "y1", "x2", "y2", "color")
    c = draw.maybe_snap(spr, draw.parse_color(a["color"]), a.get("use_palette"))
    draw.line(img, spr.width, spr.height, int(a["x1"]), int(a["y1"]), int(a["x2"]), int(a["y2"]), c, max(1, int(a.get("thickness") or 1)))
    commit(spr)
    return {"success": True}


@tool("draw_contour", "Draw a polyline or closed polygon through multiple points.",
      dict(BASE, points={"type": "array", "items": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}},
           color=COLOR, thickness=S("integer", "Thickness (default 1)", minimum=1), closed=S("boolean", "Close the shape"), use_palette=USEPAL),
      ["sprite_path", "layer_name", "frame_number", "points", "color"])
def draw_contour(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "points", "color")
    pts = a["points"]
    if len(pts) < 2:
        raise AseError("At least 2 points are required")
    c = draw.maybe_snap(spr, draw.parse_color(a["color"]), a.get("use_palette"))
    t = max(1, int(a.get("thickness") or 1))
    for p, q in zip(pts, pts[1:]):
        draw.line(img, spr.width, spr.height, int(p["x"]), int(p["y"]), int(q["x"]), int(q["y"]), c, t)
    if a.get("closed") and len(pts) > 2:
        draw.line(img, spr.width, spr.height, int(pts[-1]["x"]), int(pts[-1]["y"]), int(pts[0]["x"]), int(pts[0]["y"]), c, t)
    commit(spr)
    return {"success": True}


@tool("draw_rectangle", "Draw a rectangle (outline or filled).",
      dict(BASE, x={"type": "integer"}, y={"type": "integer"}, width={"type": "integer"}, height={"type": "integer"},
           color=COLOR, filled=S("boolean", "Fill the rectangle"), use_palette=USEPAL),
      ["sprite_path", "layer_name", "frame_number", "x", "y", "width", "height", "color"])
def draw_rectangle(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "x", "y", "width", "height", "color")
    if int(a["width"]) <= 0 or int(a["height"]) <= 0:
        raise AseError("width and height must be positive")
    c = draw.maybe_snap(spr, draw.parse_color(a["color"]), a.get("use_palette"))
    draw.rect(img, spr.width, spr.height, int(a["x"]), int(a["y"]), int(a["width"]), int(a["height"]), c, bool(a.get("filled")))
    commit(spr)
    return {"success": True}


@tool("draw_circle", "Draw a circle (outline or filled).",
      dict(BASE, center_x={"type": "integer"}, center_y={"type": "integer"}, radius=S("integer", "Radius in pixels", minimum=0),
           color=COLOR, filled=S("boolean", "Fill the circle"), use_palette=USEPAL),
      ["sprite_path", "layer_name", "frame_number", "center_x", "center_y", "radius", "color"])
def draw_circle(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "center_x", "center_y", "radius", "color")
    c = draw.maybe_snap(spr, draw.parse_color(a["color"]), a.get("use_palette"))
    draw.circle(img, spr.width, spr.height, int(a["center_x"]), int(a["center_y"]), int(a["radius"]), c, bool(a.get("filled")))
    commit(spr)
    return {"success": True}


@tool("fill_area", "Flood fill (paint bucket) from a point.",
      dict(BASE, x={"type": "integer"}, y={"type": "integer"}, color=COLOR,
           tolerance=S("integer", "Per-channel colour tolerance 0-255 (default 0)", minimum=0, maximum=255), use_palette=USEPAL),
      ["sprite_path", "layer_name", "frame_number", "x", "y", "color"])
def fill_area(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "x", "y", "color")
    c = draw.maybe_snap(spr, draw.parse_color(a["color"]), a.get("use_palette"))
    draw.flood_fill(img, spr.width, spr.height, int(a["x"]), int(a["y"]), c, int(a.get("tolerance") or 0))
    commit(spr)
    return {"success": True}


@tool("draw_with_dither", "Fill a region with a dithering pattern between two colours. Patterns: " + ", ".join(draw.PATTERNS) +
      ". density 0.0 = all color1, 1.0 = all color2 (default 0.5).",
      dict(BASE, region=REGION, color1=COLOR, color2=COLOR, pattern=S("string", "Dither pattern", enum=draw.PATTERNS),
           density=S("number", "Ratio of color2 (0-1, default 0.5)", minimum=0, maximum=1)),
      ["sprite_path", "layer_name", "frame_number", "region", "color1", "color2", "pattern"])
def draw_with_dither(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "region", "color1", "color2", "pattern")
    r = a["region"]
    if int(r["width"]) <= 0 or int(r["height"]) <= 0:
        raise AseError("region width and height must be positive")
    d = a.get("density")
    d = 0.5 if d is None or d == 0 else float(d)
    if not 0 <= d <= 1:
        raise AseError("density must be between 0.0 and 1.0")
    draw.dither_region(img, spr.width, spr.height, int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"]),
                       draw.parse_color(a["color1"]), draw.parse_color(a["color2"]), a["pattern"], d)
    commit(spr)
    return {"success": True}


@tool("apply_outline", "Draw an outline of the given colour around the non-transparent pixels of a layer/frame.",
      dict(BASE, color=COLOR, thickness=S("integer", "Outline thickness (default 1)", minimum=1)),
      ["sprite_path", "layer_name", "frame_number", "color"])
def apply_outline(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "color")
    draw.outline(img, spr.width, spr.height, draw.parse_color(a["color"]), int(a.get("thickness") or 1))
    commit(spr)
    return {"success": True}


@tool("apply_shading", "Palette-constrained shading of a region from a light direction. Styles: smooth, hard, pillow. "
      "Light: top_left, top, top_right, left, right, bottom_left, bottom, bottom_right.",
      dict(BASE, region=REGION, palette={"type": "array", "items": COLOR, "description": "Palette colours (dark to light)"},
           light_direction=S("string", "Light direction", enum=list(draw.LIGHT)), intensity=S("number", "0.0-1.0", minimum=0, maximum=1),
           style=S("string", "Shading style", enum=["smooth", "hard", "pillow"])),
      ["sprite_path", "layer_name", "frame_number", "region", "palette", "light_direction", "intensity", "style"])
def apply_shading(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "region", "palette", "light_direction", "intensity", "style")
    r = a["region"]
    pal = [draw.parse_color(c) for c in a["palette"]]
    n = draw.shade_region(img, spr.width, spr.height, int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"]),
                          pal, a["light_direction"], float(a["intensity"]), a["style"])
    commit(spr)
    return {"success": True, "pixels_changed": n}


@tool("apply_auto_shading", "Automatically shade flat-colour regions from a light direction. Styles: cell, smooth, soft. "
      "Generates shadow/highlight colours (optional hue shifting) and adds them to the palette.",
      dict(BASE, light_direction=S("string", "Light direction", enum=list(draw.LIGHT)), intensity=S("number", "0.0-1.0", minimum=0, maximum=1),
           style=S("string", "cell, smooth or soft", enum=["cell", "smooth", "soft"]),
           hue_shift=S("boolean", "Cool shadows / warm highlights")),
      ["sprite_path", "layer_name", "frame_number", "light_direction", "intensity", "style"])
def apply_auto_shading(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    need(a, "light_direction", "intensity", "style")
    inten = float(a["intensity"])
    if not 0 <= inten <= 1:
        raise AseError("intensity must be between 0.0 and 1.0")
    draw.light_vec(a["light_direction"])
    added, regions = draw.auto_shade(spr, img, spr.width, spr.height, a["light_direction"], inten, a["style"], a.get("hue_shift", True))
    commit(spr)
    return {"success": True, "colors_added": added, "palette": [draw.to_hex(c, False) for c in spr.palette], "regions_shaded": regions}


@tool("suggest_antialiasing", "Detect jagged diagonal stair-steps and suggest intermediate colours. auto_apply writes them; "
      "use_palette snaps suggestions to the palette.",
      dict(BASE, region=REGION, threshold=S("integer", "Minimum corners in a stair chain (default 2)", minimum=1),
           auto_apply=S("boolean", "Apply suggestions"), use_palette=USEPAL),
      ["sprite_path", "layer_name", "frame_number"])
def suggest_antialiasing(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    r = a.get("region") or {"x": 0, "y": 0, "width": spr.width, "height": spr.height}
    sugg = analysis.antialias_suggestions(img, spr.width, spr.height, int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"]),
                                          int(a.get("threshold") or 2))
    if a.get("use_palette"):
        for s in sugg:
            s["color"] = draw.to_hex(draw.nearest(draw.parse_color(s["color"]), spr.palette))
    if a.get("auto_apply"):
        for s in sugg:
            draw.put(img, spr.width, spr.height, s["x"], s["y"], draw.parse_color(s["color"]))
        commit(spr)
    return {"suggestions": sugg, "count": len(sugg), "applied": bool(a.get("auto_apply"))}


# ---------------------------------------------------------------------------
# inspection
# ---------------------------------------------------------------------------

@tool("get_pixels", "Read pixel colours from a rectangular region (paginated; default page 1000, max 10000).",
      dict(BASE, x={"type": "integer"}, y={"type": "integer"}, width={"type": "integer"}, height={"type": "integer"},
           cursor=S("string", "Cursor from a previous page"), page_size=S("integer", "Page size (1-10000)", minimum=1, maximum=10000)),
      ["sprite_path", "layer_name", "frame_number", "x", "y", "width", "height"])
def get_pixels(a):
    spr = open_sprite(a)
    need(a, "layer_name", "frame_number", "x", "y", "width", "height")
    li = spr.layer_index(a["layer_name"])
    fi = spr.check_frame(int(a["frame_number"]))
    img = spr.get_image(li, fi, create=False)
    x0, y0, w, h = int(a["x"]), int(a["y"]), int(a["width"]), int(a["height"])
    if w <= 0 or h <= 0:
        raise AseError("width and height must be positive")
    total = w * h
    page = min(10000, max(1, int(a.get("page_size") or 1000)))
    start = int(a.get("cursor") or 0)
    if start < 0 or start > total:
        raise AseError("Invalid cursor")
    end = min(total, start + page)
    out = []
    for k in range(start, end):
        x, y = x0 + k % w, y0 + k // w
        if img is not None and 0 <= x < spr.width and 0 <= y < spr.height:
            c = draw.getpx(img, spr.width, x, y)
        else:
            c = (0, 0, 0, 0)
        out.append({"x": x, "y": y, "color": draw.to_hex(c)})
    return {"pixels": out, "next_cursor": str(end) if end < total else "", "total_pixels": total}


# ---------------------------------------------------------------------------
# animation
# ---------------------------------------------------------------------------

@tool("set_frame_duration", "Set a frame's duration in milliseconds.",
      {"sprite_path": SP, "frame_number": FN, "duration_ms": S("integer", "Duration ms (1-65535)", minimum=1, maximum=65535)},
      ["sprite_path", "frame_number", "duration_ms"])
def set_frame_duration(a):
    spr = open_sprite(a)
    need(a, "frame_number", "duration_ms")
    fi = spr.check_frame(int(a["frame_number"]))
    d = int(a["duration_ms"])
    if not 1 <= d <= 65535:
        raise AseError("duration_ms must be between 1 and 65535")
    spr.durations[fi] = d
    commit(spr)
    return {"success": True}


@tool("create_tag", "Create an animation tag over a frame range. direction: forward, reverse, pingpong.",
      {"sprite_path": SP, "tag_name": S("string", "Tag name"), "from_frame": FN, "to_frame": FN,
       "direction": S("string", "forward, reverse or pingpong", enum=["forward", "reverse", "pingpong"])},
      ["sprite_path", "tag_name", "from_frame", "to_frame", "direction"])
def create_tag(a):
    spr = open_sprite(a)
    need(a, "tag_name", "from_frame", "to_frame", "direction")
    f0 = spr.check_frame(int(a["from_frame"]))
    f1 = spr.check_frame(int(a["to_frame"]))
    if f1 < f0:
        raise AseError("to_frame must be >= from_frame")
    if a["direction"] not in ("forward", "reverse", "pingpong"):
        raise AseError("direction must be forward, reverse or pingpong")
    if any(t.name == a["tag_name"] for t in spr.tags):
        raise AseError("A tag named '%s' already exists" % a["tag_name"])
    spr.tags.append(Tag(a["tag_name"], f0, f1, a["direction"]))
    commit(spr)
    return {"success": True}


@tool("delete_tag", "Delete an animation tag by name.", {"sprite_path": SP, "tag_name": S("string", "Tag name")}, ["sprite_path", "tag_name"])
def delete_tag(a):
    spr = open_sprite(a)
    need(a, "tag_name")
    n = len(spr.tags)
    spr.tags = [t for t in spr.tags if t.name != a["tag_name"]]
    if len(spr.tags) == n:
        raise AseError("Tag not found: %s" % a["tag_name"])
    commit(spr)
    return {"success": True}


@tool("duplicate_frame", "Duplicate a frame and insert the copy after frame insert_after (0 = at the start).",
      {"sprite_path": SP, "source_frame": FN, "insert_after": S("integer", "Insert after this frame number (0 = first)", minimum=0)},
      ["sprite_path", "source_frame", "insert_after"])
def duplicate_frame(a):
    spr = open_sprite(a)
    need(a, "source_frame", "insert_after")
    src = spr.check_frame(int(a["source_frame"]))
    after = int(a["insert_after"])
    if after < 0 or after > spr.frame_count:
        raise AseError("insert_after must be between 0 and %d" % spr.frame_count)
    new = after  # 0-based index of the new frame
    n = spr.frame_count
    mapping = {i: (i if i < new else i + 1) for i in range(n)}
    copies = {}
    for li in range(len(spr.layers)):
        cel = spr.resolve(li, src)
        raw = spr.cels.get((li, src))
        if cel is not None:
            copies[li] = Cel(bytearray(cel.data), 0, 0, raw.opacity)
    dur = spr.durations[src]
    _remap_frames(spr, mapping, n + 1)
    spr.durations.insert(new, dur)
    for li, c in copies.items():
        spr.cels[(li, new)] = c
    for t in spr.tags:
        if t.from_frame >= new:
            t.from_frame += 1
        if t.to_frame >= new:
            t.to_frame += 1
    commit(spr)
    return {"new_frame_number": new + 1}


@tool("link_cel", "Link a cel on target_frame to the cel on source_frame (shared image data).",
      {"sprite_path": SP, "layer_name": LN, "source_frame": FN, "target_frame": FN},
      ["sprite_path", "layer_name", "source_frame", "target_frame"])
def link_cel(a):
    spr = open_sprite(a)
    need(a, "layer_name", "source_frame", "target_frame")
    li = spr.layer_index(a["layer_name"])
    s = spr.check_frame(int(a["source_frame"]))
    t = spr.check_frame(int(a["target_frame"]))
    if s == t:
        raise AseError("source_frame and target_frame must differ")
    src = spr.resolve(li, s)
    if src is None:
        raise AseError("No cel on layer '%s' at frame %d to link from" % (a["layer_name"], s + 1))
    root = s
    while spr.cels[(li, root)].link is not None:
        root = spr.cels[(li, root)].link
    spr.cels[(li, t)] = Cel(None, 0, 0, spr.cels[(li, s)].opacity, link=root)
    commit(spr)
    return {"success": True}


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------

def _pal_hex(spr):
    return [draw.to_hex(c, c[3] != 255) if c[3] != 255 else draw.to_hex(c, False) for c in spr.palette]


@tool("get_palette", "Return the sprite palette as hex colours.", {"sprite_path": SP}, ["sprite_path"])
def get_palette(a):
    spr = open_sprite(a)
    return {"colors": _pal_hex(spr), "size": len(spr.palette)}


@tool("set_palette", "Replace the sprite palette (#RRGGBB colours, max 256).",
      {"sprite_path": SP, "colors": {"type": "array", "items": COLOR, "description": "Palette colours"}}, ["sprite_path", "colors"])
def set_palette(a):
    spr = open_sprite(a)
    need(a, "colors")
    if not 1 <= len(a["colors"]) <= 256:
        raise AseError("A palette needs 1-256 colours")
    spr.palette = [draw.parse_color(c) for c in a["colors"]]
    commit(spr)
    return {"success": True}


@tool("set_palette_color", "Set one palette entry.", {"sprite_path": SP, "index": S("integer", "Palette index", minimum=0), "color": COLOR},
      ["sprite_path", "index", "color"])
def set_palette_color(a):
    spr = open_sprite(a)
    need(a, "index", "color")
    i = int(a["index"])
    if not 0 <= i < len(spr.palette):
        raise AseError("index %d out of range (palette size %d)" % (i, len(spr.palette)))
    spr.palette[i] = draw.parse_color(a["color"])
    commit(spr)
    return {"success": True}


@tool("add_palette_color", "Append a colour to the palette (max 256). Returns its index.", {"sprite_path": SP, "color": COLOR},
      ["sprite_path", "color"])
def add_palette_color(a):
    spr = open_sprite(a)
    need(a, "color")
    if len(spr.palette) >= 256:
        raise AseError("Palette is full (256 colours)")
    spr.palette.append(draw.parse_color(a["color"]))
    commit(spr)
    return {"color_index": len(spr.palette) - 1}


@tool("sort_palette", "Sort the palette by hue, saturation, brightness or luminance.",
      {"sprite_path": SP, "method": S("string", "hue, saturation, brightness, luminance", enum=["hue", "saturation", "brightness", "luminance"]),
       "ascending": S("boolean", "Ascending order")}, ["sprite_path", "method"])
def sort_palette(a):
    spr = open_sprite(a)
    need(a, "method")
    spr.palette = analysis.sort_palette(spr.palette, a["method"], a.get("ascending", True))
    commit(spr)
    return {"success": True}


@tool("analyze_palette_harmonies", "Find complementary/triadic/analogous relationships and colour temperature of a palette.",
      {"palette": {"type": "array", "items": COLOR, "description": "Palette colours"}}, ["palette"])
def analyze_palette_harmonies(a):
    need(a, "palette")
    return analysis.analyze_harmonies([draw.parse_color(c) for c in a["palette"]])


@tool("quantize_palette", "Reduce the sprite's colours with median_cut, kmeans or octree, optionally with Floyd-Steinberg dithering.",
      {"sprite_path": SP, "target_colors": S("integer", "Number of colours (2-256)", minimum=2, maximum=256),
       "algorithm": S("string", "median_cut, kmeans or octree", enum=["median_cut", "kmeans", "octree"]),
       "dither": S("boolean", "Floyd-Steinberg dithering"), "preserve_transparency": S("boolean", "Keep transparent pixels (always true here)"),
       "convert_to_indexed": S("boolean", "Ignored: files are stored as RGB in this port")},
      ["sprite_path", "target_colors", "algorithm"])
def quantize_palette(a):
    spr = open_sprite(a)
    need(a, "target_colors", "algorithm")
    n = int(a["target_colors"])
    if not 2 <= n <= 256:
        raise AseError("target_colors must be between 2 and 256")
    counts = {}
    for (li, fi), cel in spr.cels.items():
        if cel.link is None:
            for k, v in analysis.unique_colors(cel.data).items():
                counts[k] = counts.get(k, 0) + v
    original = len(counts)
    pal = analysis.build_palette(counts, n, a["algorithm"])
    for (li, fi), cel in spr.cels.items():
        if cel.link is None:
            analysis.apply_palette(cel.data, spr.width, spr.height, pal, bool(a.get("dither")))
    spr.palette = pal
    commit(spr)
    return {"success": True, "original_colors": original, "quantized_colors": len(pal), "color_mode": "rgb",
            "palette": [draw.to_hex(c, False) for c in pal], "algorithm_used": a["algorithm"]}


# ---------------------------------------------------------------------------
# selection (kept in a sidecar file next to the sprite, clipboard in the temp dir)
# ---------------------------------------------------------------------------

def _sel_path(p):
    return p + ".selection"


def _load_sel(spr, p):
    try:
        with open(_sel_path(p)) as f:
            d = json.load(f)
        mask = bytearray(zlib.decompress(base64.b64decode(d["mask"])))
        if len(mask) == spr.width * spr.height:
            return mask
    except (OSError, ValueError, KeyError, zlib.error):
        pass
    return None


def _save_sel(p, mask):
    if mask is None or not any(mask):
        try:
            os.remove(_sel_path(p))
        except OSError:
            pass
        return
    with open(_sel_path(p), "w") as f:
        json.dump({"mask": base64.b64encode(zlib.compress(bytes(mask))).decode()}, f)


def _combine(spr, old, shape, mode):
    n = spr.width * spr.height
    if mode not in ("replace", "add", "subtract", "intersect"):
        raise AseError("mode must be replace, add, subtract or intersect")
    old = old or bytearray(n)
    out = bytearray(n)
    for i in range(n):
        a_, b_ = old[i], shape[i]
        out[i] = {"replace": b_, "add": a_ | b_, "subtract": a_ & (1 - b_), "intersect": a_ & b_}[mode]
    return out


def _shape(spr, x, y, w, h, ellipse):
    if w <= 0 or h <= 0:
        raise AseError("width and height must be positive")
    m = bytearray(spr.width * spr.height)
    cx, cy, rx, ry = x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0
    for yy in range(max(0, y), min(spr.height, y + h)):
        for xx in range(max(0, x), min(spr.width, x + w)):
            if not ellipse or ((xx + 0.5 - cx) / rx) ** 2 + ((yy + 0.5 - cy) / ry) ** 2 <= 1.0:
                m[yy * spr.width + xx] = 1
    return m


SEL = {"sprite_path": SP, "x": {"type": "integer"}, "y": {"type": "integer"}, "width": {"type": "integer"}, "height": {"type": "integer"},
       "mode": S("string", "replace, add, subtract, intersect", enum=["replace", "add", "subtract", "intersect"])}


@tool("select_rectangle", "Create a rectangular selection.", SEL, ["sprite_path", "x", "y", "width", "height"])
def select_rectangle(a):
    spr = open_sprite(a)
    need(a, "x", "y", "width", "height")
    p = path_of(a)
    m = _combine(spr, _load_sel(spr, p), _shape(spr, int(a["x"]), int(a["y"]), int(a["width"]), int(a["height"]), False), a.get("mode") or "replace")
    _save_sel(p, m)
    return {"success": True}


@tool("select_ellipse", "Create an elliptical selection from its bounding box.", SEL, ["sprite_path", "x", "y", "width", "height"])
def select_ellipse(a):
    spr = open_sprite(a)
    need(a, "x", "y", "width", "height")
    p = path_of(a)
    m = _combine(spr, _load_sel(spr, p), _shape(spr, int(a["x"]), int(a["y"]), int(a["width"]), int(a["height"]), True), a.get("mode") or "replace")
    _save_sel(p, m)
    return {"success": True}


@tool("select_all", "Select the whole canvas.", {"sprite_path": SP}, ["sprite_path"])
def select_all(a):
    spr = open_sprite(a)
    _save_sel(path_of(a), bytearray(b"\x01" * (spr.width * spr.height)))
    return {"success": True}


@tool("deselect", "Clear the selection.", {"sprite_path": SP}, ["sprite_path"])
def deselect(a):
    open_sprite(a)
    _save_sel(path_of(a), None)
    return {"success": True}


def _bounds(spr, mask):
    xs = [i % spr.width for i, v in enumerate(mask) if v]
    ys = [i // spr.width for i, v in enumerate(mask) if v]
    return min(xs), min(ys), max(xs), max(ys)


@tool("move_selection", "Move the selection mask by an offset (pixels are not moved).",
      {"sprite_path": SP, "dx": {"type": "integer"}, "dy": {"type": "integer"}}, ["sprite_path", "dx", "dy"])
def move_selection(a):
    spr = open_sprite(a)
    need(a, "dx", "dy")
    p = path_of(a)
    m = _load_sel(spr, p)
    if not m:
        raise AseError("No active selection")
    dx, dy = int(a["dx"]), int(a["dy"])
    out = bytearray(len(m))
    W, H = spr.width, spr.height
    for i, v in enumerate(m):
        if v:
            x, y = i % W + dx, i // W + dy
            if 0 <= x < W and 0 <= y < H:
                out[y * W + x] = 1
    _save_sel(p, out)
    return {"success": True}


def _clip_path():
    return os.path.join(temp_dir(), "clipboard.json")


def _copy_pixels(spr, img, mask):
    x0, y0, x1, y1 = _bounds(spr, mask)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    data = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            if mask[(y0 + y) * spr.width + x0 + x]:
                s = ((y0 + y) * spr.width + x0 + x) * 4
                data[(y * w + x) * 4:(y * w + x) * 4 + 4] = img[s:s + 4]
    with open(_clip_path(), "w") as f:
        json.dump({"w": w, "h": h, "x": x0, "y": y0, "data": base64.b64encode(zlib.compress(bytes(data))).decode()}, f)


@tool("copy_selection", "Copy the selected pixels (from the flattened first frame) to the clipboard.", {"sprite_path": SP}, ["sprite_path"])
def copy_selection(a):
    spr = open_sprite(a)
    m = _load_sel(spr, path_of(a))
    if not m:
        raise AseError("No active selection")
    _copy_pixels(spr, spr.flatten_frame(0), m)
    return {"success": True}


@tool("cut_selection", "Cut the selected pixels from a layer/frame to the clipboard.", dict(BASE), ["sprite_path", "layer_name", "frame_number"])
def cut_selection(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    m = _load_sel(spr, path_of(a))
    if not m:
        raise AseError("No active selection")
    _copy_pixels(spr, img, m)
    for i, v in enumerate(m):
        if v:
            img[i * 4:i * 4 + 4] = b"\x00\x00\x00\x00"
    commit(spr)
    return {"success": True}


@tool("paste_clipboard", "Paste clipboard pixels onto a layer/frame, optionally at (x, y).",
      dict(BASE, x={"type": "integer"}, y={"type": "integer"}), ["sprite_path", "layer_name", "frame_number"])
def paste_clipboard(a):
    spr = open_sprite(a)
    li, fi, img = target(spr, a)
    try:
        with open(_clip_path()) as f:
            c = json.load(f)
    except (OSError, ValueError):
        raise AseError("Clipboard is empty")
    data = zlib.decompress(base64.b64decode(c["data"]))
    ox = int(a["x"]) if a.get("x") is not None else c["x"]
    oy = int(a["y"]) if a.get("y") is not None else c["y"]
    for y in range(c["h"]):
        for x in range(c["w"]):
            px = data[(y * c["w"] + x) * 4:(y * c["w"] + x) * 4 + 4]
            if px[3]:
                draw.put(img, spr.width, spr.height, ox + x, oy + y, tuple(px))
    commit(spr)
    return {"success": True}


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------

def _scope(spr, scope):
    """Cel keys affected: sprite = all, layer = top image layer (all frames), cel = top image layer, first frame."""
    if scope not in ("sprite", "layer", "cel"):
        raise AseError("target must be sprite, layer or cel")
    spr.unlink_all()
    if scope == "sprite":
        return list(spr.cels)
    top = max(i for i, l in enumerate(spr.layers) if l.kind == 0)
    keys = [k for k in spr.cels if k[0] == top]
    return keys if scope == "layer" else [k for k in keys if k[1] == 0]


@tool("flip_sprite", "Flip horizontally or vertically. target: sprite (everything), layer (topmost layer), cel (topmost layer, frame 1).",
      {"sprite_path": SP, "direction": S("string", "horizontal or vertical", enum=["horizontal", "vertical"]),
       "target": S("string", "sprite, layer or cel (default sprite)", enum=["sprite", "layer", "cel"])}, ["sprite_path", "direction"])
def flip_sprite(a):
    spr = open_sprite(a)
    need(a, "direction")
    if a["direction"] not in ("horizontal", "vertical"):
        raise AseError("direction must be horizontal or vertical")
    for k in _scope(spr, a.get("target") or "sprite"):
        spr.cels[k].data = draw.flip_img(spr.cels[k].data, spr.width, spr.height, a["direction"])
    commit(spr)
    return {"success": True}


@tool("rotate_sprite", "Rotate clockwise by 90, 180 or 270 degrees. target: sprite, layer or cel.",
      {"sprite_path": SP, "angle": S("integer", "90, 180 or 270", enum=[90, 180, 270]),
       "target": S("string", "sprite, layer or cel (default sprite)", enum=["sprite", "layer", "cel"])}, ["sprite_path", "angle"])
def rotate_sprite(a):
    spr = open_sprite(a)
    need(a, "angle")
    ang = int(a["angle"])
    if ang not in (90, 180, 270):
        raise AseError("angle must be 90, 180 or 270")
    scope = a.get("target") or "sprite"
    if scope != "sprite" and ang != 180 and spr.width != spr.height:
        raise AseError("Rotating a single layer/cel by 90/270 needs a square canvas")
    keys = _scope(spr, scope)
    W, H = spr.width, spr.height
    for k in keys:
        img, _w, _h = draw.rotate_img(spr.cels[k].data, W, H, ang)
        spr.cels[k].data = img
    if scope == "sprite" and ang != 180:
        spr.width, spr.height = H, W
    commit(spr)
    return {"success": True}


@tool("scale_sprite", "Scale the sprite by factors. algorithm: nearest, bilinear, rotsprite (rotsprite falls back to nearest). Returns the new size.",
      {"sprite_path": SP, "scale_x": S("number", "X factor (>0)", exclusiveMinimum=0), "scale_y": S("number", "Y factor (>0)", exclusiveMinimum=0),
       "algorithm": S("string", "nearest, bilinear or rotsprite", enum=["nearest", "bilinear", "rotsprite"])},
      ["sprite_path", "scale_x", "scale_y"])
def scale_sprite(a):
    spr = open_sprite(a)
    need(a, "scale_x", "scale_y")
    sx, sy = float(a["scale_x"]), float(a["scale_y"])
    if sx <= 0 or sy <= 0:
        raise AseError("scale factors must be positive")
    algo = a.get("algorithm") or "nearest"
    if algo not in ("nearest", "bilinear", "rotsprite"):
        raise AseError("algorithm must be nearest, bilinear or rotsprite")
    nw, nh = max(1, int(round(spr.width * sx))), max(1, int(round(spr.height * sy)))
    if nw > 8192 or nh > 8192:
        raise AseError("Scaled size too large")
    spr.unlink_all()
    for k, cel in spr.cels.items():
        cel.data = draw.scale_img(cel.data, spr.width, spr.height, nw, nh, algo)
    spr.width, spr.height = nw, nh
    commit(spr)
    return {"success": True, "new_width": nw, "new_height": nh}


@tool("crop_sprite", "Crop the sprite to a rectangle (must lie inside the canvas).",
      {"sprite_path": SP, "x": {"type": "integer"}, "y": {"type": "integer"}, "width": {"type": "integer"}, "height": {"type": "integer"}},
      ["sprite_path", "x", "y", "width", "height"])
def crop_sprite(a):
    spr = open_sprite(a)
    need(a, "x", "y", "width", "height")
    x, y, w, h = int(a["x"]), int(a["y"]), int(a["width"]), int(a["height"])
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > spr.width or y + h > spr.height:
        raise AseError("Crop rectangle must be inside the %dx%d canvas" % (spr.width, spr.height))
    spr.unlink_all()
    for cel in spr.cels.values():
        cel.data = draw.crop_img(cel.data, spr.width, spr.height, x, y, w, h)
    spr.width, spr.height = w, h
    commit(spr)
    return {"success": True}


@tool("resize_canvas", "Resize the canvas without scaling; content is placed by anchor (center, top_left, top_right, bottom_left, bottom_right).",
      {"sprite_path": SP, "width": {"type": "integer"}, "height": {"type": "integer"},
       "anchor": S("string", "Anchor (default center)", enum=["center", "top_left", "top_right", "bottom_left", "bottom_right"])},
      ["sprite_path", "width", "height"])
def resize_canvas(a):
    spr = open_sprite(a)
    need(a, "width", "height")
    nw, nh = int(a["width"]), int(a["height"])
    if not (1 <= nw <= 4096 and 1 <= nh <= 4096):
        raise AseError("Canvas size must be between 1 and 4096 pixels")
    ox, oy = draw.anchor_offset(a.get("anchor") or "center", nw, nh, spr.width, spr.height)
    spr.unlink_all()
    for cel in spr.cels.values():
        cel.data = draw.crop_img(cel.data, spr.width, spr.height, -ox, -oy, nw, nh)
    spr.width, spr.height = nw, nh
    commit(spr)
    return {"success": True}


# ---------------------------------------------------------------------------
# import / export / analysis
# ---------------------------------------------------------------------------

def read_any_image(path):
    """Return (w, h, rgba, sprite_or_None) for png/aseprite natively, other formats via LibreSprite."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise AseError("Image file not found: %s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".aseprite", ".ase"):
        s = ase.load(path)
        return s.width, s.height, s.flatten_frame(0), s
    if ext != ".png":
        tmp = os.path.join(temp_dir(), "conv-%s.png" % os.urandom(4).hex())
        run_ls([path, "--save-as", tmp])
        if not os.path.isfile(tmp):
            raise AseError("Could not convert %s" % path)
        try:
            w, h, d = pngio.read_png(tmp)
        finally:
            os.remove(tmp)
        return w, h, d, None
    w, h, d = pngio.read_png(path)
    return w, h, d, None


@tool("import_image", "Import an image file (png, jpg, gif, bmp, aseprite) into a layer/frame at an optional position. Creates the layer if missing.",
      dict(BASE, image_path=S("string", "Path to the image"), position={"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}}),
      ["sprite_path", "image_path", "layer_name", "frame_number"])
def import_image(a):
    spr = open_sprite(a)
    need(a, "image_path", "layer_name", "frame_number")
    w, h, data, _ = read_any_image(a["image_path"])
    if a["layer_name"] not in [l.name for l in spr.layers]:
        spr.layers.append(Layer(a["layer_name"]))
    li = spr.layer_index(a["layer_name"])
    fi = spr.check_frame(int(a["frame_number"]))
    img = spr.get_image(li, fi)
    pos = a.get("position") or {}
    px, py = int(pos.get("x", 0)), int(pos.get("y", 0))
    for y in range(h):
        for x in range(w):
            s = (y * w + x) * 4
            if data[s + 3]:
                draw.put(img, spr.width, spr.height, px + x, py + y, tuple(data[s:s + 4]))
    commit(spr)
    return {"success": True}


@tool("save_as", "Save a copy of the sprite to a new .aseprite path.", {"sprite_path": SP, "output_path": S("string", "Destination .aseprite path")},
      ["sprite_path", "output_path"])
def save_as(a):
    spr = open_sprite(a)
    out = path_of(a, "output_path")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ase.save(spr, out)
    return {"success": True, "file_path": out}


def _scaled_copy(spr, k):
    c = ase.Sprite(spr.width * k, spr.height * k)
    c.palette, c.durations, c.tags = list(spr.palette), list(spr.durations), spr.tags
    c.layers = [Layer(l.name, opacity=l.opacity, blend=l.blend, kind=l.kind, child_level=l.child_level, flags=l.flags) for l in spr.layers]
    for key, cel in spr.cels.items():
        if cel.link is not None:
            c.cels[key] = Cel(None, 0, 0, cel.opacity, link=cel.link)
        else:
            c.cels[key] = Cel(draw.scale_img(cel.data, spr.width, spr.height, c.width, c.height, "nearest"), 0, 0, cel.opacity)
    return c


def _single_frame_sprite(spr, fi):
    t = ase.Sprite(spr.width, spr.height)
    t.palette = list(spr.palette)
    t.layers = [Layer("Layer 1")]
    t.cels = {(0, 0): Cel(spr.flatten_frame(fi))}
    t.durations = [spr.durations[fi]]
    return t


@tool("export_sprite", "Export to png, gif, jpg or bmp. frame_number 0 exports all frames (gif only); png/jpg/bmp export a single frame (default 1). "
      "gif/jpg/bmp are produced with LibreSprite.",
      {"sprite_path": SP, "output_path": S("string", "Output file"), "format": S("string", "png, gif, jpg, bmp", enum=["png", "gif", "jpg", "bmp"]),
       "frame_number": S("integer", "Frame (1-based); 0 = all frames for gif", minimum=0),
       "scale": S("integer", "Integer nearest-neighbour upscale factor (1, 2, 4, 8...; default 1)", minimum=1, maximum=32)},
      ["sprite_path", "output_path", "format"])
def export_sprite(a):
    spr = open_sprite(a)
    need(a, "output_path", "format")
    fmt = a["format"].lower()
    if fmt not in ("png", "gif", "jpg", "bmp"):
        raise AseError("format must be png, gif, jpg or bmp")
    out = path_of(a, "output_path")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fn = int(a.get("frame_number") or 0)
    if fn and not 1 <= fn <= spr.frame_count:
        raise AseError("Frame not found: %d" % fn)
    k = int(a.get("scale") or 1)
    if k < 1 or spr.width * k > 16384 or spr.height * k > 16384:
        raise AseError("Invalid scale factor")
    if k > 1:
        spr = _scaled_copy(spr, k)
    if fmt == "png":
        fi = (fn or 1) - 1
        pngio.write_png(out, spr.width, spr.height, spr.flatten_frame(fi))
    else:
        if fmt == "gif" and not fn:
            src = spr
        else:
            src = _single_frame_sprite(spr, (fn or 1) - 1)
        tmp = os.path.join(temp_dir(), "export-%s.aseprite" % os.urandom(4).hex())
        # keep layers separate-but-flattened: LibreSprite flattens visible layers on export
        ase.save(src, tmp)
        try:
            run_ls([tmp, "--save-as", out])
        finally:
            os.remove(tmp)
        if not os.path.isfile(out):
            raise AseError("LibreSprite did not produce %s" % out)
    return {"exported_path": out, "file_size": os.path.getsize(out)}


@tool("export_spritesheet", "Export frames as a spritesheet PNG (layout: horizontal, vertical, rows, columns, packed) with optional JSON metadata.",
      {"sprite_path": SP, "output_path": S("string", "Spritesheet .png path"),
       "layout": S("string", "horizontal, vertical, rows, columns, packed", enum=["horizontal", "vertical", "rows", "columns", "packed"]),
       "padding": S("integer", "Pixels between frames (default 0)", minimum=0), "include_json": S("boolean", "Write JSON metadata next to the PNG"),
       "scale": S("integer", "Integer nearest-neighbour upscale factor (default 1)", minimum=1, maximum=32)},
      ["sprite_path", "output_path"])
def export_spritesheet(a):
    spr = open_sprite(a)
    out = path_of(a, "output_path")
    layout = a.get("layout") or "horizontal"
    if layout not in ("horizontal", "vertical", "rows", "columns", "packed"):
        raise AseError("layout must be horizontal, vertical, rows, columns or packed")
    pad = max(0, int(a.get("padding") or 0))
    k = int(a.get("scale") or 1)
    if k < 1 or spr.width * k > 16384 or spr.height * k > 16384:
        raise AseError("Invalid scale factor")
    if k > 1:
        spr = _scaled_copy(spr, k)
        pad *= k
    n, fw, fh = spr.frame_count, spr.width, spr.height
    import math
    if layout == "horizontal":
        cols, rows = n, 1
    elif layout == "vertical":
        cols, rows = 1, n
    elif layout in ("rows", "packed"):
        cols = int(math.ceil(math.sqrt(n)))
        rows = int(math.ceil(n / float(cols)))
    else:  # columns
        rows = int(math.ceil(math.sqrt(n)))
        cols = int(math.ceil(n / float(rows)))
    sw, sh = cols * fw + (cols - 1) * pad, rows * fh + (rows - 1) * pad
    sheet = bytearray(sw * sh * 4)
    frames = []
    for fi in range(n):
        if layout == "columns":
            c, r = fi // rows, fi % rows
        else:
            c, r = fi % cols, fi // cols
        ox, oy = c * (fw + pad), r * (fh + pad)
        img = spr.flatten_frame(fi)
        for y in range(fh):
            s = y * fw * 4
            o = ((oy + y) * sw + ox) * 4
            sheet[o:o + fw * 4] = img[s:s + fw * 4]
        base = os.path.splitext(os.path.basename(path_of(a)))[0]
        frames.append({"filename": "%s %d.aseprite" % (base, fi), "frame": {"x": ox, "y": oy, "w": fw, "h": fh}, "rotated": False,
                       "trimmed": False, "spriteSourceSize": {"x": 0, "y": 0, "w": fw, "h": fh}, "sourceSize": {"w": fw, "h": fh},
                       "duration": spr.durations[fi]})
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pngio.write_png(out, sw, sh, sheet)
    res = {"spritesheet_path": out, "frame_count": n, "metadata_path": None}
    if a.get("include_json"):
        meta = {"frames": frames, "meta": {"app": "pixel-plugin (LibreSprite port)", "image": os.path.basename(out), "format": "RGBA8888",
                                           "size": {"w": sw, "h": sh}, "scale": "1",
                                           "frameTags": [{"name": t.name, "from": t.from_frame, "to": t.to_frame, "direction": t.direction} for t in spr.tags]}}
        jp = os.path.splitext(out)[0] + ".json"
        with open(jp, "w") as f:
            json.dump(meta, f, indent=2)
        res["metadata_path"] = jp
    return res


@tool("downsample_image", "Downsample an image (png, jpg, gif, bmp, aseprite) to a smaller size with a box filter and save it as a new sprite.",
      {"source_path": S("string", "Source image"), "target_width": S("integer", "Target width", minimum=1), "target_height": S("integer", "Target height", minimum=1),
       "output_path": S("string", "Output .aseprite (or .png) path (default: temp dir)")}, ["source_path", "target_width", "target_height"])
def downsample_image(a):
    need(a, "source_path", "target_width", "target_height")
    w, h, data, _ = read_any_image(a["source_path"])
    tw, th = int(a["target_width"]), int(a["target_height"])
    if tw < 1 or th < 1 or tw > w or th > h:
        raise AseError("Target size must be between 1 and the source size (%dx%d)" % (w, h))
    small = draw.box_downsample(data, w, h, tw, th)
    out = os.path.abspath(os.path.expanduser(a["output_path"])) if a.get("output_path") else os.path.join(temp_dir(), "downsampled-%s.aseprite" % os.urandom(4).hex())
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if out.lower().endswith(".png"):
        pngio.write_png(out, tw, th, small)
    else:
        s = ase.Sprite(tw, th)
        s.cels[(0, 0)] = Cel(small)
        ase.save(s, out)
    return {"output_path": out, "source_width": w, "source_height": h, "target_width": tw, "target_height": th}


@tool("analyze_reference", "Analyse a reference image: k-means palette, brightness map, edge map, composition guides and dithering zones.",
      {"reference_path": S("string", "Reference image"), "target_width": S("integer", "Analysis width", minimum=1), "target_height": S("integer", "Analysis height", minimum=1),
       "palette_size": S("integer", "Palette colours (default 16)", minimum=2, maximum=64), "brightness_levels": S("integer", "Brightness levels (default 5)", minimum=2, maximum=16),
       "edge_threshold": S("integer", "Edge threshold 0-255 (default 50)", minimum=0, maximum=255)},
      ["reference_path", "target_width", "target_height"])
def analyze_reference(a):
    need(a, "reference_path", "target_width", "target_height")
    w, h, data, _ = read_any_image(a["reference_path"])
    tw, th = int(a["target_width"]), int(a["target_height"])
    if tw < 1 or th < 1 or tw > 256 or th > 256:
        raise AseError("target_width/target_height must be between 1 and 256")
    if tw < w or th < h:
        data = draw.box_downsample(data, w, h, min(tw, w), min(th, h))
        w, h = min(tw, w), min(th, h)
    return analysis.analyze_reference(data, w, h, int(a.get("palette_size") or 16), int(a.get("brightness_levels") or 5),
                                      int(a.get("edge_threshold") or 50))
