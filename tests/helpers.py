"""Synthetic OCR results for unit tests (no Tesseract needed).

Layouts use pixel coordinates measured on a 460x1024 reference screenshot and
can be rescaled to other resolutions to exercise normalized-coordinate rules.
The phone number used is a dummy value.
"""

from typing import Iterable, List, Sequence, Tuple

from ocr_engine import BoundingBox, OcrResult, OcrWord, assess_reliability

REF_W, REF_H = 460, 1024
Word = Tuple[str, int, int, int, int, float]  # text, x, y, w, h, confidence


def make_ocr(words: Iterable[Word], width: int = REF_W, height: int = REF_H,
             scale_to: Tuple[int, int] = None) -> OcrResult:
    out_w, out_h = scale_to or (width, height)
    sx, sy = out_w / width, out_h / height
    ocr_words: List[OcrWord] = []
    for text, x, y, w, h, conf in words:
        bbox = BoundingBox(round(x * sx), round(y * sy), max(1, round(w * sx)), max(1, round(h * sy)))
        ocr_words.append(OcrWord(text=text, confidence=conf, bbox=bbox, norm=bbox.normalized(out_w, out_h)))
    result = OcrResult(success=True, image_width=out_w, image_height=out_h, words=ocr_words)
    assess_reliability(result)
    return result


def phone_words(x: int, y: int, h: int = 14, conf: float = 0.95,
                parts: Sequence[str] = ("+91", "98765", "43210")) -> List[Word]:
    """Lay out phone-number parts left to right with small, natural gaps."""
    words: List[Word] = []
    cursor = x
    for part in parts:
        w = max(10, round(len(part) * h * 0.7))
        words.append((part, cursor, y, w, h, conf))
        cursor += w + 6
    return words


def chat_detail_words(header_text: Sequence[Word] = None, body_phone: bool = True) -> List[Word]:
    header = list(header_text) if header_text else [("Oprtny1", 121, 66, 67, 20, 0.92)]
    words: List[Word] = header + [
        ("Missed", 93, 195, 53, 13, 0.96), ("voice", 150, 195, 40, 13, 0.96), ("call", 194, 195, 26, 13, 0.96),
        ("Tap", 93, 219, 26, 15, 0.96), ("to", 124, 220, 14, 11, 0.96), ("call", 143, 218, 24, 13, 0.96),
        ("back", 173, 218, 34, 13, 0.96), ("3:38", 224, 228, 27, 11, 0.94), ("pm", 256, 231, 19, 10, 0.94),
        ("Hello", 33, 280, 39, 14, 0.96), ("4:53", 81, 289, 27, 17, 0.96), ("pm", 117, 293, 16, 10, 0.96),
        ("Friday", 210, 330, 40, 14, 0.96),
        ("Hi", 33, 416, 16, 14, 0.96), ("11:22", 68, 426, 35, 10, 0.95), ("am", 108, 429, 19, 8, 0.96),
        ("Yesterday", 197, 644, 66, 14, 0.96),
        ("Helo", 33, 687, 37, 15, 0.96), ("12:54", 89, 698, 34, 11, 0.96), ("pm", 129, 700, 19, 10, 0.96),
        ("Today", 211, 821, 39, 14, 0.96),
        ("Message", 67, 917, 89, 29, 0.95),
    ]
    if body_phone:
        words += phone_words(184, 864) + [("11:14", 344, 872, 36, 11, 0.9), ("am", 385, 872, 20, 11, 0.9)]
    return words


def chats_list_words(phone_rows: int = 1) -> List[Word]:
    words: List[Word] = [
        ("WhatsApp", 19, 65, 131, 25, 0.91),
        ("Ask", 68, 134, 31, 15, 0.93), ("Meta", 107, 135, 41, 14, 0.91), ("AI", 154, 135, 16, 14, 0.91),
        ("or", 178, 138, 17, 11, 0.96), ("Search", 201, 134, 57, 15, 0.96),
        ("All", 34, 212, 18, 13, 0.96), ("Unread", 90, 212, 50, 13, 0.96),
        ("Favourites", 179, 212, 75, 12, 0.96), ("Groups", 292, 212, 52, 15, 0.96),
        ("Oprtny1", 87, 281, 66, 18, 0.91), ("11:14am", 379, 283, 62, 11, 0.95),
        ("Photo", 139, 310, 45, 13, 0.96),
        ("WhatsApp", 87, 465, 88, 18, 0.92), ("Yesterday", 375, 467, 66, 14, 0.96),
        ("Edit", 87, 494, 28, 13, 0.96), ("your", 122, 498, 33, 13, 0.96), ("sent", 161, 496, 32, 11, 0.96),
        ("messages", 199, 497, 80, 13, 0.96),
        ("Chats", 35, 934, 43, 13, 0.96), ("Updates", 141, 934, 61, 16, 0.96),
        ("Communities", 237, 935, 100, 12, 0.96), ("Calls", 384, 934, 36, 13, 0.96),
    ]
    row_ys = [373, 560, 650]
    for i in range(phone_rows):
        y = row_ys[i]
        parts = ("+91", "98765", "4321" + str(i))
        words += phone_words(86, y, parts=parts) + [("11:1" + str(i), 379, y, 36, 14, 0.9), ("am", 422, y + 4, 19, 8, 0.9)]
        words.append(("Photo", 139, y + 29, 45, 13, 0.96))
    return words


