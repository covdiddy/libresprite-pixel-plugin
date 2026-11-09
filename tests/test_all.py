"""End-to-end test: drives server.py over stdio JSON-RPC like an MCP client and exercises every tool."""
import json, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
proc = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "server", "server.py")], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
_id = [0]
results = {"ok": [], "fail": []}


def rpc(method, params=None):
    _id[0] += 1
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def call(name, expect_error=False, **args):
    r = rpc("tools/call", {"name": name, "arguments": args})
    res = r["result"]
    text = res["content"][0]["text"]
    if res["isError"] != expect_error:
        results["fail"].append((name, text))
        print("FAIL", name, text[:200])
        return None
    results["ok"].append(name)
    return text if expect_error else json.loads(text)


rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
tl = rpc("tools/list")["result"]["tools"]
print("tools listed:", len(tl))
out = tempfile.mkdtemp(prefix="pixtest-")

s = call("create_canvas", width=32, height=32, color_mode="rgb")["file_path"]
call("get_sprite_info", sprite_path=s)
call("add_layer", sprite_path=s, layer_name="Fx")
call("add_frame", sprite_path=s, duration_ms=150)
call("add_frame", sprite_path=s, duration_ms=200)
call("set_frame_duration", sprite_path=s, frame_number=1, duration_ms=120)
B = dict(sprite_path=s, layer_name="Layer 1", frame_number=1)
call("draw_rectangle", x=2, y=2, width=12, height=12, color="#FF0000", filled=True, **B)
call("draw_rectangle", x=2, y=2, width=12, height=12, color="#FFFF00", filled=False, **B)
call("draw_circle", center_x=22, center_y=10, radius=6, color="#00AAFF", filled=True, **B)
call("draw_circle", center_x=22, center_y=10, radius=8, color="#FFFFFF", filled=False, **B)
call("draw_line", x1=0, y1=31, x2=31, y2=20, color="#00FF00", thickness=2, **B)
call("draw_contour", points=[{"x": 4, "y": 20}, {"x": 12, "y": 18}, {"x": 10, "y": 28}], color="#FF00FF", closed=True, **B)
call("draw_pixels", pixels=[{"x": 0, "y": 0, "color": "#FFFFFF"}, {"x": 1, "y": 0, "color": "#000000FF"}], **B)
call("fill_area", x=5, y=5, color="#880000", tolerance=0, **B)
for pat in ["bayer_2x2", "bayer_4x4", "bayer_8x8", "checkerboard", "grass", "water", "stone", "cloud", "brick", "dots",
            "diagonal", "cross", "noise", "horizontal_lines", "vertical_lines", "floyd_steinberg"]:
    call("draw_with_dither", region={"x": 16, "y": 20, "width": 16, "height": 12}, color1="#223355", color2="#99AACC", pattern=pat,
         density=0.5, sprite_path=s, layer_name="Fx", frame_number=2)
call("draw_with_dither", expect_error=True, region={"x": 0, "y": 0, "width": 4, "height": 4}, color1="#000000", color2="#FFFFFF", pattern="nope", sprite_path=s, layer_name="Fx", frame_number=1)
call("apply_outline", color="#000000", thickness=1, **B)
call("apply_shading", region={"x": 2, "y": 2, "width": 12, "height": 12}, palette=["#440000", "#880000", "#CC0000", "#FF4444"],
     light_direction="top_left", intensity=0.8, style="smooth", **B)
