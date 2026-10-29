import pytest

from worker.settings import Settings
from worker.storage import resolve_object


def _spaces_settings():
    return Settings(
        mongodb_uri="mongodb://localhost/test",
        batches_collection="b",
        results_collection="r",
        spaces_bucket="my-space",
        spaces_endpoint="https://blr1.digitaloceanspaces.com",
        spaces_access_key="key",
        spaces_secret_key="secret",
        gcs_bucket=None,
        local_storage_root=None,
        poll_interval_sec=3,
        stale_processing_minutes=15,
        max_batch_attempts=5,
        worker_id="test",
        phone_region="IN",
    )


def test_resolve_bare_key_uses_spaces_bucket():
    ref = resolve_object(_spaces_settings(), "devices/abc/shot.jpeg")
    assert ref.backend == "spaces"
    assert ref.bucket == "my-space"
    assert ref.key == "devices/abc/shot.jpeg"


def test_resolve_s3_uri():
    ref = resolve_object(_spaces_settings(), "s3://other-space/path/file.jpg")
    assert ref.bucket == "other-space"
    assert ref.key == "path/file.jpg"


def test_resolve_do_virtual_host_url():
    ref = resolve_object(
        _spaces_settings(),
        "https://my-space.blr1.digitaloceanspaces.com/folder/x.jpeg",
    )
    assert ref.backend == "spaces"
    assert ref.bucket == "my-space"
    assert ref.key == "folder/x.jpeg"
