#!/usr/bin/env bash
# Registers the arena-addon MCP server with jcode by writing/merging an entry
# into ~/.jcode/mcp.json. Safe to re-run (preserves any existing entries).
#
# Usage:
#   ./register.sh                      # register with the mock driver (works without a browser)
#   ARENA_DRIVER=cdp ./register.sh     # register to drive a real logged-in browser via CDP
set -euo pipefail

ADDON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JCODE_MCP="${JCODE_MCP:-$HOME/.jcode/mcp.json}"
DRIVER="${ARENA_DRIVER:-mock}"
PY="${PYTHON:-python3}"

mkdir -p "$(dirname "$JCODE_MCP")"

"$PY" - "$JCODE_MCP" "$ADDON_DIR" "$DRIVER" <<'PY'
import json, os, sys
path, addon_dir, driver = sys.argv[1], sys.argv[2], sys.argv[3]

# Load existing config (accept both mcpServers and legacy servers keys).
data = {}
if os.path.exists(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        data = {}

key = "mcpServers" if "mcpServers" in data else ("servers" if "servers" in data else "mcpServers")
servers = data.setdefault(key, {})
servers["arena"] = {
    "command": sys.executable,
    "args": ["-m", "arena_addon", "--driver", driver],
    "env": {
        "ARENA_DRIVER": driver,
        # Ensure 'arena_addon' is importable regardless of jcode's CWD.
        "PYTHONPATH": addon_dir + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
    },
}

with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
print(f"Registered arena-addon ({driver} driver) into {path}")
PY

echo
echo "Next: restart jcode (or reload MCP servers), then ask jcode to use"
echo "  arena_spawn / arena_wait / arena_download_workspace."
echo
echo "The server must be able to import 'arena_addon'. If you run jcode from"
echo "outside this directory, add the addon to PYTHONPATH or install it:"
echo "  cd '$ADDON_DIR' && $PY -m pip install -e ."
echo
echo "Real browser usage (cdp driver) additionally needs Playwright + a logged-in browser:"
echo "  $PY -m pip install 'playwright'"
echo "  # start Chrome/Chromium with:  --remote-debugging-port=9222"
echo "  # then point ARENA_CDP_URL=http://127.0.0.1:9222 (default)"