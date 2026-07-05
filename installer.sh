#!/bin/bash
set -e

# Rumble Server web installer bootstrap (operator bundle).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
#
# Custom dir / branch (sudo resets env — use env):
#   curl -fsSL .../installer.sh | sudo env RUMBLE_DEPLOY_BRANCH=feat/installer bash
#
# Restart / update installer (on VPS):
#
# Keep wizard progress (.env + .installer-state.json):
#   kill $(cat /root/rumbleserver/.installer.pid) 2>/dev/null || true
#   curl -fsSL .../installer.sh | sudo env RUMBLE_DEPLOY_BRANCH=feat/installer bash
#
# Start completely over:
#   kill $(cat /root/rumbleserver/.installer.pid) 2>/dev/null || true
#   rm -rf /root/rumbleserver
#   curl -fsSL .../installer.sh | sudo env RUMBLE_DEPLOY_BRANCH=feat/installer bash

INSTALL_DIR="${RUMBLE_DIR:-$HOME/rumbleserver}"
DEPLOY_REPO="${RUMBLE_DEPLOY_REPO:-https://github.com/ivavalser/rumbleserver-deploy.git}"
DEPLOY_BRANCH="${RUMBLE_DEPLOY_BRANCH:-main}"
INSTALLER_PORT="${INSTALLER_PORT:-8800}"
PID_FILE="${INSTALL_DIR}/.installer.pid"
TOKEN_FILE="${INSTALL_DIR}/.installer.token"
URL_FILE="${INSTALL_DIR}/.installer-url"

_clone_deploy_branch() {
    local branch="$1"
    rm -rf "$INSTALL_DIR"
    echo "⬇️  Cloning $DEPLOY_REPO → $INSTALL_DIR (branch ${branch})..."
    git clone --depth 1 --branch "$branch" "$DEPLOY_REPO" "$INSTALL_DIR"
}

_ensure_deploy_files() {
    local -a branches=()
    local b
    branches+=("$DEPLOY_BRANCH")
    for b in feat/installer main; do
        if [ "$b" != "$DEPLOY_BRANCH" ]; then
            branches+=("$b")
        fi
    done
    for b in "${branches[@]}"; do
        _clone_deploy_branch "$b" && [ -f "$INSTALL_DIR/installer/server.py" ] && return 0
    done
    return 1
}

_open_browser() {
    local url="$1"
    if [ "${RUMBLE_OPEN_BROWSER:-1}" = "0" ]; then
        return 0
    fi
    if command -v open &>/dev/null; then
        open "$url" 2>/dev/null && return 0
    fi
    if [ -n "${DISPLAY:-}" ] && command -v xdg-open &>/dev/null; then
        xdg-open "$url" 2>/dev/null && return 0
    fi
    return 1
}

# Remote install: SSH session or sudo from SSH (sudo often clears SSH_CONNECTION).
_is_remote_session() {
    [ -n "${SSH_CONNECTION:-}${SSH_CLIENT:-}${SUDO_USER:-}" ]
}

_print_installer_ready() {
    local open_url="$1"
    echo ""
    echo "✅ Installer is running!"
    echo ""
    echo "Open: ${open_url}"
    echo "INSTALLER_URL=${open_url}"
    echo ""
    echo "Log:  tail -f $INSTALL_DIR/installer.log"
    echo "Stop: kill \$(cat $PID_FILE)"
    if _is_remote_session; then
        echo ""
        echo "Remote server — open the URL above in your local browser."
    elif _open_browser "$open_url"; then
        echo ""
        echo "Browser opened."
    fi
}

if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Run as root: curl ... | sudo bash"
    exit 1
fi

echo "🚀 Rumble Server — web installer"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    OLD_TOKEN=""
    [ -f "$TOKEN_FILE" ] && OLD_TOKEN="$(cat "$TOKEN_FILE")"
    if [ -f "$URL_FILE" ]; then
        OPEN_URL="$(cat "$URL_FILE")"
    else
        SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        OPEN_URL="http://${SERVER_IP:-127.0.0.1}:${INSTALLER_PORT}/?token=${OLD_TOKEN}"
    fi
    echo ""
    echo "⚠️  Installer is already running (PID $(cat "$PID_FILE"))."
    echo ""
    echo "To update the installer (keep progress):"
    echo "  kill \$(cat $PID_FILE)"
    echo "  curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/feat/installer/installer.sh | sudo env RUMBLE_DEPLOY_BRANCH=feat/installer bash"
    echo ""
    echo "To start completely over:"
    echo "  kill \$(cat $PID_FILE)"
    echo "  rm -rf $INSTALL_DIR"
    echo "  curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/feat/installer/installer.sh | sudo env RUMBLE_DEPLOY_BRANCH=feat/installer bash"
    _print_installer_ready "$OPEN_URL"
    exit 0
