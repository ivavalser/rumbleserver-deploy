#!/usr/bin/env python3
"""S3 access check — runs inside the Rumble Server Docker image (boto3 available)."""

from __future__ import annotations

import json
import os
import secrets
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def _client_error_detail(exc: Exception) -> tuple[str, str]:
    response = getattr(exc, "response", None) or {}
    err = response.get("Error", {}) if isinstance(response, dict) else {}
    code = str(err.get("Code", "") or type(exc).__name__)
    message = str(err.get("Message", "") or exc)
    return code, message


def _normalize_bucket_region(location_constraint: str | None) -> str:
    if not location_constraint:
        return "us-east-1"
    if location_constraint == "EU":
        return "eu-west-1"
    return location_constraint


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    vendor = os.environ.get("INSTALLER_S3_VENDOR", "aws")
    access_key = os.environ.get("S3_ACCESS_KEY_ID", "")
    secret = os.environ.get("S3_SECRET_ACCESS_KEY", "")
    bucket = os.environ.get("S3_BUCKET_NAME", "")
    region = os.environ.get("S3_REGION_NAME", "")
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    addressing = os.environ.get("S3_ADDRESSING_STYLE", "auto")
    sig = os.environ.get("S3_SIGNATURE_VERSION", "s3v4")

    checks: list[dict[str, object]] = []
    bucket_region = region

    def fail(
        message: str,
        *,
        manual: str = "",
        retryable: bool = True,
    ) -> None:
        _emit(
            {
                "ok": False,
                "message": message,
                "checks": checks,
                "manual": manual,
                "retryable": retryable,
                "bucket_region": bucket_region,
            }
        )

    if not access_key or not secret:
        fail("S3 access key and secret are required.", retryable=False)
    if not bucket:
        fail("Bucket name is required.", retryable=False)

    config_kwargs: dict = {}
    if addressing and addressing != "auto":
        config_kwargs["s3"] = {"addressing_style": addressing}
    if sig:
        config_kwargs["signature_version"] = sig
    config = Config(**config_kwargs) if config_kwargs else None

    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret,
        region_name=region,
    )

    if vendor == "aws":
        try:
            identity = session.client("sts").get_caller_identity()
            checks.append(
                {
                    "label": f"Credentials valid (account {identity.get('Account', '?')})",
                    "ok": True,
                }
            )
        except ClientError as exc:
            checks.append({"label": "Credentials valid", "ok": False})
            _code, message = _client_error_detail(exc)
            fail("AWS credentials are invalid or expired.", manual=message)

    def s3_client(region_name: str):
        kwargs: dict = {"region_name": region_name}
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        if config:
            kwargs["config"] = config
        return session.client("s3", **kwargs)

    s3 = s3_client(region)

    if vendor == "aws":
        try:
            loc_resp = s3.get_bucket_location(Bucket=bucket)
            bucket_region = _normalize_bucket_region(loc_resp.get("LocationConstraint"))
            if bucket_region != region:
                s3 = s3_client(bucket_region)
            checks.append({"label": "s3:GetBucketLocation", "ok": True})
            if bucket_region != region:
                checks.append({"label": f"Bucket region: {bucket_region}", "ok": True})
        except ClientError as exc:
            checks.append({"label": "s3:GetBucketLocation", "ok": False})
            _code, message = _client_error_detail(exc)
            fail(
                f"Cannot access bucket '{bucket}'.",
                manual=message
                or (
                    "Check bucket name, region, and IAM policy "
                    "(ListBucket + GetBucketLocation on the bucket ARN)."
                ),
            )
    else:
        try:
            s3.head_bucket(Bucket=bucket)
            checks.append({"label": "s3:HeadBucket", "ok": True})
        except ClientError as exc:
            checks.append({"label": "s3:HeadBucket", "ok": False})
            code, message = _client_error_detail(exc)
            fail(
                f"Cannot access bucket '{bucket}'.",
                manual=f"{code}: {message}",
            )

    try:
        s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
        checks.append({"label": "s3:ListBucket", "ok": True})
    except ClientError as exc:
        checks.append({"label": "s3:ListBucket", "ok": False})
        code, message = _client_error_detail(exc)
        if vendor == "aws" and code in {"AccessDenied", "403", "AllAccessDisabled"}:
            fail(
                "Missing s3:ListBucket permission.",
                manual=(
                    f"IAM user needs s3:ListBucket on arn:aws:s3:::{bucket} (not on /*). "
                    f"Bucket region: {bucket_region}. "
                    f"AWS: {code}: {message}"
                ),
                retryable=False,
            )
        fail(
            "ListBucket check failed.",
            manual=f"{code}: {message}",
        )

    test_key = f".installer-test-{secrets.token_hex(8)}"
    test_body = b"rumbleserver-installer-access-test"
    try:
        s3.put_object(Bucket=bucket, Key=test_key, Body=test_body)
        checks.append({"label": "s3:PutObject", "ok": True})
        obj = s3.get_object(Bucket=bucket, Key=test_key)
        obj["Body"].read()
        checks.append({"label": "s3:GetObject", "ok": True})
        s3.delete_object(Bucket=bucket, Key=test_key)
        checks.append({"label": "s3:DeleteObject", "ok": True})
    except ClientError as exc:
        code, message = _client_error_detail(exc)
        if not any(c["label"] == "s3:PutObject" for c in checks):
            checks.append({"label": "s3:PutObject", "ok": False})
        elif not any(c["label"] == "s3:GetObject" for c in checks):
            checks.append({"label": "s3:GetObject", "ok": False})
        else:
            checks.append({"label": "s3:DeleteObject", "ok": False})
        try:
            s3.delete_object(Bucket=bucket, Key=test_key)
        except ClientError:
            pass
        fail(
            "Missing object read/write permissions on the bucket.",
            manual=f"{code}: {message}",
            retryable=False,
        )

    vendor_label = "AWS S3" if vendor == "aws" else "S3"
    _emit(
        {
            "ok": True,
            "message": f"{vendor_label} access verified for bucket '{bucket}'.",
            "checks": checks,
            "retryable": True,
            "bucket_region": bucket_region,
        }
    )


if __name__ == "__main__":
    main()
