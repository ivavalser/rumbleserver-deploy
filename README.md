# Rumble Server — установка для оператора

Оператор **не клонирует** репозиторий с исходниками. На сервер попадают только
deploy-файлы из этого репозитория. Приложение скачивается как Docker-образ из GHCR.

## Требования

- Linux VPS (Ubuntu 22.04+ / Debian 11+) с root-доступом
- Домен с A-записью на IP сервера (для HTTPS через nginx)
- Персональный ключ доступа к образу (выдаёт мейнтейнер)

## Установка (веб-визард) — рекомендуется

### С Mac: установка + автооткрытие браузера

Из репозитория `rumbleserver` (или после `publish-operator`):

```bash
./deploy/operator/install-remote.sh root@YOUR_VPS_IP
```

Скрипт запустит установщик на VPS по SSH и откроет URL **в локальном браузере**.

### Напрямую на VPS (SSH)

**Первый запуск:**

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

**Обновить установщик (сохранить прогресс)** — только остановить процесс и запустить снова. Папку **не удалять**: в ней `.env`, `.installer-state.json` и прогресс по шагам.

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

Откроется новая ссылка с новым token, но шаги подтянутся из `.env` и state-файла — попадёшь на первый незавершённый шаг (например AWS, если `.env` уже сохранён).

**Начать установку с нуля** — только если нужен полный сброс:

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true
rm -rf "$INSTALL_DIR"

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

> `rm -rf` удаляет `.env` и `.installer-state.json` — весь прогресс визарда теряется. Для обновления UI установщика он **не нужен**.

> `sudo` сбрасывает env — нужен `sudo env VAR=... bash`, не `sudo VAR=... bash`.

Открой строку `Open: http://...` **на своём компьютере** (браузер на VPS не откроется).

Переоткрыть установщик с Mac:

```bash
open "$(ssh root@YOUR_VPS_IP 'cat /root/rumbleserver/.installer-url')"
```

Скрипт:
- скачает deploy-bundle в `~/rumbleserver` (или `RUMBLE_DIR`)
- откроет порт **8800** в ufw (если активен)
- запустит веб-установщик и выведет ссылку с одноразовым токеном

Открой ссылку в браузере — пошаговый визард проведёт через:

0. **Приветствие** — что понадобится (домен, AWS, ключ доступа к образу)
1. **Preflight** — ввод домена и ключа, проверка DNS и GHCR; после успеха — выбор режима (авто/пошагово)
2. Проверку системы и firewall
3. Установку Docker
4. Вход в GHCR (ключ уже сохранён на preflight — повторный ввод не нужен)
5. Генерацию `.env` (домен уже задан — поле только для просмотра и проверки DNS)
6. **AWS S3** — bucket, IAM-пользователь, проверка доступа
7. Деплой (`docker compose pull && up -d`)
8. Создание Django Admin суперпользователя
9. Nginx + Let's Encrypt HTTPS (домен уже задан)
10. Финальную сводку

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
├── installer.sh              # bootstrap веб-установщика (на VPS)
├── install-remote.sh         # запуск с Mac + open в локальном браузере
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
