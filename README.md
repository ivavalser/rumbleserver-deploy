# RMBL Server — operator deploy bundle

This repository contains deployment scripts and a **web installer** for **RMBL Server**.

Operators **do not clone** the application source code. Only deploy files from this repository are placed on the server. The application itself is pulled as a pre-built Docker image from GHCR.

<details>
<summary><strong>Installation Guide (EN)</strong></summary>

## Requirements

- Linux VPS (Ubuntu 22.04+ / Debian 11+) with root access
- A domain with an A record pointing to the server IP (for HTTPS via nginx)
- **S3-compatible object storage** for encrypted file uploads (photos, audio, avatars)
  - **AWS S3** is recommended — the installer can create the bucket and IAM user automatically
  - Any other S3-compatible provider also works (MinIO, Wasabi, Selectel, Cloudflare R2, etc.) — create a bucket and access keys in the provider panel
- A personal image access key (issued by the maintainer)

## Installation (web wizard) — recommended

**First run:**

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

From your local machine (opens the installer URL in the browser after SSH):

```bash
./install-remote.sh root@YOUR_SERVER_IP
```

<details>
<summary><strong>You have a fix for the installer and want to continue installation</strong></summary>

**Update the installer (keep progress)** — stop the process and run again. **Do not delete** the install directory: it holds `.env`, `.installer-state.json`, and wizard progress.

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

A new link with a new token will appear, but steps are restored from `.env` and the state file — you land on the first incomplete step (e.g. S3 storage if `.env` is already saved).

**Start from scratch** — only for a full reset:

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true
rm -rf "$INSTALL_DIR"

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

> `rm -rf` removes `.env` and `.installer-state.json` — all wizard progress is lost. It is **not needed** to update the installer UI.

</details>

> `sudo` drops environment variables — use `sudo env VAR=... bash`, not `sudo VAR=... bash`.

Open the `Open: http://...` line **on your own computer** (the browser on the VPS will not open it).

The script will:

- download the deploy bundle to `~/rumbleserver` (or `RUMBLE_DIR`)
- open port **8800** in ufw (if active)
- start the web installer and print a one-time token URL

Open the link in your browser — the step-by-step wizard walks you through:

**Before installation**

0. **Welcome** — checklist: domain, S3-compatible storage, image access key
1. **Preflight** — enter domain and GHCR key, verify DNS and registry access; after success — choose mode (auto / step-by-step)

**Installation steps**

2. System requirements (Ubuntu/Debian, root, Python 3)
3. Firewall (UFW) — ports 22, 80, 443
4. Docker installation
5. GHCR login (key already saved at preflight — no re-entry needed)
6. Server configuration (`.env`) — domain, DNS check, PostgreSQL/Redis (local Docker or external)
7. **S3 storage** — choose provider:
   - **AWS** — automatic bucket/IAM provisioning or manual setup via the built-in AWS guide
   - **Other (S3-compatible)** — endpoint URL, bucket, access keys; optional addressing style / signature version for MinIO and similar providers
   - access is verified inside the app Docker image before continuing
8. Deploy (`docker compose pull && up -d`)
9. Django Admin superuser creation
10. User registration site — choose `rumble-msg.com` or `rmbl-msg.ru`, set server domain in App Settings; optional server name and local network access key
11. Nginx + Let's Encrypt HTTPS (domain already set)
12. Final summary and health checks

At each step the installer **tries to do the work itself first**. If something fails — it shows exactly what to run and in which directory. The **“I did it — verify”** button re-checks the step.

Custom install directory:

```bash
curl -fsSL .../installer.sh | sudo RUMBLE_DIR=/opt/rumble bash
```

Installer log: `tail -f ~/rumbleserver/installer.log`

## S3-compatible storage

The server stores encrypted files (avatars, attachments) in any S3-compatible bucket. In `.env` the installer writes:

- `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`
- `S3_BUCKET_NAME`, `S3_REGION_NAME`, `S3_ENDPOINT_URL`
- optionally `S3_ADDRESSING_STYLE`, `S3_SIGNATURE_VERSION` (for non-AWS providers)

**AWS:** use the wizard step — automatic setup with a bootstrap IAM user, or follow the in-installer AWS guide for manual bucket/IAM creation.

**Other providers:** create a bucket and access keys in your provider panel, select **Other (S3-compatible)** in the wizard, and fill in endpoint + credentials. Example for MinIO in `env.example`.

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

</details>

<details>
<summary><strong>Инструкция по установке (RU)</strong></summary>

## Требования

