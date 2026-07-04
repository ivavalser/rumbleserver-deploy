#!/usr/bin/env python3
"""Rumble Server web installer steps."""

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
                f"Command failed with exit code {proc.returncode}: {display}"
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


def get_server_public_ip(ctx: InstallerContext) -> str | None:
    cached = ctx.get("public_ip")
    if cached:
        return cached
    ip = _get_public_ip()
    if ip:
        ctx.set("public_ip", ip)
    return ip


def dns_setup_hint(domain: str, public_ip: str | None) -> dict[str, str]:
    domain = domain.strip()
    parts = [p for p in domain.split(".") if p]
    if len(parts) >= 3:
        name = parts[0]
    elif len(parts) == 2:
        name = "@"
    else:
        name = domain or "api"
    return {
        "type": "A",
        "name": name,
        "host": domain or "your-domain.com",
        "value": public_ip or "",
        "ttl": "300",
    }


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
        issues.append("Root privileges required (sudo).")
    os_info = _read_os_release()
    os_id = os_info.get("ID", "")
    if os_id not in ("ubuntu", "debian"):
        issues.append(f"Expected Ubuntu/Debian, found: {os_id or 'unknown'}.")
    if not shutil.which("python3"):
        issues.append("python3 not found.")
    if issues:
        return _fail(
            " ".join(issues),
            manual=(
                "Run the installer as root on Ubuntu 22.04+ or Debian 11+:\n"
                "  curl -fsSL .../installer.sh | sudo bash"
            ),
        )
    return _ok(
        f"System: {os_info.get('PRETTY_NAME', os_id)}, python3 OK, root OK.",
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
            "UFW is not installed yet.",
            manual="sudo apt-get install -y ufw",
            cwd="/",
        )
    status = ctx.run(["ufw", "status"], check=False)
    text = (status.stdout or "") + (status.stderr or "")
    if "Status: active" not in text:
        return _fail("UFW is not enabled yet.", manual="sudo ufw enable")
    for port in ("22/tcp", "80/tcp", "443/tcp"):
        if port not in text:
            return _fail(
                f"Port {port} is not open yet.",
                manual=f"sudo ufw allow {port}",
                cwd="/",
            )
    return _ok("UFW is active, ports 22/80/443 are open.")


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
            "Docker is not installed yet.",
            manual="curl -fsSL https://get.docker.com | sudo sh",
            cwd="/",
        )
    version = ctx.run(["docker", "--version"], check=False)
    if version.returncode != 0:
        return _fail("Docker is installed but not running.", manual="sudo systemctl start docker")
    compose = ctx.run(ctx.docker_compose_cmd() + ["version"], check=False)
    if compose.returncode != 0:
        return _fail(
            "Docker Compose plugin is not installed yet.",
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
        return _ok("Logged in to GHCR.")
    if ctx.get("ghcr_key"):
        return _fail("GHCR login failed — check the access key.")
    return _fail(
        "GHCR login is required.",
        manual=(
            "Enter the image access key in the form below or run:\n"
            "  echo YOUR_KEY | docker login ghcr.io -u rmbldeploy --password-stdin"
        ),
        cwd=str(ctx.install_dir),
    )


def apply_ghcr(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    key = (payload.get("ghcr_key") or ctx.get("ghcr_key") or "").strip()
    if not key:
        return _fail("Enter the GHCR key in the form.")
    user = payload.get("ghcr_user") or ctx.get("ghcr_user") or "rmbldeploy"
    ctx.update({"ghcr_key": key, "ghcr_user": user})
    ctx.run(
        ["docker", "login", "ghcr.io", "-u", user, "--password-stdin"],
        input_text=key + "\n",
        cwd=ctx.install_dir,
        check=True,
    )
    return check_ghcr(ctx)


def _endpoint_for_region(region: str) -> str:
    return f"https://s3.{region}.amazonaws.com"


def _aws_configured(text: str) -> bool:
    placeholders = (
        "your_aws_access_key",
        "your-bucket-name",
        "your_aws_secret",
        "change_this",
    )
    if any(p in text for p in placeholders):
        return False
    for key in (
        "AWS_ACCESS_KEY_ID=",
        "AWS_SECRET_ACCESS_KEY=",
        "AWS_STORAGE_BUCKET_NAME=",
        "AWS_S3_REGION_NAME=",
        "AWS_S3_ENDPOINT_URL=",
    ):
        if key not in text:
            return False
    return True


def _env_apply_statuses(payload: dict[str, Any], ctx: InstallerContext) -> list[dict[str, Any]]:
    domain = payload.get("domain") or ctx.get("domain") or ""
    allowed = payload.get("allowed_hosts") or domain
    simple = (payload.get("env_mode") or ctx.get("env_mode") or "simple") == "simple"
    items = [
        {"label": ".env file created", "ok": ctx.env_path.exists()},
        {"label": "SECRET_KEY generated", "ok": bool(payload.get("secret_key"))},
        {"label": f"ALLOWED_HOSTS set ({allowed})", "ok": bool(allowed)},
    ]
    if simple:
        items.extend(
            [
                {
                    "label": "PostgreSQL credentials added (Docker)",
                    "ok": not payload.get("use_external_db"),
                },
                {
                    "label": "Redis credentials added (Docker)",
                    "ok": not payload.get("use_external_redis"),
                },
            ]
        )
    else:
        items.extend(
            [
                {
                    "label": "PostgreSQL connection configured",
                    "ok": bool(payload.get("db_host")),
                },
                {
                    "label": "Redis connection configured",
                    "ok": bool(payload.get("redis_host")),
                },
            ]
        )
    items.append(
        {
            "label": "AWS S3 credentials added",
            "ok": _aws_configured(ctx.env_path.read_text(encoding="utf-8"))
            if ctx.env_path.exists()
            else False,
        }
    )
    return items


def _default_env_payload(ctx: InstallerContext) -> dict[str, Any]:
    domain = ctx.get("domain") or ""
    region = ctx.get("aws_s3_region_name") or "eu-north-1"
    return {
        "env_mode": ctx.get("env_mode") or "simple",
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
        or _endpoint_for_region(region),
    }


def _write_env_file(ctx: InstallerContext, payload: dict[str, Any]) -> None:
    domain = (payload.get("domain") or "").strip()
    allowed = (payload.get("allowed_hosts") or domain or "localhost,127.0.0.1").strip()
    env_mode = payload.get("env_mode") or "simple"
    use_external_db = bool(payload.get("use_external_db")) if env_mode == "advanced" else False
    use_external_redis = bool(payload.get("use_external_redis")) if env_mode == "advanced" else False

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
    region = payload.get("aws_s3_region_name") or "eu-north-1"
    endpoint = payload.get("aws_s3_endpoint_url") or _endpoint_for_region(region)

    aws_key = (payload.get("aws_access_key_id") or "").strip()
    aws_secret = (payload.get("aws_secret_access_key") or "").strip()
    aws_bucket = (payload.get("aws_storage_bucket_name") or "").strip()

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
        f"AWS_ACCESS_KEY_ID={aws_key}",
        f"AWS_SECRET_ACCESS_KEY={aws_secret}",
        f"AWS_STORAGE_BUCKET_NAME={aws_bucket}",
        f"AWS_S3_REGION_NAME={region}",
        f"AWS_S3_ENDPOINT_URL={endpoint}",
        "",
    ]
    ctx.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.log(f"Wrote {ctx.env_path}")

    ctx.update(
        {
            "env_mode": env_mode,
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
            "aws_storage_bucket_name": aws_bucket,
            "aws_s3_region_name": region,
            "aws_s3_endpoint_url": endpoint,
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
    ctx.log(f"Wrote {ctx.override_file}")


def check_env(ctx: InstallerContext) -> StepResult:
    if not ctx.env_path.exists():
        return _fail(
            "The server .env file has not been created yet.",
            manual=f"Use the form above or create manually: {ctx.env_path}",
            cwd=str(ctx.install_dir),
        )
    text = ctx.env_path.read_text(encoding="utf-8")
    required = ["SECRET_KEY=", "DB_PASS=", "REDIS_PASSWORD=", "ALLOWED_HOSTS="]
    missing = [k for k in required if k not in text]
    if missing:
        return _fail(f"The .env file is incomplete — missing: {', '.join(missing)}")
    if "change_this" in text or "your-secret-key" in text:
        return _fail("The .env file still contains placeholder values — replace them.")
    if not _aws_configured(text):
        return _fail(
            "AWS S3 credentials are not set yet.",
            manual="Fill in AWS credentials or use Create S3 bucket & IAM user in the installer.",
        )
    defaults = _default_env_payload(ctx)
    return _ok("Server .env file is configured.", defaults=defaults)


def apply_env(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    merged = _default_env_payload(ctx)
    merged.update(payload)
    env_mode = merged.get("env_mode") or "simple"
    merged["env_mode"] = env_mode

    if env_mode == "simple":
        merged["use_external_db"] = False
        merged["use_external_redis"] = False
    else:
        if merged.get("use_external_db") and not merged.get("db_host"):
            return _fail("Set DB_HOST for external PostgreSQL.")
        if merged.get("use_external_redis") and not merged.get("redis_host"):
            return _fail("Set REDIS_HOST for external Redis.")

    if not (merged.get("aws_access_key_id") and merged.get("aws_secret_access_key")):
        return _fail("AWS access key and secret are required.")
    if not merged.get("aws_storage_bucket_name"):
        return _fail("AWS bucket name is required.")
    if not (merged.get("domain") or "").strip():
        return _fail("Enter the server domain name.")

    if env_mode == "simple":
        merged.setdefault("db_pass", _generate_secret(24))
        merged.setdefault("redis_password", _generate_secret(24))
        merged.setdefault("secret_key", _generate_django_secret())

    _write_env_file(ctx, merged)
    result = check_env(ctx)
    if result.ok:
        statuses = _env_apply_statuses(merged, ctx)
        return StepResult(
            ok=True,
            message="Configuration saved.",
            data={"defaults": _default_env_payload(ctx), "statuses": statuses},
        )
    return result


def check_deploy(ctx: InstallerContext) -> StepResult:
    if not ctx.env_path.exists():
        return _fail("Complete the .env step first.")
    ps = ctx.run(ctx.compose_base() + ["ps", "--format", "json"], check=False)
    if ps.returncode != 0:
        return _fail(
            "Application containers are not running yet.",
            manual=f"cd {ctx.install_dir} && {' '.join(ctx.compose_base())} up -d",
            cwd=str(ctx.install_dir),
        )
    running = ps.stdout.strip()
    for name in ("rumbleserver_web",):
        if name not in running:
            return _fail(
                f"Container {name} is not running yet.",
                manual=f"cd {ctx.install_dir} && {' '.join(ctx.compose_base())} up -d",
                cwd=str(ctx.install_dir),
            )
    check = ctx.run(
        ctx.compose_base() + ["exec", "-T", "web", "python", "manage.py", "check"],
        check=False,
    )
    if check.returncode != 0:
        return _fail("manage.py check failed.", manual=check.stderr or check.stdout)
    curl = ctx.run(
        ["curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1:8000/admin/"],
        check=False,
    )
    code = (curl.stdout or "").strip()
    if code not in ("200", "301", "302", "404"):
        return _fail(f"Web is not responding on :8000 (code {code or 'n/a'}).")
    return _ok("Services are running, web is responding.")


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
        return _fail("Superuser has not been created yet.", manual="Fill in the form below.")
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
        return _ok(f"Superuser '{username}' exists.")
    return _fail(
        f"Superuser '{username}' was not found.",
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
        return _fail("Enter username and password.")
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
        return _fail("Domain is not set — complete the .env step first.")
    if not shutil.which("nginx"):
        return _fail("Nginx is not installed yet.", manual="sudo apt-get install -y nginx")
    conf = Path("/etc/nginx/sites-enabled/rumbleserver")
    if not conf.exists():
        return _fail(
            "Nginx site config is not set up yet.",
            manual="sudo nano /etc/nginx/sites-available/rumbleserver",
            cwd="/etc/nginx/sites-available",
        )
    test = ctx.run(["nginx", "-t"], check=False)
    if test.returncode != 0:
        return _fail("nginx -t failed.", manual=test.stderr or test.stdout)
    https = ctx.run(
        ["curl", "-fsSI", f"https://{domain}/api/"],
        check=False,
    )
    if https.returncode != 0:
        return _fail(
            f"HTTPS for {domain} is not responding.",
            manual=f"sudo certbot --nginx -d {domain}",
            cwd="/",
        )
    return _ok(f"Nginx + HTTPS for {domain} are working.")


def check_dns(ctx: InstallerContext, domain: str | None = None) -> StepResult:
    domain = (domain or ctx.get("domain") or "").strip()
    if not domain:
        return _fail("Enter the server domain name first.")
    public_ip = get_server_public_ip(ctx)
    hint = dns_setup_hint(domain, public_ip)
    resolved = _resolve_domain(domain)
    if not resolved:
        ip_hint = public_ip or "YOUR_SERVER_IP"
        return _fail(
            f"DNS for {domain} does not resolve yet.",
            manual=(
                f"At your domain registrar, create an A record:\n"
                f"  Type:  A\n"
                f"  Name:  {hint['name']}\n"
                f"  Value: {ip_hint}\n"
                f"  TTL:   {hint['ttl']}\n\n"
                "DNS can take a few minutes to propagate. Then click Check DNS records."
            ),
            cwd="/",
            public_ip=public_ip,
            resolved=resolved,
            dns_hint=hint,
        )
    if public_ip and public_ip not in resolved:
        return _fail(
            f"{domain} points to {', '.join(resolved)}, but this server is {public_ip}.",
            manual=(
                f"Update the A record at your registrar:\n"
                f"  Name:  {hint['name']}\n"
                f"  Value: {public_ip}\n\n"
                f"Check: dig +short {domain}"
            ),
            public_ip=public_ip,
            resolved=resolved,
            dns_hint=hint,
        )
    return _ok(
        f"DNS is configured: {domain} → {', '.join(resolved)}",
        public_ip=public_ip,
        resolved=resolved,
        dns_hint=hint,
    )


def apply_nginx(ctx: InstallerContext, payload: dict[str, Any]) -> StepResult:
    domain = (ctx.get("domain") or payload.get("domain") or "").strip()
    email = (payload.get("certbot_email") or ctx.get("certbot_email") or "").strip()
    if not domain:
        return _fail("Domain not set.")
    if not email:
        return _fail("Enter email for Let's Encrypt.")

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
        "Rumble Server — installation complete",
        "",
        f"Admin:     https://{domain}/admin/",
        f"Username:  {admin}",
        f".env:      {ctx.env_path}",
        f"Directory: {ctx.install_dir}",
        "",
        "Useful commands:",
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
        title="System requirements",
        description="Ubuntu/Debian, root, Python 3",
        check=check_system,
        apply=apply_system,
        skip_manual="Ensure Ubuntu 22.04+ or Debian 11+, python3 installed, commands run as root.",
    ),
    StepDef(
        id="ufw",
        title="Firewall (UFW)",
        description="Open ports 22, 80, 443",
        check=check_ufw,
        apply=apply_ufw,
        skip_manual="Configure firewall manually: allow 22/tcp, 80/tcp, 443/tcp.",
    ),
    StepDef(
        id="docker",
        title="Docker",
        description="Engine and Compose plugin",
        check=check_docker,
        apply=apply_docker,
        skip_manual="Install Docker: curl -fsSL https://get.docker.com | sudo sh && sudo apt-get install -y docker-compose-plugin",
    ),
    StepDef(
        id="ghcr",
        title="Container registry (GHCR)",
        description="Access key from maintainer",
        check=check_ghcr,
        apply=apply_ghcr,
        needs_form=True,
        skip_manual="docker login ghcr.io -u rmbldeploy --password-stdin",
    ),
    StepDef(
        id="env",
        title="Server configuration (.env)",
        description="Domain, DNS, database, Redis, AWS S3",
        check=check_env,
        apply=apply_env,
        needs_form=True,
        skip_manual="Create .env from env.example with ALLOWED_HOSTS, DB/Redis, and AWS S3 credentials.",
    ),
    StepDef(
        id="deploy",
        title="Deploy application",
        description="Pull images and start services",
        check=check_deploy,
        apply=apply_deploy,
        skip_manual="cd ~/rumbleserver && ./prod.sh",
    ),
    StepDef(
        id="superuser",
        title="Django Admin account",
        description="First superuser",
        check=check_superuser,
        apply=apply_superuser,
        needs_form=True,
        skip_manual="docker compose exec web python manage.py createsuperuser",
    ),
    StepDef(
        id="nginx",
        title="Nginx and HTTPS",
        description="Reverse proxy and Let's Encrypt",
        check=check_nginx,
        apply=apply_nginx,
        needs_form=True,
        skip_manual="Follow the Nginx and HTTPS section in DEPLOY.md",
    ),
    StepDef(
        id="finish",
        title="Installation complete",
        description="Summary and next steps",
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
