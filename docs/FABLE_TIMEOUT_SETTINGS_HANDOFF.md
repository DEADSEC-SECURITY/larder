# Fable handoff: tunable timing settings

Larder now persists every user-facing workflow/interface timeout in the existing
household settings resource. The Angular Settings page already includes two
cards (`Workflow timing` and `Interface timing`), so Fable can restyle or regroup
them without inventing client-only state.

## API

Use the existing endpoints:

- `GET /api/v1/web/settings`
- `PUT /api/v1/web/settings` with any subset of fields below

`PUT` returns the complete settings object. Validation failures return HTTP 422.

| JSON field | Default | Allowed | UI semantics |
| --- | ---: | ---: | --- |
| `intakePopupTimeoutSeconds` | 30 | 0-3600 s | Auto-save/close after inactivity. `0` disables auto-save. |
| `intakeScanGroupTimeoutSeconds` | 30 | 1-3600 s | Maximum gap for repeated copies of the same barcode to increment one intake batch. |
| `scannerModeTimeoutSeconds` | 30 | 5-3600 s | ADD/REMOVE/TRASH/ADD TO LIST lifetime; applies to every scanner. |
| `scannerContextTimeoutSeconds` | 1800 | 30-86400 s | Shared store and storage-location context lifetime; applies to every scanner. |
| `scannerOfflineAfterSeconds` | 172800 | 60-604800 s | No-activity threshold used by scanner status and Overview attention. |
| `toastDurationSeconds` | 3 | 1-60 s | Toast visibility. |
| `confirmationTimeoutSeconds` | 3 | 1-60 s | Window for second-tap destructive confirmations. |
| `liveUpdateDebounceMs` | 300 | 50-5000 ms | Coalesces bursts of server-sent events before screen refreshes. |
| `fallbackRefreshSeconds` | 60 | 5-3600 s | Overview/scanner polling fallback while live updates are quiet. |

Example:

```json
{
  "intakePopupTimeoutSeconds": 45,
  "intakeScanGroupTimeoutSeconds": 15,
  "scannerModeTimeoutSeconds": 60,
  "scannerContextTimeoutSeconds": 3600
}
```

## UI behavior already wired

- Changing scanner mode/context timing updates all existing scanner rows and
  re-times active, unexpired contexts immediately.
- New scanners inherit the household mode/context values unless explicit CLI
  overrides are supplied.
- Store/location context is intentionally one setting: both answer “where is
  this scan happening?” and should expire together.
- Popup timeout and repeat-scan grouping are separate. The first controls browser
  auto-save; the second is enforced by the backend and remains correct even if
  the browser is closed.
- Internal HTTP, database-lock, worker-lease, printer-retry, and device-discovery
  deadlines are intentionally not exposed as household preferences.

## Suggested presentation

- Keep workflow timing prominent: popup, repeat grouping, mode, context, offline.
- Put toast, confirmation, debounce, and fallback refresh under an Advanced or
  Interface timing section.
- Display context in minutes and offline threshold in minutes/hours, but send
  whole seconds to the API. Display live debounce in milliseconds.
- Include a reset-to-default action only if it sends the defaults above through
  `PUT`; do not keep separate defaults in browser storage.
