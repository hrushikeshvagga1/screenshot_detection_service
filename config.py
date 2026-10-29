"""Central configuration for the WhatsApp screenshot POC.

All tunable numbers live here. Coordinates are normalized to the image size
(0.0 = left/top edge, 1.0 = right/bottom edge) so the rules are not tied to a
single device resolution.
"""

import os

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
SUPPORTED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
SUPPORTED_IMAGE_FORMATS = ("PNG", "JPEG")
MIN_IMAGE_SIDE_PX = 100

# ---------------------------------------------------------------------------
# OCR (Tesseract)
# ---------------------------------------------------------------------------
# Set TESSERACT_CMD in the environment if the binary is not on PATH.
TESSERACT_CMD = os.environ.get("TESSERACT_CMD") or None
OCR_LANGUAGE = "eng"
# psm 11 = sparse text; works well for UI screenshots with scattered labels.
OCR_TESSERACT_CONFIG = "--oem 3 --psm 11"
OCR_TIMEOUT_SECONDS = 60

# Preprocessing is applied to a copy of the image only.
OCR_UPSCALE_ENABLED = True
OCR_TARGET_MIN_WIDTH_PX = 920
OCR_MAX_UPSCALE = 3.0
OCR_INVERT_IF_MEAN_LUMINANCE_BELOW = 100  # dark-theme screenshots

# Confidence values are on a 0..1 scale (Tesseract's 0..100 divided by 100).
OCR_CONFIDENCE_THRESHOLD = 0.60          # phone candidates below this are "low confidence"
KEYWORD_MIN_CONFIDENCE = 0.50            # words used for screen-classification keywords
OCR_RELIABLE_MEAN_CONFIDENCE = 0.60      # mean confidence of meaningful words
OCR_MIN_MEANINGFUL_WORDS = 8             # fewer than this => OCR considered unreliable
OCR_MEANINGFUL_WORD_MIN_ALNUM = 3

# Grouping individual OCR words into visual rows.
ROW_MERGE_MAX_GAP_FACTOR = 1.5           # max horizontal gap, in multiples of word height
ROW_MERGE_MIN_VERTICAL_OVERLAP = 0.5     # overlap / smaller word height

