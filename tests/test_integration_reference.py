"""Optional end-to-end test on real reference screenshots.

Runs only when Tesseract is installed and ``screenshots/reference_labels.json``
exists. Each labelled image is copied to a neutral file name first, so the
result cannot depend on the file name.
"""

import json
import shutil
from pathlib import Path

import pytest

import config
import main

SCREENSHOTS = Path(__file__).resolve().parent.parent / "screenshots"
LABELS_FILE = SCREENSHOTS / "reference_labels.json"


def _tesseract_available() -> bool:
    return bool(config.TESSERACT_CMD or shutil.which("tesseract"))


def _labels():
    if not LABELS_FILE.exists():
        return []
    return sorted(json.loads(LABELS_FILE.read_text()).items())


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _tesseract_available(), reason="Tesseract binary not installed"),
    pytest.mark.skipif(not _labels(), reason="No reference_labels.json with labelled screenshots"),
]


@pytest.mark.parametrize("filename,expected", _labels())
def test_reference_screenshot(filename, expected, tmp_path):
    source = SCREENSHOTS / filename
    if not source.exists():
        pytest.skip(f"{filename} not present")
    neutral = tmp_path / f"screenshot{source.suffix}"
    shutil.copyfile(source, neutral)
    before = source.read_bytes()

    run = main.analyze(str(neutral))
    result = main.build_result(str(neutral), run)
    json.dumps(result)

    assert result["ocr"]["success"] is True
    assert result["screen_classification"]["type"] == expected["screen_type"]
    assert result["phone_detection"]["candidate_count"] >= expected.get("min_candidates", 0)
    assert result["decision"]["alert"] is expected["alert"]
    assert result["decision"]["review_required"] is expected.get("review_required", False)
    if "candidate_locations" in expected:
        locations = sorted(c["location"] for c in result["phone_detection"]["candidates"])
        assert locations == sorted(expected["candidate_locations"])
    assert source.read_bytes() == before
