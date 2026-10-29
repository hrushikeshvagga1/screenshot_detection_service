import pytest

import config
from decision_engine import decide
from phone_detector import detect_phone_numbers
from screen_classifier import assign_locations, classify_screen
from tests.helpers import (
    chat_detail_words, chats_list_words, contact_info_words, make_ocr, phone_words, send_contact_words,
)


def _pipeline(words):
    ocr = make_ocr(words)
    candidates = detect_phone_numbers(ocr)
    classification = classify_screen(ocr, candidates)
    assign_locations(candidates, classification, ocr)
    return decide(ocr, candidates, classification), candidates, classification


def test_no_candidate_no_alert():
    decision, _, _ = _pipeline(contact_info_words(phone=False))
    assert decision.alert is False
    assert decision.review_required is False
    assert decision.reason_code == "NO_PHONE_NUMBER_DETECTED"


def test_chats_list_row_number_alerts():
    decision, candidates, _ = _pipeline(chats_list_words(phone_rows=2))
    assert decision.alert is True
    assert decision.review_required is False
    assert decision.reason_code == "PHONE_NUMBER_VISIBLE_IN_CHATS_LIST_ROW"
    assert decision.triggering_candidate_ids == [c.candidate_id for c in candidates]


def test_contact_info_number_alerts():
    decision, _, _ = _pipeline(contact_info_words())
    assert decision.alert is True
    assert decision.reason_code == "PHONE_NUMBER_VISIBLE_IN_CONTACT_INFO"


def test_send_contact_number_alerts():
    decision, _, _ = _pipeline(send_contact_words())
    assert decision.alert is True
    assert decision.reason_code == "PHONE_NUMBER_VISIBLE_IN_SEND_CONTACT"


def test_chat_detail_header_number_alerts():
    words = chat_detail_words(header_text=phone_words(121, 66, h=20), body_phone=False)
    decision, _, _ = _pipeline(words)
    assert decision.alert is True
    assert decision.reason_code == "PHONE_NUMBER_VISIBLE_IN_CHAT_HEADER"


def test_chat_detail_message_number_alerts():
    decision, candidates, _ = _pipeline(chat_detail_words())
    assert len(candidates) == 1
    assert candidates[0].location == "MESSAGE_AREA"
    assert decision.alert is True
    assert decision.review_required is False
    assert decision.reason_code == "PHONE_NUMBER_VISIBLE_IN_CHAT_MESSAGE"


def test_unknown_screen_with_candidate_requires_review():
    words = [("Settings", 20, 65, 120, 25, 0.95), ("Account", 88, 200, 80, 18, 0.95),
             ("Privacy", 88, 280, 70, 18, 0.95)] + phone_words(88, 400)
    decision, _, classification = _pipeline(words)
    assert classification.screen_type == "UNKNOWN"
    assert decision.alert is False
    assert decision.review_required is True
    assert decision.reason_code == "PHONE_NUMBER_ON_UNKNOWN_SCREEN"
    assert decision.uncertainties


def test_low_confidence_number_in_prohibited_area_requires_review():
    decision, candidates, _ = _pipeline(contact_info_words(phone_conf=0.35))
    assert candidates[0].low_ocr_confidence is True
    assert decision.alert is False
    assert decision.review_required is True
    assert decision.reason_code == "UNCERTAIN_PHONE_NUMBER_IN_PROHIBITED_AREA"
    assert any("OCR confidence" in u for u in decision.uncertainties)


def test_low_confidence_policy_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "ALERT_REQUIRES_CONFIDENT_OCR", False)
    decision, _, _ = _pipeline(contact_info_words(phone_conf=0.35))
    assert decision.alert is True


def test_invalid_number_in_prohibited_area_requires_review():
    words = [w for w in contact_info_words(phone=False) if w[0] != "Rahul"]
    words += phone_words(120, 214, h=21, parts=("+91", "12345", "1234"))
    decision, candidates, _ = _pipeline(words)
    assert candidates and candidates[0].valid is False
    assert decision.alert is False
    assert decision.review_required is True


def test_prohibited_locations_are_configurable(monkeypatch):
    monkeypatch.setitem(config.PROHIBITED_LOCATIONS, "CHAT_DETAIL", ("TOP_HEADER", "MESSAGE_AREA"))
    decision, _, _ = _pipeline(chat_detail_words())
    assert decision.alert is True


def test_unreliable_ocr_without_candidates_reports_uncertainty():
    words = [("Hi", 33, 416, 16, 14, 0.4), ("ok", 60, 416, 16, 14, 0.3)]
    decision, _, _ = _pipeline(words)
    assert decision.alert is False
    assert decision.review_required is True
    assert decision.reason_code == "NO_PHONE_NUMBER_DETECTED_LOW_OCR_QUALITY"


def test_ocr_failure_requires_review():
    ocr = make_ocr([])
    ocr.success = False
    ocr.error = "Tesseract binary not found"
    classification = classify_screen(ocr, [])
    decision = decide(ocr, [], classification)
    assert classification.screen_type == "UNKNOWN"
    assert decision.alert is False
    assert decision.review_required is True
    assert decision.reason_code == "OCR_FAILED"


@pytest.mark.parametrize("builder", [chats_list_words, chat_detail_words, contact_info_words, send_contact_words])
def test_decision_is_json_safe(builder):
    import json

    decision, _, _ = _pipeline(builder())
    json.dumps(decision.to_dict())
