"""Worker configuration from environment (Cloud Run / GCE friendly)."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value not in ("", None) else default


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    batches_collection: str
    results_collection: str
    # Object storage (use one primary backend)
    spaces_bucket: str | None
    spaces_endpoint: str | None
    spaces_access_key: str | None
    spaces_secret_key: str | None
    gcs_bucket: str | None
    local_storage_root: str | None
    poll_interval_sec: float
    stale_processing_minutes: int
    max_batch_attempts: int
    worker_id: str
    phone_region: str

    @property
    def spaces_configured(self) -> bool:
        return bool(
            self.spaces_bucket
            and self.spaces_endpoint
            and self.spaces_access_key
            and self.spaces_secret_key
        )

    @property
    def default_object_bucket(self) -> str | None:
        """Bucket used when storageKey is a bare object path."""
        return self.spaces_bucket or self.gcs_bucket

    @classmethod
    def from_env(cls) -> "Settings":
        uri = _env("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is required")

        spaces_bucket = _env("SPACES_BUCKET") or _env("S3_BUCKET")
        spaces_endpoint = _env("SPACES_ENDPOINT") or _env("S3_ENDPOINT_URL")
        spaces_key = _env("SPACES_ACCESS_KEY") or _env("AWS_ACCESS_KEY_ID")
        spaces_secret = _env("SPACES_SECRET_KEY") or _env("AWS_SECRET_ACCESS_KEY")
        gcs = _env("GCS_BUCKET")
        local = _env("LOCAL_STORAGE_ROOT")

        has_spaces = bool(spaces_bucket and spaces_endpoint and spaces_key and spaces_secret)
        if not has_spaces and not gcs and not local:
            raise RuntimeError(
                "Configure object storage: DigitalOcean Spaces "
                "(SPACES_BUCKET, SPACES_ENDPOINT, SPACES_ACCESS_KEY, SPACES_SECRET_KEY), "
                "or GCS_BUCKET, or LOCAL_STORAGE_ROOT for dev"
            )
        if spaces_bucket and not has_spaces:
            raise RuntimeError(
                "SPACES_BUCKET is set but SPACES_ENDPOINT and/or credentials are missing"
            )

        return cls(
            mongodb_uri=uri,
            batches_collection=_env("MONGO_BATCHES_COLLECTION", "screenshot_batches") or "screenshot_batches",
            results_collection=_env("MONGO_RESULTS_COLLECTION", "screenshot_analysis_results")
            or "screenshot_analysis_results",
            spaces_bucket=spaces_bucket,
            spaces_endpoint=spaces_endpoint.rstrip("/") if spaces_endpoint else None,
            spaces_access_key=spaces_key,
            spaces_secret_key=spaces_secret,
            gcs_bucket=gcs,
            local_storage_root=local,
            poll_interval_sec=float(_env("POLL_INTERVAL_SEC", "3") or "3"),
            stale_processing_minutes=int(_env("STALE_PROCESSING_MINUTES", "15") or "15"),
            max_batch_attempts=int(_env("MAX_BATCH_ATTEMPTS", "5") or "5"),
            worker_id=_env("WORKER_ID") or socket.gethostname(),
            phone_region=_env("PHONE_REGION", "IN") or "IN",
        )
