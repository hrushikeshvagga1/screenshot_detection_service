"""CLI entry point: analyze one WhatsApp screenshot for visible phone numbers.

Usage:
    python main.py --image screenshots/reference_contact_info.jpg [--output output/result.json] [--debug]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from PIL import Image, ImageDraw

import config
from decision_engine import Decision, decide
from ocr_engine import ImageValidationError, OcrResult, load_and_validate_image, run_ocr
from phone_detector import PhoneCandidate, detect_phone_numbers
from screen_classifier import ClassificationResult, assign_locations, classify_screen

logger = logging.getLogger("whatsapp_poc")

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_OUTPUT_ERROR = 3

PII_NOTICE = (
    "'text' and 'normalized_number' contain personal information. "
    "Use for local POC debugging only."
)


def analyze(image_path: str, region: str = config.DEFAULT_PHONE_REGION) -> Dict[str, object]:
    """Run the full pipeline on one image and return its intermediate objects."""
    image, image_format = load_and_validate_image(image_path)
    ocr = run_ocr(image)
    candidates = detect_phone_numbers(ocr, region=region)
    classification = classify_screen(ocr, candidates)
    assign_locations(candidates, classification, ocr)
    decision = decide(ocr, candidates, classification)
    return {
        "image": image,
        "image_format": image_format,
        "region": region,
        "ocr": ocr,
        "candidates": candidates,
        "classification": classification,
        "decision": decision,
    }


def build_result(image_path: str, run: Dict[str, object]) -> Dict[str, object]:
    ocr: OcrResult = run["ocr"]
    candidates: List[PhoneCandidate] = run["candidates"]
    classification: ClassificationResult = run["classification"]
    decision: Decision = run["decision"]
    return {
        "image": image_path,
        "image_format": run["image_format"],
        "image_dimensions": {"width": ocr.image_width, "height": ocr.image_height},
        "screen_classification": classification.to_dict(),
        "phone_detection": {
            "number_detected": bool(candidates),
            "candidate_count": len(candidates),
            "region": run["region"],
            "pii_notice": PII_NOTICE,
            "candidates": [c.to_dict() for c in candidates],
        },
        "decision": decision.to_dict(),
        "ocr": ocr.summary_dict(),
    }


def format_summary(result: Dict[str, object], candidates: Sequence[PhoneCandidate]) -> str:
    sc = result["screen_classification"]
    dec = result["decision"]
    lines = [
        "=" * 60,
        "WhatsApp screenshot analysis",
        "=" * 60,
        f"Image          : {result['image']} "
        f"({result['image_dimensions']['width']}x{result['image_dimensions']['height']})",
        f"OCR            : {'ok' if result['ocr']['success'] else 'FAILED'}, "
        f"{result['ocr']['text_blocks_detected']} text blocks, "
        f"mean confidence {result['ocr']['mean_confidence']:.2f}, "
        f"{'reliable' if result['ocr']['reliable'] else 'UNRELIABLE'}",
        f"Screen type    : {sc['type']} (confidence {sc['confidence']:.2f}, best guess {sc['best_guess']})",
    ]
    for item in sc["evidence"]:
        lines.append(f"    + {item}")
    if sc["uncertainty"]:
        lines.append(f"    ! {sc['uncertainty']}")
    lines.append(f"Phone numbers  : {len(candidates)} candidate(s)")
    for c in candidates:
        lines.append(
            f"    #{c.candidate_id} {c.masked_number()} [{c.validation_status}] "
            f"location={c.location}{'/' + c.location_detail if c.location_detail else ''} "
            f"ocr_conf={c.ocr_confidence:.2f}"
        )
    lines.append(
        f"Decision       : alert={dec['alert']} review_required={dec['review_required']} "
        f"reason={dec['reason_code']}"
    )
    lines.append(f"    {dec['explanation']}")
    for note in dec["uncertainties"]:
        lines.append(f"    ! {note}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Debug helpers (opt-in, local only; output may contain personal data)
# ---------------------------------------------------------------------------
def _debug(msg: str) -> None:
    print(f"[DEBUG] {msg}", file=sys.stderr)


def print_debug(run: Dict[str, object]) -> None:
    ocr: OcrResult = run["ocr"]
    classification: ClassificationResult = run["classification"]
    candidates: List[PhoneCandidate] = run["candidates"]
    _debug("LOCAL DEBUG OUTPUT - may contain personal data (phone numbers, names).")
    _debug(f"Preprocessing: {ocr.preprocessing}")
    _debug(f"OCR text blocks ({len(ocr.words)}):")
    for w in ocr.words:
        b, n = w.bbox, w.norm
        _debug(
            f"  {w.text!r:30} conf={w.confidence:.2f} px=({b.x},{b.y},{b.width},{b.height}) "
            f"norm=({n.x:.3f},{n.y:.3f},{n.width:.3f},{n.height:.3f})"
        )
    _debug("Screen scores: " + ", ".join(f"{k}={v:.3f}" for k, v in classification.scores.items()))
    _debug(f"Score margin: {classification.margin:.3f}; anchors: {classification.anchors}")
    for fid, f in classification.features.items():
        _debug(f"  feature {fid:28} strength={f.strength:.2f} {f.evidence or '-'}")
    for c in candidates:
        _debug(
            f"  candidate #{c.candidate_id}: text={c.text!r} normalized={c.normalized_number} "
            f"status={c.validation_status} location={c.location} detail={c.location_detail} "
            f"norm_bbox={c.norm.to_dict()}"
        )


def _region_lines(classification: ClassificationResult) -> List[float]:
    rules = config.LOCATION_RULES.get(classification.screen_type, {})
    return sorted({v for k, v in rules.items() if k.endswith("_y")} | {config.STATUS_BAR_MAX_Y})


def save_debug_artifacts(image_path: str, run: Dict[str, object], result: Dict[str, object], out_dir: Path) -> List[Path]:
    ocr: OcrResult = run["ocr"]
    candidates: List[PhoneCandidate] = run["candidates"]
    classification: ClassificationResult = run["classification"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(image_path).stem
    source = Path(image_path).resolve()

    annotated_path = out_dir / f"{stem}_annotated.png"
    diagnostics_path = out_dir / f"{stem}_diagnostics.json"
    for path in (annotated_path, diagnostics_path):
        if path.resolve() == source:
            raise OSError(f"Refusing to overwrite the original screenshot: {path}")

    canvas: Image.Image = run["image"].convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    for y in _region_lines(classification):
        py = round(y * canvas.height)
        draw.line([(0, py), (canvas.width, py)], fill=(255, 165, 0), width=1)
    for w in ocr.words:
        b = w.bbox
        draw.rectangle([b.x, b.y, b.right, b.bottom], outline=(0, 120, 255), width=1)
    for c in candidates:
        b = c.bbox
        draw.rectangle([b.x - 2, b.y - 2, b.right + 2, b.bottom + 2], outline=(255, 0, 0), width=3)
        draw.text((b.x, max(0, b.y - 12)), f"#{c.candidate_id} {c.location}", fill=(255, 0, 0))
    draw.text((4, canvas.height - 14), f"{classification.screen_type} {classification.confidence:.2f}",
              fill=(255, 0, 0))
    canvas.save(annotated_path)

    diagnostics = {
        "result": result,
        "features": {fid: f.to_dict() for fid, f in classification.features.items()},
        "anchors": classification.anchors,
        "ocr_words": [w.to_dict() for w in ocr.words],
    }
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8")
    return [annotated_path, diagnostics_path]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect visible phone numbers in a WhatsApp screenshot.")
    parser.add_argument("--image", required=True, help="Path to a PNG/JPG/JPEG screenshot.")
    parser.add_argument("--output", help="Optional path to write the JSON result.")
    parser.add_argument("--debug", action="store_true",
                        help="Print OCR/classifier diagnostics and save an annotated image (local, may contain PII).")
    parser.add_argument("--debug-dir", default=config.DEBUG_OUTPUT_DIR,
                        help=f"Directory for debug artifacts (default: {config.DEBUG_OUTPUT_DIR}).")
    parser.add_argument("--region", default=config.DEFAULT_PHONE_REGION,
                        help=f"Default phone-number region (default: {config.DEFAULT_PHONE_REGION}).")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        run = analyze(args.image, region=args.region)
    except ImageValidationError as exc:
        print(json.dumps({"image": args.image, "error": "INVALID_IMAGE", "message": str(exc)}, indent=2))
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    result = build_result(args.image, run)
    print(format_summary(result, run["candidates"]))
    json_text = json.dumps(result, indent=2, ensure_ascii=False)
    print(json_text)

    if args.debug:
        print_debug(run)
        try:
            for path in save_debug_artifacts(args.image, run, result, Path(args.debug_dir)):
                _debug(f"Saved {path}")
        except OSError as exc:
            print(f"Error: could not save debug artifacts: {exc}", file=sys.stderr)
            return EXIT_OUTPUT_ERROR

    if args.output:
        out_path = Path(args.output)
        if out_path.resolve() == Path(args.image).resolve():
            print("Error: --output must not point to the input image.", file=sys.stderr)
            return EXIT_OUTPUT_ERROR
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json_text + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Error: could not write output file: {exc}", file=sys.stderr)
            return EXIT_OUTPUT_ERROR
        print(f"JSON result written to {out_path}", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
