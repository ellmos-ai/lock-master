#!/bin/sh
# Workspace with a foreign, active project lock on docs/.
set -e
mkdir -p docs
printf '# Guide\n\nInstallation steps.\n' > docs/guide.md
cat > docs/LOCK.txt <<LOCK
owner: codex@OTHER-HOST
created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
host: OTHER-HOST
expires_after: 24h
mode: hard
purpose: rewriting the guide
LOCK
