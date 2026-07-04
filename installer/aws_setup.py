"""AWS S3 bucket + IAM user provisioning for the installer (via AWS CLI)."""

from __future__ import annotations

import json
import os
import re
import subprocess
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


def _run_aws(
    args: list[str],
    *,
    env: dict[str, str],
    log: Callable[[str], None],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = ["aws", *args, "--output", "json"]
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


def ensure_aws_cli(log: Callable[[str], None]) -> None:
    if subprocess.run(["aws", "--version"], capture_output=True).returncode == 0:
        return
    log("Installing awscli...")
    subprocess.run(["apt-get", "update", "-qq"], check=True)
    subprocess.run(["apt-get", "install", "-y", "awscli"], check=True)


def _iam_policy_document(bucket: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ListBucket",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{bucket}",
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
                "Resource": f"arn:aws:s3:::{bucket}/*",
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

    ensure_aws_cli(log)

    env = {
        **dict(os.environ),
        "AWS_ACCESS_KEY_ID": bootstrap_access_key,
        "AWS_SECRET_ACCESS_KEY": bootstrap_secret_key,
        "AWS_DEFAULT_REGION": region,
    }

    _run_aws(["sts", "get-caller-identity"], env=env, log=log)

    create_args = ["s3api", "create-bucket", "--bucket", bucket, "--region", region]
    if region != "us-east-1":
        create_args.extend(
            ["--create-bucket-configuration", f"LocationConstraint={region}"]
        )
    try:
        _run_aws(create_args, env=env, log=log)
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