# ---------------------------------------------------------------------------
# Phone-number detection
# ---------------------------------------------------------------------------
DEFAULT_PHONE_REGION = "IN"
PHONE_MIN_DIGITS = 10
PHONE_MAX_DIGITS = 13
# National Indian mobile pattern on the digit string, with optional 91 / 0 prefix.
INDIAN_MOBILE_PATTERN = r"^(?:91|0)?[6-9]\d{9}$"
# Characters commonly misread by OCR inside otherwise-numeric tokens.
OCR_DIGIT_CONFUSIONS = {"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}
OCR_CONFUSION_MIN_REAL_DIGITS = 3
OCR_CONFUSION_MAX_SUBSTITUTIONS = 2
# A leading '#' immediately followed by a digit is treated as a misread '+'.
OCR_PLUS_CONFUSIONS = ("#",)
# Punctuation stripped from token edges before evaluation.
PHONE_TOKEN_STRIP_CHARS = ",;:\"'!?[]{}"
# Two candidates with the same number and bbox IoU above this are duplicates.
PHONE_DEDUP_IOU = 0.3

# ---------------------------------------------------------------------------
# Shared screen-layout vocabulary
# ---------------------------------------------------------------------------
STATUS_BAR_MAX_Y = 0.035
TIME_PATTERN = r"^\d{1,2}[:.]\d{2}(?:[ap]\.?m\.?)?$"
DATE_PATTERN = r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$"
DAY_LABELS = (
    "today", "yesterday", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)

# ---------------------------------------------------------------------------
# Screen-classification features
# ---------------------------------------------------------------------------
# Every feature yields a strength in [0, 1]. A screen's raw score is
# sum(weight * strength) - sum(penalty * strength); the confidence is the raw
# score divided by the sum of that screen's positive weights, clipped to [0, 1].
SCREEN_FEATURE_WEIGHTS = {
    "CHATS_LIST": {
        "app_title_header": 2.0,
        "search_bar": 1.5,
        "filter_chips": 2.0,
        "bottom_navigation": 2.0,
        "conversation_rows": 3.0,
        "phone_in_row_name_column": 0.5,
        "chat_list_privacy_footer": 0.5,
    },
    "CHAT_DETAIL": {
        "message_composer": 3.0,
        "conversation_header_title": 1.0,
        "date_separators": 1.5,
        "bubble_timestamps": 2.5,
        "call_event_bubbles": 0.5,
        "phone_in_chat_header": 1.0,
        "in_chat_vcard": 1.5,
        "message_media_bubble": 0.5,
    },
    "CONTACT_INFO": {
        "contact_settings_labels": 3.5,
        "profile_action_buttons": 2.5,
        "centered_profile_header": 2.0,
        "phone_in_profile_header": 1.0,
    },
    "SEND_CONTACT": {
        "send_contact_header": 4.0,
        "mobile_type_label": 2.5,
        "phone_in_share_contact_list": 1.5,
        "share_contact_name_row": 1.0,
    },
}

# Features that argue against a screen type.
SCREEN_FEATURE_PENALTIES = {
    "CHATS_LIST": {"message_composer": 2.0, "contact_settings_labels": 1.5, "send_contact_header": 2.0},
    "CHAT_DETAIL": {
        "conversation_rows": 1.5, "contact_settings_labels": 1.5, "filter_chips": 1.0,
        "send_contact_header": 2.5,
    },
    "CONTACT_INFO": {"message_composer": 2.0, "conversation_rows": 1.5, "send_contact_header": 2.5},
    "SEND_CONTACT": {
        "message_composer": 2.0,
        "conversation_rows": 2.0,
        "contact_settings_labels": 2.0,
        "filter_chips": 2.0,
        "bottom_navigation": 2.0,
        "profile_action_buttons": 1.5,
    },
}

SCREEN_CONFIDENCE_THRESHOLD = 0.45   # best score must reach this
SCREEN_MIN_SCORE_MARGIN = 0.20       # best minus second-best must reach this

FEATURE_PARAMS = {
    "app_title_header": {"keywords": ("whatsapp",), "max_y": 0.13, "max_x": 0.20},
    # "al" covers the common OCR misread of "AI".
    "search_bar": {"phrases": ("ask meta ai or search", "ask meta al or search", "ask meta ai",
                               "ask meta al", "search"), "max_y": 0.25},
    "filter_chips": {
        "keywords": ("all", "unread", "favourites", "favorites", "groups"),
        "max_y": 0.30, "min_count": 2, "target_count": 3,
    },
    "bottom_navigation": {
        "keywords": ("chats", "updates", "communities", "calls", "status"),
        "min_y": 0.85, "min_count": 2, "target_count": 3,
    },
    "conversation_rows": {
        "timestamp_min_x_center": 0.75,
        "name_min_x": 0.12, "name_max_x": 0.35,
        "min_y": 0.22, "max_y": 0.90,
        "header_exclusion_max_y": 0.14,
        "row_y_tolerance": 0.015,
        "min_count": 1, "target_count": 3,
    },
    "chat_list_privacy_footer": {"phrases": ("your personal messages are end to end encrypted",)},
    "message_composer": {
        "keywords": ("message",), "phrases": ("type a message",),
        "min_y": 0.82,
        "keyboard_open_min_y": 0.42,
        "max_x": 0.40, "max_row_words": 4,
    },
    "keyboard_open": {
        "min_y": 0.58,
        "language_row_min_y": 0.72,
        "language_phrases": ("english", "hindi", "keyboard"),
        "noisy_word_min_count": 10,
        "noisy_word_min_y": 0.62,
        "noisy_max_confidence": 0.72,
    },
    "conversation_header_title": {"min_y": 0.035, "max_y": 0.12, "min_x": 0.18, "max_x": 0.55},
    "date_separators": {"min_x_center": 0.35, "max_x_center": 0.65, "min_y": 0.10, "max_y": 0.88,
                        "min_count": 1, "target_count": 2},
    "bubble_timestamps": {
        "max_x_center": 0.92,
        "header_exclusion_max_y": 0.12,
        "min_y": 0.10, "max_y": 0.88,
        "min_count": 2, "target_count": 3,
    },
    "call_event_bubbles": {"phrases": ("missed voice call", "missed video call", "tap to call back")},
    "contact_settings_labels": {
        "phrases": (
            "media links and docs", "manage storage", "media visibility",
            "disappearing messages", "chat lock", "starred messages", "mute notifications",
            "custom notifications", "about and phone number", "groups in common",
            "add to favourites", "add to favorites", "chat theme", "advanced chat privacy",
            "view in address book", "kept messages",
        ),
        # Single-word labels must be the only content of their row.
        "single_word_labels": ("notifications", "encryption", "block", "report"),
        "min_count": 2, "target_count": 4,
    },
    "profile_action_buttons": {
        "keywords": ("call", "video", "voice", "audio", "save", "pay", "search", "add"),
        "min_y": 0.12, "max_y": 0.55, "row_y_tolerance": 0.015,
        "min_x_spacing": 0.12, "min_count": 2, "target_count": 3,
    },
    "centered_profile_header": {
        "min_y": 0.10, "max_y": 0.40, "max_center_offset": 0.08,
        "min_height_ratio": 1.25,  # row height vs. median word height
    },
    "phone_in_profile_header": {"min_y": 0.10, "max_y": 0.40, "max_center_offset": 0.12},
    "send_contact_header": {
        "phrases": ("send contact",),
        "max_y": 0.15,
        "min_x": 0.08,
    },
    "mobile_type_label": {
        "keywords": ("mobile", "home", "work", "other"),
        "min_y": 0.15,
        "max_y": 0.50,
        "max_x": 0.55,
    },
    "share_contact_name_row": {
        "min_y": 0.12,
        "max_y": 0.22,
        "min_x": 0.18,
        "max_x": 0.70,
    },
    "phone_in_share_contact_list": {
        "phone_min_y": 0.18,
        "phone_max_y": 0.40,
        "mobile_max_vertical_gap": 0.04,
    },
    "phone_in_chat_header": {"max_y": 0.12, "min_x": 0.18},
    "in_chat_vcard": {
        "phone_min_y": 0.12,
        "phone_max_y": 0.72,
        "mobile_max_vertical_gap": 0.05,
        "mobile_max_x": 0.55,
    },
    "message_media_bubble": {
        "keywords": ("photo", "video", "gif", "sticker", "document", "audio"),
        "min_y": 0.14, "max_y": 0.88,
    },
}

# ---------------------------------------------------------------------------
# Phone-number location rules (screen-type specific, normalized y = bbox center)
# ---------------------------------------------------------------------------
LOCATION_RULES = {
    "CHATS_LIST": {
        "header_max_y": 0.25,            # fallback when no search/filter anchor is found
        "header_anchor_margin": 0.01,
        "bottom_nav_min_y": 0.86,        # fallback when no navigation labels are found
        "bottom_nav_icon_allowance": 0.05,
        "row_min_x": 0.12,
        "row_max_x": 0.75,
    },
    "CHAT_DETAIL": {
        "header_max_y": 0.11,
        "composer_min_y": 0.87,          # fallback when no composer anchor is found
        "composer_anchor_margin": 0.01,
        "keyboard_composer_min_y": 0.38, # fallback when keyboard is open (composer sits higher)
    },
    "CONTACT_INFO": {
        "header_max_y": 0.10,
        "details_max_y": 0.40,           # fallback when no "Media, links and docs" anchor
    },
    "SEND_CONTACT": {
        "header_max_y": 0.12,
        "details_max_y": 0.55,           # selectable number rows sit below the contact name
    },
}

# ---------------------------------------------------------------------------
# Alert / review policy
# ---------------------------------------------------------------------------
PROHIBITED_LOCATIONS = {
    "CHATS_LIST": ("CONTACT_ROW",),
    # Header title and any visible number in the message stream (text, vCard, links).
    "CHAT_DETAIL": ("TOP_HEADER", "MESSAGE_AREA"),
    "CONTACT_INFO": ("CONTACT_DETAILS",),
    "SEND_CONTACT": ("CONTACT_DETAILS",),
}
ALERT_REASON_CODES = {
    "CHATS_LIST": "PHONE_NUMBER_VISIBLE_IN_CHATS_LIST_ROW",
    "CHAT_DETAIL": "PHONE_NUMBER_VISIBLE_IN_CHAT_DETAIL",
    "CONTACT_INFO": "PHONE_NUMBER_VISIBLE_IN_CONTACT_INFO",
    "SEND_CONTACT": "PHONE_NUMBER_VISIBLE_IN_SEND_CONTACT",
}
# Finer-grained codes for CHAT_DETAIL when every eligible candidate shares one location.
CHAT_DETAIL_ALERT_REASON_BY_LOCATION = {
    "TOP_HEADER": "PHONE_NUMBER_VISIBLE_IN_CHAT_HEADER",
    "MESSAGE_AREA": "PHONE_NUMBER_VISIBLE_IN_CHAT_MESSAGE",
}
# If True, only phonenumbers-valid numbers raise an alert; other candidates in a
# prohibited area are sent to review instead.
ALERT_REQUIRES_VALID_NUMBER = True
# If True, candidates below OCR_CONFIDENCE_THRESHOLD are sent to review instead of alerting.
ALERT_REQUIRES_CONFIDENT_OCR = True
REVIEW_ON_UNKNOWN_SCREEN_WITH_CANDIDATE = True
REVIEW_ON_OCR_FAILURE = True
REVIEW_ON_UNRELIABLE_OCR_WITHOUT_CANDIDATES = True
REVIEW_ON_CANDIDATE_OUTSIDE_PROHIBITED_AREA = False

# ---------------------------------------------------------------------------
# Debug output
# ---------------------------------------------------------------------------
DEBUG_OUTPUT_DIR = "output"
