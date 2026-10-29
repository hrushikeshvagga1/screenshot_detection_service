"""Entrypoint: poll Mongo for PENDING batches and process them."""

from __future__ import annotations

import logging
import signal
import sys
import time

from worker.mongo_store import BatchStore
from worker.processor import process_batch
from worker.settings import Settings

logger = logging.getLogger(__name__)
_shutdown = False


def _handle_signal(signum, _frame) -> None:
    global _shutdown
    logger.info("Received signal %s, shutting down after current batch", signum)
    _shutdown = True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    store = BatchStore(settings)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "Worker started id=%s poll=%ss batches=%s results=%s",
        settings.worker_id,
        settings.poll_interval_sec,
        settings.batches_collection,
        settings.results_collection,
    )

    try:
        while not _shutdown:
            store.requeue_stale_processing()
            batch = store.claim_next_batch()
            if batch is None:
                time.sleep(settings.poll_interval_sec)
                continue
            try:
                process_batch(store, settings, batch)
            except Exception:
                logger.exception("Batch processing crashed id=%s", batch.get("_id"))
                store.batches.update_one(
                    {"_id": batch["_id"]},
                    {"$set": {"status": "PENDING"}},
                )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
