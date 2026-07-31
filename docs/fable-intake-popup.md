# Fable handoff: scanner intake popup

The backend now groups consecutive ADD scans from the same scanner and product
into one durable intake batch. Inventory units are created immediately so a
closed or disconnected browser never loses a scan. The popup edits every
in-stock unit in the batch when the user confirms it.

## Live behavior

- Subscribe to the existing `GET /api/v1/web/events` Server-Sent Events stream.
- On connection and every message, call `GET /api/v1/web/intake-batches?status=open`.
- Show the oldest open batch (`batches[0]`) as the popup. Open batches are
  returned in FIFO order.
- If the same product is scanned consecutively, the same batch's `quantity`
  and `lastScanAt` update. Animate or pulse the quantity instead of opening a
  second popup.
- A different product creates another open batch. Keep open batches as a FIFO
  queue so none are lost.
- Product enrichment may fill in `productName`, `brand`, `imageUrl`, or a
  rule-derived store after the popup first appears. Refreshing on SSE messages
  will update those fields.

## Read contract

`GET /api/v1/web/intake-batches?status=open`

```json
{
  "batches": [
    {
      "id": "uuid",
      "status": "open",
      "scannerId": "uuid",
      "scannerCode": "SCN01",
      "scannerName": "Kitchen Scanner",
      "barcode": "0096619120291",
      "productName": "Colombian Supremo Whole Bean Coffee",
      "brand": "Kirkland Signature",
      "imageUrl": "https://...",
      "enrichmentStatus": "SUCCEEDED",
      "quantity": 3,
      "editableQuantity": 3,
      "storeId": "uuid-or-null",
      "storeName": "Costco",
      "unitPrice": null,
      "currency": null,
      "expirationDate": "2026-08-15",
      "expirationKind": "estimated",
      "createdAt": "ISO-8601",
      "updatedAt": "ISO-8601",
      "lastScanAt": "ISO-8601",
      "finalizedAt": null
    }
  ]
}
```

The same object is available at `GET /api/v1/web/intake-batches/{id}`.

## Save contract

`POST /api/v1/web/intake-batches/{id}/confirm`

```json
{
  "storeId": "uuid-or-null",
  "unitPrice": 4.99,
  "expirationDate": "2026-08-15"
}
```

Each field has three useful states:

- Omitted: retain the scan-time value/default.
- A value: apply it to every in-stock unit in the batch.
- `null`: explicitly clear that field. For `expirationDate`, null means no
  expiration date rather than reverting to an estimate.

The response is the finalized batch with `status: "confirmed"`. A second
confirm returns HTTP 409.

## Dismiss contract

`POST /api/v1/web/intake-batches/{id}/dismiss` with `{}` closes the prompt and
keeps the units with their safe scan-time defaults. Label this action "Later"
or "Keep defaults", not "Cancel", because it does not undo the scans.

## Suggested popup fields

- Product image, name, brand, and barcode.
- Large quantity indicator; pulse it when `quantity` increases.
- Store selector, prefilled from scanner context or routing rules.
- Unit price input.
- Expiration date input and a clear "No expiration" choice that sends null.
- Primary action: "Add {quantity} items" / "Save {quantity} items".
- Secondary action: "Keep defaults".
- Optional pending queue count when more than one batch is open.

Angular contracts and API methods are already present in
`apps/web/src/app/core/models.ts` and `apps/web/src/app/core/api.service.ts`.
