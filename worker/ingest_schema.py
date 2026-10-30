"""Map device / ingest batch documents to worker item fields.

Ingest may label object location as gcsBucket + gcsPath (historical naming).
Staging/production here use DigitalOcean Spaces only: those fields are mapped to
s3://bucket/key and downloaded via boto3 + SPACES_* env — not Google Cloud Storage.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from worker.mongo_store import ITEM_COMPLETED, ITEM_FAILED


def batch_user_id(batch: Dict[str, Any]) -> Optional[str]:
    return batch.get("userId") or batch.get("staffId") or batch.get("cpId")


def item_id(item: Dict[str, Any]) -> str:
    for key in ("itemId", "jobId", "clientId"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value)
    raise KeyError("item missing itemId, jobId, and clientId")


def item_storage_key(item: Dict[str, Any]) -> str:
    """Resolve object location for Spaces (S3-compatible), regardless of field names."""
    if item.get("storageKey"):
        return str(item["storageKey"]).strip()
    # gcsBucket/gcsPath = Spaces bucket + object key in this product (not GCS API).
    bucket = item.get("gcsBucket") or item.get("bucket")
    path = item.get("gcsPath") or item.get("path")
    if bucket and path:
        return f"s3://{bucket}/{str(path).lstrip('/')}"
    if path:
        return str(path).lstrip("/")
    raise KeyError("item missing storageKey or gcsBucket+gcsPath")


def item_captured_at(item: Dict[str, Any]) -> Any:
    if item.get("capturedAt") is not None:
        return item.get("capturedAt")
    if item.get("deviceTs") is not None:
        return item.get("deviceTs")
    return None


def should_process_item(item: Dict[str, Any]) -> bool:
    """Ingest uses lowercase 'completed' for upload; worker uses 'COMPLETED' for analysis done."""
    analysis = item.get("analysisStatus") or item.get("analysis_status")
    if isinstance(analysis, str) and analysis.upper() == ITEM_COMPLETED:
        return False

    status = item.get("status")
    if status is None:
        return True
    if not isinstance(status, str):
        return True
    if status == ITEM_COMPLETED:
        return False
    if status.upper() == ITEM_FAILED:
        return True
    return True
