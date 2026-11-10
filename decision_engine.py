"""Business rules: turn OCR, phone candidates and screen classification into severity decisions.

Severity replaces a single boolean alert for production integrations. ``alert`` is
kept for backward compatibility: True when severity is CRITICAL, HIGH, or MEDIUM.
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
    severity: str
    alert: bool
    review_required: bool
    reason_code: str
    explanation: str
    triggering_candidate_ids: List[int] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "severity": self.severity,
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


def _severity_to_alert(severity: str) -> bool:
    return severity in (
        config.SEVERITY_CRITICAL,
        config.SEVERITY_HIGH,
        config.SEVERITY_MEDIUM,
    )


def _critical_decision(
    screen: str,
    classification: ClassificationResult,
    candidates: Sequence[PhoneCandidate],
    uncertainties: List[str],
) -> Decision:
    reason = config.CRITICAL_SCREEN_REASON_CODES.get(screen, "SENSITIVE_SCREEN_DETECTED")
    phone_note = ""
    ids: List[int] = []
    if candidates:
        ids = [c.candidate_id for c in candidates]
        phone_note = f" Also detected {len(candidates)} phone-number candidate(s)."
    return Decision(
        severity=config.SEVERITY_CRITICAL,
        alert=True,
        review_required=False,
        reason_code=reason,
        explanation=(
            f"Classified as {screen} (confidence {classification.confidence:.2f}). "
            f"This screen type is treated as critical regardless of visible phone numbers.{phone_note}"
        ),
        triggering_candidate_ids=ids,
        uncertainties=uncertainties,
    )


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
            severity=config.SEVERITY_NONE,
            alert=False,
            review_required=config.REVIEW_ON_OCR_FAILURE,
            reason_code="OCR_FAILED",
            explanation=f"OCR could not be performed, so the screenshot was not analyzed. {ocr.error or ''}".strip(),
            uncertainties=uncertainties,
        )

    screen = classification.screen_type

    if screen in config.CRITICAL_SCREEN_TYPES and screen != UNKNOWN:
        return _critical_decision(screen, classification, candidates, uncertainties)

    if not candidates:
        if not ocr.reliable:
            return Decision(
                severity=config.SEVERITY_NONE,
                alert=False,
                review_required=config.REVIEW_ON_UNRELIABLE_OCR_WITHOUT_CANDIDATES,
                reason_code="NO_PHONE_NUMBER_DETECTED_LOW_OCR_QUALITY",
                explanation="No phone number was detected, but OCR quality was too low to rely on that result.",
                uncertainties=uncertainties,
            )
        return Decision(
            severity=config.SEVERITY_NONE,
            alert=False,
            review_required=False,
            reason_code="NO_PHONE_NUMBER_DETECTED",
            explanation="No phone-number candidate was detected in the screenshot.",
            uncertainties=uncertainties,
        )

    if screen == UNKNOWN:
        eligible = [c for c in candidates if _alert_eligible(c)]
        if eligible:
            return Decision(
                severity=config.SEVERITY_HIGH,
                alert=True,
                review_required=False,
                reason_code="PHONE_NUMBER_ON_UNKNOWN_SCREEN",
                explanation=(
                    f"{len(eligible)} phone number(s) detected on an unrecognized screen "
                    f"(best guess {classification.best_guess or 'none'})."
                ),
                triggering_candidate_ids=[c.candidate_id for c in eligible],
                uncertainties=uncertainties,
            )
        return Decision(
            severity=config.SEVERITY_MEDIUM,
            alert=_severity_to_alert(config.SEVERITY_MEDIUM),
            review_required=config.REVIEW_ON_UNKNOWN_SCREEN_WITH_CANDIDATE,
            reason_code="PHONE_NUMBER_ON_UNKNOWN_SCREEN",
            explanation=(
                f"{len(candidates)} phone-number candidate(s) on an unrecognized screen, but none met "
                "alert policy (validation or OCR confidence)."
            ),
            triggering_candidate_ids=[c.candidate_id for c in candidates],
            uncertainties=uncertainties,
        )

    prohibited = set(config.PROHIBITED_LOCATIONS.get(screen, ()))
    in_prohibited = [c for c in candidates if c.location in prohibited]
    eligible = [c for c in in_prohibited if _alert_eligible(c)]
    areas = ", ".join(sorted(prohibited)) or "none"

    if eligible and screen in config.HIGH_PHONE_SCREEN_TYPES:
        if screen == "CHAT_DETAIL":
            reason_code = _chat_detail_reason_code(eligible)
        else:
            reason_code = config.ALERT_REASON_CODES.get(screen, "PHONE_NUMBER_VISIBLE_IN_PROHIBITED_AREA")
        return Decision(
            severity=config.SEVERITY_HIGH,
            alert=True,
            review_required=False,
            reason_code=reason_code,
            explanation=(
                f"{len(eligible)} phone number(s) meeting the policy detected in a prohibited area "
                f"({', '.join(sorted({c.location for c in eligible}))}) of a {screen} screen (high severity)."
            ),
            triggering_candidate_ids=[c.candidate_id for c in eligible],
            uncertainties=uncertainties,
        )

    if in_prohibited:
        return Decision(
            severity=config.SEVERITY_MEDIUM,
            alert=_severity_to_alert(config.SEVERITY_MEDIUM),
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
        severity=config.SEVERITY_NONE,
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
