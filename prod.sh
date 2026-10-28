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

_rmbl_prod_domain() {
    local d allowed part
    d="$(grep -E '^TURN_REALM=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]')"
    if [ -n "$d" ]; then
        echo "$d"
        return
    fi
    allowed="$(grep -E '^ALLOWED_HOSTS=' .env 2>/dev/null | head -1 | cut -d= -f2-)"
    IFS=',' read -ra parts <<< "$allowed"
    for part in "${parts[@]}"; do
        part="${part#"${part%%[![:space:]]*}"}"
        part="${part%"${part##*[![:space:]]}"}"
        if [ -n "$part" ] && [ "$part" != "localhost" ] && [ "$part" != "127.0.0.1" ]; then
            echo "$part"
            return
        fi
    done
}

_run_setup_turn=0
_setup_turn_args=()
if [ -x ./setup-turn.sh ] && ! grep -qE '^TURN_AUTO_SETUP=false' .env 2>/dev/null; then
    if grep -qE '^COTURN_AUTH_SECRET=.+' .env 2>/dev/null || grep -qE '^TURN_AUTO_SETUP=(true|1)' .env 2>/dev/null; then
        _run_setup_turn=1
        _setup_turn_args=(--quiet)
    else
        _domain="$(_rmbl_prod_domain)"
        if [ -n "$_domain" ] && [ -f "/etc/letsencrypt/live/${_domain}/fullchain.pem" ]; then
            echo "🔀 HTTPS для ${_domain} — настраиваю TURN автоматически (TURN_AUTO_SETUP=false в .env чтобы отключить)"
            _run_setup_turn=1
            _setup_turn_args=(--auto --quiet)
        fi
    fi
fi
if [ "$_run_setup_turn" -eq 1 ]; then
    ./setup-turn.sh "${_setup_turn_args[@]}" || echo "⚠️  setup-turn.sh failed — fix TURN settings in .env and retry."
fi

COMPOSE="$DOCKER_COMPOSE --env-file .env -f docker-compose.yml"
COMPOSE_PROFILES="${COMPOSE_PROFILES:-local-db,local-redis}"
if grep -qE '^COTURN_AUTH_SECRET=.+' .env 2>/dev/null; then
    case ",${COMPOSE_PROFILES}," in
        *,turn,*) ;;
        *) COMPOSE_PROFILES="${COMPOSE_PROFILES},turn" ;;
    esac
    if [ ! -f coturn/certs/fullchain.pem ]; then
        echo "⚠️  coturn/certs/fullchain.pem missing — coturn runs without TLS (turns:5349 off until cert hook runs)."
    fi
fi

echo "⬇️  Подтягиваю образ (VERSION=${VERSION:-stable})..."
VERSION="${VERSION:-stable}" COMPOSE_PROFILES="$COMPOSE_PROFILES" $COMPOSE pull

echo "▶️  Запускаю сервисы..."
VERSION="${VERSION:-stable}" COMPOSE_PROFILES="$COMPOSE_PROFILES" $COMPOSE up -d

echo ""
echo "📊 Статус:"
VERSION="${VERSION:-stable}" COMPOSE_PROFILES="$COMPOSE_PROFILES" $COMPOSE ps

echo ""
echo "✅ Готово. Логи: COMPOSE_PROFILES=\"$COMPOSE_PROFILES\" $COMPOSE logs -f web"
