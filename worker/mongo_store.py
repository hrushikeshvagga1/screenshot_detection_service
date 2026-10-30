"""MongoDB batch claim (tick) and result upsert."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

from worker.settings import Settings

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"
STATUS_PARTIAL_FAILED = "PARTIAL_FAILED"
STATUS_FAILED = "FAILED"

ITEM_PENDING = "PENDING"
ITEM_COMPLETED = "COMPLETED"
ITEM_FAILED = "FAILED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BatchStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = MongoClient(settings.mongodb_uri)
        self._db: Database = self._client.get_default_database()
        self.batches: Collection = self._db[settings.batches_collection]
        self.results: Collection = self._db[settings.results_collection]
        self._ensure_indexes()

    def close(self) -> None:
        self._client.close()

    def _ensure_indexes(self) -> None:
        self.batches.create_index([("status", ASCENDING), ("receivedAt", ASCENDING)])
        self.results.create_index([("batchId", ASCENDING), ("itemId", ASCENDING)], unique=True)
        self.results.create_index([("deviceId", ASCENDING), ("processedAt", ASCENDING)])
        self.results.create_index([("analysis.alert", ASCENDING), ("processedAt", ASCENDING)])

    def requeue_stale_processing(self) -> int:
        """Reset batches stuck in PROCESSING (worker crash / Cloud Run restart)."""
        from datetime import timedelta

        cutoff = utcnow() - timedelta(minutes=self._settings.stale_processing_minutes)
        result = self.batches.update_many(
            {
                "status": STATUS_PROCESSING,
                "claim.claimedAt": {"$lt": cutoff},
                "claim.attempts": {"$lt": self._settings.max_batch_attempts},
            },
            {"$set": {"status": STATUS_PENDING}, "$inc": {"claim.attempts": 1}},
        )
        if result.modified_count:
            logger.warning("Re-queued %d stale PROCESSING batch(es)", result.modified_count)
        return result.modified_count

    def claim_next_batch(self) -> Optional[Dict[str, Any]]:
        """Atomic tick: take one PENDING batch."""
        now = utcnow()
        doc = self.batches.find_one_and_update(
            {
                "status": STATUS_PENDING,
                "claim.attempts": {"$lt": self._settings.max_batch_attempts},
            },
            {
                "$set": {
                    "status": STATUS_PROCESSING,
                    "claim.workerId": self._settings.worker_id,
                    "claim.claimedAt": now,
                },
                "$inc": {"claim.attempts": 1},
            },
            sort=[("receivedAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return doc

    @staticmethod
    def _parse_batch_id(batch_id: Union[str, ObjectId]) -> Union[ObjectId, str]:
        if isinstance(batch_id, ObjectId):
            return batch_id
        try:
            return ObjectId(batch_id)
        except (InvalidId, TypeError):
            return batch_id

    ClaimOutcome = Literal["claimed", "already_done", "in_progress", "missing", "not_claimable"]

    def claim_batch_by_id(self, batch_id: Union[str, ObjectId]) -> Tuple[ClaimOutcome, Optional[Dict[str, Any]]]:
        """Claim a batch referenced by a queue message (idempotent for duplicates)."""
        oid = self._parse_batch_id(batch_id)
        existing = self.batches.find_one({"_id": oid})
        if existing is None:
            return "missing", None
        if existing.get("status") in (STATUS_COMPLETED, STATUS_PARTIAL_FAILED, STATUS_FAILED):
            return "already_done", None
        if existing.get("status") == STATUS_PROCESSING:
            return "in_progress", None
        if existing.get("status") != STATUS_PENDING:
            return "not_claimable", None
        if int(existing.get("claim", {}).get("attempts", 0)) >= self._settings.max_batch_attempts:
            return "not_claimable", None

        now = utcnow()
        doc = self.batches.find_one_and_update(
            {"_id": oid, "status": STATUS_PENDING},
            {
                "$set": {
                    "status": STATUS_PROCESSING,
                    "claim.workerId": self._settings.worker_id,
                    "claim.claimedAt": now,
                },
                "$inc": {"claim.attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            return "in_progress", None
        return "claimed", doc

    def mark_batch_finished(self, batch_id: Any, failed_items: int, total: int) -> None:
        if failed_items == 0:
            status = STATUS_COMPLETED
        elif failed_items >= total:
            status = STATUS_FAILED
        else:
            status = STATUS_PARTIAL_FAILED
        self.batches.update_one(
            {"_id": batch_id},
            {
                "$set": {
                    "status": status,
                    "completedAt": utcnow(),
                    "counts.failed": failed_items,
                    "counts.processed": total - failed_items,
                }
            },
        )

    def set_item_status(self, batch_id: Any, item_id: str, status: str) -> None:
        update = {"$set": {"items.$.status": status, "items.$.analysisStatus": status}}
        for field in ("itemId", "jobId", "clientId"):
            result = self.batches.update_one({"_id": batch_id, f"items.{field}": item_id}, update)
            if result.modified_count:
                return

    def upsert_result(self, document: Dict[str, Any]) -> None:
        self.results.update_one(
            {"batchId": document["batchId"], "itemId": document["itemId"]},
            {"$set": document},
            upsert=True,
        )


def summarize_result(full: Dict[str, Any]) -> Dict[str, Any]:
    sc = full.get("screen_classification") or {}
    dec = full.get("decision") or {}
    pd = full.get("phone_detection") or {}
    ocr = full.get("ocr") or {}
    return {
        "screenType": sc.get("type"),
        "screenConfidence": sc.get("confidence"),
        "alert": dec.get("alert"),
        "reviewRequired": dec.get("review_required"),
        "reasonCode": dec.get("reason_code"),
        "candidateCount": pd.get("candidate_count", 0),
        "ocrReliable": ocr.get("reliable"),
        "ocrSuccess": ocr.get("success"),
    }
