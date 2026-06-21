#!/bin/bash
set -e

# Установка operator-bundle без доступа к репозиторию с исходниками.
# Скачивает только deploy-файлы из публичного rumbleserver-deploy.
#
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash
#   RUMBLE_DIR=/opt/rumble curl -fsSL .../install.sh | bash

INSTALL_DIR="${RUMBLE_DIR:-$HOME/rumbleserver}"
DEPLOY_REPO="${RUMBLE_DEPLOY_REPO:-https://github.com/ivavalser/rumbleserver-deploy.git}"

echo "📦 Установка Rumble Server (operator bundle)..."

if ! command -v git &> /dev/null; then
    echo "❌ git не установлен. Установи: sudo apt-get install git"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен."
    echo "   curl -fsSL https://get.docker.com | sudo sh"
    exit 1
fi

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "🔄 Обновляю deploy-файлы в $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "⬇️  Скачиваю deploy-файлы в $INSTALL_DIR..."
    git clone --depth 1 "$DEPLOY_REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
chmod +x prod.sh install.sh 2>/dev/null || chmod +x prod.sh

mkdir -p backups

if [ ! -f .env ]; then
    cp env.example .env
    echo ""
    echo "📝 Создан .env из шаблона."
    echo "   Заполни его перед деплоем: nano $INSTALL_DIR/.env"
    echo "   Нужно: ALLOWED_HOSTS, DB_PASS, REDIS_PASSWORD, AWS"
    echo ""
else
    echo "✅ .env уже существует, не перезаписываю."
fi

echo ""
echo "✅ Установка завершена."
echo ""
echo "Дальше:"
echo "  1. nano $INSTALL_DIR/.env"
echo "  2. cd $INSTALL_DIR && ./prod.sh"
