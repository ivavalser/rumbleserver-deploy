# Rumble Server — установка для оператора

Оператор **не клонирует** репозиторий с исходниками. На сервер попадают только
deploy-файлы из этого репозитория. Приложение скачивается как Docker-образ из GHCR.

## Требования

- Linux VPS (Ubuntu 22.04+ / Debian 11+) с root-доступом
- Домен с A-записью на IP сервера (для HTTPS через nginx)
- Персональный ключ доступа к образу (выдаёт мейнтейнер)

## Установка (веб-визард) — рекомендуется

Одна команда на чистом сервере:

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

Скрипт:
- скачает deploy-bundle в `~/rumbleserver` (или `RUMBLE_DIR`)
- откроет порт **8800** в ufw (если активен)
- запустит веб-установщик и выведет ссылку с одноразовым токеном

Открой ссылку в браузере — пошаговый визард проведёт через:

1. Проверку системы и firewall
2. Установку Docker
3. Вход в GHCR (ключ от мейнтейнера)
4. Генерацию `.env` (пароли БД/Redis, домен, опционально внешние БД/Redis и AWS)
5. Деплой (`docker compose pull && up -d`)
6. Создание Django Admin суперпользователя
7. Nginx + Let's Encrypt HTTPS
8. Финальную сводку

На каждом шаге установщик **сначала пробует сделать сам**. Если не получилось — показывает, что именно выполнить и в какой директории. Кнопка **«Я сделал — проверить»** перепроверяет шаг.

Другая директория:

```bash
curl -fsSL .../installer.sh | sudo RUMBLE_DIR=/opt/rumble bash
```

Лог установщика: `tail -f ~/rumbleserver/installer.log`

## Установка (ручная) — fallback

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash
```

Скрипт создаст `~/rumbleserver/` с `prod.sh`, `docker-compose.yml`, `env.example`.

```bash
nano ~/rumbleserver/.env    # ALLOWED_HOSTS, DB_PASS, REDIS_PASSWORD, AWS
cd ~/rumbleserver
./prod.sh                   # спросит ключ доступа к образу при первом запуске
```

Подробная инструкция — в [DEPLOY.md](../DEPLOY.md) (для деплоя из исходников) или разделы nginx/HTTPS там же.

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
docker compose --env-file .env --profile local-db --profile local-redis ps
docker compose --env-file .env --profile local-db --profile local-redis logs -f web
docker compose --env-file .env --profile local-db --profile local-redis down    # volumes с БД сохраняются
```

## Внешняя PostgreSQL / Redis

В веб-установщике на шаге `.env` включи «Внешняя PostgreSQL» или «Внешний Redis» — соответствующие контейнеры не поднимутся, в `.env` пропишутся внешние хосты.

Вручную — см. раздел «Использование внешней БД/Redis» в [DEPLOY.md](../DEPLOY.md).

## Миграция со старого деплоя (git pull + update.sh)

На сервере уже есть `~/rumbleserver` с исходниками — **не перезаписывай его**.
Поставь operator-bundle в отдельную папку:

```bash
export RUMBLE_DIR=/opt/rumble
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash
cp ~/rumbleserver/.env /opt/rumble/.env
cd ~/rumbleserver && docker compose --env-file .env -f deploy/docker-compose.yml down
cd /opt/rumble && ./prod.sh
```

Старый clone с исходниками можно удалить после проверки: `rm -rf ~/rumbleserver`

## Структура operator bundle

```
~/rumbleserver/
├── installer.sh              # bootstrap веб-установщика
├── installer/
│   ├── server.py             # HTTP API + UI
│   ├── steps.py              # логика шагов
│   ├── index.html            # веб-визард
│   └── nginx.conf.template
├── install.sh                # ручная установка bundle
├── prod.sh                   # деплой/обновление образа
├── docker-compose.yml
├── env.example
└── .env                      # создаётся установщиком
```
