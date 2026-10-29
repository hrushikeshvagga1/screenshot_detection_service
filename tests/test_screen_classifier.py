import pytest

import config
from phone_detector import detect_phone_numbers
from screen_classifier import (
    CHAT_DETAIL, CHATS_LIST, CONTACT_INFO, LOC_BOTTOM_COMPOSER, LOC_CONTACT_DETAILS, LOC_CONTACT_ROW,
    LOC_MESSAGE_AREA, LOC_TOP_HEADER, LOC_UNKNOWN, SEND_CONTACT, UNKNOWN, assign_locations, classify_screen,
)
from tests.helpers import (
    chat_detail_words, chats_list_words, contact_info_words, make_ocr, phone_words,
    send_contact_words,
)

RESOLUTIONS = [None, (1080, 2400), (720, 1600)]


def _run(words, scale_to=None):
    ocr = make_ocr(words, scale_to=scale_to)
    candidates = detect_phone_numbers(ocr)
    result = classify_screen(ocr, candidates)
    assign_locations(candidates, result, ocr)
    return result, candidates


@pytest.mark.parametrize("scale_to", RESOLUTIONS)
def test_chats_list_classified(scale_to):
    result, candidates = _run(chats_list_words(phone_rows=1), scale_to)
    assert result.screen_type == CHATS_LIST
    assert result.confidence >= config.SCREEN_CONFIDENCE_THRESHOLD
    assert "conversation_rows" in result.layout_patterns
    assert {"unread", "groups", "chats"} <= set(result.recognized_keywords)
    assert result.evidence and CHAT_DETAIL in result.evidence_against
    assert [c.location for c in candidates] == [LOC_CONTACT_ROW]
    assert candidates[0].location_detail == "ROW_TITLE"


@pytest.mark.parametrize("scale_to", RESOLUTIONS)
def test_chat_detail_classified(scale_to):
    result, candidates = _run(chat_detail_words(), scale_to)
    assert result.screen_type == CHAT_DETAIL
    assert "message_composer" in result.layout_patterns
    assert [c.location for c in candidates] == [LOC_MESSAGE_AREA]


@pytest.mark.parametrize("scale_to", RESOLUTIONS)
def test_send_contact_classified(scale_to):
    result, candidates = _run(send_contact_words(), scale_to)
    assert result.screen_type == SEND_CONTACT
    assert result.confidence >= config.SCREEN_CONFIDENCE_THRESHOLD
    assert "send contact" in result.recognized_keywords
    assert "mobile" in result.recognized_keywords
    assert len(candidates) == 1
    assert candidates[0].location == LOC_CONTACT_DETAILS
    assert candidates[0].location_detail == "SHARE_CONTACT_NUMBER_ROW"


@pytest.mark.parametrize("scale_to", RESOLUTIONS)
def test_contact_info_classified(scale_to):
    result, candidates = _run(contact_info_words(), scale_to)
    assert result.screen_type == CONTACT_INFO
    assert "media links and docs" in result.recognized_keywords
    assert {"profile_action_buttons", "centered_profile_header", "phone_in_profile_header"} <= set(
        result.layout_patterns
    )
    assert [c.location for c in candidates] == [LOC_CONTACT_DETAILS]


def test_chat_detail_number_in_header_labelled_top_header():
    header = phone_words(121, 66, h=20)
    result, candidates = _run(chat_detail_words(header_text=header, body_phone=False))
    assert result.screen_type == CHAT_DETAIL
    assert [c.location for c in candidates] == [LOC_TOP_HEADER]


def test_chat_detail_number_typed_in_composer():
    words = [w for w in chat_detail_words(body_phone=False) if w[0] != "Message"]
    words += [("Message", 67, 917, 89, 29, 0.95)] + phone_words(170, 925)
    result, candidates = _run(words)
    assert result.screen_type == CHAT_DETAIL
    assert [c.location for c in candidates] == [LOC_BOTTOM_COMPOSER]


def test_screens_without_phone_numbers_still_classified():
    assert _run(contact_info_words(phone=False))[0].screen_type == CONTACT_INFO
    assert _run(chats_list_words(phone_rows=0))[0].screen_type == CHATS_LIST
    assert _run(chat_detail_words(body_phone=False))[0].screen_type == CHAT_DETAIL


def test_chats_list_with_multiple_numbers():
    result, candidates = _run(chats_list_words(phone_rows=3))
    assert result.screen_type == CHATS_LIST
    assert len(candidates) == 3
    assert all(c.location == LOC_CONTACT_ROW for c in candidates)
    assert "phone_in_row_name_column" in result.layout_patterns


def test_unrelated_screen_is_unknown():
    words = [
        ("Settings", 20, 65, 120, 25, 0.95), ("Account", 88, 200, 80, 18, 0.95),
        ("Privacy", 88, 280, 70, 18, 0.95), ("Avatar", 88, 360, 60, 18, 0.95),
        ("Lists", 88, 440, 50, 18, 0.95), ("Storage", 88, 520, 70, 18, 0.95),
        ("Language", 88, 600, 80, 18, 0.95), ("Help", 88, 680, 40, 18, 0.95),
    ]
    result, candidates = _run(words + phone_words(88, 760))
    assert result.screen_type == UNKNOWN
    assert result.uncertainty and "Insufficient evidence" in result.uncertainty
    assert [c.location for c in candidates] == [LOC_UNKNOWN]


def test_empty_ocr_is_unknown():
    result, _ = _run([])
    assert result.screen_type == UNKNOWN


def test_ambiguous_mixed_layout_is_unknown():
    chats_top = [w for w in chats_list_words(phone_rows=0) if w[2] < 300 or w[2] > 900]
    contact_mid = [w for w in contact_info_words(phone=False) if 300 < w[2] < 900]
    result, _ = _run(chats_top + contact_mid)
    assert result.screen_type == UNKNOWN
    assert "Ambiguous" in result.uncertainty
    assert result.margin < config.SCREEN_MIN_SCORE_MARGIN


def test_margin_rule_forces_unknown(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_MIN_SCORE_MARGIN", 1.01)
    result, _ = _run(contact_info_words())
    assert result.screen_type == UNKNOWN
    assert result.best_guess == CONTACT_INFO


def test_low_confidence_words_ignored_for_keywords():
    low = [(t, x, y, w, h, 0.2) for t, x, y, w, h, _ in contact_info_words(phone=False)]
    result, _ = _run(low)
    assert result.screen_type == UNKNOWN


def test_single_keyword_is_not_enough():
    result, _ = _run([("Notifications", 88, 682, 113, 17, 0.95), ("Message", 67, 917, 89, 29, 0.95)])
    assert result.screen_type == UNKNOWN
