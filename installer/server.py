#!/usr/bin/env python3
"""Веб-установщик Rumble Server — HTTP-сервер на stdlib."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import traceback
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

INSTALL_DIR = Path(os.environ.get("RUMBLE_INSTALL_DIR", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(INSTALL_DIR / "installer"))

from steps import (  # noqa: E402
    STEP_MAP,
    STEPS,
    InstallerContext,
    StepResult,
    _default_aws_payload,
    _default_env_payload,
    check_aws_access,
    check_dns,
    dns_setup_hint,
    get_server_public_ip,
    invalidate_step_check_cache,
    save_step_check_cache,
    step_statuses,
)

TOKEN = os.environ.get("RUMBLE_INSTALLER_TOKEN", "")
PORT = int(os.environ.get("RUMBLE_INSTALLER_PORT", "8800"))
PID_FILE = INSTALL_DIR / ".installer.pid"

LOG_BUFFER: deque[str] = deque(maxlen=500)
LOG_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
SHUTDOWN = threading.Event()


def append_log(line: str) -> None:
    with LOG_LOCK:
        LOG_BUFFER.append(line)


class InstallerHandler(BaseHTTPRequestHandler):
    server_version = "RumbleInstaller/1.0"

    def log_message(self, fmt: str, *args) -> None:
        pass  # skip HTTP access noise in installer log

    @property
    def ctx(self) -> InstallerContext:
        return self.server.installer_ctx  # type: ignore[attr-defined]

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _token_ok(self) -> bool:
        if not TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == TOKEN:
            return True
        qs = parse_qs(urlparse(self.path).query)
        if qs.get("token", [""])[0] == TOKEN:
            return True
        body = {}
        if self.command == "POST":
            # Token may be only in query for POST; body read happens in handlers
            pass
        return False

    def _require_auth(self) -> bool:
        if self._token_ok():
            return True
        self._json_response(HTTPStatus.UNAUTHORIZED, {"error": "invalid token"})
        return False

    def _json_response(self, status: HTTPStatus, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file_response(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._file_response(INSTALL_DIR / "installer" / "index.html", "text/html; charset=utf-8")
            return

        if path in ("/aws-guide.html", "/aws-guide"):
            self._file_response(INSTALL_DIR / "installer" / "aws-guide.html", "text/html; charset=utf-8")
            return

        if path == "/api/state":
            if not self._require_auth():
                return
            with STATE_LOCK:
                statuses = step_statuses(self.ctx)
                current = next((s for s in statuses if s["status"] != "done"), statuses[-1])
                defaults = _default_env_payload(self.ctx)
                public_ip = get_server_public_ip(self.ctx) or ""
                domain = self.ctx.get("domain", "") or defaults.get("domain", "")
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "steps": statuses,
                        "current_step": current["id"],
                        "install_dir": str(INSTALL_DIR),
                        "defaults": defaults,
                        "aws_defaults": _default_aws_payload(self.ctx),
                        "domain": domain,
                        "public_ip": public_ip,
                        "dns_hint": dns_setup_hint(domain, public_ip or None),
                        "admin_username": self.ctx.get("admin_username", ""),
                    },
                )
            return

        if path == "/api/log":
            if not self._require_auth():
                return
            offset = int(parse_qs(parsed.query).get("offset", ["0"])[0])
            with LOG_LOCK:
                lines = [l for l in LOG_BUFFER if not l.startswith("[http]")]
            if offset < 0:
                offset = 0
            self._json_response(
                HTTPStatus.OK,
                {"lines": lines[offset:], "total": len(lines)},
            )
            return

        if path.startswith("/api/step/") and path.endswith("/dns-check"):
            if not self._require_auth():
                return
            step_id = path.split("/")[3]
            if step_id not in ("nginx", "env"):
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": "unsupported"})
                return
            qs = parse_qs(parsed.query)
            domain = (qs.get("domain", [""])[0] or "").strip() or None
            result = check_dns(self.ctx, domain=domain)
            self._json_response(
                HTTPStatus.OK,
                {
                    "ok": result.ok,
                    "message": result.message,
                    "manual": result.manual,
                    "data": result.data,
                },
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs_token = parse_qs(parsed.query).get("token", [""])[0]
        if TOKEN and qs_token != TOKEN:
            auth = self.headers.get("Authorization", "")
            if not (auth.startswith("Bearer ") and auth[7:] == TOKEN):
                self._json_response(HTTPStatus.UNAUTHORIZED, {"error": "invalid token"})
                return

        if path == "/api/step/aws/access-check":
            if not self._require_auth():
                return
            payload = self._read_json()
            with STATE_LOCK:
                invalidate_step_check_cache(self.ctx, "aws")
                result = check_aws_access(self.ctx, payload)
                save_step_check_cache(self.ctx, "aws", result)
            self._json_response(
                HTTPStatus.OK,
                {
                    "ok": result.ok,
                    "message": result.message,
                    "manual": result.manual,
                    "data": result.data,
                },
            )
            return

        if path.startswith("/api/step/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            _, _, step_id, action = parts
            step = STEP_MAP.get(step_id)
            if not step:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "unknown step"})
                return
            payload = self._read_json()
            try:
                with STATE_LOCK:
                    invalidate_step_check_cache(self.ctx, step_id)
                    if action == "check":
                        result = step.check(self.ctx)
                    elif action == "apply":
                        if not step.apply:
                            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "no apply"})
                            return
                        result = step.apply(self.ctx, payload)
                    elif action == "skip":
                        result = StepResult(
                            ok=True,
                            message="Skipped manually",
                            manual=step.skip_manual,
                            cwd=str(INSTALL_DIR),
                        )
                        self.ctx.set(f"skipped_{step_id}", True)
                    else:
                        self._json_response(HTTPStatus.BAD_REQUEST, {"error": "unknown action"})
                        return
                    save_step_check_cache(self.ctx, step_id, result)
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "ok": result.ok,
                        "message": result.message,
                        "manual": result.manual,
                        "cwd": result.cwd,
                        "data": result.data,
                    },
                )
            except Exception as exc:
                append_log(traceback.format_exc())
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "ok": False,
                        "message": str(exc),
                        "manual": step.skip_manual,
                        "cwd": str(INSTALL_DIR),
                        "data": {},
                    },
                )
            return

        if path == "/api/aws/provision":
            payload = self._read_json()
            try:
                from aws_setup import provision_s3

                with STATE_LOCK:
                    invalidate_step_check_cache(self.ctx, "aws")
                    result = provision_s3(
                        bootstrap_access_key=(payload.get("aws_bootstrap_access_key_id") or "").strip(),
                        bootstrap_secret_key=(payload.get("aws_bootstrap_secret_access_key") or "").strip(),
                        region=(payload.get("aws_s3_region_name") or "eu-north-1").strip(),
                        bucket_name=(payload.get("aws_storage_bucket_name") or "").strip(),
                        log=append_log,
                    )
                self._json_response(
                    HTTPStatus.OK,
                    {"ok": True, "message": "AWS S3 bucket and IAM user created.", **result},
                )
            except Exception as exc:
                append_log(traceback.format_exc())
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "ok": False,
                        "message": str(exc),
                        "manual": "Open /aws-guide.html for manual setup instructions.",
                    },
                )
            return

        if path == "/api/finish":
            try:
                port = PORT
                if shutil_which_ufw_active():
                    import subprocess

                    subprocess.run(
                        ["ufw", "delete", "allow", f"{port}/tcp"],
                        capture_output=True,
                        check=False,
                    )
            except OSError:
                pass
            SHUTDOWN.set()
            self._json_response(HTTPStatus.OK, {"ok": True, "message": "Installer is shutting down."})
            threading.Thread(target=self.server.shutdown, daemon=True).start()  # type: ignore[attr-defined]
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()


def shutil_which_ufw_active() -> bool:
    import shutil
    import subprocess

    if not shutil.which("ufw"):
        return False
    proc = subprocess.run(["ufw", "status"], capture_output=True, text=True, check=False)
    return "Status: active" in (proc.stdout or "")


def main() -> None:
    if not TOKEN:
        print("RUMBLE_INSTALLER_TOKEN is not set", file=sys.stderr)
        sys.exit(1)

    ctx = InstallerContext(INSTALL_DIR, append_log)
    append_log(f"Installer started: {INSTALL_DIR}, port {PORT}")

    class Server(ThreadingHTTPServer):
        pass

    Server.installer_ctx = ctx  # type: ignore[attr-defined]

    httpd = Server(("0.0.0.0", PORT), InstallerHandler)

    def handle_signal(signum: int, _frame) -> None:
        append_log(f"Signal {signum}, shutting down...")
        SHUTDOWN.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        httpd.serve_forever()
    finally:
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except OSError:
                pass
        append_log("Installer stopped.")


if __name__ == "__main__":
    main()
