"""Verify object storage: resolve key and optionally download first bytes."""

from __future__ import annotations

import argparse
import sys

from worker.ingest_schema import item_storage_key
from worker.mongo_store import BatchStore
from worker.settings import Settings
from worker.storage import resolve_object, _read_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Spaces/S3/GCS for one object key")
    parser.add_argument("--key", help="storageKey or s3://bucket/key")
    parser.add_argument("--device-batch-id", help="Use first item from this batch in Mongo")
    parser.add_argument("--download", action="store_true", help="Try full download")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    storage_key = args.key
    store: BatchStore | None = None

    if args.device_batch_id:
        store = BatchStore(settings)
        batch = store.batches.find_one({"deviceBatchId": args.device_batch_id})
        if not batch or not batch.get("items"):
            print("Batch not found or has no items", file=sys.stderr)
            return 1
        item = batch["items"][0]
        storage_key = item_storage_key(item)
        print("First item jobId:", item.get("jobId"))
        print("gcsBucket:", item.get("gcsBucket"))
        print("gcsPath:", item.get("gcsPath"))

    if not storage_key:
        parser.error("Provide --key or --device-batch-id")

    ref = resolve_object(settings, storage_key)
    print("resolved:", ref)
    print("SPACES_BUCKET env:", settings.spaces_bucket)
    print("SPACES_ENDPOINT env:", settings.spaces_endpoint)

    if args.download:
        data = _read_bytes(settings, storage_key)
        print("download ok, bytes:", len(data))

    if store:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
