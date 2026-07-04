#!/bin/bash
set -e

# Bootstrap веб-установщика Rumble Server (operator bundle).
#
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
#
# Другая директория / ветка:
#   curl -fsSL .../installer.sh | sudo RUMBLE_DIR=/opt/rumble RUMBLE_DEPLOY_BRANCH=feat/installer bash

INSTALL_DIR="${RUMBLE_DIR:-$HOME/rumbleserver}"
DEPLOY_REPO="${RUMBLE_DEPLOY_REPO:-https://github.com/ivavalser/rumbleserver-deploy.git}"
DEPLOY_BRANCH="${RUMBLE_DEPLOY_BRANCH:-main}"
INSTALLER_PORT="${INSTALLER_PORT:-8800}"
PID_FILE="${INSTALL_DIR}/.installer.pid"
TOKEN_FILE="${INSTALL_DIR}/.installer.token"

if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Запусти от root: curl ... | sudo bash"
    exit 1
fi

echo "🚀 Rumble Server — веб-установщик"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    OLD_TOKEN=""
    [ -f "$TOKEN_FILE" ] && OLD_TOKEN="$(cat "$TOKEN_FILE")"
    SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo ""
    echo "⚠️  Установщик уже запущен (PID $(cat "$PID_FILE"))."
    echo "   http://${SERVER_IP:-localhost}:${INSTALLER_PORT}/?token=${OLD_TOKEN}"
    exit 0
fi

if ! command -v git &>/dev/null; then
    echo "📦 Устанавливаю git..."
    apt-get update -qq
    apt-get install -y git
fi

if [ -d "$INSTALL_DIR/.git" ]; then
    REMOTE="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
    if [[ "$REMOTE" == *"rumbleserver-deploy"* ]]; then
        echo "🔄 Обновляю deploy-файлы в $INSTALL_DIR (ветка ${DEPLOY_BRANCH})..."
        git -C "$INSTALL_DIR" fetch origin "$DEPLOY_BRANCH" --depth 1 2>/dev/null || true
        git -C "$INSTALL_DIR" checkout "$DEPLOY_BRANCH" 2>/dev/null || true
        git -C "$INSTALL_DIR" pull --ff-only origin "$DEPLOY_BRANCH" 2>/dev/null || true
    elif [ -f "$INSTALL_DIR/installer/server.py" ]; then
        echo "✅ Использую локальный bundle в $INSTALL_DIR"
    else
        echo "❌ $INSTALL_DIR занят другим репозиторием."
        echo "   export RUMBLE_DIR=/opt/rumble"
        exit 1
    fi
elif [ -f "$(dirname "$0")/installer/server.py" ]; then
    INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
    echo "✅ Локальный режим: $INSTALL_DIR"
else
    echo "⬇️  Клонирую deploy-репозиторий в $INSTALL_DIR (ветка ${DEPLOY_BRANCH})..."
    git clone --depth 1 --branch "$DEPLOY_BRANCH" "$DEPLOY_REPO" "$INSTALL_DIR"
fi

# Если ранее клонировали main без installer/ — переклонируем нужную ветку
if [ ! -f "$INSTALL_DIR/installer/server.py" ] && [ -d "$INSTALL_DIR/.git" ]; then
    echo "⚠️  installer/ не найден, переклонирую ветку ${DEPLOY_BRANCH}..."
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 --branch "$DEPLOY_BRANCH" "$DEPLOY_REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

if [ ! -f installer/server.py ]; then
    echo "❌ installer/server.py не найден в $INSTALL_DIR"
    exit 1
fi

mkdir -p backups

TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "$TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    echo "🔓 Открываю порт ${INSTALLER_PORT} в ufw..."
    ufw allow "${INSTALLER_PORT}/tcp" || true
fi

export RUMBLE_INSTALL_DIR="$INSTALL_DIR"
export RUMBLE_INSTALLER_TOKEN="$TOKEN"
export RUMBLE_INSTALLER_PORT="$INSTALLER_PORT"

echo "▶️  Запускаю веб-установщик на порту ${INSTALLER_PORT}..."
nohup python3 "$INSTALL_DIR/installer/server.py" >> "$INSTALL_DIR/installer.log" 2>&1 &
echo $! > "$PID_FILE"

sleep 1
if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "❌ Не удалось запустить установщик. Лог:"
    tail -20 "$INSTALL_DIR/installer.log" 2>/dev/null || true
    exit 1
fi

SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PUBLIC_IP="$(curl -fsSL --max-time 5 https://api.ipify.org 2>/dev/null || true)"

echo ""
echo "✅ Установщик запущен!"
echo ""
echo "   Локально:  http://127.0.0.1:${INSTALLER_PORT}/?token=${TOKEN}"
[ -n "$SERVER_IP" ] && echo "   В сети:    http://${SERVER_IP}:${INSTALLER_PORT}/?token=${TOKEN}"
[ -n "$PUBLIC_IP" ] && echo "   Публично:  http://${PUBLIC_IP}:${INSTALLER_PORT}/?token=${TOKEN}"
echo ""
echo "   Лог: tail -f $INSTALL_DIR/installer.log"
echo "   Остановить: kill \$(cat $PID_FILE)"
