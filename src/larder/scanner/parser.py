from __future__ import annotations

import re

from larder.domain.enums import ScanKind
from larder.schemas.scans import NormalizedBarcode, ParsedFrame

SCANNER_CODE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
GTIN_LENGTHS = {8, 12, 13, 14}
NORMALIZATION_VERSION = 1
MAX_AUDIT_CHARACTERS = 4096


class FrameError(ValueError):
    def __init__(self, code: str, detail: str, raw_frame: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.raw_frame = raw_frame


class BarcodeError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def audit_text(raw: bytes) -> str:
    decoded = raw.rstrip(b"\r\n").decode("utf-8", errors="backslashreplace")
    return decoded[:MAX_AUDIT_CHARACTERS]


def strip_redundant_scanner_prefix(scanner_code: str, payload: str) -> str:
    """Remove a hardware-programmed scanner prefix already carried by the frame."""
    if payload.startswith(scanner_code):
        stripped = payload[len(scanner_code) :]
        if stripped:
            return stripped
    return payload


def normalize_control_payload(payload: str) -> str:
    """Canonicalize supported command separators while preserving product payloads."""
    if payload.startswith(("CMD:", "CMD_")):
        return re.sub(r"[:_]+", ":", payload)
    return payload


def parse_frame(
    raw: bytes,
    *,
    expected_scanner_code: str | None = None,
    max_frame_bytes: int = 128,
) -> ParsedFrame:
    raw_audit = audit_text(raw)
    if not raw:
        raise FrameError("EMPTY_FRAME", "frame is empty", raw_audit)
    if len(raw) > max_frame_bytes:
        raise FrameError("FRAME_TOO_LONG", "frame exceeds configured maximum", raw_audit)
    if not raw.endswith((b"\n", b"\r")):
        raise FrameError("UNTERMINATED_FRAME", "frame has no line terminator", raw_audit)
    try:
        frame = raw.rstrip(b"\r\n").decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FrameError("NON_UTF8_FRAME", "frame is not valid UTF-8", raw_audit) from exc
    if "\r" in frame or "\n" in frame:
        raise FrameError("EMBEDDED_NEWLINE", "frame contains an embedded newline", raw_audit)
    if not frame.startswith("LDR:"):
        raise FrameError("BAD_PROTOCOL_MARKER", "frame does not start with LDR:", raw_audit)
    body = frame[4:]
    if body.count("|") != 1:
        raise FrameError("BAD_FRAME_SHAPE", "frame must contain one scanner separator", raw_audit)
    scanner_code, payload = body.split("|", 1)
    if not SCANNER_CODE.fullmatch(scanner_code):
        raise FrameError("BAD_SCANNER_CODE", "scanner code is invalid", raw_audit)
    if expected_scanner_code is not None and scanner_code != expected_scanner_code:
        raise FrameError(
            "UNEXPECTED_SCANNER_CODE",
            "frame scanner does not match the configured serial port",
            raw_audit,
        )
    if not payload:
        raise FrameError("EMPTY_PAYLOAD", "frame payload is empty", raw_audit)
    payload = strip_redundant_scanner_prefix(scanner_code, payload)
    payload = normalize_control_payload(payload)
    kind = ScanKind.CONTROL if payload.startswith("CMD:") else ScanKind.PRODUCT
    return ParsedFrame(scanner_code, payload, frame, kind)


def has_valid_gtin_check_digit(value: str) -> bool:
    if not value.isdecimal() or len(value) not in GTIN_LENGTHS:
        return False
    digits = [int(char) for char in value]
    weighted = sum(
        digit * (3 if offset % 2 == 0 else 1) for offset, digit in enumerate(reversed(digits[:-1]))
    )
    expected = (10 - weighted % 10) % 10
    return expected == digits[-1]


def normalize_gtin(value: str, *, accept_invalid_check_digit: bool = False) -> NormalizedBarcode:
    if not value.isascii() or not value.isdecimal():
        raise BarcodeError("GTIN_NOT_DECIMAL", "product barcode must contain ASCII digits only")
    if len(value) not in GTIN_LENGTHS:
        raise BarcodeError("GTIN_BAD_LENGTH", "product barcode must have 8, 12, 13, or 14 digits")
    if not accept_invalid_check_digit and not has_valid_gtin_check_digit(value):
        raise BarcodeError("GTIN_BAD_CHECK_DIGIT", "product barcode check digit is invalid")

    if len(value) == 8:
        canonical, barcode_format = value, "EAN-8"
    elif len(value) == 12:
        canonical, barcode_format = f"0{value}", "UPC-A"
    elif len(value) == 13:
        canonical, barcode_format = value, "EAN-13"
    else:
        canonical, barcode_format = value, "GTIN-14"
    return NormalizedBarcode(value, canonical, barcode_format, NORMALIZATION_VERSION)
