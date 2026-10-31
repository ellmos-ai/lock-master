#!/bin/sh
# Workspace with this host's team lock, on which another agent has claimed docs/guide.md for editing.
set -e
mkdir -p docs
printf '# Guide

Installation steps.
' > docs/guide.md
HOST=$(printf '%s' "${COMPUTERNAME:-$(hostname)}" | tr 'a-z' 'A-Z')
cat > "LOCK.team.$HOST.txt" <<LOCK
owner: kimi@$HOST
created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
host: $HOST
expires_after: 24h
mode: hard
purpose: parallel work on the guide

# ============================================================================
# SECTION 1 -- PRESENCE LOG
# ============================================================================
# PRESENCE:

# ============================================================================
# SECTION 2 -- FILE / FOLDER CLAIMS + QUEUE
# ============================================================================
# FILE-CLAIMS:
[codex@$HOST.99999999] EDITING  order=000001 | docs/guide.md

# ============================================================================
# SECTION 3 -- TOOL / SOFTWARE / MCP CLAIMS + QUEUE
# ============================================================================
# TOOL-CLAIMS:

# ============================================================================
# SECTION 4 -- MESSAGES / TIPS / LESSONS LEARNED
# ============================================================================
# MESSAGES:
LOCK
