"""Entrypoint: consume batch jobs from Redis or SQS, then process via Mongo claim."""

from __future__ import annotations

import logging
import signal
import sys

from worker.mongo_store import BatchStore
from worker.processor import process_batch
from worker.queue_backend import ReceivedMessage, create_queue
from worker.settings import Settings

logger = logging.getLogger(__name__)
_shutdown = False


def _handle_signal(signum, _frame) -> None:
    global _shutdown
    logger.info("Received signal %s, shutting down after current message", signum)
    _shutdown = True


def _handle_message(store: BatchStore, settings: Settings, received: ReceivedMessage, queue) -> None:
    batch_id = received.body.batch_id
    outcome, batch = store.claim_batch_by_id(batch_id)

    if outcome == "missing":
        logger.error("Queue message references missing batch id=%s — acking to drop", batch_id)
        queue.ack(received)
        return
    if outcome == "already_done":
        logger.info("Batch id=%s already finished — acking duplicate message", batch_id)
        queue.ack(received)
        return
    if outcome in ("in_progress", "not_claimable"):
        logger.warning("Batch id=%s not claimable (%s) — nack for retry", batch_id, outcome)
        queue.nack(received)
        return

    try:
        process_batch(store, settings, batch)
        queue.ack(received)
    except Exception:
        logger.exception("Batch processing crashed id=%s", batch_id)
        store.batches.update_one({"_id": batch["_id"]}, {"$set": {"status": "PENDING"}})
        queue.nack(received)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    store = BatchStore(settings)
    queue = create_queue(settings)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "Queue worker started id=%s backend=%s batches=%s results=%s",
        settings.worker_id,
        settings.queue_backend,
        settings.batches_collection,
        settings.results_collection,
    )

    try:
        while not _shutdown:
            store.requeue_stale_processing()
            received = queue.receive(wait_seconds=settings.queue_receive_wait_seconds)
            if received is None:
                continue
            _handle_message(store, settings, received, queue)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
