"""Download screenshot bytes to a local temp path.

Supports DigitalOcean Spaces (S3-compatible), Google Cloud Storage, and local files.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from worker.settings import Settings

logger = logging.getLogger(__name__)

_GS_URI = re.compile(r"^gs://([^/]+)/(.+)$")
_S3_URI = re.compile(r"^s3://([^/]+)/(.+)$")
_DO_VHOST = re.compile(r"^https?://([^.]+)\.[a-z0-9]+\.digitaloceanspaces\.com/(.+)$", re.I)
_DO_PATH = re.compile(r"^https?://[a-z0-9]+\.digitaloceanspaces\.com/([^/]+)/(.+)$", re.I)


@dataclass(frozen=True)
class ObjectRef:
    backend: str  # "spaces" | "gcs" | "local"
    bucket: str
    key: str


def download_to_temp(settings: Settings, storage_key: str) -> Path:
    """Return path to a temporary file; caller must delete when done."""
    suffix = Path(storage_key.split("?")[0].split("/")[-1]).suffix or ".jpg"
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="screenshot_")
    path = Path(tmp)
    try:
        data = _read_bytes(settings, storage_key)
        with open(fd, "wb") as f:
            f.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def resolve_object(settings: Settings, storage_key: str) -> ObjectRef:
    key = storage_key.strip()
    if not key:
        raise ValueError("Empty storageKey")

    match = _GS_URI.match(key)
    if match:
        return ObjectRef("gcs", match.group(1), match.group(2))

    match = _S3_URI.match(key)
    if match:
        return ObjectRef("spaces", match.group(1), match.group(2))

    if key.startswith("http://") or key.startswith("https://"):
        match = _DO_VHOST.match(key)
        if match:
            return ObjectRef("spaces", match.group(1), unquote(match.group(2)))
        match = _DO_PATH.match(key)
        if match:
            return ObjectRef("spaces", match.group(1), unquote(match.group(2)))
        parsed = urlparse(key)
        # Generic S3-compatible URL: path /bucket/key
        path = parsed.path.lstrip("/")
        if "/" in path:
            bucket, obj = path.split("/", 1)
            return ObjectRef("spaces", bucket, unquote(obj))

    default = settings.default_object_bucket
    if default:
        backend = "spaces" if settings.spaces_configured else "gcs"
        return ObjectRef(backend, default, key.lstrip("/"))

    if settings.local_storage_root:
        return ObjectRef("local", "", key.lstrip("/"))

    raise ValueError(f"Cannot resolve storage key: {storage_key!r}")


def _read_bytes(settings: Settings, storage_key: str) -> bytes:
    ref = resolve_object(settings, storage_key)
    if ref.backend == "local":
        local = Path(settings.local_storage_root) / ref.key  # type: ignore[arg-type]
        if not local.is_file():
            raise FileNotFoundError(f"Local storage file not found: {local}")
        return local.read_bytes()
    if ref.backend == "spaces":
        return _spaces_read(settings, ref.bucket, ref.key)
    if ref.backend == "gcs":
        return _gcs_read(ref.bucket, ref.key)
    raise ValueError(f"Unknown backend for {storage_key!r}")


def _spaces_read(settings: Settings, bucket: str, key: str) -> bytes:
    if not settings.spaces_configured:
        raise RuntimeError("DigitalOcean Spaces is not configured (missing endpoint or credentials)")
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=settings.spaces_endpoint,
        aws_access_key_id=settings.spaces_access_key,
        aws_secret_access_key=settings.spaces_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    logger.debug("Downloading s3://%s/%s via %s", bucket, key, settings.spaces_endpoint)
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def _gcs_read(bucket: str, blob_name: str) -> bytes:
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket).blob(blob_name)
    logger.debug("Downloading gs://%s/%s", bucket, blob_name)
    return blob.download_as_bytes()
