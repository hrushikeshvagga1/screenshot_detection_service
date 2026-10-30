"""Re-publish PENDING batches that never got a queue message (safety net)."""

from __future__ import annotations

import logging
import sys

from worker.messages import BatchJobMessage
from worker.mongo_store import BatchStore, STATUS_PENDING
from worker.queue_backend import create_queue
from worker.settings import Settings

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    store = BatchStore(settings)
    queue = create_queue(settings)
    limit = settings.reconcile_batch_limit
    cursor = store.batches.find({"status": STATUS_PENDING}).sort("receivedAt", 1).limit(limit)
    count = 0
    for doc in cursor:
        queue.publish(BatchJobMessage.for_batch(doc["_id"], source="reconcile"))
        count += 1
    store.close()
    logger.info("Re-enqueued %d PENDING batch(es)", count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