fi

if ! command -v git &>/dev/null; then
    echo "📦 Installing git..."
    apt-get update -qq
    apt-get install -y git
fi

_update_deploy_repo() {
    local branch="$1"
    echo "🔄 Updating deploy files in $INSTALL_DIR (branch ${branch})..."
    git -C "$INSTALL_DIR" fetch origin "$branch" --depth 1 2>/dev/null || true
    git -C "$INSTALL_DIR" checkout "$branch" 2>/dev/null || true
    if git -C "$INSTALL_DIR" rev-parse "origin/${branch}" >/dev/null 2>&1; then
        git -C "$INSTALL_DIR" reset --hard "origin/${branch}"
    else
        git -C "$INSTALL_DIR" pull --ff-only origin "$branch" 2>/dev/null || true
    fi
}

if [ -d "$INSTALL_DIR/.git" ]; then
    REMOTE="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
    if [[ "$REMOTE" == *"rumbleserver-deploy"* ]]; then
        _update_deploy_repo "$DEPLOY_BRANCH"
    elif [ -f "$INSTALL_DIR/installer/server.py" ]; then
        echo "✅ Using local bundle at $INSTALL_DIR"
    else
        echo "❌ $INSTALL_DIR is occupied by another repository."
        echo "   export RUMBLE_DIR=/opt/rumble"
        exit 1
    fi
elif [ -f "$(dirname "$0")/installer/server.py" ]; then
    INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
    echo "✅ Local mode: $INSTALL_DIR"
else
    _ensure_deploy_files || true
fi

if [ ! -f "$INSTALL_DIR/installer/server.py" ]; then
    if [ -d "$INSTALL_DIR/.git" ]; then
        REMOTE="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
        if [[ "$REMOTE" == *"rumbleserver-deploy"* ]]; then
            _update_deploy_repo "$DEPLOY_BRANCH"
        fi
    fi
fi

if [ ! -f "$INSTALL_DIR/installer/server.py" ]; then
    echo "⚠️  installer/ not found, trying other branches..."
    _ensure_deploy_files || true
fi

cd "$INSTALL_DIR" 2>/dev/null || true

if [ ! -f "$INSTALL_DIR/installer/server.py" ]; then
    echo "❌ installer/server.py not found in $INSTALL_DIR"
    echo ""
    echo "   Specify branch explicitly (sudo clears env vars):"
    echo "   curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/feat/installer/installer.sh | sudo env RUMBLE_DEPLOY_BRANCH=feat/installer bash"
    echo ""
    echo "   Or manually:"
    echo "   rm -rf $INSTALL_DIR"
    echo "   git clone --depth 1 --branch feat/installer $DEPLOY_REPO $INSTALL_DIR"
    echo "   $INSTALL_DIR/installer.sh"
    exit 1
fi

cd "$INSTALL_DIR"

mkdir -p backups

TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "$TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    echo "🔓 Opening port ${INSTALLER_PORT} in ufw..."
    ufw allow "${INSTALLER_PORT}/tcp" || true
fi

export RUMBLE_INSTALL_DIR="$INSTALL_DIR"
export RUMBLE_INSTALLER_TOKEN="$TOKEN"
export RUMBLE_INSTALLER_PORT="$INSTALLER_PORT"
export PATH="/usr/local/bin:/usr/local/aws-cli/v2/current/bin:${PATH:-/usr/bin:/bin}"

echo "▶️  Starting web installer on port ${INSTALLER_PORT}..."
nohup python3 "$INSTALL_DIR/installer/server.py" >> "$INSTALL_DIR/installer.log" 2>&1 &
echo $! > "$PID_FILE"

sleep 1
if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "❌ Failed to start installer. Log:"
    tail -20 "$INSTALL_DIR/installer.log" 2>/dev/null || true
    exit 1
fi

SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PUBLIC_IP="$(curl -fsSL --max-time 5 https://api.ipify.org 2>/dev/null || true)"

# Prefer public IP for browser URL when available
if [ -n "$PUBLIC_IP" ]; then
    OPEN_URL="http://${PUBLIC_IP}:${INSTALLER_PORT}/?token=${TOKEN}"
elif [ -n "$SERVER_IP" ]; then
    OPEN_URL="http://${SERVER_IP}:${INSTALLER_PORT}/?token=${TOKEN}"
else
    OPEN_URL="http://127.0.0.1:${INSTALLER_PORT}/?token=${TOKEN}"
fi

echo "$OPEN_URL" > "$URL_FILE"
chmod 600 "$URL_FILE"

_print_installer_ready "$OPEN_URL"
