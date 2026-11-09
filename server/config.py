"""Configuration and LibreSprite discovery."""
import json
import os
import platform
import shutil
import subprocess

CONFIG_PATH = os.environ.get("PIXEL_MCP_CONFIG") or os.path.expanduser("~/.config/pixel-mcp/config.json")

CANDIDATES = {
    "Darwin": [
        "/Applications/LibreSprite.app/Contents/MacOS/libresprite",
        "/Applications/libresprite.app/Contents/MacOS/libresprite",
        os.path.expanduser("~/Applications/LibreSprite.app/Contents/MacOS/libresprite"),
        "/opt/homebrew/bin/libresprite",
        "/usr/local/bin/libresprite",
    ],
    "Linux": ["/usr/bin/libresprite", "/usr/local/bin/libresprite", "/opt/libresprite/libresprite",
              os.path.expanduser("~/.local/bin/libresprite")],
    "Windows": [r"C:\Program Files\LibreSprite\libresprite.exe", r"C:\Program Files (x86)\LibreSprite\libresprite.exe"],
}


def detect_libresprite():
    p = shutil.which("libresprite")
    if p:
        return p
    for c in CANDIDATES.get(platform.system(), []):
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def load_config():
    cfg = {}
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    path = cfg.get("libresprite_path") or cfg.get("aseprite_path")
    if not path or not os.path.isfile(path):
        path = detect_libresprite()
    cfg["libresprite_path"] = path
    cfg.setdefault("temp_dir", os.path.join(os.path.expanduser("~"), ".cache", "pixel-mcp"))
    cfg.setdefault("timeout", 60)
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def libresprite_version(path):
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        for line in (r.stdout + r.stderr).splitlines():
            if "ibre" in line or "prite" in line:
                return line.strip()
        return (r.stdout or r.stderr).strip().splitlines()[-1]
    except Exception as e:  # noqa: BLE001
        return "unknown (%s)" % e


if __name__ == "__main__":
    # Usage: config.py [path-to-libresprite]  -> detect/validate and write the config file
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else detect_libresprite()
    if not path or not os.path.isfile(path):
        print(json.dumps({"ok": False, "error": "LibreSprite not found. Pass its path: config.py /path/to/libresprite"}))
        sys.exit(1)
    cfg = load_config()
    cfg["libresprite_path"] = path
    save_config(cfg)
    print(json.dumps({"ok": True, "libresprite_path": path, "version": libresprite_version(path), "config": CONFIG_PATH}))
