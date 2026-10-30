"""Enqueue one batch by Mongo _id or deviceBatchId."""

from __future__ import annotations

import argparse
import logging
import sys

from bson import ObjectId
from bson.errors import InvalidId

from worker.mongo_store import BatchStore
from worker.publish import publish_batch_job
from worker.settings import Settings

logger = logging.getLogger(__name__)


def _find_batch(store: BatchStore, batch_id: str | None, device_batch_id: str | None):
    if batch_id:
        try:
            oid = ObjectId(batch_id)
            doc = store.batches.find_one({"_id": oid})
            if doc:
                return doc
        except (InvalidId, TypeError):
            doc = store.batches.find_one({"_id": batch_id})
            if doc:
                return doc
    if device_batch_id:
        return store.batches.find_one({"deviceBatchId": device_batch_id})
    return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Publish one batch job to the queue")
    parser.add_argument("id", nargs="?", help="Mongo _id (24-char ObjectId or string _id)")
    parser.add_argument("--device-batch-id", dest="device_batch_id", help="Lookup by deviceBatchId UUID")
    args = parser.parse_args(argv)

    if not args.id and not args.device_batch_id:
        parser.error("Provide Mongo _id or --device-batch-id")

    settings = Settings.from_env()
    store = BatchStore(settings)
    doc = _find_batch(store, args.id, args.device_batch_id)
    if doc is None:
        logger.error(
            "Batch not found db=%s collection=%s id=%r deviceBatchId=%r",
            store._db.name,
            store.batches.name,
            args.id,
            args.device_batch_id,
        )
        store.close()
        return 1

    mongo_id = doc["_id"]
    logger.info(
        "Found batch _id=%s deviceBatchId=%s status=%s items=%d",
        mongo_id,
        doc.get("deviceBatchId"),
        doc.get("status"),
        len(doc.get("items") or []),
    )
    publish_batch_job(mongo_id, source="manual")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
