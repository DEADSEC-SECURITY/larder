from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol


class ScannerSource(Protocol):
    async def frames(self) -> AsyncIterator[bytes]: ...


class SerialScannerSource:
    def __init__(self, device: str, baudrate: int) -> None:
        self.device = device
        self.baudrate = baudrate
        self._writer = None

    async def frames(self) -> AsyncIterator[bytes]:
        try:
            import serial_asyncio_fast as serial_asyncio
        except ImportError:
            import serial_asyncio  # type: ignore[no-redef]

        reader, self._writer = await serial_asyncio.open_serial_connection(
            url=self.device, baudrate=self.baudrate
        )
        while True:
            frame = await reader.readline()
            if not frame:
                raise ConnectionError(f"serial device closed: {self.device}")
            yield frame

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None


class FakeScannerSource:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def emit(self, frame: bytes) -> None:
        await self.queue.put(frame)

    async def frames(self) -> AsyncIterator[bytes]:
        while (frame := await self.queue.get()) is not None:
            yield frame

    async def close(self) -> None:
        await self.queue.put(None)
