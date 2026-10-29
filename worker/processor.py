"""Process one batch item through the POC pipeline."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict

from main import analyze, build_result

from worker.mongo_store import BatchStore, ITEM_COMPLETED, ITEM_FAILED, summarize_result, utcnow
from worker.settings import Settings
from worker.storage import download_to_temp

logger = logging.getLogger(__name__)


def process_item(
    store: BatchStore,
    settings: Settings,
    batch: Dict[str, Any],
    item: Dict[str, Any],
) -> bool:
    """Process one item. Returns True if failed."""
    batch_id = batch["_id"]
    item_id = str(item["itemId"])
    storage_key = item["storageKey"]
    tmp: Path | None = None
    started = time.perf_counter()
    try:
        tmp = download_to_temp(settings, storage_key)
        run = analyze(str(tmp), region=settings.phone_region)
        result = build_result(storage_key, run)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        doc = {
            "batchId": batch_id,
            "itemId": item_id,
            "deviceId": batch.get("deviceId"),
            "userId": batch.get("userId"),
            "storageKey": storage_key,
            "capturedAt": item.get("capturedAt"),
            "processedAt": utcnow(),
            "processingMs": elapsed_ms,
            "status": "COMPLETED",
            "analysis": summarize_result(result),
            "result": result,
            "error": None,
        }
        store.upsert_result(doc)
        store.set_item_status(batch_id, item_id, ITEM_COMPLETED)
        analysis = doc["analysis"]
        logger.info(
            "Item done batch=%s item=%s screen=%s alert=%s reason=%s ms=%d",
            batch_id,
            item_id,
            analysis.get("screenType"),
            analysis.get("alert"),
            analysis.get("reasonCode"),
            elapsed_ms,
        )
        return False
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("Item failed batch=%s item=%s", batch_id, item_id)
        store.upsert_result(
            {
                "batchId": batch_id,
                "itemId": item_id,
                "deviceId": batch.get("deviceId"),
                "userId": batch.get("userId"),
                "storageKey": storage_key,
                "capturedAt": item.get("capturedAt"),
                "processedAt": utcnow(),
                "processingMs": elapsed_ms,
                "status": "FAILED",
                "analysis": None,
                "result": None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        store.set_item_status(batch_id, item_id, ITEM_FAILED)
        return True
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def process_batch(store: BatchStore, settings: Settings, batch: Dict[str, Any]) -> None:
    items = batch.get("items") or []
    failed = 0
    for item in items:
        if item.get("status") not in (None, "PENDING", ITEM_FAILED):
            continue
        if process_item(store, settings, batch, item):
            failed += 1
    store.mark_batch_finished(batch["_id"], failed, len(items))
    logger.info("Batch completed id=%s items=%d failed=%d", batch["_id"], len(items), failed)