def contact_info_words(phone: bool = True, phone_conf: float = 0.94) -> List[Word]:
    words: List[Word] = []
    if phone:
        words += phone_words(120, 214, h=21, conf=phone_conf)
    else:
        words.append(("Rahul", 190, 214, 80, 21, 0.95))
    words += [
        ("Call", 59, 325, 24, 11, 0.95), ("Save", 161, 325, 32, 11, 0.96),
        ("Pay", 272, 325, 23, 14, 0.96), ("Search", 367, 325, 45, 11, 0.96),
        ("Media,", 20, 389, 49, 15, 0.96), ("links,", 75, 389, 36, 14, 0.96),
        ("and", 117, 389, 25, 12, 0.96), ("docs", 148, 389, 35, 12, 0.96),
        ("Manage", 88, 593, 71, 19, 0.96), ("storage", 166, 594, 66, 18, 0.96),
        ("345", 87, 619, 29, 14, 0.93), ("kB", 123, 619, 17, 13, 0.93),
        ("Notifications", 88, 682, 113, 17, 0.95),
        ("Media", 88, 750, 54, 16, 0.96), ("visibility", 148, 750, 73, 20, 0.96),
        ("Encryption", 88, 823, 93, 19, 0.96),
        ("Disappearing", 88, 925, 117, 20, 0.96), ("messages", 213, 929, 92, 15, 0.96),
        ("Off", 87, 951, 24, 14, 0.96),
    ]
    return words


def business_contact_info_words(phone: bool = True) -> List[Word]:
    """WhatsApp Business contact profile (Share/Search, phone row, Business Account banner)."""
    words: List[Word] = [
        ("Acolead", 177, 318, 90, 28, 0.95),
        ("Stage", 160, 352, 70, 24, 0.95),
        ("Share", 177, 466, 50, 14, 0.95),
        ("Search", 283, 466, 55, 14, 0.95),
        ("Business", 88, 668, 72, 18, 0.95),
        ("Account", 165, 668, 70, 18, 0.95),
        ("This", 30, 696, 38, 14, 0.94),
        ("account", 72, 696, 62, 14, 0.94),
        ("uses", 138, 696, 38, 14, 0.94),
        ("WhatsApp", 180, 696, 88, 14, 0.94),
        ("Business", 272, 696, 72, 14, 0.94),
        ("Notifications", 88, 768, 113, 17, 0.95),
        ("Media", 88, 840, 54, 16, 0.96),
        ("visibility", 148, 840, 73, 20, 0.96),
        ("Security", 88, 912, 72, 18, 0.95),
    ]
    if phone:
        words += phone_words(120, 533, h=18)
    return words


def link_device_qr_words() -> List[Word]:
    return [
      ("Linked", 95, 66, 72, 22, 0.94),
      ("devices", 175, 66, 88, 22, 0.93),
      ("Use", 88, 520, 40, 18, 0.92),
      ("WhatsApp", 134, 520, 95, 18, 0.92),
      ("on", 234, 524, 26, 14, 0.95),
      ("other", 266, 520, 52, 18, 0.94),
      ("devices", 324, 520, 72, 18, 0.94),
      ("Link", 168, 880, 44, 20, 0.95),
      ("a", 218, 880, 14, 20, 0.95),
      ("device", 238, 880, 66, 20, 0.95),
      ("Your", 62, 948, 42, 14, 0.9),
      ("personal", 109, 948, 70, 14, 0.9),
      ("messages", 185, 948, 78, 14, 0.9),
      ("are", 268, 948, 32, 14, 0.9),
      ("end", 306, 948, 36, 14, 0.9),
      ("to", 348, 948, 22, 14, 0.9),
      ("end", 376, 948, 36, 14, 0.9),
      ("encrypted", 418, 948, 92, 14, 0.9),
    ]


def link_device_code_words() -> List[Word]:
    return [
      ("Enter", 128, 66, 58, 22, 0.94),
      ("code", 192, 66, 52, 22, 0.94),
      ("To", 42, 200, 26, 16, 0.92),
      ("get", 74, 200, 32, 16, 0.92),
      ("your", 112, 200, 44, 16, 0.92),
      ("code", 162, 200, 42, 16, 0.92),
      ("click", 88, 260, 48, 16, 0.93),
      ("Link", 142, 260, 40, 16, 0.93),
      ("with", 188, 260, 40, 16, 0.93),
      ("phone", 234, 260, 54, 16, 0.93),
      ("number", 294, 260, 66, 16, 0.93),
      ("Never", 88, 420, 50, 15, 0.92),
      ("enter", 144, 420, 48, 15, 0.92),
      ("a", 198, 420, 12, 15, 0.92),
      ("code", 216, 420, 46, 15, 0.92),
      ("if", 268, 420, 16, 15, 0.92),
      ("you", 290, 420, 32, 15, 0.92),
      ("didn't", 328, 420, 58, 15, 0.92),
      ("request", 392, 420, 66, 15, 0.92),
      ("it", 464, 420, 18, 15, 0.92),
    ]


def send_contact_words(phone: bool = True, phone_conf: float = 0.93) -> List[Word]:
    """Layout measured from the send-contact reference screenshot (460x1024)."""
    words: List[Word] = [
        ("Send", 63, 64, 56, 20, 0.73),
        ("contact", 128, 67, 85, 18, 0.73),
        ("opprtny#3", 104, 151, 108, 23, 0.92),
        ("Mobile", 83, 260, 52, 13, 0.96),
    ]
    if phone:
        words = (
            words[:3]
            + phone_words(82, 233, h=15, conf=phone_conf, parts=("+91", "72768", "04641"))
            + words[3:]
        )
    return words