- Linux VPS (Ubuntu 22.04+ / Debian 11+) с root-доступом
- Домен с A-записью на IP сервера (для HTTPS через nginx)
- **S3-совместимое объектное хранилище** для зашифрованных файлов (фото, аудио, аватарки)
  - **AWS S3** рекомендуется — установщик может автоматически создать бакет и IAM-пользователя
  - Подойдёт и любой другой S3-compatible провайдер (MinIO, Wasabi, Selectel, Cloudflare R2 и т.д.) — бакет и ключи создаются в панели провайдера
- Персональный ключ доступа к образу (выдаёт мейнтейнер)

## Установка (веб-визард) — рекомендуется

**Первый запуск:**

```bash
curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

С локальной машины (после SSH откроет URL установщика в браузере):

```bash
./install-remote.sh root@IP_СЕРВЕРА
```

<details>
<summary><strong>Есть исправление установщика и нужно продолжить установку</strong></summary>

**Обновить установщик (сохранить прогресс)** — только остановить процесс и запустить снова. Папку **не удалять**: в ней `.env`, `.installer-state.json` и прогресс по шагам.

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

Откроется новая ссылка с новым token, но шаги подтянутся из `.env` и state-файла — попадёшь на первый незавершённый шаг (например S3, если `.env` уже сохранён).

**Начать установку с нуля** — только если нужен полный сброс:

```bash
INSTALL_DIR="${RUMBLE_DIR:-/root/rumbleserver}"

kill "$(cat "$INSTALL_DIR/.installer.pid")" 2>/dev/null || true
rm -rf "$INSTALL_DIR"

curl -fsSL https://raw.githubusercontent.com/ivavalser/rumbleserver-deploy/main/installer.sh | sudo bash
```

> `rm -rf` удаляет `.env` и `.installer-state.json` — весь прогресс визарда теряется. Для обновления UI установщика он **не нужен**.

</details>

> `sudo` сбрасывает env — нужен `sudo env VAR=... bash`, не `sudo VAR=... bash`.

Открой строку `Open: http://...` **на своём компьютере** (браузер на VPS не откроется).

Скрипт:

- скачает deploy-bundle в `~/rumbleserver` (или `RUMBLE_DIR`)
- откроет порт **8800** в ufw (если активен)
- запустит веб-установщик и выведет ссылку с одноразовым токеном

Открой ссылку в браузере — пошаговый визард проведёт через:

**До установки**

0. **Приветствие** — чеклист: домен, S3-хранилище, ключ доступа к образу
1. **Preflight** — ввод домена и GHCR-ключа, проверка DNS и доступа к registry; после успеха — выбор режима (авто / пошагово)

**Шаги установки**

2. Требования к системе (Ubuntu/Debian, root, Python 3)
3. Firewall (UFW) — порты 22, 80, 443
4. Установку Docker
5. Вход в GHCR (ключ уже сохранён на preflight — повторный ввод не нужен)
6. Конфигурация сервера (`.env`) — домен, проверка DNS, PostgreSQL/Redis (локально в Docker или внешние)
7. **S3-хранилище** — выбор провайдера:
   - **AWS** — автоматическое создание бакета/IAM или ручная настройка по встроенному AWS-гайду
   - **Другой (S3-compatible)** — endpoint URL, бакет, ключи; при необходимости addressing style / signature version (MinIO и аналоги)
   - доступ проверяется внутри Docker-образа приложения перед продолжением
8. Деплой (`docker compose pull && up -d`)
9. Создание Django Admin суперпользователя
10. Сайт регистрации пользователей — выбор `rumble-msg.com` или `rmbl-msg.ru`, домен сервера в App Settings; по желанию название сервера и ключ локальной сети
11. Nginx + Let's Encrypt HTTPS (домен уже задан)
12. Финальная сводка и проверки сервисов

На каждом шаге установщик **сначала пробует сделать сам**. Если не получилось — показывает, что именно выполнить и в какой директории. Кнопка **«Я сделал — проверить»** перепроверяет шаг.

Другая директория:

```bash
curl -fsSL .../installer.sh | sudo RUMBLE_DIR=/opt/rumble bash
```

Лог установщика: `tail -f ~/rumbleserver/installer.log`

## S3-совместимое хранилище

Сервер хранит зашифрованные файлы (аватарки, вложения) в любом S3-compatible бакете. Установщик прописывает в `.env`:

- `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`
- `S3_BUCKET_NAME`, `S3_REGION_NAME`, `S3_ENDPOINT_URL`
- опционально `S3_ADDRESSING_STYLE`, `S3_SIGNATURE_VERSION` (для не-AWS провайдеров)

**AWS:** шаг в визарде — автоматическая настройка через bootstrap IAM или ручное создание бакета/IAM по встроенному гайду.

**Другие провайдеры:** создай бакет и ключи в панели провайдера, в визарде выбери **Другой (S3-compatible)** и укажи endpoint + credentials. Пример для MinIO — в `env.example`.

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

</details>
