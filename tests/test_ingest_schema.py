from worker.ingest_schema import item_id, item_storage_key, should_process_item


def test_gcs_path_item():
    item = {
        "jobId": "4b75cf36-d16e-432a-acd7-e98930b4898a",
        "status": "completed",
        "gcsBucket": "property-media-temp",
        "gcsPath": "phone-capture/cp/device/file.webp",
    }
    assert item_id(item) == "4b75cf36-d16e-432a-acd7-e98930b4898a"
    assert item_storage_key(item) == "s3://property-media-temp/phone-capture/cp/device/file.webp"
    assert should_process_item(item) is True


def test_worker_completed_item_skipped():
    assert should_process_item({"status": "COMPLETED", "analysisStatus": "COMPLETED"}) is False
