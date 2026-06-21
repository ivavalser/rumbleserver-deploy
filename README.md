# Rumble Server — установка для оператора

Оператор **не клонирует** репозиторий с исходниками. На сервер попадают только
deploy-файлы из этого репозитория. Приложение скачивается как Docker-образ из GHCR.

## Требования

- Linux VPS с Docker и Docker Compose plugin
- Домен с A-записью на IP сервера (для HTTPS через nginx/caddy)
- Персональный ключ доступа к образу (выдаёт мейнтейнер)

## Установка

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash
```

Скрипт создаст `~/rumbleserver/` с `prod.sh`, `docker-compose.yml`, `env.example`.

Другая директория:

```bash
RUMBLE_DIR=/opt/rumble curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash
```

## Первый запуск

```bash
nano ~/rumbleserver/.env    # ALLOWED_HOSTS, DB_PASS, REDIS_PASSWORD, AWS
cd ~/rumbleserver
./prod.sh                   # спросит ключ доступа к образу при первом запуске
```

## Обновление

```bash
cd ~/rumbleserver
./prod.sh                   # stable
VERSION=1.0.0 ./prod.sh     # конкретная версия
```

Обновить deploy-скрипты (без исходников приложения):

```bash
cd ~/rumbleserver && git pull
```

## Полезные команды

```bash
cd ~/rumbleserver
docker compose --env-file .env ps
docker compose --env-file .env logs -f web
docker compose --env-file .env down    # volumes с БД сохраняются
```

## Миграция со старого деплоя (git pull + update.sh)

```bash
# 1. Установить operator bundle
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash

# 2. Перенести старый .env
cp /path/to/old/rumbleserver/.env ~/rumbleserver/.env

# 3. Остановить старые контейнеры (данные в volumes сохранятся)
cd /path/to/old/rumbleserver
docker compose --env-file .env -f deploy/docker-compose.yml down

# 4. Запустить из образа
cd ~/rumbleserver && ./prod.sh
```

Старый clone с исходниками можно удалить после проверки.
