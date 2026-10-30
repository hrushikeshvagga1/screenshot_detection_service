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
    queue_backend: str
    redis_url: str | None
    redis_queue_key: str
    sqs_queue_url: str | None
    sqs_wait_time_seconds: int
    aws_region: str
    queue_receive_wait_seconds: int
    reconcile_batch_limit: int

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

        queue_backend = (_env("QUEUE_BACKEND", "redis") or "redis").lower()
        redis_url = _env("REDIS_URL")
        sqs_url = _env("SQS_QUEUE_URL")
        if queue_backend == "redis" and not redis_url:
            raise RuntimeError("QUEUE_BACKEND=redis requires REDIS_URL")
        if queue_backend == "sqs" and not sqs_url:
            raise RuntimeError("QUEUE_BACKEND=sqs requires SQS_QUEUE_URL")

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
            queue_backend=queue_backend,
            redis_url=redis_url,
            redis_queue_key=_env("REDIS_QUEUE_KEY", "screenshot:batch:jobs") or "screenshot:batch:jobs",
            sqs_queue_url=sqs_url,
            sqs_wait_time_seconds=int(_env("SQS_WAIT_TIME_SECONDS", "20") or "20"),
            aws_region=_env("AWS_REGION", "ap-south-1") or "ap-south-1",
            queue_receive_wait_seconds=int(_env("QUEUE_RECEIVE_WAIT_SECONDS", "20") or "20"),
            reconcile_batch_limit=int(_env("RECONCILE_BATCH_LIMIT", "500") or "500"),
        )
