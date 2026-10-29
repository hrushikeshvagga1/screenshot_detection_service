import json

import pytest
from PIL import Image

import main
import ocr_engine
from ocr_engine import ImageValidationError, load_and_validate_image, run_ocr


def _png(path, size=(200, 400), color=(255, 255, 255)):
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def test_valid_png_and_jpeg_accepted(tmp_path):
    png = _png(tmp_path / "a.png")
    jpg = tmp_path / "b.jpeg"
    Image.new("RGB", (200, 400), "white").save(jpg, format="JPEG")
    assert load_and_validate_image(str(png))[1] == "PNG"
    assert load_and_validate_image(str(jpg))[1] == "JPEG"


def test_missing_file(tmp_path):
    with pytest.raises(ImageValidationError, match="not found"):
        load_and_validate_image(str(tmp_path / "nope.png"))


def test_directory_rejected(tmp_path):
    folder = tmp_path / "dir.png"
    folder.mkdir()
    with pytest.raises(ImageValidationError, match="not a regular file"):
        load_and_validate_image(str(folder))


def test_unsupported_extension(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(ImageValidationError, match="Unsupported file extension"):
        load_and_validate_image(str(path))


def test_empty_file(tmp_path):
    path = tmp_path / "empty.png"
    path.write_bytes(b"")
    with pytest.raises(ImageValidationError, match="empty"):
        load_and_validate_image(str(path))


def test_garbage_bytes(tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"this is not an image at all" * 10)
    with pytest.raises(ImageValidationError, match="not a valid or readable image"):
        load_and_validate_image(str(path))


def test_truncated_image(tmp_path):
    good = _png(tmp_path / "good.png", size=(300, 600))
    data = good.read_bytes()
    bad = tmp_path / "truncated.png"
    bad.write_bytes(data[: len(data) // 2])
    with pytest.raises(ImageValidationError):
        load_and_validate_image(str(bad))


def test_unsupported_format_with_supported_extension(tmp_path):
    path = tmp_path / "anim.png"
    Image.new("RGB", (200, 400), "white").save(path, format="GIF")
    with pytest.raises(ImageValidationError, match="Unsupported image format"):
        load_and_validate_image(str(path))


def test_too_small_image(tmp_path):
    path = _png(tmp_path / "tiny.png", size=(40, 40))
    with pytest.raises(ImageValidationError, match="too small"):
        load_and_validate_image(str(path))


def test_ocr_missing_binary_is_handled(monkeypatch):
    import pytesseract

    def boom(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    result = run_ocr(Image.new("RGB", (200, 400), "white"))
    assert result.success is False
    assert "Tesseract binary not found" in result.error
    assert result.reliable is False


def test_ocr_runtime_error_is_handled(monkeypatch):
    import pytesseract

    def boom(*args, **kwargs):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    result = run_ocr(Image.new("RGB", (200, 400), "white"))
    assert result.success is False
    assert "timeout" in result.error


def test_preprocessing_does_not_modify_original():
    original = Image.new("RGB", (300, 600), (20, 20, 20))
    before = original.tobytes()
    processed, sx, sy, info = ocr_engine.preprocess_for_ocr(original)
    assert original.tobytes() == before
    assert info["inverted"] is True
    assert processed.width > original.width and sx == pytest.approx(processed.width / 300)


def test_cli_missing_image_returns_error(tmp_path, capsys):
    code = main.main(["--image", str(tmp_path / "missing.png")])
    out = capsys.readouterr().out
    assert code == main.EXIT_INVALID_INPUT
    assert json.loads(out)["error"] == "INVALID_IMAGE"


def test_cli_malformed_image_returns_error(tmp_path, capsys):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff garbage")
    code = main.main(["--image", str(bad)])
    assert code == main.EXIT_INVALID_INPUT
    assert "not a valid or readable image" in json.loads(capsys.readouterr().out)["message"]


def test_cli_ocr_failure_produces_review_json(tmp_path, capsys, monkeypatch):
    import pytesseract

    def boom(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    image = _png(tmp_path / "blank.png")
    output = tmp_path / "out" / "result.json"
    code = main.main(["--image", str(image), "--output", str(output)])
    assert code == main.EXIT_OK
    result = json.loads(output.read_text())
    assert result["ocr"]["success"] is False
    assert result["decision"]["reason_code"] == "OCR_FAILED"
    assert result["decision"]["review_required"] is True


def test_cli_refuses_to_overwrite_input(tmp_path, monkeypatch):
    import pytesseract

    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: {"text": []})
    image = _png(tmp_path / "shot.png")
    before = image.read_bytes()
    code = main.main(["--image", str(image), "--output", str(image)])
    assert code == main.EXIT_OUTPUT_ERROR
    assert image.read_bytes() == before
