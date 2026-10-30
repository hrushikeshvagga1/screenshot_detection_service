import json

import pytest

from worker.messages import BatchJobMessage


def test_roundtrip_json():
    msg = BatchJobMessage.for_batch("507f1f77bcf86cd799439011", source="ingest")
    restored = BatchJobMessage.from_json(msg.to_json())
    assert restored.batch_id == msg.batch_id
    assert restored.source == "ingest"


def test_from_json_requires_batch_id():
    with pytest.raises(ValueError):
        BatchJobMessage.from_json(json.dumps({"enqueuedAt": "x"}))
