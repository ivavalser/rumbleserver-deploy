"""AWS S3 bucket + IAM user provisioning for the installer (via AWS CLI)."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


def _endpoint_for_region(region: str) -> str:
    return f"https://s3.{region}.amazonaws.com"


def _sanitize_bucket_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9.-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:63]


_AWS_BIN_CANDIDATES = (
    "/usr/local/bin/aws",
    "/usr/bin/aws",
    "/usr/local/aws-cli/v2/current/bin/aws",
)


def _extend_installer_path() -> None:
    extra = (
        "/usr/local/bin",
        "/usr/local/aws-cli/v2/current/bin",
        str(Path.home() / ".local/bin"),
    )
    current = os.environ.get("PATH", "")
    prefix = os.pathsep.join(p for p in extra if p not in current.split(os.pathsep))
    if prefix:
        os.environ["PATH"] = prefix + os.pathsep + current


def _locate_aws_bin() -> str | None:
    _extend_installer_path()
    path = shutil.which("aws")
    if path:
        return path
    for candidate in _AWS_BIN_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    local = Path.home() / ".local/bin/aws"
    if local.is_file():
        return str(local)
    return None


def _install_aws_cli_v2(log: Callable[[str], None]) -> None:
    import platform

    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
    zip_path = Path("/tmp/awscliv2.zip")
    log(f"Installing AWS CLI v2 ({arch}) from aws.amazon.com...")
    subprocess.run(
        ["apt-get", "install", "-y", "unzip", "curl", "ca-certificates"],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "curl",
            "-fsSL",
            f"https://awscli.amazonaws.com/awscli-exe-linux-{arch}.zip",
            "-o",
            str(zip_path),
        ],
        check=True,
    )
    subprocess.run(["unzip", "-oq", str(zip_path), "-d", "/tmp"], check=True)
    subprocess.run(["/tmp/aws/install", "--update"], check=True)


def find_aws_cli() -> str | None:
    """Return path to aws binary if already installed."""
    return _locate_aws_bin()


def ensure_aws_cli(log: Callable[[str], None]) -> str:
    """Return path to aws binary, installing awscli via apt/pip/AWS v2 if needed."""
    path = _locate_aws_bin()
    if path:
        return path

    log("AWS CLI not found — installing awscli (apt)...")
    subprocess.run(["apt-get", "update", "-qq"], check=False)
    apt = subprocess.run(
        ["apt-get", "install", "-y", "awscli", "unzip", "curl", "ca-certificates"],
        capture_output=True,
        text=True,
    )
    if apt.stdout:
        for line in apt.stdout.strip().split("\n"):
            if line.strip():
                log(line)
    if apt.stderr:
        for line in apt.stderr.strip().split("\n"):
            if line.strip():
                log(line)

    path = _locate_aws_bin()
    if path:
        log(f"AWS CLI installed: {path}")
        return path

    log("apt awscli missing — trying pip install awscli...")
    subprocess.run(["apt-get", "install", "-y", "python3-pip"], check=False)
    for pip_args in (
        [sys.executable, "-m", "pip", "install", "awscli"],
        [sys.executable, "-m", "pip", "install", "awscli", "--break-system-packages"],
    ):
        pip = subprocess.run(pip_args, capture_output=True, text=True)
        if pip.stdout:
            for line in pip.stdout.strip().split("\n"):
                if line.strip():
                    log(line)
        if pip.stderr:
            for line in pip.stderr.strip().split("\n"):
                if line.strip():
                    log(line)
        path = _locate_aws_bin()
        if path:
            log(f"AWS CLI installed via pip: {path}")
            return path

    try:
        _install_aws_cli_v2(log)
    except subprocess.CalledProcessError as exc:
        log(f"AWS CLI v2 install failed: {exc}")

    path = _locate_aws_bin()
    if not path:
        raise RuntimeError(
            "AWS CLI (aws) is not installed and automatic install failed. "
            "SSH to the server and run: apt-get update && apt-get install -y awscli unzip curl"
        )
    log(f"AWS CLI installed: {path}")
    return path


def _run_aws(
    args: list[str],
    *,
    env: dict[str, str],
    log: Callable[[str], None],
    check: bool = True,
    aws_bin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_path = aws_bin or ensure_aws_cli(log)
    cmd = [bin_path, *args, "--output", "json"]
    log("$ aws " + " ".join(args))
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"AWS CLI binary not found at {bin_path!r}. "
            "Restart the installer (kill PID + curl installer.sh again) so it can install awscli."
        ) from exc
    if proc.stdout:
        for line in proc.stdout.strip().split("\n"):
            if line.strip():
                log(line)
    if proc.stderr:
        for line in proc.stderr.strip().split("\n"):
            if line.strip():
                log(line)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"aws {' '.join(args)} failed: {detail}")
    return proc


def _normalize_bucket_region(location_constraint: str | None) -> str:
    if not location_constraint:
        return "us-east-1"
    if location_constraint == "EU":
        return "eu-west-1"
    return location_constraint


def _ensure_iam_policy(
    *,
    policy_name: str,
    policy_file: str,
    env: dict[str, str],
    log: Callable[[str], None],
    aws_bin: str,
) -> str:
    """Create IAM policy or publish a new default version if it already exists."""
    try:
        create_policy = _run_aws(
            [
                "iam",
                "create-policy",
                "--policy-name",
                policy_name,
                "--policy-document",
                f"file://{policy_file}",
            ],
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        policy_arn = json.loads(create_policy.stdout)["Policy"]["Arn"]
        log(f"IAM policy {policy_name} created.")
        return policy_arn
    except RuntimeError as exc:
        msg = str(exc)
        if "EntityAlreadyExists" not in msg and "already exists" not in msg.lower():
            raise
        account = json.loads(
            _run_aws(["sts", "get-caller-identity"], env=env, log=log, aws_bin=aws_bin).stdout
        )["Account"]
        policy_arn = f"arn:aws:iam::{account}:policy/{policy_name}"
        log(f"IAM policy {policy_name} already exists — publishing updated document.")
        try:
            _run_aws(
                [
                    "iam",
                    "create-policy-version",
                    "--policy-arn",
                    policy_arn,
                    "--policy-document",
                    f"file://{policy_file}",
                    "--set-as-default",
                ],
                env=env,
                log=log,
                aws_bin=aws_bin,
            )
        except RuntimeError as version_exc:
            if "LimitExceeded" in str(version_exc):
                log("Policy version limit reached — deleting oldest non-default version.")
                versions = json.loads(
                    _run_aws(
                        ["iam", "list-policy-versions", "--policy-arn", policy_arn],
                        env=env,
                        log=log,
                        aws_bin=aws_bin,
                    ).stdout
                )["Versions"]
                deletable = [
                    v["VersionId"]
                    for v in versions
                    if not v.get("IsDefaultVersion")
                ]
                if deletable:
                    _run_aws(
                        [
                            "iam",
                            "delete-policy-version",
                            "--policy-arn",
                            policy_arn,
                            "--version-id",
                            sorted(deletable)[0],
                        ],
                        env=env,
                        log=log,
                        aws_bin=aws_bin,
                    )
                    _run_aws(
                        [
                            "iam",
                            "create-policy-version",
                            "--policy-arn",
                            policy_arn,
                            "--policy-document",
                            f"file://{policy_file}",
                            "--set-as-default",
                        ],
                        env=env,
                        log=log,
                        aws_bin=aws_bin,
                    )
                else:
                    raise
            else:
                raise
        return policy_arn


def _ensure_user_access_key(
    user_name: str,
    *,
    env: dict[str, str],
    log: Callable[[str], None],
    aws_bin: str,
) -> dict[str, str]:
    """Create a new access key, deleting an old one if the 2-key quota is full."""
    try:
        created = json.loads(
            _run_aws(
                ["iam", "create-access-key", "--user-name", user_name],
                env=env,
                log=log,
                aws_bin=aws_bin,
            ).stdout
        )["AccessKey"]
        return {
            "aws_access_key_id": created["AccessKeyId"],
            "aws_secret_access_key": created["SecretAccessKey"],
        }
    except RuntimeError as exc:
        msg = str(exc)
        if "LimitExceeded" not in msg and "AccessKeysPerUser" not in msg:
            raise
        log(
            f"IAM user {user_name} already has 2 access keys — "
            "removing the oldest inactive key (or the oldest key)."
        )
        listed = json.loads(
            _run_aws(
                ["iam", "list-access-keys", "--user-name", user_name],
                env=env,
                log=log,
                aws_bin=aws_bin,
            ).stdout
        )["AccessKeyMetadata"]
        if not listed:
            raise RuntimeError(
                f"Cannot create access key for {user_name}: quota exceeded and no keys found."
            ) from exc
        inactive = [k for k in listed if k.get("Status") == "Inactive"]
        victim = (
            sorted(inactive, key=lambda k: k.get("CreateDate", ""))[0]
            if inactive
            else sorted(listed, key=lambda k: k.get("CreateDate", ""))[0]
        )
        key_id = victim["AccessKeyId"]
        log(
            f"Deleting access key {key_id} "
            f"(status={victim.get('Status', '?')}, created={victim.get('CreateDate', '?')})."
        )
        _run_aws(
            [
                "iam",
                "delete-access-key",
                "--user-name",
                user_name,
                "--access-key-id",
                key_id,
            ],
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        created = json.loads(
            _run_aws(
                ["iam", "create-access-key", "--user-name", user_name],
                env=env,
                log=log,
                aws_bin=aws_bin,
            ).stdout
        )["AccessKey"]
        return {
            "aws_access_key_id": created["AccessKeyId"],
            "aws_secret_access_key": created["SecretAccessKey"],
        }


def _iam_policy_document(bucket: str) -> dict[str, Any]:
    bucket_arn = f"arn:aws:s3:::{bucket}"
    objects_arn = f"arn:aws:s3:::{bucket}/*"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BucketAccess",
                "Effect": "Allow",
                "Action": [
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                ],
                "Resource": bucket_arn,
            },
            {
                "Sid": "ObjectAccess",
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:AbortMultipartUpload",
                    "s3:ListMultipartUploadParts",
                ],
                "Resource": objects_arn,
            },
        ],
    }


def provision_s3(
    *,
    bootstrap_access_key: str,
    bootstrap_secret_key: str,
    region: str,
    bucket_name: str,
    log: Callable[[str], None],
) -> dict[str, str]:
    """Create bucket, block public access, IAM user + policy + access key."""
    bucket = _sanitize_bucket_name(bucket_name)
    if len(bucket) < 3:
        raise ValueError("Bucket name must be at least 3 characters.")

    aws_bin = ensure_aws_cli(log)

    env = {
        **dict(os.environ),
        "AWS_ACCESS_KEY_ID": bootstrap_access_key,
        "AWS_SECRET_ACCESS_KEY": bootstrap_secret_key,
        "AWS_DEFAULT_REGION": region,
    }

    _run_aws(["sts", "get-caller-identity"], env=env, log=log, aws_bin=aws_bin)

    create_args = ["s3api", "create-bucket", "--bucket", bucket, "--region", region]
    if region != "us-east-1":
        create_args.extend(
            ["--create-bucket-configuration", f"LocationConstraint={region}"]
        )
    try:
        _run_aws(create_args, env=env, log=log, aws_bin=aws_bin)
        log(f"Bucket {bucket} created.")
    except RuntimeError as exc:
        msg = str(exc)
        if "BucketAlreadyOwnedByYou" in msg or "BucketAlreadyExists" in msg:
            log(f"Bucket {bucket} already exists, continuing.")
        else:
            raise

    _run_aws(
        [
            "s3api",
            "put-public-access-block",
            "--bucket",
            bucket,
            "--public-access-block-configuration",
            "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true",
        ],
        env=env,
        log=log,
        aws_bin=aws_bin,
    )

    policy_name = f"RumbleServerS3-{bucket}"[:128]
    user_name = f"rumbleserver-s3-{bucket}"[:64]
    policy_doc = _iam_policy_document(bucket)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(policy_doc, tmp)
        policy_file = tmp.name

    try:
        policy_arn = _ensure_iam_policy(
            policy_name=policy_name,
            policy_file=policy_file,
            env=env,
            log=log,
            aws_bin=aws_bin,
        )

        try:
            _run_aws(["iam", "create-user", "--user-name", user_name], env=env, log=log, aws_bin=aws_bin)
        except RuntimeError as exc:
            if "EntityAlreadyExists" not in str(exc):
                raise
            log(f"IAM user {user_name} already exists.")

        _run_aws(
            [
                "iam",
                "attach-user-policy",
                "--user-name",
                user_name,
                "--policy-arn",
                policy_arn,
            ],
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        log(f"Policy attached to IAM user {user_name}.")

        key_pair = _ensure_user_access_key(
            user_name,
            env=env,
            log=log,
            aws_bin=aws_bin,
        )

        return {
            **key_pair,
            "aws_storage_bucket_name": bucket,
            "aws_s3_region_name": region,
            "aws_s3_endpoint_url": _endpoint_for_region(region),
        }
    finally:
        Path(policy_file).unlink(missing_ok=True)


def verify_s3_access(
    *,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    bucket: str,
    log: Callable[[str], None],
) -> dict[str, Any]:
    """Verify app IAM user can use the bucket (list / put / get / delete)."""
    bucket = _sanitize_bucket_name(bucket)
    if not access_key_id or not secret_access_key:
        raise ValueError("AWS access key and secret are required.")
    if not bucket:
        raise ValueError("Bucket name is required.")

    aws_bin = ensure_aws_cli(log)

    env = {
        **dict(os.environ),
        "AWS_ACCESS_KEY_ID": access_key_id,
        "AWS_SECRET_ACCESS_KEY": secret_access_key,
        "AWS_DEFAULT_REGION": region,
    }

    checks: list[dict[str, Any]] = []
    bucket_region = region

    def add(label: str, ok: bool) -> None:
        checks.append({"label": label, "ok": ok})

    def fail(
        message: str,
        *,
        manual: str = "",
        retryable: bool = True,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "message": message,
            "checks": checks,
            "manual": manual,
            "retryable": retryable,
            "bucket_region": bucket_region,
        }

    try:
        identity = json.loads(
            _run_aws(["sts", "get-caller-identity"], env=env, log=log, aws_bin=aws_bin).stdout
        )
        add(f"Credentials valid (account {identity.get('Account', '?')})", True)
    except RuntimeError as exc:
        add("Credentials valid", False)
        return fail(
            "AWS credentials are invalid or expired.",
            manual=str(exc),
            retryable=True,
        )

    try:
        loc_resp = json.loads(
            _run_aws(
                ["s3api", "get-bucket-location", "--bucket", bucket],
                env=env,
                log=log,
                aws_bin=aws_bin,
            ).stdout
        )
        bucket_region = _normalize_bucket_region(loc_resp.get("LocationConstraint"))
        env["AWS_DEFAULT_REGION"] = bucket_region
        add("s3:GetBucketLocation", True)
        if bucket_region != region:
            add(f"Bucket region: {bucket_region}", True)
            log(
                f"Bucket {bucket} is in {bucket_region}; "
                f"installer had {region} — using bucket region for S3 checks."
            )
    except RuntimeError as exc:
        add("s3:GetBucketLocation", False)
        return fail(
            f"Cannot access bucket '{bucket}'.",
            manual=str(exc) or (
                "Check bucket name, region, and IAM policy "
                "(ListBucket + GetBucketLocation on the bucket ARN)."
            ),
            retryable=True,
        )

    def s3_args(base: list[str]) -> list[str]:
        return [*base, "--region", bucket_region]

    try:
        _run_aws(
            s3_args(
                ["s3api", "list-objects-v2", "--bucket", bucket, "--max-items", "1"]
            ),
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:ListBucket", True)
    except RuntimeError as exc:
        add("s3:ListBucket", False)
        detail = str(exc).strip()
        return fail(
            "Missing s3:ListBucket permission.",
            manual=(
                f"IAM policy must allow s3:ListBucket on arn:aws:s3:::{bucket} "
                f"(bucket ARN, not /*). Bucket region: {bucket_region}. "
                f"Re-run Create S3 bucket to refresh the policy, or fix JSON in IAM. "
                f"AWS: {detail}"
            ),
            retryable=False,
        )

    test_key = f".installer-test-{secrets.token_hex(8)}"
    body_file = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
            tmp.write("rumbleserver-installer-access-test")
            body_file = tmp.name

        _run_aws(
            s3_args(
                [
                    "s3api",
                    "put-object",
                    "--bucket",
                    bucket,
                    "--key",
                    test_key,
                    "--body",
                    body_file,
                ]
            ),
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:PutObject", True)

        _run_aws(
            s3_args(
                [
                    "s3api",
                    "get-object",
                    "--bucket",
                    bucket,
                    "--key",
                    test_key,
                    "/tmp/rumble-installer-get-test",
                ]
            ),
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:GetObject", True)

        _run_aws(
            s3_args(
                ["s3api", "delete-object", "--bucket", bucket, "--key", test_key]
            ),
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:DeleteObject", True)
    except RuntimeError as exc:
        if not any(c["label"] == "s3:PutObject" for c in checks):
            add("s3:PutObject", False)
        elif not any(c["label"] == "s3:GetObject" for c in checks):
            add("s3:GetObject", False)
        else:
            add("s3:DeleteObject", False)
        _run_aws(
            s3_args(["s3api", "delete-object", "--bucket", bucket, "--key", test_key]),
            env=env,
            log=log,
            check=False,
            aws_bin=aws_bin,
        )
        return fail(
            "Missing object read/write permissions on the bucket.",
            manual=str(exc),
            retryable=False,
        )
    finally:
        if body_file:
            Path(body_file).unlink(missing_ok=True)
        Path("/tmp/rumble-installer-get-test").unlink(missing_ok=True)

    return {
        "ok": True,
        "message": f"AWS S3 access verified for bucket '{bucket}'.",
        "checks": checks,
        "retryable": True,
        "bucket_region": bucket_region,
    }
