#!/usr/bin/env python3
"""Шаги веб-установщика Rumble Server."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class StepResult:
    ok: bool
    message: str = ""
    manual: str = ""
    cwd: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepDef:
    id: str
    title: str
    description: str
    check: Callable[[], StepResult]
    apply: Callable[[dict[str, Any]], StepResult] | None = None
    skip_manual: str = ""
    needs_form: bool = False


class InstallerContext:
    def __init__(self, install_dir: Path, log_fn: Callable[[str], None]):
        self.install_dir = install_dir
        self.log = log_fn
        self.state_file = install_dir / ".installer-state.json"
        self.env_path = install_dir / ".env"
        self.env_example = install_dir / "env.example"
        self.compose_file = install_dir / "docker-compose.yml"
        self.override_file = install_dir / "docker-compose.override.yml"
        self.nginx_template = install_dir / "installer" / "nginx.conf.template"
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def save_state(self) -> None:
        self.state_file.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state[key] = value
        self.save_state()

    def update(self, data: dict[str, Any]) -> None:
        self._state.update(data)
        self.save_state()

    def run(
        self,
        cmd: list[str] | str,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, str):
            display = cmd
            shell = True
            args: list[str] | str = cmd
        else:
            display = " ".join(cmd)
            shell = False
            args = cmd
        self.log(f"$ {display}")
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        proc = subprocess.run(
            args,
            cwd=str(cwd or self.install_dir),
            env=run_env,
            input=input_text,
            text=True,
            capture_output=True,
            shell=shell,
        )
        if proc.stdout:
            for line in proc.stdout.rstrip("\n").split("\n"):
                self.log(line)
        if proc.stderr:
            for line in proc.stderr.rstrip("\n").split("\n"):
                self.log(line)
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Команда завершилась с кодом {proc.returncode}: {display}"
            )
        return proc

    def docker_compose_cmd(self) -> list[str]:
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        return ["docker", "compose"]

    def compose_profiles(self) -> list[str]:
        profiles: list[str] = []
        if not self.get("use_external_db"):
            profiles.append("local-db")
        if not self.get("use_external_redis"):
            profiles.append("local-redis")
        return profiles

    def compose_base(self) -> list[str]:
        cmd = self.docker_compose_cmd() + [
            "--env-file",
            str(self.env_path),
            "-f",
            str(self.compose_file),
        ]
        if self.override_file.exists():
            cmd.extend(["-f", str(self.override_file)])
        for profile in self.compose_profiles():
            cmd.extend(["--profile", profile])
        return cmd


def _ok(msg: str = "", **data: Any) -> StepResult:
    return StepResult(ok=True, message=msg, data=data)


def _fail(msg: str, manual: str = "", cwd: str = "", **data: Any) -> StepResult:
    return StepResult(ok=False, message=msg, manual=manual, cwd=cwd, data=data)


def _read_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data


def _get_public_ip() -> str | None:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
            return resp.read().decode().strip()
    except (urllib.error.URLError, OSError):
        return None


def _resolve_domain(domain: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
        return sorted({item[4][0] for item in infos})
    except socket.gaierror:
        return []


def _generate_secret(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def _generate_django_secret() -> str:
    try:
        proc = subprocess.run(
            [
                "python3",
                "-c",
                "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return _generate_secret(50)


def check_system(ctx: InstallerContext) -> StepResult:
    issues: list[str] = []
    if os.geteuid() != 0:
        issues.append("Нужны права root (sudo).")
    os_info = _read_os_release()
    os_id = os_info.get("ID", "")
    if os_id not in ("ubuntu", "debian"):
        issues.append(f"Ожидается Ubuntu/Debian, обнаружено: {os_id or 'unknown'}.")
    if not shutil.which("python3"):
        issues.append("python3 не найден.")
    if issues:
        return _fail(
            " ".join(issues),
            manual=(
                "Запусти установщик от root на Ubuntu 22.04+ или Debian 11+:\n"
                "  curl -fsSL .../installer.sh | sudo bash"
            ),
        )
    return _ok(
        f"Система: {os_info.get('PRETTY_NAME', os_id)}, python3 OK, root OK.",
        os=os_info.get("PRETTY_NAME", os_id),
    )


def apply_system(ctx: InstallerContext, _payload: dict[str, Any]) -> StepResult:
    if not shutil.which("python3"):
        ctx.run(["apt-get", "update", "-qq"])
        ctx.run(["apt-get", "install", "-y", "python3", "curl", "wget", "ca-certificates"])
    return check_system(ctx)


def check_ufw(ctx: InstallerContext) -> StepResult:
    if not shutil.which("ufw"):
        return _fail(
            "ufw не установлен.",
            manual="sudo apt-get install -y ufw",
            cwd="/",
        )
    status = ctx.run(["ufw", "status"], check=False)
    text = (status.stdout or "") + (status.stderr or "")
    if "Status: active" not in text:
        return _fail("ufw не включён.", manual="sudo ufw enable")
    for port in ("22/tcp", "80/tcp", "443/tcp"):
        if port not in text:
            return _fail(
                f"Порт {port} не открыт в ufw.",
                manual=f"sudo ufw allow {port}",
                cwd="/",
            )
    return _ok("ufw активен, порты 22/80/443 открыты.")


def apply_ufw(ctx: InstallerContext, _payload: dict[str, Any]) -> StepResult:
    if not shutil.which("ufw"):
        ctx.run(["apt-get", "update", "-qq"])
        ctx.run(["apt-get", "install", "-y", "ufw"])
    ctx.run(["ufw", "allow", "22/tcp"], check=False)
    ctx.run(["ufw", "allow", "80/tcp"], check=False)
    ctx.run(["ufw", "allow", "443/tcp"], check=False)
    port = os.environ.get("RUMBLE_INSTALLER_PORT", "8800")
    ctx.run(["ufw", "allow", f"{port}/tcp"], check=False)
    ctx.run(["ufw", "--force", "enable"], check=False)
    return check_ufw(ctx)


def check_docker(ctx: InstallerContext) -> StepResult:
    if not shutil.which("docker"):
        return _fail(
            "Docker не установлен.",
            manual="curl -fsSL https://get.docker.com | sudo sh",
            cwd="/",
        )
    version = ctx.run(["docker", "--version"], check=False)
    if version.returncode != 0:
        return _fail("docker не работает.", manual="sudo systemctl start docker")
    compose = ctx.run(ctx.docker_compose_cmd() + ["version"], check=False)
    if compose.returncode != 0:
        return _fail(
            "Docker Compose plugin не установлен.",
            manual="sudo apt-get install -y docker-compose-plugin",
            cwd="/",
        )
    return _ok(f"{version.stdout.strip()}, {compose.stdout.strip()}")


def apply_docker(ctx: InstallerContext, _payload: dict[str, Any]) -> StepResult:
    ctx.run("curl -fsSL https://get.docker.com -o /tmp/get-docker.sh", cwd=Path("/tmp"))
    ctx.run(["sh", "/tmp/get-docker.sh"])
    ctx.run(["apt-get", "install", "-y", "docker-compose-plugin"], check=False)
    return check_docker(ctx)


def check_ghcr(ctx: InstallerContext) -> StepResult:
    registry = "ghcr.io"
    config_path = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"
    if config_path.exists() and registry in config_path.read_text(encoding="utf-8"):
        return _ok("Вход в GHCR выполнен.")
    if ctx.get("ghcr_key"):
        return _fail("Ключ сохранён, но вход в GHCR не выполнен.")
    return _fail(
        "Нет доступа к ghcr.io.",
        manual=(
            "Введи ключ доступа к образу в форме ниже или выполни:\n"
            "  echo YOUR_KEY | docker login ghcr.io -u rmbldeploy --password-stdin"
        ),
        cwd=str(ctx.install_dir),
    )


def apply_ghcr(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    key = (payload.get("ghcr_key") or ctx.get("ghcr_key") or "").strip()
    if not key:
        return _fail("Укажи ключ GHCR в форме.")
    user = payload.get("ghcr_user") or ctx.get("ghcr_user") or "rmbldeploy"
    ctx.update({"ghcr_key": key, "ghcr_user": user})
    ctx.run(
        ["docker", "login", "ghcr.io", "-u", user, "--password-stdin"],
        input_text=key + "\n",
        cwd=ctx.install_dir,
        check=True,
    )
    return check_ghcr(ctx)


def _default_env_payload(ctx: InstallerContext) -> dict[str, Any]:
    domain = ctx.get("domain") or ""
    return {
        "domain": domain,
        "allowed_hosts": ctx.get("allowed_hosts") or domain or "localhost,127.0.0.1",
        "db_pass": ctx.get("db_pass") or _generate_secret(24),
        "redis_password": ctx.get("redis_password") or _generate_secret(24),
        "secret_key": ctx.get("secret_key") or _generate_django_secret(),
        "db_name": ctx.get("db_name") or "rumbleserver_db",
        "db_user": ctx.get("db_user") or "rumbleserver_user",
        "db_host": ctx.get("db_host") or "db",
        "db_port": ctx.get("db_port") or "5432",
        "redis_host": ctx.get("redis_host") or "redis",
        "redis_port": ctx.get("redis_port") or "6379",
        "use_external_db": bool(ctx.get("use_external_db")),
        "use_external_redis": bool(ctx.get("use_external_redis")),
        "aws_access_key_id": ctx.get("aws_access_key_id") or "",
        "aws_secret_access_key": ctx.get("aws_secret_access_key") or "",
        "aws_storage_bucket_name": ctx.get("aws_storage_bucket_name") or "",
        "aws_s3_region_name": ctx.get("aws_s3_region_name") or "eu-north-1",
        "aws_s3_endpoint_url": ctx.get("aws_s3_endpoint_url")
        or "https://s3.eu-north-1.amazonaws.com",
    }


def _write_env_file(ctx: InstallerContext, payload: dict[str, Any]) -> None:
    domain = (payload.get("domain") or "").strip()
    allowed = (payload.get("allowed_hosts") or domain or "localhost,127.0.0.1").strip()
    use_external_db = bool(payload.get("use_external_db"))
    use_external_redis = bool(payload.get("use_external_redis"))

    db_name = payload.get("db_name") or "rumbleserver_db"
    db_user = payload.get("db_user") or "rumbleserver_user"
    db_pass = payload.get("db_pass") or _generate_secret(24)
    db_host = payload.get("db_host") or ("db" if not use_external_db else "")
    db_port = payload.get("db_port") or "5432"

    redis_password = payload.get("redis_password") or _generate_secret(24)
    redis_host = payload.get("redis_host") or ("redis" if not use_external_redis else "")
    redis_port = payload.get("redis_port") or "6379"
    redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/0"

    secret_key = payload.get("secret_key") or _generate_django_secret()

    lines = [
        "# Generated by Rumble Server installer",
        "DEBUG=False",
        f"SECRET_KEY={secret_key}",
        f"ALLOWED_HOSTS={allowed}",
        "",
        f"DB_NAME={db_name}",
        f"DB_USER={db_user}",
        f"DB_PASS={db_pass}",
        f"DB_HOST={db_host}",
        f"DB_PORT={db_port}",
        "",
        f"REDIS_HOST={redis_host}",
        f"REDIS_PORT={redis_port}",
        f"REDIS_PASSWORD={redis_password}",
        f"REDIS_URL={redis_url}",
        "",
        f"AWS_ACCESS_KEY_ID={payload.get('aws_access_key_id') or 'your_aws_access_key_id'}",
        f"AWS_SECRET_ACCESS_KEY={payload.get('aws_secret_access_key') or 'your_aws_secret_access_key'}",
        f"AWS_STORAGE_BUCKET_NAME={payload.get('aws_storage_bucket_name') or 'your-bucket-name'}",
        f"AWS_S3_REGION_NAME={payload.get('aws_s3_region_name') or 'eu-north-1'}",
        f"AWS_S3_ENDPOINT_URL={payload.get('aws_s3_endpoint_url') or 'https://s3.eu-north-1.amazonaws.com'}",
        "",
    ]
    ctx.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.log(f"Записан {ctx.env_path}")

    ctx.update(
        {
            "domain": domain,
            "allowed_hosts": allowed,
            "db_pass": db_pass,
            "redis_password": redis_password,
            "secret_key": secret_key,
            "db_name": db_name,
            "db_user": db_user,
            "db_host": db_host,
            "db_port": db_port,
            "redis_host": redis_host,
            "redis_port": redis_port,
            "use_external_db": use_external_db,
            "use_external_redis": use_external_redis,
            "aws_access_key_id": payload.get("aws_access_key_id") or "",
            "aws_secret_access_key": payload.get("aws_secret_access_key") or "",
            "aws_storage_bucket_name": payload.get("aws_storage_bucket_name") or "",
            "aws_s3_region_name": payload.get("aws_s3_region_name") or "eu-north-1",
            "aws_s3_endpoint_url": payload.get("aws_s3_endpoint_url")
            or "https://s3.eu-north-1.amazonaws.com",
        }
    )
    _write_compose_override(ctx, use_external_db, use_external_redis)


def _write_compose_override(
    ctx: InstallerContext,
    use_external_db: bool,
    use_external_redis: bool,
) -> None:
    if not use_external_db and not use_external_redis:
        if ctx.override_file.exists():
            ctx.override_file.unlink()
        return

    depends_web: dict[str, Any] = {}
    depends_worker: list[str] = ["web"]
    depends_push: list[str] = ["web"]

    if not use_external_db:
        depends_web["db"] = {"condition": "service_healthy"}
    if not use_external_redis:
        depends_web["redis"] = {"condition": "service_healthy"}
        depends_worker.append("redis")
        depends_push.append("redis")
    if not use_external_db:
        depends_worker.append("db")
        depends_push.append("db")

    lines = ["services:"]
    if depends_web:
        lines.extend(["  web:", "    depends_on:"])
        for key, val in depends_web.items():
            if isinstance(val, dict):
                lines.append(f"      {key}:")
                for k2, v2 in val.items():
                    lines.append(f"        {k2}: {v2}")
            else:
                lines.append(f"      - {key}")
    lines.extend(["  worker:", "    depends_on:"])
    for item in depends_worker:
        lines.append(f"      - {item}")
    lines.extend(["  push_worker:", "    depends_on:"])
    for item in depends_push:
        lines.append(f"      - {item}")
    content = "\n".join(lines) + "\n"

    ctx.override_file.write_text(content, encoding="utf-8")
    ctx.log(f"Записан {ctx.override_file}")


def check_env(ctx: InstallerContext) -> StepResult:
    if not ctx.env_path.exists():
        return _fail(
            ".env не создан.",
            manual=f"Заполни форму или создай файл: {ctx.env_path}",
            cwd=str(ctx.install_dir),
        )
    text = ctx.env_path.read_text(encoding="utf-8")
    required = ["SECRET_KEY=", "DB_PASS=", "REDIS_PASSWORD=", "ALLOWED_HOSTS="]
    missing = [k for k in required if k not in text]
    if missing:
        return _fail(f"В .env не хватает: {', '.join(missing)}")
    if "change_this" in text or "your-secret-key" in text:
        return _fail("В .env остались значения-заглушки.")
    return _ok(".env настроен.", defaults=_default_env_payload(ctx))


def apply_env(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    merged = _default_env_payload(ctx)
    merged.update(payload)
    if merged.get("use_external_db") and not merged.get("db_host"):
        return _fail("Укажи DB_HOST для внешней PostgreSQL.")
    if merged.get("use_external_redis") and not merged.get("redis_host"):
        return _fail("Укажи REDIS_HOST для внешнего Redis.")
    _write_env_file(ctx, merged)
    return check_env(ctx)


def check_deploy(ctx: InstallerContext) -> StepResult:
    if not ctx.env_path.exists():
        return _fail("Сначала настрой .env.")
    ps = ctx.run(ctx.compose_base() + ["ps", "--format", "json"], check=False)
    if ps.returncode != 0:
        return _fail(
            "Контейнеры не запущены.",
            manual=f"cd {ctx.install_dir} && {' '.join(ctx.compose_base())} up -d",
            cwd=str(ctx.install_dir),
        )
    running = ps.stdout.strip()
    for name in ("rumbleserver_web",):
        if name not in running:
            return _fail(
                f"Контейнер {name} не запущен.",
                manual=f"cd {ctx.install_dir} && {' '.join(ctx.compose_base())} up -d",
                cwd=str(ctx.install_dir),
            )
    check = ctx.run(
        ctx.compose_base() + ["exec", "-T", "web", "python", "manage.py", "check"],
        check=False,
    )
    if check.returncode != 0:
        return _fail("manage.py check не прошёл.", manual=check.stderr or check.stdout)
    curl = ctx.run(
        ["curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1:8000/admin/"],
        check=False,
    )
    code = (curl.stdout or "").strip()
    if code not in ("200", "301", "302", "404"):
        return _fail(f"Web не отвечает на :8000 (код {code or 'n/a'}).")
    return _ok("Сервисы запущены, web отвечает.")


def apply_deploy(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    version = payload.get("version") or ctx.get("version") or "stable"
    ctx.set("version", version)
    env = os.environ.copy()
    env["VERSION"] = version
    ctx.run(ctx.compose_base() + ["pull"], env=env)
    ctx.run(ctx.compose_base() + ["up", "-d"], env=env)
    import time

    for attempt in range(30):
        result = check_deploy(ctx)
        if result.ok:
            return result
        time.sleep(2)
    return check_deploy(ctx)


def check_superuser(ctx: InstallerContext) -> StepResult:
    username = ctx.get("admin_username")
    if not username:
        return _fail("Суперпользователь не создан.", manual="Заполни форму ниже.")
    safe_user = username.replace("'", "\\'")
    proc = ctx.run(
        ctx.compose_base()
        + [
            "exec",
            "-T",
            "web",
            "python",
            "manage.py",
            "shell",
            "-c",
            (
                "from django.contrib.auth import get_user_model; "
                f"print(get_user_model().objects.filter(username='{safe_user}', is_superuser=True).exists())"
            ),
        ],
        check=False,
    )
    if proc.returncode == 0 and "True" in (proc.stdout or ""):
        return _ok(f"Суперпользователь '{username}' существует.")
    return _fail(
        f"Суперпользователь '{username}' не найден.",
        manual=(
            f"cd {ctx.install_dir}\n"
            f"{' '.join(ctx.compose_base())} exec web python manage.py createsuperuser"
        ),
        cwd=str(ctx.install_dir),
    )


def apply_superuser(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    username = (payload.get("admin_username") or "").strip()
    email = (payload.get("admin_email") or "").strip()
    password = (payload.get("admin_password") or "").strip()
    if not username or not password:
        return _fail("Укажи username и password.")
    ctx.update(
        {
            "admin_username": username,
            "admin_email": email,
            "admin_password": password,
        }
    )
    env = {
        "DJANGO_SUPERUSER_PASSWORD": password,
        "DJANGO_SUPERUSER_USERNAME": username,
        "DJANGO_SUPERUSER_EMAIL": email,
    }
    ctx.run(
        ctx.compose_base()
        + [
            "exec",
            "-T",
            "web",
            "python",
            "manage.py",
            "createsuperuser",
            "--noinput",
            "--username",
            username,
            "--email",
            email or f"{username}@localhost",
        ],
        env=env,
        check=False,
    )
    return check_superuser(ctx)


def check_nginx(ctx: InstallerContext) -> StepResult:
    domain = (ctx.get("domain") or "").strip()
    if not domain:
        return _fail("Домен не указан. Вернись к шагу .env.")
    if not shutil.which("nginx"):
        return _fail("nginx не установлен.", manual="sudo apt-get install -y nginx")
    conf = Path("/etc/nginx/sites-enabled/rumbleserver")
    if not conf.exists():
        return _fail(
            "Конфиг nginx не найден.",
            manual="sudo nano /etc/nginx/sites-available/rumbleserver",
            cwd="/etc/nginx/sites-available",
        )
    test = ctx.run(["nginx", "-t"], check=False)
    if test.returncode != 0:
        return _fail("nginx -t не прошёл.", manual=test.stderr or test.stdout)
    https = ctx.run(
        ["curl", "-fsSI", f"https://{domain}/api/"],
        check=False,
    )
    if https.returncode != 0:
        return _fail(
            f"HTTPS для {domain} не отвечает.",
            manual=f"sudo certbot --nginx -d {domain}",
            cwd="/",
        )
    return _ok(f"Nginx + HTTPS для {domain} работают.")


def check_dns(ctx: InstallerContext) -> StepResult:
    domain = (ctx.get("domain") or "").strip()
    if not domain:
        return _fail("Домен не указан.")
    public_ip = _get_public_ip()
    resolved = _resolve_domain(domain)
    if not resolved:
        return _fail(
            f"DNS для {domain} не резолвится.",
            manual=(
                f"Создай A-запись:\n  {domain} → {public_ip or 'IP_СЕРВЕРА'}\n"
                "Подожди распространения DNS и нажми «Я сделал — проверить»."
            ),
            cwd="/",
            public_ip=public_ip,
            resolved=resolved,
        )
    if public_ip and public_ip not in resolved:
        return _fail(
            f"DNS указывает на {resolved}, ожидается {public_ip}.",
            manual=(
                f"Исправь A-запись {domain} → {public_ip}\n"
                "Проверка: dig +short " + domain
            ),
            public_ip=public_ip,
            resolved=resolved,
        )
    return _ok(f"DNS OK: {domain} → {', '.join(resolved)}", public_ip=public_ip)


def apply_nginx(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    domain = (ctx.get("domain") or payload.get("domain") or "").strip()
    email = (payload.get("certbot_email") or ctx.get("certbot_email") or "").strip()
    if not domain:
        return _fail("Домен не указан.")
    if not email:
        return _fail("Укажи email для Let's Encrypt.")

    dns = check_dns(ctx)
    if not dns.ok:
        return dns

    ctx.update({"domain": domain, "certbot_email": email})

    ctx.run(["apt-get", "update", "-qq"])
    ctx.run(["apt-get", "install", "-y", "nginx", "certbot", "python3-certbot-nginx"])
    ctx.run(["rm", "-f", "/etc/nginx/sites-enabled/default"], check=False)
    ctx.run(["mkdir", "-p", "/var/www/html"])

    template = ctx.nginx_template.read_text(encoding="utf-8")
    conf_content = template.replace("YOUR_DOMAIN", domain)
    conf_path = Path("/etc/nginx/sites-available/rumbleserver")
    conf_path.write_text(conf_content, encoding="utf-8")
    ctx.run(
        ["ln", "-sf", str(conf_path), "/etc/nginx/sites-enabled/rumbleserver"],
        check=False,
    )
    ctx.run(["nginx", "-t"])
    ctx.run(["systemctl", "reload", "nginx"])

    ctx.run(
        [
            "certbot",
            "--nginx",
            "-d",
            domain,
            "--non-interactive",
            "--agree-tos",
            "-m",
            email,
            "--redirect",
        ],
        check=False,
    )
    ctx.run(["certbot", "renew", "--dry-run"], check=False)
    return check_nginx(ctx)


def check_finish(ctx: InstallerContext) -> StepResult:
    domain = ctx.get("domain") or "localhost"
    admin = ctx.get("admin_username") or "admin"
    summary_lines = [
        "Rumble Server — установка завершена",
        "",
        f"Admin:     https://{domain}/admin/",
        f"Username:  {admin}",
        f".env:      {ctx.env_path}",
        f"Directory: {ctx.install_dir}",
        "",
        "Полезные команды:",
        f"  cd {ctx.install_dir}",
        f"  {' '.join(ctx.compose_base())} ps",
        f"  {' '.join(ctx.compose_base())} logs -f web",
    ]
    summary = "\n".join(summary_lines)
    summary_path = ctx.install_dir / "install-summary.txt"
    summary_path.write_text(summary + "\n", encoding="utf-8")
    return _ok(summary, summary=summary)


def apply_finish(ctx: InstallerContext, _payload: dict[str, Any]) -> StepResult:
    return check_finish(ctx)


STEPS: list[StepDef] = [
    StepDef(
        id="system",
        title="Проверка системы",
        description="Ubuntu/Debian, root, python3",
        check=check_system,
        apply=apply_system,
        skip_manual="Убедись, что сервер Ubuntu 22.04+ или Debian 11+, установлен python3, команды выполняются от root.",
    ),
    StepDef(
        id="ufw",
        title="Firewall (UFW)",
        description="Порты 22, 80, 443",
        check=check_ufw,
        apply=apply_ufw,
        skip_manual="Настрой firewall вручную: открой 22/tcp, 80/tcp, 443/tcp.",
    ),
    StepDef(
        id="docker",
        title="Docker",
        description="Docker Engine + Compose plugin",
        check=check_docker,
        apply=apply_docker,
        skip_manual="Установи Docker: curl -fsSL https://get.docker.com | sudo sh && sudo apt-get install -y docker-compose-plugin",
    ),
    StepDef(
        id="ghcr",
        title="Доступ к образу (GHCR)",
        description="Ключ от мейнтейнера",
        check=check_ghcr,
        apply=apply_ghcr,
        needs_form=True,
        skip_manual="docker login ghcr.io -u rmbldeploy --password-stdin",
    ),
    StepDef(
        id="env",
        title="Конфигурация .env",
        description="Домен, пароли, AWS",
        check=check_env,
        apply=apply_env,
        needs_form=True,
        skip_manual=f"Создай .env из env.example и заполни ALLOWED_HOSTS, DB_PASS, REDIS_PASSWORD.",
    ),
    StepDef(
        id="deploy",
        title="Деплой",
        description="docker compose pull && up -d",
        check=check_deploy,
        apply=apply_deploy,
        skip_manual="cd ~/rumbleserver && ./prod.sh",
    ),
    StepDef(
        id="superuser",
        title="Django Admin",
        description="Первый суперпользователь",
        check=check_superuser,
        apply=apply_superuser,
        needs_form=True,
        skip_manual="docker compose exec web python manage.py createsuperuser",
    ),
    StepDef(
        id="nginx",
        title="Nginx + HTTPS",
        description="Reverse proxy и Let's Encrypt",
        check=check_nginx,
        apply=apply_nginx,
        needs_form=True,
        skip_manual="Следуй разделу «Nginx и HTTPS» в DEPLOY.md",
    ),
    StepDef(
        id="finish",
        title="Готово",
        description="Сводка установки",
        check=check_finish,
        apply=apply_finish,
    ),
]

STEP_MAP = {step.id: step for step in STEPS}


def step_statuses(ctx: InstallerContext) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for step in STEPS:
        try:
            result = step.check(ctx)
            status = "done" if result.ok else "pending"
        except Exception as exc:
            status = "error"
            result = StepResult(ok=False, message=str(exc))
        items.append(
            {
                "id": step.id,
                "title": step.title,
                "description": step.description,
                "status": status,
                "message": result.message,
                "needs_form": step.needs_form,
                "manual": result.manual,
                "cwd": result.cwd,
                "data": result.data,
            }
        )
    return items
