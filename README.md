# RMBL Server — operator deploy bundle

This repository contains deployment scripts and a **web installer** for **RMBL Server** (Rumble messenger backend).

Operators **do not clone** the application source code. Only deploy files from this repository are placed on the server. The application itself is pulled as a pre-built Docker image from GHCR.

<details>
<summary><strong>Installation Guide (EN)</strong></summary>

## Requirements

- Linux VPS (Ubuntu 22.04+ / Debian 11+) with root access
- A domain with an A record pointing to the server IP (for HTTPS via nginx)
- A personal image access key (issued by the maintainer)

## Installation (web wizard) — recommended

### From Mac: remote install + auto-open browser

From the `rumbleserver` source repo (or after `publish-operator`):

```bash
./deploy/operator/install-remote.sh root@YOUR_VPS_IP
```

The script starts the installer on the VPS over SSH and opens the URL **in your local browser**.

### Directly on the VPS (SSH)

**First run:**

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

**Update the installer (keep progress)** — stop the process and run again. **Do not delete** the install directory: it holds `.env`, `.installer-state.json`, and wizard progress.

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

A new link with a new token will appear, but steps are restored from `.env` and the state file — you land on the first incomplete step (e.g. AWS if `.env` is already saved).

**Start from scratch** — only for a full reset:

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true
rm -rf "$INSTALL_DIR"

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

> `rm -rf` removes `.env` and `.installer-state.json` — all wizard progress is lost. It is **not needed** to update the installer UI.

> `sudo` drops environment variables — use `sudo env VAR=... bash`, not `sudo VAR=... bash`.

Open the `Open: http://...` line **on your own computer** (the browser on the VPS will not open it).

Re-open the installer from Mac:

```bash
open "$(ssh root@YOUR_VPS_IP 'cat /root/rumbleserver/.installer-url')"
```

The script will:

- download the deploy bundle to `~/rumbleserver` (or `RUMBLE_DIR`)
- open port **8800** in ufw (if active)
- start the web installer and print a one-time token URL

Open the link in your browser — the step-by-step wizard walks you through:

0. **Welcome** — what you need (domain, AWS, image access key)
1. **Preflight** — enter domain and key, verify DNS and GHCR; after success — choose mode (auto/step-by-step)
2. System and firewall checks
3. Docker installation
4. GHCR login (key already saved at preflight — no re-entry needed)
5. `.env` generation (domain already set — field is read-only with DNS check)
6. **AWS S3** — bucket, IAM user, access verification
7. Deploy (`docker compose pull && up -d`)
8. Django Admin superuser creation
9. Nginx + Let's Encrypt HTTPS (domain already set)
10. Final summary

At each step the installer **tries to do the work itself first**. If something fails — it shows exactly what to run and in which directory. The **“I did it — verify”** button re-checks the step.

Custom install directory:

```bash
curl -fsSL .../installer.sh | sudo RUMBLE_DIR=/opt/rumble bash
```

Installer log: `tail -f ~/rumbleserver/installer.log`

## Installation (manual) — fallback

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash
```

The script creates `~/rumbleserver/` with `prod.sh`, `docker-compose.yml`, `env.example`.

```bash
nano ~/rumbleserver/.env    # ALLOWED_HOSTS, DB_PASS, REDIS_PASSWORD, AWS
cd ~/rumbleserver
./prod.sh                   # prompts for image access key on first run
```

For more detail see [DEPLOY.md](https://github.com/ivavalser/rumbleserver/blob/master/deploy/DEPLOY.md) (source-based deploy) or the nginx/HTTPS sections there.

## Updates

```bash
cd ~/rumbleserver
./prod.sh                   # stable
VERSION=1.0.0 ./prod.sh     # specific version
```

Update deploy scripts (without application source):

```bash
cd ~/rumbleserver && git pull
```

## Useful commands

```bash
cd ~/rumbleserver
docker compose --env-file .env --profile local-db --profile local-redis ps
docker compose --env-file .env --profile local-db --profile local-redis logs -f web
docker compose --env-file .env --profile local-db --profile local-redis down    # DB volumes are preserved
```

## External PostgreSQL / Redis

In the web installer, on the `.env` step enable “External PostgreSQL” or “External Redis” — the corresponding containers will not start; external hosts are written to `.env`.

Manual setup — see “External DB/Redis” in [DEPLOY.md](https://github.com/ivavalser/rumbleserver/blob/master/deploy/DEPLOY.md).

## Migration from legacy deploy (git pull + update.sh)

If the server already has `~/rumbleserver` with source code — **do not overwrite it**.
Install the operator bundle in a separate directory:

```bash
export RUMBLE_DIR=/opt/rumble
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/install.sh | bash
cp ~/rumbleserver/.env /opt/rumble/.env
cd ~/rumbleserver && docker compose --env-file .env -f deploy/docker-compose.yml down
cd /opt/rumble && ./prod.sh
```

After verification you can remove the old source clone: `rm -rf ~/rumbleserver`

## Operator bundle layout

```
~/rumbleserver/
├── installer.sh              # web installer bootstrap (on VPS)
├── install-remote.sh         # run from Mac + open in local browser
├── installer/
│   ├── server.py             # HTTP API + UI
│   ├── steps.py              # step logic
│   ├── index.html            # web wizard
│   └── nginx.conf.template
├── install.sh                # manual bundle install
├── prod.sh                   # deploy / update image
├── docker-compose.yml
├── env.example
└── .env                      # created by the installer
```

</details>

<details>
<summary><strong>Инструкция по установке (RU)</strong></summary>

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

Подробная инструкция — в [DEPLOY.md](https://github.com/ivavalser/rumbleserver/blob/master/deploy/DEPLOY.md) (для деплоя из исходников) или разделы nginx/HTTPS там же.

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

Вручную — см. раздел «Использование внешней БД/Redis» в [DEPLOY.md](https://github.com/ivavalser/rumbleserver/blob/master/deploy/DEPLOY.md).

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

</details>
