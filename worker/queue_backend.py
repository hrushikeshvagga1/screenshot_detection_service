"""Queue backends: Redis (DigitalOcean staging) and SQS (AWS production)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

from worker.messages import BatchJobMessage
from worker.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class ReceivedMessage:
    body: BatchJobMessage
    receipt: str  # ack token (Redis token or SQS receipt handle)
    raw: str


class QueueBackend(Protocol):
    def publish(self, message: BatchJobMessage) -> None: ...

    def receive(self, wait_seconds: int) -> Optional[ReceivedMessage]: ...

    def ack(self, message: ReceivedMessage) -> None: ...

    def nack(self, message: ReceivedMessage) -> None: ...


class RedisQueue:
    """Simple reliable-enough queue: LPUSH + BRPOP with processing list for ack."""

    def __init__(self, settings: Settings) -> None:
        import redis

        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required for QUEUE_BACKEND=redis")
        block_sec = max(1, settings.queue_receive_wait_seconds)
        # BRPOP blocks on the server; socket read timeout must exceed that wait or redis-py raises.
        self._client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=30,
            socket_timeout=block_sec + 15,
        )
        self._queue_key = settings.redis_queue_key
        self._processing_key = f"{self._queue_key}:processing"

    def publish(self, message: BatchJobMessage) -> None:
        self._client.lpush(self._queue_key, message.to_json())
        logger.debug("Published batch job to Redis queue %s", self._queue_key)

    def receive(self, wait_seconds: int) -> Optional[ReceivedMessage]:
        import redis

        timeout = max(1, wait_seconds)
        try:
            item = self._client.brpop(self._queue_key, timeout=timeout)
        except redis.exceptions.TimeoutError:
            # Idle queue: treat as no message (do not crash the worker loop).
            return None
        if not item:
            return None
        _, raw = item
        token = str(uuid.uuid4())
        self._client.hset(self._processing_key, token, raw)
        return ReceivedMessage(body=BatchJobMessage.from_json(raw), receipt=token, raw=raw)

    def ack(self, message: ReceivedMessage) -> None:
        self._client.hdel(self._processing_key, message.receipt)

    def nack(self, message: ReceivedMessage) -> None:
        self._client.hdel(self._processing_key, message.receipt)
        self._client.lpush(self._queue_key, message.raw)


class SqsQueue:
    def __init__(self, settings: Settings) -> None:
        import boto3

        if not settings.sqs_queue_url:
            raise RuntimeError("SQS_QUEUE_URL is required for QUEUE_BACKEND=sqs")
        self._queue_url = settings.sqs_queue_url
        self._client = boto3.client("sqs", region_name=settings.aws_region)
        self._wait = settings.sqs_wait_time_seconds

    def publish(self, message: BatchJobMessage) -> None:
        self._client.send_message(QueueUrl=self._queue_url, MessageBody=message.to_json())

    def receive(self, wait_seconds: int) -> Optional[ReceivedMessage]:
        resp = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=min(20, max(0, wait_seconds)),
            MessageAttributeNames=["All"],
        )
        messages = resp.get("Messages") or []
        if not messages:
            return None
        msg = messages[0]
        raw = msg["Body"]
        return ReceivedMessage(
            body=BatchJobMessage.from_json(raw),
            receipt=msg["ReceiptHandle"],
            raw=raw,
        )

    def ack(self, message: ReceivedMessage) -> None:
        self._client.delete_message(QueueUrl=self._queue_url, ReceiptHandle=message.receipt)

    def nack(self, message: ReceivedMessage) -> None:
        # SQS: visibility timeout handles retry; no explicit nack
        logger.info("Leaving SQS message for retry (visibility timeout)")


def create_queue(settings: Settings) -> QueueBackend:
    backend = (settings.queue_backend or "redis").lower()
    if backend == "redis":
        return RedisQueue(settings)
    if backend == "sqs":
        return SqsQueue(settings)
    raise RuntimeError(f"Unknown QUEUE_BACKEND: {backend}")
