#!/usr/bin/env python3
"""Minimal MCP server (stdio, newline-delimited JSON-RPC) for the LibreSprite pixel-art plugin.

Standard library only: no packages to install.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools  # noqa: E402
from ase import AseError  # noqa: E402

SERVER_INFO = {"name": "libresprite-pixel-mcp", "version": "0.1.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "tools/list":
        lst = [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _f) in tools.TOOLS.items()]
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": lst}})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in tools.TOOLS:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown tool: %s" % name}})
            return
        try:
            res = tools.TOOLS[name][2](args)
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(res)}], "isError": False}})
        except AseError as e:
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}})
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(traceback.format_exc())
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "Internal error: %s" % e}], "isError": True}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}})
    # notifications (no id) are ignored


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})


if __name__ == "__main__":
    main()
