from __future__ import annotations


def strip_scanner_prefix(scanner_code: str, payload: str) -> str:
    """Remove the scanner's configured HID prefix without accepting an empty scan."""
    if payload.startswith(scanner_code):
        stripped = payload[len(scanner_code) :]
        if stripped:
            return stripped
    return payload
