from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from barcode import Code128
from ledger import PrintLedger
from PIL import Image, ImageDraw, ImageFont

LABEL_WIDTH_DOTS = 456
LABEL_HEIGHT_DOTS = 254
LABEL_WIDTH_BYTES = LABEL_WIDTH_DOTS // 8


@dataclass(frozen=True, slots=True)
class PrintJob:
    job_id: str
    kind: str
    value: str
    title: str
    subtitle: str | None
    copies: int
    attempt: int

    @classmethod
    def from_payload(cls, payload: dict) -> PrintJob:
        return cls(**payload)

    def digest(self) -> str:
        # Attempts are lease metadata, not job identity. Keeping them out of the
        # digest lets the receipt ledger suppress a duplicate after an ACK fails
        # and the same central job is claimed again.
        payload = json.dumps(
            {
                "job_id": self.job_id,
                "kind": self.kind,
                "value": self.value,
                "title": self.title,
                "subtitle": self.subtitle,
                "copies": self.copies,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


def emit(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields},
            separators=(",", ":"),
        ),
        flush=True,
    )


def safe_tspl_text(value: str, max_length: int) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    compact = " ".join(ascii_value.split())
    return compact.replace('"', "'").replace("\\", "/")[:max_length]


def fit_text(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont, width: int) -> str:
    if draw.textbbox((0, 0), value, font=font)[2] <= width:
        return value
    shortened = value
    while shortened and draw.textbbox((0, 0), f"{shortened}...", font=font)[2] > width:
        shortened = shortened[:-1]
    return f"{shortened}..." if shortened else ""


def render_code128(value: str, max_width: int, max_height: int) -> Image.Image:
    modules = Code128(value).build()[0]
    quiet_zone_modules = 10
    module_scale = max_width / (len(modules) + (quiet_zone_modules * 2))
    if module_scale < 1:
        raise ValueError("barcode value is too long for the configured label")

    quiet_zone_width = round(quiet_zone_modules * module_scale)
    symbol_width = max_width - (quiet_zone_width * 2)
    barcode_image = Image.new("1", (max_width, max_height), 1)
    draw = ImageDraw.Draw(barcode_image)
    index = 0
    while index < len(modules):
        if modules[index] == "0":
            index += 1
            continue
        run_start = index
        while index < len(modules) and modules[index] == "1":
            index += 1
        x_start = quiet_zone_width + round((run_start * symbol_width) / len(modules))
        x_end = quiet_zone_width + round((index * symbol_width) / len(modules)) - 1
        draw.rectangle((x_start, 0, x_end, max_height - 1), fill=0)
    return barcode_image


def pack_monochrome(image: Image.Image) -> bytes:
    pixels = image.convert("1").load()
    packed = bytearray(LABEL_WIDTH_BYTES * LABEL_HEIGHT_DOTS)
    offset = 0
    for y in range(LABEL_HEIGHT_DOTS):
        for byte_x in range(LABEL_WIDTH_BYTES):
            value = 0
            for bit in range(8):
                if pixels[(byte_x * 8) + bit, y] == 0:
                    value |= 1 << (7 - bit)
            # RW405B raw bitmap mode 1 uses inverted polarity: set bits are
            # white and clear bits activate the thermal print head.
            packed[offset] = value ^ 0xFF
            offset += 1
    return bytes(packed)


