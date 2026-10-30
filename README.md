# WhatsApp Screenshot Phone-Number Detection POC

A local, rule-based Python POC that analyzes **one** WhatsApp screenshot and reports:

1. whether a phone number is visible,
2. which WhatsApp screen it is (`CHATS_LIST`, `CHAT_DETAIL`, `CONTACT_INFO`, `SEND_CONTACT`, or `UNKNOWN`),
3. where each detected number appears on that screen,
4. whether the screenshot should raise an alert or needs manual review, and
5. the evidence behind each decision.

It uses only Tesseract OCR, regular expressions, the `phonenumbers` library, bounding-box
coordinates and layout heuristics. No machine-learning model, cloud service or external API is used.

## Requirements

* Python **3.9+** (developed and tested with 3.12)
* Tesseract OCR **binary** 4.x or 5.x (tested with 5.5.0)
* Python packages from `requirements.txt` (`pytesseract`, `Pillow`, `phonenumbers`, `pytest`)

OpenCV is not required: the preprocessing (grayscale, optional dark-theme inversion and
upscaling of a copy) is done with Pillow.

### 1. Install Tesseract (system binary)

`pip install pytesseract` installs only the Python wrapper, **not** the OCR engine.

| OS | Command |
|----|---------|
| macOS (Homebrew) | `brew install tesseract` |
| Ubuntu / Debian | `sudo apt-get install tesseract-ocr` |
| Windows | Install from the [UB Mannheim builds](https://github.com/UB-Mannheim/tesseract/wiki) and add it to `PATH` |

Verify with `tesseract --version`. If the binary is not on `PATH`, point to it explicitly:

```bash
export TESSERACT_CMD=/opt/homebrew/bin/tesseract
```

### 2. Create a virtual environment and install dependencies

```bash
cd whatsapp_screenshot_poc
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Batch worker (queue + Mongo)

| Environment | Queue | Guide |
|-------------|-------|--------|
| **Start here** | Redis / SQS | **[deploy/QUEUE-FROM-SCRATCH.md](deploy/QUEUE-FROM-SCRATCH.md)** |
| **Staging (DO)** | Managed Redis | [deploy/DigitalOcean-staging.md](deploy/DigitalOcean-staging.md) |
| **Production (AWS)** | SQS | [deploy/aws-production.md](deploy/aws-production.md) |

Install worker deps: `pip install -r requirements.txt -r requirements-worker.txt`  
Run consumer: `python -m worker.run_queue`  
Reconcile stuck batches: `python -m worker.reconcile`  

Staging: copy `deploy/env.staging.example` → `.env.staging`, then  
`docker compose -f deploy/docker-compose.staging.yml up -d --build`

## Running

```bash
python main.py --image screenshots/reference_contact_info.jpg
python main.py --image screenshots/reference_contact_info.jpg --output output/contact_info.json
python main.py --image screenshots/reference_contact_info.jpg --debug
```

| Argument | Meaning |
|----------|---------|
| `--image PATH` | Required. PNG, JPG or JPEG screenshot. |
| `--output PATH` | Optional. Also write the JSON result to this file (never the input image). |
| `--debug` | Optional. Print OCR boxes, feature scores and candidates to stderr; save an annotated image and diagnostics JSON. |
| `--debug-dir DIR` | Where debug artifacts go (default `output/`). |
| `--region CC` | Default phone region for parsing numbers without `+` (default `IN`). |

Exit codes: `0` analysis completed (including OCR failures, which are reported in JSON),
`2` invalid/missing/unreadable image, `3` could not write output.

### Processing the three reference screenshots

```bash
python main.py --image screenshots/reference_chats_list.jpg   --output output/chats_list.json
python main.py --image screenshots/reference_chat_detail.jpg  --output output/chat_detail.json
python main.py --image screenshots/reference_contact_info.jpg --output output/contact_info.json
```

Results:

| Screenshot | Screen type | Candidates (location) | Decision |
|------------|-------------|-----------------------|----------|
| `reference_chats_list.jpg` | `CHATS_LIST` (1.00) | 2 (`CONTACT_ROW` title + preview) | `alert: true`, `PHONE_NUMBER_VISIBLE_IN_CHATS_LIST_ROW` |
| `reference_chat_detail.jpg` | `CHAT_DETAIL` (1.00) | 1 (`MESSAGE_AREA`, outgoing bubble) | `alert: true`, `PHONE_NUMBER_VISIBLE_IN_CHAT_MESSAGE` |
| `reference_contact_info.jpg` | `CONTACT_INFO` (1.00) | 1 (`CONTACT_DETAILS`) | `alert: true`, `PHONE_NUMBER_VISIBLE_IN_CONTACT_INFO` |
| `reference_send_contact.jpg` | `SEND_CONTACT` (1.00) | 1 (`CONTACT_DETAILS`, share row) | `alert: true`, `PHONE_NUMBER_VISIBLE_IN_SEND_CONTACT` |

The supplied reference files were JPEG data with a `.png` extension; they were renamed to `.jpg`.
The classifier never looks at file names: the integration test copies each image to a neutral name first.

### Debug mode

`--debug` is explicitly local and opt-in. It:

* prints every OCR text block with pixel and normalized coordinates,
* prints all feature strengths, screen scores, the score margin and layout anchors,
* prints candidates with their location labels,
* saves `output/<name>_annotated.png` (blue = OCR boxes, red = phone candidates with location,
  orange = configured region boundaries), and
* saves `output/<name>_diagnostics.json`.

The original screenshot is never modified or overwritten. **Debug output contains raw OCR text,
including phone numbers and names.** Normal logs (warnings/info) never include phone numbers, and the
human-readable summary masks them (for example `+**********92`).

## Interpreting the JSON

```text
image, image_format, image_dimensions
screen_classification
  type               CHATS_LIST | CHAT_DETAIL | CONTACT_INFO | SEND_CONTACT | UNKNOWN
  confidence         heuristic score of the best-scoring screen type, 0..1 (NOT a probability)
  best_guess         best-scoring type even when `type` is UNKNOWN
  scores             score of every screen type
  score_margin       best minus second-best score
  evidence           human-readable signals supporting the best guess
  evidence_against   per screen type: missing signals and penalties applied
  contributing_signals  feature, weight, strength and contribution for each detected signal
  recognized_keywords / layout_patterns
  uncertainty        why the result is UNKNOWN or otherwise uncertain (null if none)
phone_detection
  number_detected, candidate_count, region, pii_notice
  candidates[]
    text               raw OCR text                      (PERSONAL DATA)
    normalized_number  E.164 form, e.g. +919876543210    (PERSONAL DATA)
    valid / validation_status   phonenumbers result: VALID | POSSIBLE | INVALID
    structural_reasons why it was considered a phone number at all
    ocr_confidence     minimum word confidence (0..1); ocr_mean_confidence also given
    low_ocr_confidence, ocr_corrections_applied, split_across_boxes
    location / location_detail   e.g. CONTACT_ROW / ROW_TITLE
    bbox (pixels) and bbox_normalized (fractions of width/height)
decision
  alert              true = screenshot should be investigated (not proof of a violation)
  review_required    true = automated result is uncertain; a human should look
  reason_code, explanation, triggering_candidate_ids, uncertainties
ocr
  success, text_blocks_detected, meaningful_words, mean_confidence, reliable,
  reliability_notes, engine_version, preprocessing, error
```

The `text` and `normalized_number` fields contain personal information. They are included only for
local POC debugging. Remove or hash them before using results anywhere else.

### Reason codes

| Code | alert | review |
|------|-------|--------|
| `PHONE_NUMBER_VISIBLE_IN_CHATS_LIST_ROW` / `..._IN_CHAT_MESSAGE` / `..._IN_CHAT_HEADER` / `..._IN_CHAT_DETAIL` (mixed) / `..._IN_CONTACT_INFO` / `..._IN_SEND_CONTACT` | true | false |
| `UNCERTAIN_PHONE_NUMBER_IN_PROHIBITED_AREA` (invalid number or low OCR confidence) | false | true |
| `PHONE_NUMBER_ON_UNKNOWN_SCREEN` | false | true |
| `PHONE_NUMBER_OUTSIDE_PROHIBITED_AREA` | false | configurable (false) |
| `NO_PHONE_NUMBER_DETECTED` | false | false |
| `NO_PHONE_NUMBER_DETECTED_LOW_OCR_QUALITY` | false | true |
| `OCR_FAILED` | false | true |

## How it works

### Pipeline

`main.py` → `ocr_engine.py` (validate image, OCR) → `phone_detector.py` (candidates) →
`screen_classifier.py` (screen type + location labels) → `decision_engine.py` (policy).

### OCR (`ocr_engine.py`)

* Validates existence, file type, extension, PNG/JPEG format, full decode (catches truncation) and minimum size.
* Preprocesses a **copy**: grayscale, inverts dark-theme images, upscales narrow images to about 920 px
  wide (the reference screenshots are 460 px wide; at 1x Tesseract misread `+91` as `#91`).
* Runs Tesseract with `--psm 11` (sparse text), then maps every box back to original pixel coordinates.
* Returns structured `OcrWord` objects (text, confidence 0..1, pixel and normalized bbox).
* Marks OCR as unreliable if there are fewer than 8 meaningful words or their mean confidence is below 0.60.
* Words are grouped into visual rows by vertical overlap and horizontal gap. Tesseract block numbers
  are ignored, so numbers split across OCR boxes are re-joined.

### Phone-number detection (`phone_detector.py`)

1. Each row is split into tokens. A token is phone-like if it only contains digits, `+` (leading),
   `-`, `.`, `(` or `)` after stripping edge punctuation. Tokens with `:` or `/` (times, dates) are excluded.
2. Conservative OCR fixes: a leading `#` before a digit becomes `+`; `O/o/I/l/|` become digits only
   inside tokens that already have at least 3 real digits (at most 2 substitutions). Corrected candidates are flagged.
3. Every contiguous span of phone-like tokens with 10–13 digits is evaluated. A span is only a
   **candidate** if it has structural evidence: an explicit `+` prefix, the Indian mobile pattern
   `(91|0)?[6-9]XXXXXXXXX`, or a `0` trunk prefix plus a valid number.
   `phonenumbers` validity alone is not enough, because it accepts almost any 10-digit string starting
   with 1–5 as an Indian landline (for example `1234567890` and `2026102211`).
4. Candidates are then **validated** separately with `phonenumbers` (`VALID`/`POSSIBLE`/`INVALID`).
5. Non-overlapping best spans are kept (valid > possible > invalid, then more evidence, then longer).
   Duplicates (same number, overlapping boxes) are merged. The same number at two positions stays as
   two candidates, because they are two visible instances.

### Screen classification (`screen_classifier.py`)

Sixteen named features are computed from OCR words that have confidence ≥ 0.50, using normalized coordinates:

| Screen | Features (weight) |
|--------|-------------------|
| `CHATS_LIST` | `WhatsApp` title top-left (2.0), search bar "Ask Meta AI or Search" (1.5), filter chips All/Unread/Favourites/Groups (2.0), bottom tabs Chats/Updates/Communities/Calls (2.0), conversation rows with a title at x≈0.12–0.35 and a right-aligned timestamp (3.0), phone number used as row title (0.5), "Your personal messages are end-to-end encrypted" footer (0.5) |
| `CHAT_DETAIL` | "Message" composer at the bottom (3.0), title right of avatar in header (1.0), centered date separators such as Today/Yesterday/weekday (1.5), in-bubble timestamps (2.5), "Missed voice call / Tap to call back" bubbles (0.5) |
| `CONTACT_INFO` | section labels such as Media, links and docs / Manage storage / Notifications / Encryption / Disappearing messages (3.5), spaced action-button row Call/Video/Save/Pay/Search (2.5), large centered profile title (relative to the median text height) (2.0), phone number as that title (1.0) |
| `SEND_CONTACT` | **Send contact** header (4.0), **Mobile** / Home / Work line-type label beside a selectable number (2.5), phone row with that label (1.5), contact name row under the header (1.0) |

Penalties (counter-evidence): a composer lowers `CHATS_LIST` and `CONTACT_INFO`; conversation rows lower
`CHAT_DETAIL` and `CONTACT_INFO`; contact labels lower `CHATS_LIST` and `CHAT_DETAIL`; filter chips lower `CHAT_DETAIL`.

`score = clip((Σ weight × strength − Σ penalty × strength) / Σ weights, 0, 1)`.
The result is `UNKNOWN` if the best score is below **0.45**, or if the best and second-best scores
differ by less than **0.20**. A single keyword can never classify a screen.

**The confidence is a heuristic rule score, not a statistically calibrated probability.** A score of
1.0 means "every configured signal for this screen fired", not "100% certain".

### Location labels

Labels use each candidate's normalized bounding-box center and the classified screen type. Where
possible they are anchored to recognized UI text rather than fixed coordinates:

| Screen | Rules |
|--------|-------|
| `CHATS_LIST` | `TOP_HEADER` above the search bar/filter chips (fallback y ≤ 0.25); `OTHER/BOTTOM_NAVIGATION` near the tab bar; otherwise `CONTACT_ROW` if x is in 0.12–0.75 (`ROW_TITLE` if a timestamp is on the same line, else `ROW_PREVIEW`) |
| `CHAT_DETAIL` | `TOP_HEADER` y ≤ 0.11; `BOTTOM_COMPOSER` from the composer row down (fallback y ≥ 0.87); otherwise `MESSAGE_AREA` |
| `CONTACT_INFO` | `TOP_HEADER` y ≤ 0.10; `CONTACT_DETAILS` above the "Media, links and docs" label (fallback y < 0.40); otherwise `OTHER` |
| `SEND_CONTACT` | `TOP_HEADER` y ≤ 0.12; `CONTACT_DETAILS` for selectable number rows (y ≤ 0.55, detail `SHARE_CONTACT_NUMBER_ROW`); otherwise `OTHER` |
| `UNKNOWN` | `UNKNOWN` |

Anything inside the status bar (y < 0.035) is `OTHER/STATUS_BAR`.

### Decision policy (`decision_engine.py`)

Prohibited areas: `CHATS_LIST → CONTACT_ROW`, `CHAT_DETAIL → TOP_HEADER` and `MESSAGE_AREA` (message text, vCards, links),
`CONTACT_INFO → CONTACT_DETAILS`, `SEND_CONTACT → CONTACT_DETAILS` (visible number while picking a contact to share).
By default, an alert needs a `VALID` number with OCR confidence
≥ 0.60. Weaker candidates in a prohibited area go to review instead. All policies are in `config.py`.

## Testing

```bash
python -m pytest                 # all tests (96)
python -m pytest -m "not integration"   # unit tests only, no Tesseract needed
python -m pytest -m integration -v      # reference screenshots only
```

* `tests/test_phone_detector.py`: Indian formats, split boxes, invalid/short strings, timestamps,
  dates, counters, multiple numbers, duplicates, low confidence, OCR corrections, region config.
* `tests/test_screen_classifier.py`: each screen type at 460×1024, 1080×2400 and 720×1600; screens
  without numbers; multiple numbers; unknown, empty, ambiguous and low-confidence layouts.
* `tests/test_decision_engine.py`: every reason code and configurable policy.
* `tests/test_image_input.py`: missing, directory, wrong extension, empty, garbage, truncated, GIF
  disguised as PNG, too small; OCR binary missing or failing; CLI error handling; no input overwrite.
* `tests/test_integration_reference.py`: optional end-to-end run on the labelled images in
  `screenshots/reference_labels.json`. It is skipped if Tesseract or the labels are missing.
  To add more cases, drop a screenshot into `screenshots/` and add its expected labels to that file.

Unit tests use synthetic OCR results, with coordinates measured from the reference screenshots, so they
run without Tesseract.

## Known limitations and false-positive / false-negative risks

* **OCR quality.** Low-resolution, compressed or blurred screenshots, unusual fonts and emoji can break
  digits, which causes missed numbers or wrong digits. Tesseract may also miss text on busy
  wallpapers and inside images.
* **English UI only.** Keywords such as "Message", "Notifications" and "Unread" are English. Other
  UI languages will usually give `UNKNOWN` (and therefore review, not a silent pass).
* **WhatsApp versions.** Labels and layout change between versions and between Android, iOS,
  WhatsApp Business and Web/Desktop. The rules were derived from three Android screenshots of
  one version in light theme.
* **Dark theme** is handled only by inverting the image, which was checked on an inverted copy of the
  references but not on real dark-mode screenshots.
* **False positives.** Any 10-digit number that matches the Indian mobile pattern (for example an
  order ID starting with 6–9) or any `+`-prefixed number is a candidate. A phone number written in
  a chat-list message preview is labelled `CONTACT_ROW` and raises an alert.
* **False negatives.** Numbers written with letters attached (`call9876543210`), split across lines,
  partially hidden, displayed inside images or media thumbnails, or local landlines without a `0`
  prefix are not detected. Numbers typed only in the composer (`BOTTOM_COMPOSER`) are outside the current prohibited set unless you add that location in config.
* **Layout.** Location labels depend on vertical bands and anchors. Scrolled screens, such as a
  scrolled contact-info page with the "About and phone number" section, landscape or split-screen
  layouts, tablets, and large accessibility fonts may be labelled incorrectly.
* **Confidence** is a heuristic rule score, not a calibrated probability.
* **Privacy.** JSON results and debug artifacts contain phone numbers; `output/` is git-ignored.

## Tuning for other devices and WhatsApp versions

1. Run with `--debug` and open `output/<name>_annotated.png` and `<name>_diagnostics.json`.
2. Compare the normalized coordinates of headers, rows and composers with `FEATURE_PARAMS` and
   `LOCATION_RULES` in `config.py`, and adjust the bands.
3. Add new UI vocabulary (other languages, renamed labels) to the `keywords` / `phrases` lists.
4. Adjust `SCREEN_FEATURE_WEIGHTS`, `SCREEN_FEATURE_PENALTIES`, `SCREEN_CONFIDENCE_THRESHOLD` and
   `SCREEN_MIN_SCORE_MARGIN`. Raise the margin to send more borderline cases to review.
5. Tune `OCR_TARGET_MIN_WIDTH_PX`, `OCR_TESSERACT_CONFIG` and `OCR_CONFIDENCE_THRESHOLD` for OCR.
6. Add the new screenshot and its expected labels to `screenshots/reference_labels.json`, then
   run `python -m pytest` to check for regressions.
# screenshot_detection_service
