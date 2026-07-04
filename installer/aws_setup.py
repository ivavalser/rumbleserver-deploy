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


def ensure_aws_cli(log: Callable[[str], None]) -> str:
    """Return path to aws binary, installing awscli via apt/pip if needed."""
    path = shutil.which("aws")
    if path:
        return path

    log("AWS CLI not found — installing awscli...")
    subprocess.run(["apt-get", "update", "-qq"], check=True)
    apt = subprocess.run(
        ["apt-get", "install", "-y", "awscli"],
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

    path = shutil.which("aws")
    if path:
        log(f"AWS CLI installed: {path}")
        return path

    log("apt awscli missing — trying pip install awscli...")
    subprocess.run(["apt-get", "install", "-y", "python3-pip"], check=False)
    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "awscli"],
        capture_output=True,
        text=True,
    )
    if pip.returncode != 0:
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "awscli", "--break-system-packages"],
            capture_output=True,
            text=True,
        )
    if pip.stderr:
        for line in pip.stderr.strip().split("\n"):
            if line.strip():
                log(line)

    path = shutil.which("aws")
    if not path:
        raise RuntimeError(
            "AWS CLI (aws) is not installed and automatic install failed. "
            "On the server run: apt-get update && apt-get install -y awscli"
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
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
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
    )

    policy_name = f"RumbleServerS3-{bucket}"[:128]
    user_name = f"rumbleserver-s3-{bucket}"[:64]
    policy_doc = _iam_policy_document(bucket)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(policy_doc, tmp)
        policy_file = tmp.name

    try:
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
            )
            policy_arn = json.loads(create_policy.stdout)["Policy"]["Arn"]
        except RuntimeError:
            account = json.loads(
                _run_aws(["sts", "get-caller-identity"], env=env, log=log).stdout
            )["Account"]
            policy_arn = f"arn:aws:iam::{account}:policy/{policy_name}"
            log(f"Using existing policy ARN: {policy_arn}")

        try:
            _run_aws(["iam", "create-user", "--user-name", user_name], env=env, log=log)
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
            check=False,
        )

        keys = json.loads(
            _run_aws(
                ["iam", "create-access-key", "--user-name", user_name],
                env=env,
                log=log,
            ).stdout
        )["AccessKey"]

        return {
            "aws_access_key_id": keys["AccessKeyId"],
            "aws_secret_access_key": keys["SecretAccessKey"],
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

    def add(label: str, ok: bool) -> None:
        checks.append({"label": label, "ok": ok})

    try:
        identity = json.loads(
            _run_aws(["sts", "get-caller-identity"], env=env, log=log, aws_bin=aws_bin).stdout
        )
        add(f"Credentials valid (account {identity.get('Account', '?')})", True)
    except RuntimeError as exc:
        add("Credentials valid", False)
        return {
            "ok": False,
            "message": "AWS credentials are invalid or expired.",
            "checks": checks,
            "manual": str(exc),
        }

    try:
        _run_aws(
            ["s3api", "get-bucket-location", "--bucket", bucket],
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:GetBucketLocation", True)
    except RuntimeError:
        add("s3:GetBucketLocation", False)
        return {
            "ok": False,
            "message": f"Cannot access bucket '{bucket}'.",
            "checks": checks,
            "manual": "Check bucket name, region, and IAM policy (ListBucket + GetBucketLocation on the bucket ARN).",
        }

    try:
        _run_aws(
            ["s3api", "list-objects-v2", "--bucket", bucket, "--max-items", "1"],
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:ListBucket", True)
    except RuntimeError:
        add("s3:ListBucket", False)
        return {
            "ok": False,
            "message": "Missing s3:ListBucket permission.",
            "checks": checks,
        }

    test_key = f".installer-test-{secrets.token_hex(8)}"
    body_file = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
            tmp.write("rumbleserver-installer-access-test")
            body_file = tmp.name

        _run_aws(
            [
                "s3api",
                "put-object",
                "--bucket",
                bucket,
                "--key",
                test_key,
                "--body",
                body_file,
            ],
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:PutObject", True)

        _run_aws(
            [
                "s3api",
                "get-object",
                "--bucket",
                bucket,
                "--key",
                test_key,
                "/tmp/rumble-installer-get-test",
            ],
            env=env,
            log=log,
            aws_bin=aws_bin,
        )
        add("s3:GetObject", True)

        _run_aws(
            ["s3api", "delete-object", "--bucket", bucket, "--key", test_key],
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
            ["s3api", "delete-object", "--bucket", bucket, "--key", test_key],
            env=env,
            log=log,
            check=False,
        )
        return {
            "ok": False,
            "message": "Missing object read/write permissions on the bucket.",
            "checks": checks,
            "manual": str(exc),
        }
    finally:
        if body_file:
            Path(body_file).unlink(missing_ok=True)
        Path("/tmp/rumble-installer-get-test").unlink(missing_ok=True)

    return {
        "ok": True,
        "message": f"AWS S3 access verified for bucket '{bucket}'.",
        "checks": checks,
    }
