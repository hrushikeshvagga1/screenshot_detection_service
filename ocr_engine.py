"""Image validation and Tesseract OCR with coordinate-preserving structured output."""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError

import config

logger = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


class ImageValidationError(Exception):
    """Raised when the input path is not a readable, supported image."""


def normalize_text(text: str) -> str:
    """Lowercase and replace punctuation with single spaces (for keyword matching)."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def alnum_count(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


@dataclass(frozen=True)
class BoundingBox:
    """Pixel box in original-image coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def normalized(self, image_width: int, image_height: int) -> "NormalizedBox":
        return NormalizedBox(
            x=self.x / image_width,
            y=self.y / image_height,
            width=self.width / image_width,
            height=self.height / image_height,
        )

    def iou(self, other: "BoundingBox") -> float:
        ix = max(0, min(self.right, other.right) - max(self.x, other.x))
        iy = max(0, min(self.bottom, other.bottom) - max(self.y, other.y))
        inter = ix * iy
        union = self.width * self.height + other.width * other.height - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def union(boxes: Sequence["BoundingBox"]) -> "BoundingBox":
        x0 = min(b.x for b in boxes)
        y0 = min(b.y for b in boxes)
        x1 = max(b.right for b in boxes)
        y1 = max(b.bottom for b in boxes)
        return BoundingBox(x0, y0, x1 - x0, y1 - y0)

    def to_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class NormalizedBox:
    """Box with coordinates expressed as fractions of image width/height."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    def to_dict(self) -> Dict[str, float]:
        return {
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "width": round(self.width, 4),
            "height": round(self.height, 4),
        }


@dataclass
class OcrWord:
    text: str
    confidence: float  # 0..1
    bbox: BoundingBox
    norm: NormalizedBox
    block_num: int = 0
    line_num: int = 0
    word_num: int = 0

    @property
    def normalized_text(self) -> str:
        return normalize_text(self.text)

    def to_dict(self) -> Dict[str, object]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "bbox": self.bbox.to_dict(),
            "bbox_normalized": self.norm.to_dict(),
            "block_num": self.block_num,
            "line_num": self.line_num,
        }


@dataclass
class OcrRow:
    """Words that sit on the same visual line and are horizontally close."""

    words: List[OcrWord]
    image_width: int
    image_height: int

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def normalized_text(self) -> str:
        return normalize_text(self.text)

    @property
    def content_words(self) -> List[OcrWord]:
        """Words with at least two alphanumeric characters (drops icon noise)."""
        return [w for w in self.words if alnum_count(w.text) >= 2]

    @property
    def content_text(self) -> str:
        return normalize_text(" ".join(w.text for w in self.content_words))

    @property
    def bbox(self) -> BoundingBox:
        return BoundingBox.union([w.bbox for w in self.words])

    @property
    def norm(self) -> NormalizedBox:
        return self.bbox.normalized(self.image_width, self.image_height)

    @property
    def min_confidence(self) -> float:
        return min(w.confidence for w in self.words)


@dataclass
class OcrResult:
    success: bool
    image_width: int
    image_height: int
    words: List[OcrWord] = field(default_factory=list)
    error: Optional[str] = None
    engine_version: Optional[str] = None
    preprocessing: Dict[str, object] = field(default_factory=dict)
    mean_confidence: float = 0.0
    meaningful_word_count: int = 0
    reliable: bool = False
    reliability_notes: List[str] = field(default_factory=list)

    def rows(self, min_confidence: float = 0.0) -> List[OcrRow]:
        words = [w for w in self.words if w.confidence >= min_confidence]
        return group_words_into_rows(words, self.image_width, self.image_height)

    def summary_dict(self) -> Dict[str, object]:
        return {
            "success": self.success,
            "text_blocks_detected": len(self.words),
            "meaningful_words": self.meaningful_word_count,
            "mean_confidence": round(self.mean_confidence, 3),
            "reliable": self.reliable,
            "reliability_notes": list(self.reliability_notes),
            "engine": "tesseract",
            "engine_version": self.engine_version,
            "preprocessing": dict(self.preprocessing),
            "error": self.error,
        }


def _vertical_overlap_ratio(a: BoundingBox, b: BoundingBox) -> float:
    overlap = min(a.bottom, b.bottom) - max(a.y, b.y)
    smaller = min(a.height, b.height)
    return overlap / smaller if smaller > 0 else 0.0


def group_words_into_rows(
    words: Sequence[OcrWord],
    image_width: int,
    image_height: int,
    max_gap_factor: float = config.ROW_MERGE_MAX_GAP_FACTOR,
    min_vertical_overlap: float = config.ROW_MERGE_MIN_VERTICAL_OVERLAP,
) -> List[OcrRow]:
    """Greedy left-to-right grouping of words into visual rows.

    A word joins the row whose last word is vertically aligned with it and
    within ``max_gap_factor`` word-heights to its left. Tesseract block/line
    numbers are ignored on purpose so that text split across OCR blocks
    (for example ``+91`` | ``98765`` | ``43210``) can be re-joined.
    """
    rows: List[List[OcrWord]] = []
    for word in sorted(words, key=lambda w: (w.bbox.x, w.bbox.y)):
        best_row: Optional[List[OcrWord]] = None
        best_gap = 0.0
        for row in rows:
            last = row[-1]
            ref_height = min(word.bbox.height, last.bbox.height)
            gap = word.bbox.x - last.bbox.right
            if gap > max_gap_factor * ref_height or gap < -ref_height:
                continue
            if _vertical_overlap_ratio(word.bbox, last.bbox) < min_vertical_overlap:
                continue
            if best_row is None or gap < best_gap:
                best_row, best_gap = row, gap
        if best_row is None:
            rows.append([word])
        else:
            best_row.append(word)
    result = [OcrRow(r, image_width, image_height) for r in rows]
    result.sort(key=lambda r: (r.bbox.y, r.bbox.x))
    return result


def load_and_validate_image(path_str: str) -> Tuple[Image.Image, str]:
    """Return (image copy, detected format) or raise ImageValidationError."""
    path = Path(path_str)
    if not path.exists():
        raise ImageValidationError(f"Image file not found: {path}")
    if not path.is_file():
        raise ImageValidationError(f"Image path is not a regular file: {path}")
    if path.suffix.lower() not in config.SUPPORTED_IMAGE_EXTENSIONS:
        raise ImageValidationError(
            f"Unsupported file extension '{path.suffix}'. "
            f"Supported: {', '.join(config.SUPPORTED_IMAGE_EXTENSIONS)}"
        )
    if path.stat().st_size == 0:
        raise ImageValidationError(f"Image file is empty: {path}")
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as img:
            image_format = img.format or ""
            img.load()
            image = img.copy()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageValidationError(f"File is not a valid or readable image: {path} ({exc})") from exc
    if image_format not in config.SUPPORTED_IMAGE_FORMATS:
        raise ImageValidationError(
            f"Unsupported image format '{image_format}'. "
            f"Supported: {', '.join(config.SUPPORTED_IMAGE_FORMATS)}"
        )
    if min(image.size) < config.MIN_IMAGE_SIDE_PX:
        raise ImageValidationError(
            f"Image is too small ({image.width}x{image.height}); "
            f"minimum side is {config.MIN_IMAGE_SIDE_PX}px"
        )
    return image, image_format


def preprocess_for_ocr(image: Image.Image) -> Tuple[Image.Image, float, float, Dict[str, object]]:
    """Return (processed copy, x scale, y scale, description). The input is not modified."""
    gray = ImageOps.grayscale(image.convert("RGB"))
    info: Dict[str, object] = {"grayscale": True, "inverted": False, "scale": 1.0}

    mean_luminance = ImageStat.Stat(gray).mean[0]
    if mean_luminance < config.OCR_INVERT_IF_MEAN_LUMINANCE_BELOW:
        gray = ImageOps.invert(gray)
        info["inverted"] = True

    scale = 1.0
    if config.OCR_UPSCALE_ENABLED and gray.width < config.OCR_TARGET_MIN_WIDTH_PX:
        scale = min(config.OCR_MAX_UPSCALE, config.OCR_TARGET_MIN_WIDTH_PX / gray.width)
        new_size = (round(gray.width * scale), round(gray.height * scale))
        gray = gray.resize(new_size, Image.Resampling.LANCZOS)
    scale_x = gray.width / image.width
    scale_y = gray.height / image.height
    info["scale"] = round(scale, 3)
    return gray, scale_x, scale_y, info


def assess_reliability(result: OcrResult) -> None:
    meaningful = [w for w in result.words if alnum_count(w.text) >= config.OCR_MEANINGFUL_WORD_MIN_ALNUM]
    result.meaningful_word_count = len(meaningful)
    result.mean_confidence = statistics.mean(w.confidence for w in meaningful) if meaningful else 0.0
    notes: List[str] = []
    if not result.success:
        notes.append("OCR engine failed")
    if result.meaningful_word_count < config.OCR_MIN_MEANINGFUL_WORDS:
        notes.append(
            f"Only {result.meaningful_word_count} meaningful words recognized "
            f"(minimum {config.OCR_MIN_MEANINGFUL_WORDS})"
        )
    if meaningful and result.mean_confidence < config.OCR_RELIABLE_MEAN_CONFIDENCE:
        notes.append(
            f"Mean OCR confidence {result.mean_confidence:.2f} is below "
            f"{config.OCR_RELIABLE_MEAN_CONFIDENCE:.2f}"
        )
    result.reliability_notes = notes
    result.reliable = not notes


def _tesseract_version() -> Optional[str]:
    import pytesseract

    try:
        return str(pytesseract.get_tesseract_version())
    except Exception:  # version lookup is informational only
        return None


def run_ocr(image: Image.Image) -> OcrResult:
    """Run Tesseract on a preprocessed copy and map boxes back to original pixels."""
    import pytesseract

    if config.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

    width, height = image.size
    processed, scale_x, scale_y, prep_info = preprocess_for_ocr(image)
    result = OcrResult(success=False, image_width=width, image_height=height, preprocessing=prep_info)

    try:
        data = pytesseract.image_to_data(
            processed,
            lang=config.OCR_LANGUAGE,
            config=config.OCR_TESSERACT_CONFIG,
            output_type=pytesseract.Output.DICT,
            timeout=config.OCR_TIMEOUT_SECONDS,
        )
    except pytesseract.TesseractNotFoundError:
        result.error = (
            "Tesseract binary not found. Install Tesseract OCR and make sure it is on PATH "
            "or set the TESSERACT_CMD environment variable."
        )
        logger.error("OCR failed: Tesseract binary not found")
        assess_reliability(result)
        return result
    except (pytesseract.TesseractError, RuntimeError, OSError) as exc:
        result.error = f"Tesseract OCR failed: {exc}"
        logger.error("OCR failed: %s", type(exc).__name__)
        assess_reliability(result)
        return result

    result.engine_version = _tesseract_version()
    words: List[OcrWord] = []
    for i, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text).strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue
        x = max(0, min(width - 1, round(data["left"][i] / scale_x)))
        y = max(0, min(height - 1, round(data["top"][i] / scale_y)))
        w = max(1, min(width - x, round(data["width"][i] / scale_x)))
        h = max(1, min(height - y, round(data["height"][i] / scale_y)))
        bbox = BoundingBox(x, y, w, h)
        words.append(
            OcrWord(
                text=text,
                confidence=min(1.0, conf / 100.0),
                bbox=bbox,
                norm=bbox.normalized(width, height),
                block_num=int(data["block_num"][i]),
                line_num=int(data["line_num"][i]),
                word_num=int(data["word_num"][i]),
            )
        )
    result.words = words
    result.success = True
    assess_reliability(result)
    logger.info("OCR extracted %d text blocks (mean confidence %.2f)", len(words), result.mean_confidence)
    return result
