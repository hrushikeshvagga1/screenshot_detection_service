"""Deterministic, rule-based WhatsApp screen classification and candidate location labelling.

Each feature inspects OCR words/rows using normalized coordinates and returns a
strength in [0, 1]. Screen scores are weighted sums of feature strengths (see
``config.SCREEN_FEATURE_WEIGHTS``). Scores are heuristic, not probabilities.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import config
from ocr_engine import OcrResult, OcrRow, OcrWord, alnum_count
from phone_detector import PhoneCandidate

CHATS_LIST = "CHATS_LIST"
CHAT_DETAIL = "CHAT_DETAIL"
CONTACT_INFO = "CONTACT_INFO"
SEND_CONTACT = "SEND_CONTACT"
UNKNOWN = "UNKNOWN"
SCREEN_TYPES = (CHATS_LIST, CHAT_DETAIL, CONTACT_INFO, SEND_CONTACT)

LOC_TOP_HEADER = "TOP_HEADER"
LOC_CONTACT_ROW = "CONTACT_ROW"
LOC_MESSAGE_AREA = "MESSAGE_AREA"
LOC_CONTACT_DETAILS = "CONTACT_DETAILS"
LOC_BOTTOM_COMPOSER = "BOTTOM_COMPOSER"
LOC_OTHER = "OTHER"
LOC_UNKNOWN = "UNKNOWN"

_TIME = re.compile(config.TIME_PATTERN, re.IGNORECASE)
_DATE = re.compile(config.DATE_PATTERN)


@dataclass
class FeatureResult:
    feature_id: str
    kind: str                 # "keyword" or "layout"
    description: str
    strength: float = 0.0
    evidence: Optional[str] = None
    matched_keywords: List[str] = field(default_factory=list)
    anchors: Dict[str, float] = field(default_factory=dict)

    @property
    def detected(self) -> bool:
        return self.strength > 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "feature": self.feature_id,
            "kind": self.kind,
            "strength": round(self.strength, 3),
            "evidence": self.evidence,
            "matched_keywords": list(self.matched_keywords),
        }


@dataclass
class ClassificationResult:
    screen_type: str
    confidence: float
    best_guess: Optional[str]
    scores: Dict[str, float]
    margin: float
    evidence: List[str]
    evidence_against: Dict[str, List[str]]
    contributing_signals: List[Dict[str, object]]
    recognized_keywords: List[str]
    layout_patterns: List[str]
    uncertainty: Optional[str]
    features: Dict[str, FeatureResult]
    anchors: Dict[str, float]

    def to_dict(self) -> Dict[str, object]:
        return {
            "type": self.screen_type,
            "confidence": round(self.confidence, 3),
            "confidence_note": "Heuristic rule score in [0,1]; not a calibrated probability.",
            "best_guess": self.best_guess,
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "score_margin": round(self.margin, 3),
            "evidence": list(self.evidence),
            "evidence_against": {k: list(v) for k, v in self.evidence_against.items()},
            "contributing_signals": list(self.contributing_signals),
            "recognized_keywords": list(self.recognized_keywords),
            "layout_patterns": list(self.layout_patterns),
            "uncertainty": self.uncertainty,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_time(word: OcrWord) -> bool:
    return bool(_TIME.match(word.text.strip().lower().replace(" ", "")))


def _is_day_or_date(word: OcrWord) -> bool:
    return word.normalized_text in config.DAY_LABELS or bool(_DATE.match(word.text.strip()))


def _is_timestamp_like(word: OcrWord) -> bool:
    return _is_time(word) or _is_day_or_date(word)


def _contains_phrase(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {text} "


def _strength_from_count(count: int, params: Dict) -> float:
    if count < params["min_count"]:
        return 0.0
    return min(1.0, count / params["target_count"])


@dataclass
class _Context:
    ocr: OcrResult
    words: List[OcrWord]
    rows: List[OcrRow]
    candidates: Sequence[PhoneCandidate]
    median_word_height: float


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
def _f_app_title_header(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["app_title_header"]
    f = FeatureResult("app_title_header", "keyword", "'WhatsApp' app title in the top-left header")
    for w in ctx.words:
        if w.normalized_text in p["keywords"] and w.norm.center_y <= p["max_y"] and w.norm.x <= p["max_x"]:
            f.strength = 1.0
            f.evidence = "'WhatsApp' app title found in the top-left header"
            f.matched_keywords = [w.normalized_text]
            break
    return f


def _f_search_bar(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["search_bar"]
    f = FeatureResult("search_bar", "keyword", "Chat-list search bar near the top")
    for row in ctx.rows:
        if row.norm.center_y > p["max_y"]:
            continue
        for phrase in p["phrases"]:
            if _contains_phrase(row.normalized_text, phrase):
                f.strength = 1.0
                f.evidence = f"Search bar text '{phrase}' found near the top"
                f.matched_keywords = [phrase]
                f.anchors["list_header_bottom_y"] = row.norm.bottom
                return f
    return f


def _f_filter_chips(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["filter_chips"]
    f = FeatureResult("filter_chips", "keyword", "Chat-list filter chips (All/Unread/Favourites/Groups)")
    hits = [w for w in ctx.words if w.normalized_text in p["keywords"] and w.norm.center_y <= p["max_y"]]
    names = sorted({w.normalized_text for w in hits})
    f.strength = _strength_from_count(len(names), p)
    if f.detected:
        f.evidence = f"{len(names)} chat-list filter chips found: {', '.join(names)}"
        f.matched_keywords = names
        f.anchors["list_header_bottom_y"] = max(w.norm.bottom for w in hits)
    return f


def _f_bottom_navigation(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["bottom_navigation"]
    f = FeatureResult("bottom_navigation", "keyword", "Bottom navigation tabs (Chats/Updates/Communities/Calls)")
    hits = [w for w in ctx.words if w.normalized_text in p["keywords"] and w.norm.center_y >= p["min_y"]]
    names = sorted({w.normalized_text for w in hits})
    f.strength = _strength_from_count(len(names), p)
    if f.detected:
        f.evidence = f"{len(names)} bottom navigation tabs found: {', '.join(names)}"
        f.matched_keywords = names
        f.anchors["bottom_nav_top_y"] = min(w.norm.y for w in hits)
    return f


def _has_send_contact_header(ctx: _Context) -> bool:
    p = config.FEATURE_PARAMS["send_contact_header"]
    for row in ctx.rows:
        if row.norm.center_y > p["max_y"]:
            continue
        for phrase in p["phrases"]:
            if _contains_phrase(row.normalized_text, phrase):
                return True
    return False


def _keyboard_open(ctx: _Context) -> bool:
    p = config.FEATURE_PARAMS["keyboard_open"]
    for row in ctx.rows:
        if row.norm.center_y < p["language_row_min_y"]:
            continue
        text = row.normalized_text
        if any(ph in text for ph in p["language_phrases"]):
            return True
    noisy = [
        w for w in ctx.words
        if w.norm.center_y >= p["noisy_word_min_y"]
        and w.confidence <= p["noisy_max_confidence"]
        and alnum_count(w.text) >= 4
    ]
    return len(noisy) >= p["noisy_word_min_count"]


def _conversation_rows(ctx: _Context) -> List[Dict[str, float]]:
    """Rows with a left-aligned title (right of the avatar) and a right-aligned timestamp."""
    p = config.FEATURE_PARAMS["conversation_rows"]
    title_words = [
        w for w in ctx.words
        if p["name_min_x"] <= w.norm.x <= p["name_max_x"]
        and alnum_count(w.text) >= 2
        and w.norm.center_y > p.get("header_exclusion_max_y", 0.14)
    ]
    rows: List[Dict[str, float]] = []
    for ts in ctx.words:
        if not _is_timestamp_like(ts):
            continue
        if ts.norm.center_y <= p.get("header_exclusion_max_y", 0.14):
            continue
        if ts.norm.center_x < p["timestamp_min_x_center"] or not (p["min_y"] <= ts.norm.center_y <= p["max_y"]):
            continue
        titles = [t for t in title_words if abs(t.norm.center_y - ts.norm.center_y) <= p["row_y_tolerance"]]
        if not titles:
            continue
        if any(abs(r["y"] - ts.norm.center_y) <= p["row_y_tolerance"] for r in rows):
            continue
        rows.append({"y": ts.norm.center_y, "title_x": min(t.norm.x for t in titles)})
    return rows


def _f_conversation_rows(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["conversation_rows"]
    f = FeatureResult("conversation_rows", "layout", "Repeated conversation rows (title left, timestamp right)")
    rows = _conversation_rows(ctx)
    f.strength = _strength_from_count(len(rows), p)
    if f.detected:
        f.evidence = f"{len(rows)} conversation row(s) with a left title and right-aligned timestamp"
    return f


def _f_phone_in_row_name_column(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["conversation_rows"]
    f = FeatureResult("phone_in_row_name_column", "layout", "Phone-number-like text in the chat-row title column")
    rows = _conversation_rows(ctx)
    header_max = p.get("header_exclusion_max_y", 0.14)
    count = sum(
        1 for c in ctx.candidates
        if c.norm.center_y > header_max
        and p["name_min_x"] <= c.norm.x <= p["name_max_x"]
        and any(abs(r["y"] - c.norm.center_y) <= p["row_y_tolerance"] for r in rows)
    )
    if count:
        f.strength = 1.0
        f.evidence = f"{count} phone-number-like string(s) used as a chat-row title"
    return f


def _phrase_feature(ctx: _Context, feature_id: str, description: str, found_text: str) -> FeatureResult:
    p = config.FEATURE_PARAMS[feature_id]
    f = FeatureResult(feature_id, "keyword", description)
    matched = sorted({ph for row in ctx.rows for ph in p["phrases"] if _contains_phrase(row.normalized_text, ph)})
    if matched:
        f.strength = 1.0
        f.evidence = f"{found_text}: {', '.join(matched)}"
        f.matched_keywords = matched
    return f


def _f_chat_list_privacy_footer(ctx: _Context) -> FeatureResult:
    return _phrase_feature(ctx, "chat_list_privacy_footer", "Chat-list end-to-end encryption footer",
                           "Chat-list encryption footer found")


def _f_call_event_bubbles(ctx: _Context) -> FeatureResult:
    return _phrase_feature(ctx, "call_event_bubbles", "Call-event bubbles (missed call / tap to call back)",
                           "Call-event bubble text found")


def _f_message_composer(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["message_composer"]
    f = FeatureResult("message_composer", "layout", "Message composer near the bottom")
    keyboard = _keyboard_open(ctx)
    min_y = p["keyboard_open_min_y"] if keyboard else p["min_y"]
    if keyboard:
        f.anchors["keyboard_open"] = 1.0
    for row in ctx.rows:
        if row.norm.center_y < min_y:
            continue
        alpha_words = [w for w in row.words if sum(c.isalpha() for c in w.text) >= 2]
        if len(alpha_words) > p["max_row_words"]:
            continue
        text = row.normalized_text
        hit_word = next(
            (w for w in row.words if w.normalized_text in p["keywords"] and w.norm.x <= p["max_x"]), None
        )
        hit_phrase = next((ph for ph in p["phrases"] if _contains_phrase(text, ph)), None)
        if hit_word or hit_phrase:
            f.strength = 1.0
            keyword = hit_phrase or hit_word.normalized_text
            f.evidence = f"Message composer placeholder '{keyword}' found near the bottom"
            f.matched_keywords = [keyword]
            f.anchors["composer_top_y"] = row.norm.y
            return f
    return f


def _f_conversation_header_title(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["conversation_header_title"]
    f = FeatureResult("conversation_header_title", "layout", "Conversation title next to back arrow/avatar in header")
    for row in ctx.rows:
        if _contains_phrase(row.normalized_text, "send contact"):
            continue
        for w in row.content_words:
            if (
                p["min_y"] <= w.norm.center_y <= p["max_y"]
                and p["min_x"] <= w.norm.x <= p["max_x"]
                and alnum_count(w.text) >= 2
                and not _is_time(w)
            ):
                f.strength = 1.0
                f.evidence = "Conversation title text found in the header right of the avatar"
                return f
    return f


def _f_date_separators(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["date_separators"]
    f = FeatureResult("date_separators", "layout", "Centered date separators between messages")
    hits = [
        w for w in ctx.words
        if _is_day_or_date(w)
        and p["min_x_center"] <= w.norm.center_x <= p["max_x_center"]
        and p["min_y"] <= w.norm.center_y <= p["max_y"]
    ]
    f.strength = _strength_from_count(len(hits), p)
    if f.detected:
        labels = sorted({w.normalized_text for w in hits})
        f.evidence = f"{len(hits)} centered date separator(s): {', '.join(labels)}"
        f.matched_keywords = labels
    return f


def _f_bubble_timestamps(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["bubble_timestamps"]
    f = FeatureResult("bubble_timestamps", "layout", "Message-bubble timestamps in the conversation area")
    header_max = p.get("header_exclusion_max_y", 0.12)
    hits = [
        w for w in ctx.words
        if _is_time(w)
        and w.norm.center_x <= p["max_x_center"]
        and header_max < w.norm.center_y <= p["max_y"]
    ]
    f.strength = _strength_from_count(len(hits), p)
    if f.detected:
        f.evidence = f"{len(hits)} in-bubble message timestamps found"
    return f


def _f_contact_settings_labels(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["contact_settings_labels"]
    f = FeatureResult("contact_settings_labels", "keyword", "Contact-info section labels (Media, Notifications, ...)")
    matched: List[str] = []
    for row in ctx.rows:
        text = row.normalized_text
        phrase = next((ph for ph in p["phrases"] if _contains_phrase(text, ph)), None)
        if phrase is None and row.content_text in p["single_word_labels"]:
            phrase = row.content_text
        if phrase:
            matched.append(phrase)
            if phrase == "media links and docs":
                f.anchors["media_label_top_y"] = row.norm.y
    f.strength = _strength_from_count(len(matched), p)
    if f.detected:
        f.evidence = f"{len(matched)} contact-info labels found: {', '.join(matched)}"
        f.matched_keywords = sorted(set(matched))
    return f


def _f_profile_action_buttons(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["profile_action_buttons"]
    f = FeatureResult("profile_action_buttons", "layout", "Row of spaced profile action buttons (Call/Video/Search)")
    hits = [
        w for w in ctx.words
        if w.normalized_text in p["keywords"] and p["min_y"] <= w.norm.center_y <= p["max_y"]
    ]
    best: List[OcrWord] = []
    for anchor in hits:
        same_row = sorted(
            (w for w in hits if abs(w.norm.center_y - anchor.norm.center_y) <= p["row_y_tolerance"]),
            key=lambda w: w.norm.center_x,
        )
        spaced: List[OcrWord] = []
        for w in same_row:
            if w.normalized_text in {s.normalized_text for s in spaced}:
                continue
            if spaced and w.norm.center_x - spaced[-1].norm.center_x < p["min_x_spacing"]:
                continue
            spaced.append(w)
        if len(spaced) > len(best):
            best = spaced
    f.strength = _strength_from_count(len(best), p)
    if f.detected:
        names = [w.normalized_text for w in best]
        f.evidence = f"{len(best)} evenly spaced profile action buttons: {', '.join(names)}"
        f.matched_keywords = names
    return f


def _is_centered_large(norm, height_px: int, ctx: _Context, p: Dict, max_offset: float) -> bool:
    return (
        p["min_y"] <= norm.center_y <= p["max_y"]
        and abs(norm.center_x - 0.5) <= max_offset
        and ctx.median_word_height > 0
        and height_px >= config.FEATURE_PARAMS["centered_profile_header"]["min_height_ratio"] * ctx.median_word_height
    )


def _f_centered_profile_header(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["centered_profile_header"]
    f = FeatureResult("centered_profile_header", "layout", "Large centered name/number below a profile avatar")
    for row in ctx.rows:
        if not row.content_words or any(_is_timestamp_like(w) for w in row.words):
            continue
        if _is_centered_large(row.norm, row.bbox.height, ctx, p, p["max_center_offset"]):
            f.strength = 1.0
            f.evidence = "Large centered profile title found in the upper part of the screen"
            return f
    return f


def _f_phone_in_profile_header(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["phone_in_profile_header"]
    f = FeatureResult("phone_in_profile_header", "layout", "Phone number shown as the large centered profile title")
    for c in ctx.candidates:
        if _is_centered_large(c.norm, c.bbox.height, ctx, p, p["max_center_offset"]):
            f.strength = 1.0
            f.evidence = "Phone number shown as the large centered profile title"
            return f
    return f


def _f_send_contact_header(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["send_contact_header"]
    f = FeatureResult("send_contact_header", "keyword", "Send-contact picker title in the top header")
    for row in ctx.rows:
        if row.norm.center_y > p["max_y"]:
            continue
        for phrase in p["phrases"]:
            if not _contains_phrase(row.normalized_text, phrase):
                continue
            if any(w.norm.x >= p["min_x"] for w in row.content_words):
                f.strength = 1.0
                f.evidence = f"'{phrase.title()}' header found (contact-sharing flow)"
                f.matched_keywords = [phrase]
                f.anchors["send_contact_header_bottom_y"] = row.norm.bottom
                return f
    return f


def _f_mobile_type_label(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["mobile_type_label"]
    f = FeatureResult("mobile_type_label", "keyword", "Phone-line type label (Mobile/Home/Work) in share picker")
    if not _has_send_contact_header(ctx):
        return f
    hits = [
        w for w in ctx.words
        if w.normalized_text in p["keywords"]
        and p["min_y"] <= w.norm.center_y <= p["max_y"]
        and w.norm.x <= p["max_x"]
    ]
    if hits:
        f.strength = 1.0
        labels = sorted({w.normalized_text for w in hits})
        f.evidence = f"Contact line type label(s) found: {', '.join(labels)}"
        f.matched_keywords = labels
        f.anchors["mobile_label_y"] = min(w.norm.y for w in hits)
    return f


def _send_contact_header_bottom(ctx: _Context) -> float:
    p = config.FEATURE_PARAMS["send_contact_header"]
    bottom = config.FEATURE_PARAMS["share_contact_name_row"]["min_y"]
    for row in ctx.rows:
        if row.norm.center_y > p["max_y"]:
            continue
        for phrase in p["phrases"]:
            if _contains_phrase(row.normalized_text, phrase):
                bottom = max(bottom, row.norm.bottom)
    return bottom


def _f_share_contact_name_row(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["share_contact_name_row"]
    f = FeatureResult("share_contact_name_row", "layout", "Contact name row below the send-contact header")
    if not _has_send_contact_header(ctx):
        return f
    header_bottom = _send_contact_header_bottom(ctx)
    for row in ctx.rows:
        if row.norm.center_y <= header_bottom or row.norm.center_y > p["max_y"]:
            continue
        if not (p["min_x"] <= row.norm.x <= p["max_x"]):
            continue
        if row.content_words and not any(_is_timestamp_like(w) for w in row.words):
            if _contains_phrase(row.normalized_text, "send contact"):
                continue
            f.strength = 1.0
            f.evidence = "Contact name row found below the send-contact header"
            f.anchors["share_contact_name_bottom_y"] = row.norm.bottom
            return f
    return f


def _f_phone_in_share_contact_list(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["phone_in_share_contact_list"]
    f = FeatureResult("phone_in_share_contact_list", "layout", "Phone number listed for sharing with a type label")
    if not _has_send_contact_header(ctx):
        return f
    mobile_words = [
        w for w in ctx.words
        if w.normalized_text in config.FEATURE_PARAMS["mobile_type_label"]["keywords"]
    ]
    for c in ctx.candidates:
        if not (p["phone_min_y"] <= c.norm.center_y <= p["phone_max_y"]):
            continue
        for mw in mobile_words:
            if (
                mw.norm.y >= c.norm.bottom - 0.01
                and (mw.norm.y - c.norm.bottom) <= p["mobile_max_vertical_gap"]
                and abs(mw.norm.x - c.norm.x) < 0.15
            ):
                f.strength = 1.0
                f.evidence = "Phone number row with a Mobile/Home/Work type label (share-contact list)"
                return f
    return f


def _f_phone_in_chat_header(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["phone_in_chat_header"]
    f = FeatureResult("phone_in_chat_header", "layout", "Phone number shown as the chat header title")
    for c in ctx.candidates:
        if c.norm.center_y <= p["max_y"] and c.norm.x >= p["min_x"]:
            f.strength = 1.0
            f.evidence = "Phone number used as the conversation header title"
            return f
    return f


def _f_in_chat_vcard(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["in_chat_vcard"]
    f = FeatureResult("in_chat_vcard", "layout", "In-chat shared contact card (number with Mobile/Home label)")
    if _has_send_contact_header(ctx):
        return f
    mobile_words = [
        w for w in ctx.words
        if w.normalized_text in config.FEATURE_PARAMS["mobile_type_label"]["keywords"]
        and w.norm.x <= p["mobile_max_x"]
    ]
    for c in ctx.candidates:
        if not (p["phone_min_y"] <= c.norm.center_y <= p["phone_max_y"]):
            continue
        if c.norm.center_y <= config.FEATURE_PARAMS["phone_in_chat_header"]["max_y"]:
            continue
        for mw in mobile_words:
            if (
                mw.norm.y >= c.norm.bottom - 0.01
                and (mw.norm.y - c.norm.bottom) <= p["mobile_max_vertical_gap"]
                and abs(mw.norm.x - c.norm.x) < 0.18
            ):
                f.strength = 1.0
                f.evidence = "Shared contact card in the message stream (number with type label)"
                f.matched_keywords = [mw.normalized_text]
                return f
    return f


def _f_message_media_bubble(ctx: _Context) -> FeatureResult:
    p = config.FEATURE_PARAMS["message_media_bubble"]
    f = FeatureResult("message_media_bubble", "keyword", "Photo/video/document bubble labels in the chat body")
    hits = [
        w for w in ctx.words
        if w.normalized_text in p["keywords"] and p["min_y"] <= w.norm.center_y <= p["max_y"]
    ]
    f.strength = _strength_from_count(len(hits), {"min_count": 1, "target_count": 2})
    if f.detected:
        names = sorted({w.normalized_text for w in hits})
        f.evidence = f"Media bubble label(s) in chat: {', '.join(names)}"
        f.matched_keywords = names
    return f


FEATURES: Dict[str, Callable[[_Context], FeatureResult]] = {
    "app_title_header": _f_app_title_header,
    "search_bar": _f_search_bar,
    "filter_chips": _f_filter_chips,
    "bottom_navigation": _f_bottom_navigation,
    "conversation_rows": _f_conversation_rows,
    "phone_in_row_name_column": _f_phone_in_row_name_column,
    "chat_list_privacy_footer": _f_chat_list_privacy_footer,
    "message_composer": _f_message_composer,
    "conversation_header_title": _f_conversation_header_title,
    "date_separators": _f_date_separators,
    "bubble_timestamps": _f_bubble_timestamps,
    "call_event_bubbles": _f_call_event_bubbles,
    "contact_settings_labels": _f_contact_settings_labels,
    "profile_action_buttons": _f_profile_action_buttons,
    "centered_profile_header": _f_centered_profile_header,
    "phone_in_profile_header": _f_phone_in_profile_header,
    "send_contact_header": _f_send_contact_header,
    "mobile_type_label": _f_mobile_type_label,
    "share_contact_name_row": _f_share_contact_name_row,
    "phone_in_share_contact_list": _f_phone_in_share_contact_list,
    "phone_in_chat_header": _f_phone_in_chat_header,
    "in_chat_vcard": _f_in_chat_vcard,
    "message_media_bubble": _f_message_media_bubble,
}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def _build_context(ocr: OcrResult, candidates: Sequence[PhoneCandidate]) -> _Context:
    words = [w for w in ocr.words if w.confidence >= config.KEYWORD_MIN_CONFIDENCE]
    rows = ocr.rows(min_confidence=config.KEYWORD_MIN_CONFIDENCE)
    heights = [w.bbox.height for w in words if alnum_count(w.text) >= 2]
    return _Context(ocr, words, rows, candidates, statistics.median(heights) if heights else 0.0)


def classify_screen(ocr: OcrResult, candidates: Sequence[PhoneCandidate] = ()) -> ClassificationResult:
    ctx = _build_context(ocr, candidates)
    features = {fid: fn(ctx) for fid, fn in FEATURES.items()}

    scores: Dict[str, float] = {}
    contributions: Dict[str, List[Dict[str, object]]] = {}
    for screen in SCREEN_TYPES:
        weights = config.SCREEN_FEATURE_WEIGHTS[screen]
        penalties = config.SCREEN_FEATURE_PENALTIES.get(screen, {})
        positive = sum(weights[fid] * features[fid].strength for fid in weights)
        negative = sum(penalties[fid] * features[fid].strength for fid in penalties)
        max_positive = sum(weights.values())
        scores[screen] = max(0.0, min(1.0, (positive - negative) / max_positive)) if max_positive else 0.0
        contributions[screen] = [
            {"screen": screen, "feature": fid, "weight": weights[fid],
             "strength": round(features[fid].strength, 3),
             "contribution": round(weights[fid] * features[fid].strength, 3)}
            for fid in weights if features[fid].detected
        ] + [
            {"screen": screen, "feature": fid, "weight": -penalties[fid],
             "strength": round(features[fid].strength, 3),
             "contribution": round(-penalties[fid] * features[fid].strength, 3)}
            for fid in penalties if features[fid].detected
        ]

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ranked[0]
    second_score = ranked[1][1]
    margin = best_score - second_score

    unknown_reason: Optional[str] = None
    if not ocr.success:
        unknown_reason = "OCR failed; screen cannot be classified"
    elif best_score < config.SCREEN_CONFIDENCE_THRESHOLD:
        unknown_reason = (
            f"Insufficient evidence: best score {best_score:.2f} ({best_type}) is below "
            f"threshold {config.SCREEN_CONFIDENCE_THRESHOLD:.2f}"
        )
    elif margin < config.SCREEN_MIN_SCORE_MARGIN:
        unknown_reason = (
            f"Ambiguous layout: {best_type} ({best_score:.2f}) and {ranked[1][0]} ({second_score:.2f}) "
            f"differ by less than {config.SCREEN_MIN_SCORE_MARGIN:.2f}"
        )
    caveat: Optional[str] = None
    if unknown_reason is None and not ocr.reliable:
        caveat = "Classification made from unreliable OCR: " + "; ".join(ocr.reliability_notes)

    screen_type = UNKNOWN if unknown_reason else best_type
    uncertainty = unknown_reason or caveat
    focus = best_type

    evidence = [
        features[fid].evidence for fid in sorted(
            config.SCREEN_FEATURE_WEIGHTS[focus],
            key=lambda fid: config.SCREEN_FEATURE_WEIGHTS[focus][fid] * features[fid].strength,
            reverse=True,
        )
        if features[fid].detected
    ]

    evidence_against: Dict[str, List[str]] = {}
    for screen in SCREEN_TYPES:
        notes: List[str] = []
        if screen != focus:
            missing = sorted(
                (fid for fid in config.SCREEN_FEATURE_WEIGHTS[screen] if not features[fid].detected),
                key=lambda fid: config.SCREEN_FEATURE_WEIGHTS[screen][fid],
                reverse=True,
            )
            notes.extend(f"{features[fid].description}: not detected" for fid in missing)
        for fid in config.SCREEN_FEATURE_PENALTIES.get(screen, {}):
            if features[fid].detected:
                notes.append(f"{features[fid].evidence} (counts against {screen})")
        if notes:
            evidence_against[screen] = notes

    keywords = sorted({kw for f in features.values() if f.detected for kw in f.matched_keywords})
    layouts = sorted(fid for fid, f in features.items() if f.detected and f.kind == "layout")
    anchors: Dict[str, float] = {}
    for f in features.values():
        for key, value in f.anchors.items():
            if key == "list_header_bottom_y":
                anchors[key] = max(anchors.get(key, 0.0), value)
            else:
                anchors.setdefault(key, value)

    signals = sorted(
        (s for screen in SCREEN_TYPES for s in contributions[screen]),
        key=lambda s: abs(s["contribution"]),
        reverse=True,
    )
    return ClassificationResult(
        screen_type=screen_type,
        confidence=best_score,
        best_guess=best_type,
        scores=scores,
        margin=margin,
        evidence=evidence,
        evidence_against=evidence_against,
        contributing_signals=signals,
        recognized_keywords=keywords,
        layout_patterns=layouts,
        uncertainty=uncertainty,
        features=features,
        anchors=anchors,
    )


# ---------------------------------------------------------------------------
# Location labelling
# ---------------------------------------------------------------------------
def _mobile_label_below(candidate: PhoneCandidate, ocr: OcrResult) -> bool:
    gap = config.FEATURE_PARAMS["in_chat_vcard"]["mobile_max_vertical_gap"]
    for w in ocr.words:
        if w.normalized_text not in config.FEATURE_PARAMS["mobile_type_label"]["keywords"]:
            continue
        if (
            w.norm.y >= candidate.norm.bottom - 0.01
            and (w.norm.y - candidate.norm.bottom) <= gap
            and abs(w.norm.x - candidate.norm.x) < 0.18
        ):
            return True
    return False


def _has_right_timestamp(candidate: PhoneCandidate, ocr: OcrResult) -> bool:
    p = config.FEATURE_PARAMS["conversation_rows"]
    return any(
        _is_timestamp_like(w)
        and w.norm.center_x >= p["timestamp_min_x_center"]
        and abs(w.norm.center_y - candidate.norm.center_y) <= p["row_y_tolerance"]
        for w in ocr.words
    )


def assign_location(candidate: PhoneCandidate, classification: ClassificationResult, ocr: OcrResult) -> None:
    screen = classification.screen_type
    y = candidate.norm.center_y
    anchors = classification.anchors
    candidate.location_detail = None

    if screen == UNKNOWN:
        candidate.location = LOC_UNKNOWN
        return
    if y < config.STATUS_BAR_MAX_Y:
        candidate.location, candidate.location_detail = LOC_OTHER, "STATUS_BAR"
        return

    rules = config.LOCATION_RULES[screen]
    if screen == CHATS_LIST:
        header_max = (
            anchors["list_header_bottom_y"] + rules["header_anchor_margin"]
            if "list_header_bottom_y" in anchors else rules["header_max_y"]
        )
        nav_min = (
            anchors["bottom_nav_top_y"] - rules["bottom_nav_icon_allowance"]
            if "bottom_nav_top_y" in anchors else rules["bottom_nav_min_y"]
        )
        if y <= header_max:
            candidate.location = LOC_TOP_HEADER
        elif y >= nav_min:
            candidate.location, candidate.location_detail = LOC_OTHER, "BOTTOM_NAVIGATION"
        elif rules["row_min_x"] <= candidate.norm.x <= rules["row_max_x"]:
            candidate.location = LOC_CONTACT_ROW
            candidate.location_detail = "ROW_TITLE" if _has_right_timestamp(candidate, ocr) else "ROW_PREVIEW"
        else:
            candidate.location = LOC_OTHER
    elif screen == CHAT_DETAIL:
        keyboard = bool(anchors.get("keyboard_open"))
        fallback_composer = rules["keyboard_composer_min_y"] if keyboard else rules["composer_min_y"]
        composer_min = (
            anchors["composer_top_y"] - rules["composer_anchor_margin"]
            if "composer_top_y" in anchors else fallback_composer
        )
        if y <= rules["header_max_y"]:
            candidate.location = LOC_TOP_HEADER
        elif y >= composer_min:
            candidate.location = LOC_BOTTOM_COMPOSER
        else:
            candidate.location = LOC_MESSAGE_AREA
            if candidate.location_detail is None and _mobile_label_below(candidate, ocr):
                candidate.location_detail = "IN_CHAT_VCARD"
    elif screen == CONTACT_INFO:
        details_max = anchors.get("media_label_top_y", rules["details_max_y"])
        if y <= rules["header_max_y"]:
            candidate.location = LOC_TOP_HEADER
        elif y < details_max:
            candidate.location = LOC_CONTACT_DETAILS
        else:
            candidate.location = LOC_OTHER
    elif screen == SEND_CONTACT:
        if y <= rules["header_max_y"]:
            candidate.location = LOC_TOP_HEADER
        elif y <= rules["details_max_y"]:
            candidate.location = LOC_CONTACT_DETAILS
            candidate.location_detail = "SHARE_CONTACT_NUMBER_ROW"
        else:
            candidate.location = LOC_OTHER


def assign_locations(
    candidates: Sequence[PhoneCandidate], classification: ClassificationResult, ocr: OcrResult
) -> None:
    for candidate in candidates:
        assign_location(candidate, classification, ocr)
