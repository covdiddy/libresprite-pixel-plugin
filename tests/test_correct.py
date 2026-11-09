"""Deterministic correctness checks (known pixel results) + pixel-for-pixel comparison against LibreSprite's own renderer."""
import os, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import ase, draw, pngio, tools
from config import load_config

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + str(detail) if (detail and not cond) else ""))
    if not cond: fails.append(name)

def T(name, **a): return tools.TOOLS[name][2](a)
def px(spr, li, fi, x, y): return tuple(spr.get_image(li, fi)[(y * spr.width + x) * 4:(y * spr.width + x) * 4 + 4])

d = tempfile.mkdtemp(prefix="pixcorrect-")
s = T("create_canvas", width=8, height=6, color_mode="rgb")["file_path"]
B = dict(sprite_path=s, layer_name="Layer 1", frame_number=1)

# primitives
T("draw_pixels", pixels=[{"x": 1, "y": 2, "color": "#FF0000"}, {"x": 7, "y": 5, "color": "#00FF0080"}], **B)
sp = ase.load(s); check("pixel", px(sp, 0, 0, 1, 2) == (255, 0, 0, 255)); check("pixel alpha", px(sp, 0, 0, 7, 5) == (0, 255, 0, 128))
T("draw_rectangle", x=0, y=0, width=3, height=2, color="#0000FF", filled=True, **B)
sp = ase.load(s); check("rect filled", all(px(sp, 0, 0, x, y) == (0, 0, 255, 255) for x in range(3) for y in range(2)) and px(sp, 0, 0, 3, 0)[3] == 0)
T("draw_line", x1=0, y1=5, x2=5, y2=5, color="#FFFFFF", thickness=1, **B)
sp = ase.load(s); check("line", all(px(sp, 0, 0, x, 5) == (255, 255, 255, 255) for x in range(6)))
T("fill_area", x=7, y=0, color="#112233", tolerance=0, **B)
sp = ase.load(s); check("flood fill reaches far corner", px(sp, 0, 0, 7, 0) == (0x11, 0x22, 0x33, 255) and px(sp, 0, 0, 1, 2) == (255, 0, 0, 255))
c = tools.TOOLS  # circle symmetry
s2 = T("create_canvas", width=21, height=21, color_mode="rgb")["file_path"]
T("draw_circle", sprite_path=s2, layer_name="Layer 1", frame_number=1, center_x=10, center_y=10, radius=8, color="#FFFFFF", filled=True)
sp2 = ase.load(s2); img = sp2.get_image(0, 0)
sym = all(px(sp2, 0, 0, x, y) == px(sp2, 0, 0, 20 - x, y) == px(sp2, 0, 0, x, 20 - y) for x in range(21) for y in range(21))
check("filled circle symmetric", sym); check("circle centre/outside", px(sp2, 0, 0, 10, 10)[3] == 255 and px(sp2, 0, 0, 0, 0)[3] == 0)

# transforms: known geometry
s3 = T("create_canvas", width=4, height=2, color_mode="rgb")["file_path"]
T("draw_pixels", sprite_path=s3, layer_name="Layer 1", frame_number=1, pixels=[{"x": 0, "y": 0, "color": "#FF0000"}, {"x": 3, "y": 1, "color": "#00FF00"}])
T("flip_sprite", sprite_path=s3, direction="horizontal", target="sprite")
sp3 = ase.load(s3); check("flip h", px(sp3, 0, 0, 3, 0) == (255, 0, 0, 255) and px(sp3, 0, 0, 0, 1) == (0, 255, 0, 255))
T("flip_sprite", sprite_path=s3, direction="horizontal", target="sprite")
T("rotate_sprite", sprite_path=s3, angle=90, target="sprite")
sp3 = ase.load(s3); check("rotate 90 size", (sp3.width, sp3.height) == (2, 4))
check("rotate 90 cw pixels", px(sp3, 0, 0, 1, 0) == (255, 0, 0, 255) and px(sp3, 0, 0, 0, 3) == (0, 255, 0, 255), [px(sp3, 0, 0, 1, 0), px(sp3, 0, 0, 0, 3)])
T("crop_sprite", sprite_path=s3, x=0, y=0, width=2, height=2)
sp3 = ase.load(s3); check("crop", (sp3.width, sp3.height) == (2, 2) and px(sp3, 0, 0, 1, 0) == (255, 0, 0, 255))
T("resize_canvas", sprite_path=s3, width=4, height=4, anchor="bottom_right")
sp3 = ase.load(s3); check("resize canvas anchor", px(sp3, 0, 0, 3, 2) == (255, 0, 0, 255), px(sp3, 0, 0, 3, 2))
r = T("scale_sprite", sprite_path=s3, scale_x=2, scale_y=2, algorithm="nearest")
sp3 = ase.load(s3); check("scale nearest", (r["new_width"], r["new_height"]) == (8, 8) and px(sp3, 0, 0, 6, 4) == (255, 0, 0, 255) and px(sp3, 0, 0, 7, 5) == (255, 0, 0, 255))

