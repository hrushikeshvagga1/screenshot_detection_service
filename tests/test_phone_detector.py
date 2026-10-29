import pytest

import config
from phone_detector import STATUS_VALID, detect_phone_numbers, mask_number
from tests.helpers import make_ocr, phone_words


def _detect_single(text, conf=0.95):
    return detect_phone_numbers(make_ocr([(text, 100, 400, 12 * len(text), 16, conf)]))


@pytest.mark.parametrize(
    "text",
    ["9876543210", "+91-9876543210", "+91(98765)43210", "09876543210", "+919876543210", "919876543210",
     "98765-43210"],
)
def test_single_token_indian_formats(text):
    candidates = _detect_single(text)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.normalized_number == "+919876543210"
    assert c.valid is True
    assert c.validation_status == STATUS_VALID
    assert c.number_type == "MOBILE"


@pytest.mark.parametrize(
    "parts",
    [("+91", "98765", "43210"), ("+91", "(98765)", "43210"), ("98765", "43210"), ("+91-98765", "43210")],
)
def test_number_split_across_adjacent_boxes(parts):
    candidates = detect_phone_numbers(make_ocr(phone_words(50, 300, parts=parts)))
    assert len(candidates) == 1
    assert candidates[0].normalized_number == "+919876543210"
    assert candidates[0].split_across_boxes is True
    assert candidates[0].bbox.x == 50


def test_parts_far_apart_are_not_joined():
    words = [("98765", 20, 300, 50, 14, 0.95), ("43210", 380, 300, 50, 14, 0.95)]
    assert detect_phone_numbers(make_ocr(words)) == []


def test_parts_on_different_rows_are_not_joined():
    words = [("98765", 20, 300, 50, 14, 0.95), ("43210", 76, 340, 50, 14, 0.95)]
    assert detect_phone_numbers(make_ocr(words)) == []


@pytest.mark.parametrize(
    "text",
    ["12345", "987654", "1234567890", "2026102211", "123456789012345678", "4821", "100", "8",
     "ID12345678901", "98765432"],
)
def test_short_or_non_phone_numeric_strings_rejected(text):
    assert _detect_single(text) == []


@pytest.mark.parametrize(
    "words",
    [
        [("11:14", 100, 400, 40, 14, 0.95), ("am", 146, 404, 20, 10, 0.95)],
        [("22/10/2026", 100, 400, 90, 14, 0.95)],
        [("2026-10-22", 100, 400, 90, 14, 0.95), ("11:14", 196, 400, 40, 14, 0.95)],
        [("12:12", 100, 400, 40, 14, 0.95), ("12:27", 146, 400, 40, 14, 0.95), ("12:54", 192, 400, 40, 14, 0.95)],
        [("22.10.2026", 100, 400, 90, 14, 0.95), ("10:45", 196, 400, 40, 14, 0.95)],
        [("345", 100, 400, 30, 14, 0.95), ("kB", 136, 400, 20, 14, 0.95)],
        [("1", 46, 436, 3, 7, 0.95), ("2", 76, 436, 6, 8, 0.95), ("3", 108, 436, 5, 8, 0.95)],
    ],
)
def test_timestamps_dates_and_counters_rejected(words):
    assert detect_phone_numbers(make_ocr(words)) == []


def test_number_adjacent_to_timestamp_is_isolated():
    words = phone_words(184, 864) + [("11:14", 344, 864, 36, 14, 0.9), ("am", 385, 868, 20, 10, 0.9)]
    candidates = detect_phone_numbers(make_ocr(words))
    assert len(candidates) == 1
    assert candidates[0].normalized_number == "+919876543210"
    assert "11:14" not in candidates[0].text


def test_explicit_plus_invalid_number_is_candidate_but_not_valid():
    candidates = detect_phone_numbers(make_ocr(phone_words(50, 300, parts=("+91", "12345", "1234"))))
    assert len(candidates) == 1
    assert candidates[0].valid is False
    assert candidates[0].validation_status != STATUS_VALID


def test_trunk_prefixed_landline_detected():
    candidates = detect_phone_numbers(make_ocr(phone_words(50, 300, parts=("080", "2345", "6789"))))
    assert len(candidates) == 1
    assert candidates[0].valid is True
    assert "trunk_prefix_valid_number" in candidates[0].structural_reasons


def test_multiple_candidates_in_different_rows():
    words = phone_words(50, 300) + phone_words(50, 500, parts=("+91", "91234", "56789"))
    candidates = detect_phone_numbers(make_ocr(words))
    assert [c.normalized_number for c in candidates] == ["+919876543210", "+919123456789"]
    assert [c.candidate_id for c in candidates] == [1, 2]


def test_two_numbers_on_same_row_are_separated():
    words = phone_words(10, 300, parts=("9876543210",)) + phone_words(120, 300, parts=("9123456789",))
    ocr = make_ocr(words)
    assert len(ocr.rows()) == 1
    candidates = detect_phone_numbers(ocr)
    assert sorted(c.normalized_number for c in candidates) == ["+919123456789", "+919876543210"]


def test_same_number_at_two_positions_kept_overlapping_duplicates_removed():
    words = phone_words(50, 300) + phone_words(50, 500)
    assert len(detect_phone_numbers(make_ocr(words))) == 2
    duplicate = [("9876543210", 50, 300, 120, 14, 0.95), ("9876543210", 52, 301, 120, 14, 0.80)]
    candidates = detect_phone_numbers(make_ocr(duplicate))
    assert len(candidates) == 1
    assert candidates[0].ocr_confidence == pytest.approx(0.95)


def test_low_confidence_candidate_flagged_not_dropped():
    candidates = detect_phone_numbers(make_ocr(phone_words(50, 300, conf=0.40)))
    assert len(candidates) == 1
    assert candidates[0].low_ocr_confidence is True
    assert candidates[0].ocr_confidence < config.OCR_CONFIDENCE_THRESHOLD


def test_ocr_confusions_corrected_and_reported():
    candidates = detect_phone_numbers(make_ocr(phone_words(50, 300, parts=("#91", "98765", "4321O"))))
    assert len(candidates) == 1
    assert candidates[0].normalized_number == "+919876543210"
    assert candidates[0].ocr_corrections_applied is True


def test_words_with_letters_not_treated_as_numbers():
    words = [("Oprtny1", 50, 300, 67, 18, 0.95), ("Call", 120, 300, 30, 18, 0.95)]
    assert detect_phone_numbers(make_ocr(words)) == []


def test_original_text_and_bbox_retained():
    c = detect_phone_numbers(make_ocr(phone_words(120, 214, h=21)))[0]
    assert c.text == "+91 98765 43210"
    assert c.bbox.y == 214 and c.bbox.height == 21
    assert 0 < c.norm.x < 1 and 0 < c.norm.y < 1


def test_region_is_configurable():
    candidates = detect_phone_numbers(make_ocr([("+14155552671", 50, 300, 120, 14, 0.95)]), region="US")
    assert candidates[0].valid is True
    assert candidates[0].region_code == "US"


def test_mask_number_hides_digits():
    assert mask_number("+919876543210") == "+**********10"
    assert "98765" not in mask_number("+919876543210")


def test_failed_ocr_yields_no_candidates():
    ocr = make_ocr([("9876543210", 50, 300, 120, 14, 0.95)])
    ocr.success = False
    assert detect_phone_numbers(ocr) == []