r = call("apply_auto_shading", light_direction="top_left", intensity=0.7, style="cell", hue_shift=True, **B)
print("auto shading:", r["colors_added"], "colors,", r["regions_shaded"], "regions")
r = call("suggest_antialiasing", threshold=2, **B); print("aa suggestions:", r["count"])
r = call("get_pixels", x=0, y=0, width=4, height=2, **B); print("pixels:", r["total_pixels"], r["pixels"][0])
call("create_tag", sprite_path=s, tag_name="anim", from_frame=1, to_frame=3, direction="pingpong")
call("duplicate_frame", sprite_path=s, source_frame=1, insert_after=1)
call("link_cel", sprite_path=s, layer_name="Layer 1", source_frame=1, target_frame=4)
info = call("get_sprite_info", sprite_path=s); print("info:", info["frame_count"], "frames", info["layers"], info["tags"])
call("delete_frame", sprite_path=s, frame_number=2)
call("delete_tag", sprite_path=s, tag_name="anim")
call("get_palette", sprite_path=s)
call("set_palette", sprite_path=s, colors=["#000000", "#FFFFFF", "#FF0000", "#00FF00", "#0000FF"])
call("set_palette_color", sprite_path=s, index=1, color="#EEEEEE")
call("add_palette_color", sprite_path=s, color="#123456")
call("sort_palette", sprite_path=s, method="luminance", ascending=True)
r = call("analyze_palette_harmonies", palette=["#FF0000", "#00FFFF", "#00FF00", "#0000FF", "#FF8800"]); print("harmonies:", r["temperature"], len(r["complementary"]), "comp")
call("select_rectangle", sprite_path=s, x=2, y=2, width=8, height=8, mode="replace")
call("select_ellipse", sprite_path=s, x=4, y=4, width=10, height=10, mode="add")
call("move_selection", sprite_path=s, dx=1, dy=1)
call("copy_selection", sprite_path=s)
call("cut_selection", **B)
call("paste_clipboard", x=16, y=16, **B)
call("deselect", sprite_path=s)
call("select_all", sprite_path=s)
call("deselect", sprite_path=s)
call("flip_sprite", sprite_path=s, direction="horizontal", target="sprite")
call("rotate_sprite", sprite_path=s, angle=90, target="sprite")
r = call("scale_sprite", sprite_path=s, scale_x=2, scale_y=2, algorithm="nearest"); print("scaled:", r)
call("crop_sprite", sprite_path=s, x=0, y=0, width=48, height=48)
call("resize_canvas", sprite_path=s, width=64, height=64, anchor="center")
call("flatten_layers", sprite_path=s)
r = call("quantize_palette", sprite_path=s, target_colors=6, algorithm="median_cut", dither=False); print("quant:", r["original_colors"], "->", r["quantized_colors"])
call("quantize_palette", sprite_path=s, target_colors=4, algorithm="kmeans", dither=True)
call("quantize_palette", sprite_path=s, target_colors=4, algorithm="octree", dither=False)
call("add_layer", sprite_path=s, layer_name="Top")
call("delete_layer", sprite_path=s, layer_name="Top")
call("delete_layer", expect_error=True, sprite_path=s, layer_name="Layer 1")
png = os.path.join(out, "f1.png"); call("export_sprite", sprite_path=s, output_path=png, format="png", frame_number=1)
gif = os.path.join(out, "anim.gif"); call("export_sprite", sprite_path=s, output_path=gif, format="gif", frame_number=0)
jpg = os.path.join(out, "f1.jpg"); call("export_sprite", sprite_path=s, output_path=jpg, format="jpg", frame_number=1)
bmp = os.path.join(out, "f1.bmp"); call("export_sprite", sprite_path=s, output_path=bmp, format="bmp", frame_number=1)
for lay in ["horizontal", "vertical", "rows", "columns", "packed"]:
    call("export_spritesheet", sprite_path=s, output_path=os.path.join(out, "sheet_%s.png" % lay), layout=lay, padding=1, include_json=True)
call("import_image", sprite_path=s, image_path=png, layer_name="Imported", frame_number=1, position={"x": 1, "y": 1})
call("import_image", sprite_path=s, image_path=jpg, layer_name="Imported2", frame_number=1)
ds = call("downsample_image", source_path=png, target_width=16, target_height=16, output_path=os.path.join(out, "small.aseprite")); print("downsample:", ds["output_path"])
r = call("analyze_reference", reference_path=png, target_width=32, target_height=32, palette_size=6); print("reference palette:", len(r["palette"]))
saved = os.path.join(out, "copy.aseprite"); call("save_as", sprite_path=s, output_path=saved)
print("\nOK tools exercised:", len(set(results["ok"])), "of", len(tl), "| failures:", len(results["fail"]))
missing = sorted(set(t["name"] for t in tl) - set(results["ok"]))
print("not exercised:", missing)
print("outdir:", out, "| final sprite:", s)
proc.stdin.close()
err = proc.stderr.read()
if err.strip():
    print("STDERR:", err[:1500])
sys.exit(1 if results["fail"] else 0)
