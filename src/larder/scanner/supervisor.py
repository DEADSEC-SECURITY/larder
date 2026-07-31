from __future__ import annotations

import asyncio
from collections.abc import Callable

import structlog

from larder.scanner.serial import ScannerSource
from larder.schemas.scans import ScanEnvelope

log = structlog.get_logger()


def bind_scanner_frame(scanner_code: str, frame: bytes) -> bytes:
    """Attach the configured scanner identity to a normal scanner payload."""
    if frame.startswith(b"LDR:"):
        return frame
    payload = frame.rstrip(b"\r\n")
    if frame.endswith(b"\r\n"):
        terminator = b"\r\n"
    elif frame[-1:] in (b"\r", b"\n"):
        terminator = frame[-1:]
    else:
        terminator = b""
    return b"LDR:" + scanner_code.encode("ascii") + b"|" + payload + terminator


async def supervise_reader(
    *,
    scanner_code: str,
    port: str,
    source_factory: Callable[[], ScannerSource],
    queue: asyncio.Queue[ScanEnvelope],
    stop: asyncio.Event,
) -> None:
    delay = 1.0
    while not stop.is_set():
        source = source_factory()
        try:
            log.info("scanner_connecting", scanner_code=scanner_code, device=port)
            async for frame in source.frames():
                delay = 1.0
                if queue.full():
                    log.critical(
                        "scan_queue_full",
                        scanner_code=scanner_code,
                        device=port,
                        queue_size=queue.maxsize,
                    )
                await queue.put(
                    ScanEnvelope(
                        raw_frame=bind_scanner_frame(scanner_code, frame),
                        port=port,
                        expected_scanner_code=scanner_code,
                    )
                )
                if stop.is_set():
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(
                "scanner_disconnected",
                scanner_code=scanner_code,
                device=port,
                error=type(exc).__name__,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 60.0)
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:
                    log.warning(
                        "scanner_close_failed",
                        scanner_code=scanner_code,
                        device=port,
                        error=type(exc).__name__,
                    )