# animation structure
s4 = T("create_canvas", width=2, height=2, color_mode="rgb")["file_path"]
for dur in (200, 300): T("add_frame", sprite_path=s4, duration_ms=dur)
for f, col in enumerate(("#FF0000", "#00FF00", "#0000FF"), 1):
    T("draw_pixels", sprite_path=s4, layer_name="Layer 1", frame_number=f, pixels=[{"x": 0, "y": 0, "color": col}])
T("create_tag", sprite_path=s4, tag_name="all", from_frame=1, to_frame=3, direction="forward")
T("duplicate_frame", sprite_path=s4, source_frame=2, insert_after=1)   # frames: R G G' B
sp4 = ase.load(s4)
check("dup frame count", sp4.frame_count == 4)
check("dup order", [px(sp4, 0, i, 0, 0)[:3] for i in range(4)] == [(255, 0, 0), (0, 255, 0), (0, 255, 0), (0, 0, 255)])
check("dup durations (copy keeps source duration)", sp4.durations == [100, 200, 200, 300], sp4.durations)
check("tag extended", (sp4.tags[0].from_frame, sp4.tags[0].to_frame) == (0, 3), (sp4.tags[0].from_frame, sp4.tags[0].to_frame))
T("link_cel", sprite_path=s4, layer_name="Layer 1", source_frame=1, target_frame=4)  # last frame now shows frame 1
T("delete_frame", sprite_path=s4, frame_number=2)
sp4 = ase.load(s4)
check("delete frame count", sp4.frame_count == 3)
check("link survives delete", [px(sp4, 0, i, 0, 0)[:3] for i in range(3)] == [(255, 0, 0), (0, 255, 0), (255, 0, 0)], [px(sp4, 0, i, 0, 0)[:3] for i in range(3)])
check("tag shrunk", (sp4.tags[0].from_frame, sp4.tags[0].to_frame) == (0, 2))

# LibreSprite renders my file identically (multi-layer, alpha, opacity, linked cel, tags)
s5 = T("create_canvas", width=12, height=10, color_mode="rgb")["file_path"]
T("add_layer", sprite_path=s5, layer_name="Top")
T("add_frame", sprite_path=s5, duration_ms=120)
T("draw_rectangle", sprite_path=s5, layer_name="Layer 1", frame_number=1, x=0, y=0, width=12, height=10, color="#204080", filled=True)
T("draw_circle", sprite_path=s5, layer_name="Top", frame_number=1, center_x=6, center_y=5, radius=4, color="#FFCC00", filled=True)
T("draw_pixels", sprite_path=s5, layer_name="Top", frame_number=1, pixels=[{"x": 1, "y": 1, "color": "#FF000080"}, {"x": 2, "y": 1, "color": "#00FF0040"}])
T("link_cel", sprite_path=s5, layer_name="Layer 1", source_frame=1, target_frame=2)
T("draw_line", sprite_path=s5, layer_name="Top", frame_number=2, x1=0, y1=9, x2=11, y2=0, color="#FFFFFF", thickness=1)
mine = ase.load(s5)
sheet = os.path.join(d, "ls_sheet.png")
tools.run_ls([s5, "--sheet", sheet, "--sheet-type", "horizontal"])
lw, lh, ld = pngio.read_png(sheet)
mw, mh, md = lw, lh, None
diffs = 0
for fi in (0, 1):
    mine_f = mine.flatten_frame(fi)
    for y in range(mine.height):
        for x in range(mine.width):
            a = tuple(mine_f[(y * mine.width + x) * 4:(y * mine.width + x) * 4 + 4])
            o = (y * lw + fi * mine.width + x) * 4
            b = tuple(ld[o:o + 4])
            if a[3] == 0 and b[3] == 0: continue
            if any(abs(a[i] - b[i]) > 2 for i in range(4)): diffs += 1
check("LibreSprite render == mine (2 frames, 2 layers, alpha)", diffs == 0, "%d differing pixels, sheet %dx%d" % (diffs, lw, lh))

# self round trip through LibreSprite: LibreSprite re-saves my file, I re-read it
resaved = os.path.join(d, "resaved.aseprite")
tools.run_ls([s5, "--save-as", resaved])
rs = ase.load(resaved)
check("LibreSprite re-save readable", rs.frame_count == mine.frame_count and [l.name for l in rs.layers] == [l.name for l in mine.layers], (rs.frame_count, [l.name for l in rs.layers]))
check("LibreSprite re-save pixels", all(rs.flatten_frame(i) == mine.flatten_frame(i) for i in range(2)))

print("\nFAILED:" if fails else "\nALL CORRECTNESS CHECKS PASSED", fails if fails else "")
sys.exit(1 if fails else 0)
