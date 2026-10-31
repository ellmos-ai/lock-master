#!/bin/sh
# Starts plugin/lock_guard.py (modes pretooluse|session|sessionend) or, for mode `mcp`, the team-board MCP server
# plugin/board_mcp.py, with a real Python 3 interpreter.
# On Windows, `python3` can be the Microsoft Store placeholder, so `py -3` is tried first
# and every candidate must actually run before it is used.
mode="$1"
script="${CLAUDE_PLUGIN_ROOT}/plugin/lock_guard.py"
[ "$mode" = "mcp" ] && script="${CLAUDE_PLUGIN_ROOT}/plugin/board_mcp.py"
if [ -n "$ELLMOS_PYTHON" ]; then
  exec "$ELLMOS_PYTHON" "$script" "$mode"
fi
if command -v py >/dev/null 2>&1 && py -3 -c "import sys" >/dev/null 2>&1; then
  exec py -3 "$script" "$mode"
fi
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
    exec "$candidate" "$script" "$mode"
  fi
done
echo "lock-master: no Python 3.10+ found (set ELLMOS_PYTHON); lock checks are inactive" >&2
exit 0
