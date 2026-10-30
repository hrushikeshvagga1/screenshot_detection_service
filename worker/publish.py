"""Publish batch jobs — call from ingest API after Mongo insert, or from reconcile cron."""

from __future__ import annotations

import logging
from typing import Any

from worker.messages import BatchJobMessage
from worker.queue_backend import create_queue
from worker.settings import Settings

logger = logging.getLogger(__name__)


def publish_batch_job(batch_id: Any, source: str = "ingest") -> None:
    settings = Settings.from_env()
    queue = create_queue(settings)
    message = BatchJobMessage.for_batch(batch_id, source=source)
    queue.publish(message)
    logger.info("Enqueued batch job id=%s source=%s backend=%s", batch_id, source, settings.queue_backend)
