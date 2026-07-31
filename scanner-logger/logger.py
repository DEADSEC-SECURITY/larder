from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime

from evdev import InputDevice, categorize, ecodes, list_devices
from outbox import ScanOutbox, run_sender
from payloads import strip_scanner_prefix

SHIFT_KEYS = {"KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"}
KEYS = {
    **{f"KEY_{digit}": digit for digit in "0123456789"},
    **{f"KEY_{letter}": letter.lower() for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    "KEY_MINUS": "-",
    "KEY_EQUAL": "=",
    "KEY_LEFTBRACE": "[",
    "KEY_RIGHTBRACE": "]",
    "KEY_BACKSLASH": "\\",
    "KEY_SEMICOLON": ";",
    "KEY_APOSTROPHE": "'",
    "KEY_GRAVE": "`",
    "KEY_COMMA": ",",
    "KEY_DOT": ".",
    "KEY_SLASH": "/",
    "KEY_SPACE": " ",
}
SHIFTED = {
    "1": "!",
    "2": "@",
    "3": "#",
    "4": "$",
    "5": "%",
    "6": "^",
    "7": "&",
    "8": "*",
    "9": "(",
    "0": ")",
    "-": "_",
    "=": "+",
    "[": "{",
    "]": "}",
    "\\": "|",
    ";": ":",
    "'": '"',
    "`": "~",
    ",": "<",
    ".": ">",
    "/": "?",
}


def emit(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                **fields,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def describe_devices() -> list[dict[str, object]]:
    devices = []
    for path in list_devices():
        try:
            device = InputDevice(path)
            devices.append(
                {
                    "path": path,
                    "name": device.name,
                    "phys": device.phys,
                    "bustype": device.info.bustype,
                    "vendor": f"{device.info.vendor:04x}",
                    "product": f"{device.info.product:04x}",
                }
            )
            device.close()
        except OSError as exc:
            devices.append({"path": path, "error": type(exc).__name__, "detail": str(exc)})
    return devices


def find_scanner(configured_path: str) -> InputDevice | None:
    if configured_path:
        try:
            return InputDevice(configured_path)
        except OSError as exc:
            emit("configured_device_unavailable", path=configured_path, error=str(exc))
            return None

    for path in list_devices():
        try:
            device = InputDevice(path)
            keys = set(device.capabilities().get(ecodes.EV_KEY, []))
            if (
                device.info.bustype == ecodes.BUS_USB
                and ecodes.KEY_ENTER in keys
                and ecodes.KEY_0 in keys
                and ecodes.KEY_9 in keys
            ):
                return device
            device.close()
        except OSError:
            continue
    return None


def read_scans(
    device: InputDevice,
    scanner_code: str,
    should_grab: bool,
    on_scan,
) -> None:
    grabbed = False
    if should_grab:
        device.grab()
        grabbed = True
    emit(
        "scanner_connected",
        scanner_code=scanner_code,
        path=device.path,
        name=device.name,
        phys=device.phys,
        vendor=f"{device.info.vendor:04x}",
        product=f"{device.info.product:04x}",
        grabbed=grabbed,
    )

    buffer: list[str] = []
    shift = False
    try:
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY:
                continue
            key = categorize(event)
            keycode = key.keycode
            if isinstance(keycode, list):
                keycode = keycode[0]

            if keycode in SHIFT_KEYS:
                shift = key.keystate != key.key_up
                continue
            if key.keystate != key.key_down:
                continue
            if keycode in {"KEY_ENTER", "KEY_KPENTER"}:
                payload = "".join(buffer)
                buffer.clear()
                if payload:
                    normalized = strip_scanner_prefix(scanner_code, payload)
                    if normalized != payload:
                        emit("scanner_prefix_stripped", scanner_code=scanner_code)
                    on_scan(normalized)
                continue
            if keycode == "KEY_BACKSPACE":
                if buffer:
                    buffer.pop()
                continue

            character = KEYS.get(keycode)
            if character is None:
                continue
            if character.isalpha() and shift:
                character = character.upper()
            elif shift:
                character = SHIFTED.get(character, character)
            buffer.append(character)
    finally:
        if grabbed:
            try:
                device.ungrab()
            except OSError:
                pass
        device.close()


def main() -> None:
    scanner_code = os.environ.get("SCANNER_CODE", "SCN01")
    configured_path = os.environ.get("SCANNER_DEVICE", "").strip()
    should_grab = os.environ.get("SCANNER_GRAB", "true").lower() in {"1", "true", "yes"}
    interval = float(os.environ.get("DISCOVERY_INTERVAL_SECONDS", "5"))
    api_url = os.environ.get("LARDER_SCAN_API_URL", "").strip()
    api_token = os.environ.get("LARDER_SCAN_API_TOKEN", "").strip()
    outbox_path = os.environ.get("LARDER_OUTBOX_PATH", "/var/lib/larder-scanner/outbox.sqlite3")
    stop = threading.Event()
    outbox = ScanOutbox(outbox_path) if api_url else None

    if api_url and not api_token:
        raise RuntimeError("LARDER_SCAN_API_TOKEN is required when delivery is enabled")
    if outbox is not None:
        sender = threading.Thread(
            target=run_sender,
            args=(outbox, api_url, api_token, stop, emit),
            name="larder-outbox-sender",
            daemon=True,
        )
        sender.start()
        emit("scanner_bridge_started", api_url=api_url, pending=outbox.pending_count())

    def handle_scan(payload: str) -> None:
        if outbox is None:
            emit("scan", scanner_code=scanner_code, payload=payload, delivery="disabled")
            return
        scan = outbox.enqueue(scanner_code, payload)
        emit(
            "scan_queued",
            event_id=scan.event_id,
            scanner_code=scanner_code,
            payload=payload,
            pending=outbox.pending_count(),
        )

    try:
        while True:
            device = find_scanner(configured_path)
            if device is None:
                emit("scanner_not_found", devices=describe_devices())
                time.sleep(interval)
                continue
            try:
                read_scans(device, scanner_code, should_grab, handle_scan)
            except (OSError, PermissionError) as exc:
                emit("scanner_disconnected", error=type(exc).__name__, detail=str(exc))
                time.sleep(interval)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
