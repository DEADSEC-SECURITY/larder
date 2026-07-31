# Scanner bridge

This service reads the BCST-73 USB HID receiver through Linux evdev, commits each Enter-terminated
scan and UUID to a local SQLite outbox, and retries delivery to the authenticated Larder API. With
`LARDER_SCAN_API_URL` unset it remains usable as a JSON-only test logger.

```bash
docker compose up --build -d
docker compose logs -f scanner-logger
```

By default it auto-detects the first USB keyboard-like input device. Set `SCANNER_DEVICE` to a
stable path such as `/dev/input/by-id/usb-...-event-kbd` once the receiver is visible. The service
uses an exclusive evdev grab by default so scans are not delivered as ordinary keyboard input.

For delivery, set `LARDER_SCAN_API_URL` and `LARDER_SCAN_API_TOKEN` in `.env`. The named
`larder-scanner-outbox` volume must be retained across upgrades; it is the durability boundary
while the API or network is unavailable.
