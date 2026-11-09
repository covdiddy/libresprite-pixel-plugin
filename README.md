# pixel-plugin for LibreSprite

A LibreSprite port of [willibrandon/pixel-plugin](https://github.com/willibrandon/pixel-plugin) (MIT): create, animate and
export pixel art from Claude Code with natural language and slash commands.

The commands and skills are adapted from the original. The server is a new implementation: **50 tools, Python 3 standard
library only, no compiled binary, nothing to install.**

## Why it doesn't use LibreSprite scripting

The original generates Lua scripts and runs them through Aseprite. LibreSprite uses a JavaScript API, and in `--batch`
mode its `app.command.*` calls (new frame, new layer, tags, resize, flip...) silently do nothing, so frames, layers and
tags cannot be created from scripts. This port therefore reads and writes the open `.aseprite` file format directly and
uses LibreSprite only where it helps (GIF/JPG/BMP export, importing non-PNG images).
Files written by this plugin open in LibreSprite with layers, frames, durations and tags intact, and LibreSprite renders
them pixel-for-pixel like the plugin's own renderer (see `tests/`).

## Install

Requires Python 3. LibreSprite is only needed for GIF/JPG/BMP export and non-PNG imports.

```bash
claude --plugin-dir /path/to/libresprite-pixel-plugin
```

Then run `/pixel-setup` once (detects LibreSprite and writes `~/.config/pixel-mcp/config.json`). The server also
auto-detects LibreSprite without a config file.

## Tools (50)

- **Canvas**: `create_canvas`, `get_sprite_info`, `add_layer`, `delete_layer`, `flatten_layers`, `add_frame`, `delete_frame`, `duplicate_frame`, `save_as`
- **Drawing**: `draw_pixels`, `draw_line`, `draw_contour`, `draw_rectangle`, `draw_circle`, `fill_area`, `draw_with_dither` (16 patterns), `apply_outline`, `apply_shading`, `apply_auto_shading`, `suggest_antialiasing`
- **Inspection/analysis**: `get_pixels`, `analyze_reference`, `analyze_palette_harmonies`
- **Animation**: `set_frame_duration`, `create_tag`, `delete_tag`, `link_cel`
- **Palette**: `get_palette`, `set_palette`, `set_palette_color`, `add_palette_color`, `sort_palette`, `quantize_palette` (median_cut, kmeans, octree)
- **Selection**: `select_rectangle`, `select_ellipse`, `select_all`, `deselect`, `move_selection`, `copy_selection`, `cut_selection`, `paste_clipboard`
- **Transform**: `flip_sprite`, `rotate_sprite`, `scale_sprite`, `crop_sprite`, `resize_canvas`, `downsample_image`
- **Export/import**: `export_sprite` (png/gif/jpg/bmp, `scale`), `export_spritesheet` (5 layouts, padding, JSON, `scale`), `import_image`

## Differences from the original (read this)

- **RGB only.** `indexed`/`grayscale` are accepted by `create_canvas` but stored as RGB; `quantize_palette` reduces colours but does not
  convert to indexed mode. Indexed/grayscale files are read (converted to RGBA) and saved as RGB.
- **Canvas limit 4096 px** (original: 65535).
- **Re-implemented effects.** Dithering, shading, auto-shading, antialiasing suggestions and `analyze_reference` were rewritten from the
  original's descriptions, not ported line by line, so results can differ. Binary texture dither patterns ignore `density` except at 0 and 1
  (approximated); `floyd_steinberg` renders a colour1->colour2 gradient across the region.
- **`rotsprite`** in `scale_sprite` falls back to nearest-neighbour.
- **Selections** are stored in a `<sprite>.selection` sidecar file (not inside the `.aseprite`), do not restrict drawing tools,
  and `copy_selection` copies from the flattened first frame. The clipboard lives in the temp dir.
- **`flip_sprite` / `rotate_sprite`** with `target: layer|cel` act on the topmost image layer (cel = its first frame).
- Tags, layer groups and tilemaps are preserved only partly: tags are read/written; groups keep their structure but are not drawn to;
  tilemap cels and slices/user data are dropped on save.
- Frame numbers are 1-based everywhere.

## Tests

```bash
python3 tests/test_all.py        # drives the server over MCP stdio and calls all 50 tools
python3 tests/test_correct.py    # known-result checks + pixel comparison against LibreSprite's renderer (needs LibreSprite)
```

## Credits

Original plugin and pixel-mcp by Brandon Williams (MIT). Commands, skills and the tool set follow his design.
