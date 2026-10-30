"""Queue message format shared by ingest, worker, and reconcile jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict


@dataclass(frozen=True)
class BatchJobMessage:
    """One batch to analyze (all items in Mongo batch document)."""

    batch_id: str
    enqueued_at: str
    source: str = "ingest"

    def to_json(self) -> str:
        return json.dumps(
            {
                "batchId": self.batch_id,
                "enqueuedAt": self.enqueued_at,
                "source": self.source,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "BatchJobMessage":
        data: Dict[str, Any] = json.loads(raw)
        batch_id = data.get("batchId") or data.get("batch_id")
        if not batch_id:
            raise ValueError("Queue message missing batchId")
        return cls(
            batch_id=str(batch_id),
            enqueued_at=str(data.get("enqueuedAt") or datetime.now(timezone.utc).isoformat()),
            source=str(data.get("source") or "unknown"),
        )

    @classmethod
    def for_batch(cls, batch_id: Any, source: str = "ingest") -> "BatchJobMessage":
        return cls(
            batch_id=str(batch_id),
            enqueued_at=datetime.now(timezone.utc).isoformat(),
            source=source,
        )
