"""Phone-number candidate detection on structured OCR output.

Detection (does this text look like a phone number?) and validation (does
``phonenumbers`` accept it?) are deliberately kept separate: a candidate may be
reported with ``valid = False``.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType

import config
from ocr_engine import BoundingBox, NormalizedBox, OcrResult, OcrWord, group_words_into_rows

logger = logging.getLogger(__name__)

_PHONE_TOKEN = re.compile(r"^\+?[\d().\-]*\d[\d().\-]*$")
_INDIAN_MOBILE = re.compile(config.INDIAN_MOBILE_PATTERN)
_NUMBER_TYPE_NAMES = {
    value: name for name, value in vars(PhoneNumberType).items() if name.isupper() and isinstance(value, int)
}

STATUS_VALID = "VALID"
STATUS_POSSIBLE = "POSSIBLE"
STATUS_INVALID = "INVALID"


@dataclass
class PhoneCandidate:
    candidate_id: int
    text: str                    # original OCR text (personal data)
    normalized_number: str       # E.164 when parseable, else digits (personal data)
    valid: bool
    validation_status: str
    number_type: Optional[str]
    region_code: Optional[str]
    structural_reasons: List[str]
    ocr_confidence: float        # minimum word confidence across the candidate
    ocr_mean_confidence: float
    low_ocr_confidence: bool
    ocr_corrections_applied: bool
    source_word_count: int
    bbox: BoundingBox
    norm: NormalizedBox
    location: str = "UNKNOWN"
    location_detail: Optional[str] = None

    @property
    def split_across_boxes(self) -> bool:
        return self.source_word_count > 1

    def masked_number(self) -> str:
        return mask_number(self.normalized_number)

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.candidate_id,
            "text": self.text,
            "normalized_number": self.normalized_number,
            "valid": self.valid,
            "validation_status": self.validation_status,
            "number_type": self.number_type,
            "region_code": self.region_code,
            "structural_reasons": list(self.structural_reasons),
            "ocr_confidence": round(self.ocr_confidence, 3),
            "ocr_mean_confidence": round(self.ocr_mean_confidence, 3),
            "low_ocr_confidence": self.low_ocr_confidence,
            "ocr_corrections_applied": self.ocr_corrections_applied,
            "split_across_boxes": self.split_across_boxes,
            "location": self.location,
            "location_detail": self.location_detail,
            "bbox": self.bbox.to_dict(),
            "bbox_normalized": self.norm.to_dict(),
        }


def mask_number(number: str) -> str:
    """Hide all but the last two digits, for summaries and logs."""
    digits = [c for c in number if c.isdigit()]
    if len(digits) <= 2:
        return "*" * len(digits)
    prefix = "+" if number.startswith("+") else ""
    return prefix + "*" * (len(digits) - 2) + "".join(digits[-2:])


@dataclass
class _Token:
    word: OcrWord
    cleaned: str       # phone-ish text after stripping and OCR corrections
    corrected: bool

    @property
    def digits(self) -> str:
        return re.sub(r"\D", "", self.cleaned)

    @property
    def has_plus(self) -> bool:
        return self.cleaned.startswith("+")


def _clean_token(text: str) -> Optional[Tuple[str, bool]]:
    """Return (phone-ish text, corrected?) or None if the token cannot be part of a number."""
    token = text.strip().strip(config.PHONE_TOKEN_STRIP_CHARS)
    if token.endswith("."):
        token = token.rstrip(".")
    if not token:
        return None
    corrected = False
    if token[0] in config.OCR_PLUS_CONFUSIONS and len(token) > 1 and token[1].isdigit():
        token = "+" + token[1:]
        corrected = True
    real_digits = sum(c.isdigit() for c in token)
    confusable = sum(c in config.OCR_DIGIT_CONFUSIONS for c in token)
    if (
        confusable
        and real_digits >= config.OCR_CONFUSION_MIN_REAL_DIGITS
        and confusable <= config.OCR_CONFUSION_MAX_SUBSTITUTIONS
    ):
        mapped = "".join(config.OCR_DIGIT_CONFUSIONS.get(c, c) for c in token)
        if _PHONE_TOKEN.match(mapped):
            return mapped, True
    if _PHONE_TOKEN.match(token):
        return token, corrected
    return None


def _evaluate(digits: str, has_plus: bool, region: str) -> Optional[Dict[str, object]]:
    """Apply structural rules and phonenumbers validation to a digit string."""
    if not (config.PHONE_MIN_DIGITS <= len(digits) <= config.PHONE_MAX_DIGITS):
        return None
    raw = ("+" if has_plus else "") + digits
    parsed = None
    try:
        parsed = phonenumbers.parse(raw, region)
    except NumberParseException:
        parsed = None

    valid = bool(parsed is not None and phonenumbers.is_valid_number(parsed))
    possible = bool(parsed is not None and phonenumbers.is_possible_number(parsed))

    # phonenumbers accepts almost any 10-digit string starting 1-5 as an Indian
    # landline, so validity alone is not enough to make a bare digit string a candidate.
    reasons: List[str] = []
    if has_plus:
        reasons.append("explicit_country_code_prefix")
    if _INDIAN_MOBILE.match(digits):
        reasons.append("indian_mobile_pattern")
    if digits.startswith("0") and valid:
        reasons.append("trunk_prefix_valid_number")
    if not reasons:
        return None

    if valid:
        status = STATUS_VALID
    elif possible:
        status = STATUS_POSSIBLE
    else:
        status = STATUS_INVALID
    normalized = phonenumbers.format_number(parsed, PhoneNumberFormat.E164) if parsed is not None else raw
    return {
        "normalized": normalized,
        "valid": valid,
        "status": status,
        "number_type": _NUMBER_TYPE_NAMES.get(phonenumbers.number_type(parsed)) if valid else None,
        "region_code": phonenumbers.region_code_for_number(parsed) if valid else None,
        "reasons": reasons,
    }


def _span_rank(evaluation: Dict[str, object], span_len: int) -> Tuple[int, int, int]:
    status_rank = {STATUS_VALID: 2, STATUS_POSSIBLE: 1, STATUS_INVALID: 0}[evaluation["status"]]
    return status_rank, len(evaluation["reasons"]), span_len


def _phone_runs(tokens: Sequence[Optional[_Token]]) -> List[List[_Token]]:
    """Split a row's tokens into maximal runs of consecutive phone-ish tokens."""
    runs: List[List[_Token]] = []
    current: List[_Token] = []
    for token in tokens:
        if token is None:
            if current:
                runs.append(current)
            current = []
        else:
            current.append(token)
    if current:
        runs.append(current)
    return runs


