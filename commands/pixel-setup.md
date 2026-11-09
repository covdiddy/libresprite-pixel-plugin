---
description: Detect LibreSprite and write the plugin configuration
argument-hint: [libresprite-path]
allowed-tools: Read, Bash
---

## /pixel-setup - Plugin Configuration

Detect the LibreSprite installation and write `~/.config/pixel-mcp/config.json`.

### Usage

```
/pixel-setup [libresprite-path]
```

**libresprite-path** (optional): path to the LibreSprite executable. If omitted, auto-detection is used.

### Implementation

1. Run the detection/validation script (it needs only Python 3, no packages):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/server/config.py" [libresprite-path]
   ```

   It prints JSON like `{"ok": true, "libresprite_path": "...", "version": "LibreSprite 1.3", "config": "..."}`.

2. If `ok` is false, tell the user LibreSprite was not found and ask for the path
   (macOS: `/Applications/LibreSprite.app/Contents/MacOS/libresprite`).

3. Confirm the MCP server is reachable by calling `get_sprite_info`/`create_canvas`
   (for example `create_canvas` 16x16 rgb) and report success or the error.

### Auto-detection paths

- macOS: `/Applications/LibreSprite.app/Contents/MacOS/libresprite`, `~/Applications/LibreSprite.app/...`, `/opt/homebrew/bin/libresprite`, `/usr/local/bin/libresprite`
- Linux: `/usr/bin/libresprite`, `/usr/local/bin/libresprite`, `/opt/libresprite/libresprite`, `~/.local/bin/libresprite`
- Windows: `C:\Program Files\LibreSprite\libresprite.exe`, `C:\Program Files (x86)\LibreSprite\libresprite.exe`

### Configuration file

```json
{
  "libresprite_path": "/Applications/LibreSprite.app/Contents/MacOS/libresprite",
  "temp_dir": "~/.cache/pixel-mcp",
  "timeout": 60
}
```

- `libresprite_path`: LibreSprite executable (used only for GIF/JPG/BMP export and importing non-PNG images)
- `temp_dir`: where new sprites from `create_canvas` are stored (default `~/.cache/pixel-mcp`)
- `timeout`: seconds allowed for a LibreSprite call (default 60)

LibreSprite is **optional** for most tools: sprites are edited as `.aseprite` files directly, and PNG/spritesheet export is native.

### Notes

- Setup only needs to be run once; re-run if the install path changes.
- Set `PIXEL_MCP_CONFIG` to use a different config file location.
