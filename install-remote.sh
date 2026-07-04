#!/bin/bash
set -e

# Run the web installer on a remote VPS and open it in the local browser (Mac/Linux desktop).
#
# Usage:
#   ./install-remote.sh root@167.233.165.200
#   ./install-remote.sh root@167.233.165.200 feat/installer
#
# To run a fresh installer on VPS (stop old process, remove dir) — SSH first:
#   kill $(cat /root/rumbleserver/.installer.pid) 2>/dev/null || true
#   rm -rf /root/rumbleserver
#
# To update installer UI but keep progress — only kill + rerun curl (no rm -rf).
#
# Requires: ssh, curl on the remote host, open (macOS) or xdg-open (Linux desktop)

VPS="${1:?Usage: $0 user@host [deploy-branch]}"
BRANCH="${2:-main}"
INSTALLER_RAW="https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/${BRANCH}/installer.sh"

TMPLOG="$(mktemp)"
trap 'rm -f "$TMPLOG"' EXIT

echo "🚀 Starting installer on ${VPS} (branch ${BRANCH})..."
ssh -t "$VPS" "curl -fsSL '${INSTALLER_RAW}' | sudo env RUMBLE_DEPLOY_BRANCH='${BRANCH}' bash" | tee "$TMPLOG"

URL="$(grep '^INSTALLER_URL=' "$TMPLOG" | tail -1 | cut -d= -f2-)"
if [ -z "$URL" ]; then
    URL="$(grep '^Open: ' "$TMPLOG" | tail -1 | sed 's/^Open: //')"
fi

if [ -z "$URL" ]; then
    echo "❌ Could not find installer URL in output."
    exit 1
fi

echo ""
if command -v open &>/dev/null; then
    open "$URL"
    echo "✅ Opened in browser: $URL"
elif [ -n "${DISPLAY:-}" ] && command -v xdg-open &>/dev/null; then
    xdg-open "$URL"
    echo "✅ Opened in browser: $URL"
else
    echo "Open in browser: $URL"
fi
