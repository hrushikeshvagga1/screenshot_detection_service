"""Business rules: turn OCR, phone candidates and screen classification into an alert decision.

An alert means "investigate this screenshot"; it does not prove a privacy violation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import config
from ocr_engine import OcrResult
from phone_detector import PhoneCandidate
from screen_classifier import UNKNOWN, ClassificationResult


@dataclass
class Decision:
    alert: bool
    review_required: bool
    reason_code: str
    explanation: str
    triggering_candidate_ids: List[int] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "alert": self.alert,
            "review_required": self.review_required,
            "reason_code": self.reason_code,
            "explanation": self.explanation,
            "triggering_candidate_ids": list(self.triggering_candidate_ids),
            "uncertainties": list(self.uncertainties),
        }


def _chat_detail_reason_code(eligible: Sequence[PhoneCandidate]) -> str:
    locations = {c.location for c in eligible}
    if len(locations) == 1:
        loc = next(iter(locations))
        return config.CHAT_DETAIL_ALERT_REASON_BY_LOCATION.get(
            loc, config.ALERT_REASON_CODES["CHAT_DETAIL"]
        )
    return config.ALERT_REASON_CODES["CHAT_DETAIL"]


def _alert_eligible(candidate: PhoneCandidate) -> bool:
    if config.ALERT_REQUIRES_VALID_NUMBER and not candidate.valid:
        return False
    if config.ALERT_REQUIRES_CONFIDENT_OCR and candidate.low_ocr_confidence:
        return False
    return True


def _collect_uncertainties(
    ocr: OcrResult, candidates: Sequence[PhoneCandidate], classification: ClassificationResult
) -> List[str]:
    notes: List[str] = []
    if ocr.success and not ocr.reliable:
        notes.extend(f"OCR: {n}" for n in ocr.reliability_notes)
    if classification.uncertainty:
        notes.append(f"Screen classification: {classification.uncertainty}")
    for c in candidates:
        if c.low_ocr_confidence:
            notes.append(
                f"Candidate {c.candidate_id}: OCR confidence {c.ocr_confidence:.2f} is below "
                f"{config.OCR_CONFIDENCE_THRESHOLD:.2f}"
            )
        if not c.valid:
            notes.append(f"Candidate {c.candidate_id}: failed phonenumbers validation ({c.validation_status})")
        if c.ocr_corrections_applied:
            notes.append(f"Candidate {c.candidate_id}: OCR character corrections were applied")
    return notes


def decide(
    ocr: OcrResult, candidates: Sequence[PhoneCandidate], classification: ClassificationResult
) -> Decision:
    uncertainties = _collect_uncertainties(ocr, candidates, classification)

    if not ocr.success:
        return Decision(
            alert=False,
            review_required=config.REVIEW_ON_OCR_FAILURE,
            reason_code="OCR_FAILED",
            explanation=f"OCR could not be performed, so the screenshot was not analyzed. {ocr.error or ''}".strip(),
            uncertainties=uncertainties,
        )

    if not candidates:
        if not ocr.reliable:
            return Decision(
                alert=False,
                review_required=config.REVIEW_ON_UNRELIABLE_OCR_WITHOUT_CANDIDATES,
                reason_code="NO_PHONE_NUMBER_DETECTED_LOW_OCR_QUALITY",
                explanation="No phone number was detected, but OCR quality was too low to rely on that result.",
                uncertainties=uncertainties,
            )
        return Decision(
            alert=False,
            review_required=False,
            reason_code="NO_PHONE_NUMBER_DETECTED",
            explanation="No phone-number candidate was detected in the screenshot.",
            uncertainties=uncertainties,
        )

    screen = classification.screen_type
    if screen == UNKNOWN:
        return Decision(
            alert=False,
            review_required=config.REVIEW_ON_UNKNOWN_SCREEN_WITH_CANDIDATE,
            reason_code="PHONE_NUMBER_ON_UNKNOWN_SCREEN",
            explanation=(
                f"{len(candidates)} phone-number candidate(s) found, but the screen type could not be "
                "determined reliably, so the location cannot be judged. Manual review is required."
            ),
            triggering_candidate_ids=[c.candidate_id for c in candidates],
            uncertainties=uncertainties,
        )

    prohibited = set(config.PROHIBITED_LOCATIONS.get(screen, ()))
    in_prohibited = [c for c in candidates if c.location in prohibited]
    eligible = [c for c in in_prohibited if _alert_eligible(c)]
    areas = ", ".join(sorted(prohibited)) or "none"

    if eligible:
        if screen == "CHAT_DETAIL":
            reason_code = _chat_detail_reason_code(eligible)
        else:
            reason_code = config.ALERT_REASON_CODES.get(screen, "PHONE_NUMBER_VISIBLE_IN_PROHIBITED_AREA")
        return Decision(
            alert=True,
            review_required=False,
            reason_code=reason_code,
            explanation=(
                f"{len(eligible)} phone number(s) meeting the alert policy detected in a prohibited area "
                f"({', '.join(sorted({c.location for c in eligible}))}) of a {screen} screen."
            ),
            triggering_candidate_ids=[c.candidate_id for c in eligible],
            uncertainties=uncertainties,
        )

    if in_prohibited:
        return Decision(
            alert=False,
            review_required=True,
            reason_code="UNCERTAIN_PHONE_NUMBER_IN_PROHIBITED_AREA",
            explanation=(
                f"{len(in_prohibited)} phone-number candidate(s) found in a prohibited area of a {screen} "
                "screen, but they failed validation or had low OCR confidence. Manual review is required."
            ),
            triggering_candidate_ids=[c.candidate_id for c in in_prohibited],
            uncertainties=uncertainties,
        )

    return Decision(
        alert=False,
        review_required=config.REVIEW_ON_CANDIDATE_OUTSIDE_PROHIBITED_AREA,
        reason_code="PHONE_NUMBER_OUTSIDE_PROHIBITED_AREA",
        explanation=(
            f"{len(candidates)} phone-number candidate(s) found on a {screen} screen, but none are in a "
            f"prohibited area ({areas}). Locations: "
            f"{', '.join(sorted({c.location for c in candidates}))}."
        ),
        uncertainties=uncertainties,
    )