def detect_phone_numbers(ocr: OcrResult, region: str = config.DEFAULT_PHONE_REGION) -> List[PhoneCandidate]:
    if not ocr.success or not ocr.words:
        return []

    rows = group_words_into_rows(ocr.words, ocr.image_width, ocr.image_height)
    found: List[PhoneCandidate] = []

    for row in rows:
        tokens: List[Optional[_Token]] = []
        for word in row.words:
            cleaned = _clean_token(word.text)
            tokens.append(_Token(word, cleaned[0], cleaned[1]) if cleaned else None)

        for run in _phone_runs(tokens):
            spans = []
            for start in range(len(run)):
                for end in range(start, len(run)):
                    span = run[start:end + 1]
                    if any(t.has_plus for t in span[1:]):
                        break
                    digits = "".join(t.digits for t in span)
                    if len(digits) > config.PHONE_MAX_DIGITS:
                        break
                    evaluation = _evaluate(digits, span[0].has_plus, region)
                    if evaluation:
                        spans.append((_span_rank(evaluation, len(span)), start, end, evaluation))

            # Greedy: keep the best-ranked spans that do not overlap.
            used = set()
            for _, start, end, evaluation in sorted(spans, key=lambda s: s[0], reverse=True):
                indices = set(range(start, end + 1))
                if indices & used:
                    continue
                used |= indices
                found.append(_build_candidate(run[start:end + 1], evaluation, ocr))

    candidates = _deduplicate(found)
    candidates.sort(key=lambda c: (c.bbox.y, c.bbox.x))
    for i, candidate in enumerate(candidates, start=1):
        candidate.candidate_id = i
    logger.info("Detected %d phone-number candidate(s)", len(candidates))
    return candidates


def _build_candidate(span: Sequence[_Token], evaluation: Dict[str, object], ocr: OcrResult) -> PhoneCandidate:
    confidences = [t.word.confidence for t in span]
    bbox = BoundingBox.union([t.word.bbox for t in span])
    min_conf = min(confidences)
    return PhoneCandidate(
        candidate_id=0,
        text=" ".join(t.word.text for t in span),
        normalized_number=str(evaluation["normalized"]),
        valid=bool(evaluation["valid"]),
        validation_status=str(evaluation["status"]),
        number_type=evaluation["number_type"],
        region_code=evaluation["region_code"],
        structural_reasons=list(evaluation["reasons"]),
        ocr_confidence=min_conf,
        ocr_mean_confidence=statistics.mean(confidences),
        low_ocr_confidence=min_conf < config.OCR_CONFIDENCE_THRESHOLD,
        ocr_corrections_applied=any(t.corrected for t in span),
        source_word_count=len(span),
        bbox=bbox,
        norm=bbox.normalized(ocr.image_width, ocr.image_height),
    )


def _deduplicate(candidates: Sequence[PhoneCandidate]) -> List[PhoneCandidate]:
    """Merge candidates for the same number whose boxes overlap (same visible instance)."""
    kept: List[PhoneCandidate] = []
    for candidate in sorted(candidates, key=lambda c: c.ocr_confidence, reverse=True):
        duplicate = any(
            k.normalized_number == candidate.normalized_number
            and k.bbox.iou(candidate.bbox) >= config.PHONE_DEDUP_IOU
            for k in kept
        )
        if not duplicate:
            kept.append(candidate)
    return kept
