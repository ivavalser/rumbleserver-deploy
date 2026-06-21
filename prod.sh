#!/bin/bash
set -e

# Деплой и обновление Rumble Server из готового образа GHCR.
# Использование:
#   ./prod.sh                  # версия stable
#   VERSION=1.0.0 ./prod.sh    # конкретная версия / откат
#   RUMBLE_KEY=xxx ./prod.sh   # перелогиниться новым ключом

REGISTRY="ghcr.io"
GHCR_USER="${GHCR_USER:-rmbldeploy}"

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$INSTALL_DIR"

echo "🚀 Rumble Server (prod, образ из GHCR)..."

if docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    echo "❌ Docker Compose не установлен."
    echo "   sudo apt-get install docker-compose-plugin"
    exit 1
fi

if [ ! -f .env ]; then
    echo "❌ Нет .env. Создай его:"
    echo "   cp env.example .env && nano .env"
    exit 1
fi

if ! grep -q "^SECRET_KEY=" .env || grep -q "SECRET_KEY=your-secret-key" .env; then
    echo "🔑 Генерирую SECRET_KEY..."
    SECRET_KEY=$(openssl rand -base64 50 | tr -d "=+/" | cut -c1-50)
    if grep -q "^SECRET_KEY=" .env; then
        awk -v key="$SECRET_KEY" '/^SECRET_KEY=/ {print "SECRET_KEY=" key; next} {print}' .env > .env.tmp && mv .env.tmp .env
    else
        echo "SECRET_KEY=$SECRET_KEY" >> .env
    fi
    echo "✅ SECRET_KEY сгенерирован"
fi

mkdir -p backups

if [ -n "$RUMBLE_KEY" ]; then
    echo "🔐 Логин в $REGISTRY как $GHCR_USER..."
    echo "$RUMBLE_KEY" | docker login "$REGISTRY" -u "$GHCR_USER" --password-stdin
elif ! grep -q "$REGISTRY" "${DOCKER_CONFIG:-$HOME/.docker}/config.json" 2>/dev/null; then
    echo "🔐 Нет доступа к $REGISTRY. Вставь ключ, выданный мейнтейнером."
    read -rsp "   Ключ: " RUMBLE_KEY; echo
    echo "$RUMBLE_KEY" | docker login "$REGISTRY" -u "$GHCR_USER" --password-stdin
fi

COMPOSE="$DOCKER_COMPOSE --env-file .env -f docker-compose.yml"

echo "⬇️  Подтягиваю образ (VERSION=${VERSION:-stable})..."
VERSION="${VERSION:-stable}" $COMPOSE pull

echo "▶️  Запускаю сервисы..."
VERSION="${VERSION:-stable}" $COMPOSE up -d

echo ""
echo "📊 Статус:"
$COMPOSE ps

echo ""
echo "✅ Готово. Логи: $COMPOSE logs -f web"