def render_tspl(job: PrintJob) -> bytes:
    if not job.value or len(job.value) > 128:
        raise ValueError("barcode value length is invalid")
    if any(ord(character) < 32 or ord(character) > 126 for character in job.value):
        raise ValueError("barcode value must be printable ASCII")
    value = safe_tspl_text(job.value, 128)
    title = safe_tspl_text(job.title, 80) or "Larder Barcode"
    subtitle = safe_tspl_text(job.subtitle or f"{job.kind.title()} Barcode", 80)

    canvas = Image.new("1", (LABEL_WIDTH_DOTS, LABEL_HEIGHT_DOTS), 1)
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.load_default(size=28)
    subtitle_font = ImageFont.load_default(size=20)

    title = fit_text(draw, title, title_font, LABEL_WIDTH_DOTS - 24)
    subtitle = fit_text(draw, subtitle, subtitle_font, LABEL_WIDTH_DOTS - 24)
    for text, y, font in ((title, 4, title_font), (subtitle, 38, subtitle_font)):
        bounds = draw.textbbox((0, 0), text, font=font)
        draw.text(((LABEL_WIDTH_DOTS - bounds[2]) // 2, y), text, fill=0, font=font)

    barcode_image = render_code128(value, LABEL_WIDTH_DOTS - 8, 176)
    canvas.paste(barcode_image, ((LABEL_WIDTH_DOTS - barcode_image.width) // 2, 70))

    raster = pack_monochrome(canvas)
    commands = [
        "SIZE 57 mm,31 mm",
        "GAP 3 mm,0 mm",
        "REFERENCE 0,0",
        "OFFSET 0 mm",
        "SETC AUTODOTTED OFF",
        "SETC PAUSEKEY OFF",
        "DENSITY 8",
        "SPEED 4",
        "DIRECTION 0,0",
        "CLS",
    ]
    header = (
        "\r\n".join(commands) + f"\r\nBITMAP 0,0,{LABEL_WIDTH_BYTES},{LABEL_HEIGHT_DOTS},1,"
    ).encode("ascii")
    return header + raster + f"\r\nPRINT 1,{job.copies}\r\n".encode("ascii")


class LarderPrintClient:
    def __init__(self, base_url: str, token: str, bridge_id: str, timeout: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.bridge_id = bridge_id
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> tuple[int, dict | None]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None

    def claim(self) -> PrintJob | None:
        status, payload = self._post("/v1/print-jobs/claim", {"bridge_id": self.bridge_id})
        if status == 204:
            return None
        assert payload is not None
        return PrintJob.from_payload(payload)

    def complete(self, job_id: str) -> None:
        self._post(f"/v1/print-jobs/{job_id}/complete", {"bridge_id": self.bridge_id})

    def fail(self, job_id: str, error: str, *, retryable: bool) -> None:
        self._post(
            f"/v1/print-jobs/{job_id}/fail",
            {
                "bridge_id": self.bridge_id,
                "error": error[:1000],
                "retryable": retryable,
            },
        )


def write_to_printer(device_path: str, payload: bytes) -> None:
    with open(device_path, "wb", buffering=0) as printer:
        written = printer.write(payload)
    if written != len(payload):
        raise OSError(f"short printer write: {written} of {len(payload)} bytes")


def acknowledge(client: LarderPrintClient, job: PrintJob, retry_seconds: float) -> None:
    while True:
        try:
            client.complete(job.job_id)
            emit("print_job_completed", job_id=job.job_id)
            return
        except Exception as error:
            emit(
                "print_ack_retry",
                job_id=job.job_id,
                error=type(error).__name__,
                detail=str(error),
            )
            time.sleep(retry_seconds)


def run() -> None:
    scan_api_url = os.environ.get("LARDER_SCAN_API_URL", "").strip()
    base_url = os.environ.get("LARDER_API_BASE_URL", "").strip()
    if not base_url and scan_api_url.endswith("/v1/scans"):
        base_url = scan_api_url[: -len("/v1/scans")]
    token = os.environ.get("LARDER_SCAN_API_TOKEN", "").strip()
    bridge_id = os.environ.get("PRINTER_BRIDGE_ID", "larder-printer-1").strip()
    device_path = os.environ.get("PRINTER_DEVICE", "/dev/usb/lp1").strip()
    ledger_path = os.environ.get("PRINTER_LEDGER_PATH", "/var/lib/larder-printer/printed.sqlite3")
    poll_seconds = float(os.environ.get("PRINTER_POLL_SECONDS", "1"))
    retry_seconds = float(os.environ.get("PRINTER_RETRY_SECONDS", "5"))
    if not base_url or not token:
        raise RuntimeError("Larder API URL and token are required")

    ledger = PrintLedger(ledger_path)
    client = LarderPrintClient(base_url, token, bridge_id)
    emit("printer_bridge_started", bridge_id=bridge_id, device=device_path, api=base_url)

    while True:
        try:
            job = client.claim()
        except Exception as error:
            emit("print_claim_retry", error=type(error).__name__, detail=str(error))
            time.sleep(retry_seconds)
            continue
        if job is None:
            time.sleep(poll_seconds)
            continue

        digest = job.digest()
        printed_digest = ledger.printed_digest(job.job_id)
        if printed_digest is not None:
            if printed_digest != digest:
                emit("print_job_digest_mismatch", job_id=job.job_id)
                client.fail(job.job_id, "printed job payload changed", retryable=False)
                continue
            emit("print_duplicate_suppressed", job_id=job.job_id)
            acknowledge(client, job, retry_seconds)
            continue

        try:
            payload = render_tspl(job)
            write_to_printer(device_path, payload)
            ledger.mark_printed(job.job_id, digest)
            emit(
                "print_sent",
                job_id=job.job_id,
                kind=job.kind,
                copies=job.copies,
                bytes=len(payload),
            )
        except ValueError as error:
            emit("print_rejected", job_id=job.job_id, detail=str(error))
            client.fail(job.job_id, str(error), retryable=False)
            continue
        except OSError as error:
            emit("printer_unavailable", job_id=job.job_id, detail=str(error))
            try:
                client.fail(job.job_id, str(error), retryable=True)
            except (urllib.error.URLError, TimeoutError) as report_error:
                emit("print_failure_report_retry", error=type(report_error).__name__)
            time.sleep(retry_seconds)
            continue

        acknowledge(client, job, retry_seconds)


if __name__ == "__main__":
    run()
