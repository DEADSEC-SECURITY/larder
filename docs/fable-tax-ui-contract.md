# Larder tax-rate UI contract

## Product behavior

Every place that accepts a price should show the same two controls:

1. **Price before tax** (the existing price input).
2. **Tax rate** (a selector populated from `GET /v1/web/tax-locations`).

The selector should group rates by saved location and display labels such as
`Rochester · Unprepared groceries · 0.00%`. Include a first option named **No tax (0%)**.

- Send the selected rate UUID as `taxRateId`.
- Send `taxRateId: null` for the explicit **No tax** option.
- If `taxRateId` is omitted, the backend uses `settings.defaultTaxRateId`.
- The backend treats the entered price as the subtotal, calculates tax, and returns the final total.
- Existing records whose tax was never recorded return `null` tax fields. Do not label those as 0%.

Use this control in:

- the scanner intake popup (`POST /intake-batches/{id}/confirm`),
- quick/manual add (`POST /quick-add`),
- single inventory-unit edit (`PATCH /units/{id}`), and
- bulk inventory edit (`POST /units/bulk`).

## Read APIs

### `GET /v1/web/tax-classes`

Returns provider status and the supported classes:

- `GENERAL` — general merchandise, available on every ZipTax plan;
- `GROCERY` — TIC 40030, unprepared groceries;
- `CANDY` — TIC 40010;
- `SUPPLEMENT` — TIC 40020;
- `SOFT_DRINK` — TIC 40050; and
- `PREPARED_FOOD` — TIC 41000.

Non-general product rates require a ZipTax Pro or Enterprise account. The response marks this in
`providerPlan` and exposes whether a server-side key is configured. Never ask for or store the API
key in the browser.

### `GET /v1/web/tax-locations`

```json
{
  "locations": [
    {
      "id": "uuid",
      "label": "Home",
      "city": "Rochester",
      "state": "NY",
      "postalCode": "14604",
      "normalizedAddress": null,
      "provider": "manual",
      "active": true,
      "rates": [
        {
          "id": "uuid",
          "taxClass": "GROCERY",
          "label": "Unprepared groceries",
          "rate": 0.04,
          "percentage": 4.0,
          "taxabilityCode": "40030",
          "source": "manual",
          "fetchedAt": null,
          "active": true
        }
      ]
    }
  ]
}
```

Rates are decimal fractions. `0.08875` means `8.875%`.

## Settings screen

Add a **Tax locations** section.

### Manual location

`POST /v1/web/tax-locations`

```json
{
  "label": "Home",
  "addressLine1": "optional for a manual profile",
  "city": "Rochester",
  "state": "NY",
  "postalCode": "14604",
  "rates": [
    { "taxClass": "GROCERY", "rate": 0.04 },
    { "taxClass": "GENERAL", "rate": 0.08 }
  ]
}
```

### Provider lookup

`POST /v1/web/tax-locations/lookup`

```json
{
  "label": "Downtown store",
  "addressLine1": "full street address",
  "city": "Rochester",
  "state": "NY",
  "postalCode": "14604",
  "taxClasses": ["GENERAL"]
}
```

Use a full street address rather than only a city because county, special district, and
unincorporated-area rates can differ. A product-specific class may return a provider-plan error if
the configured account does not include product rules; leave the manual entry path available.

Other management calls:

- `PATCH /v1/web/tax-rates/{rateId}` with any of `{ "label", "rate", "active" }`.
- `DELETE /v1/web/tax-locations/{locationId}` deactivates the location and its rates.
- `GET /v1/web/tax-locations?include_inactive=true` includes deactivated rows.

### Household default

The existing settings response now has `defaultTaxRateId`. Update it with:

```json
{ "defaultTaxRateId": "rate-uuid" }
```

Send `{ "defaultTaxRateId": null }` to clear the default. Use the same grouped rate selector as the
price forms.

## Price write payloads

Keep all current field names and add `taxRateId`:

- intake popup: `{ "unitPrice": 4.99, "taxRateId": "uuid" }`
- quick add: `{ "priceTotal": 9.98, "quantity": 2, "taxRateId": "uuid" }`
- unit edit: `{ "price": 4.99, "taxRateId": "uuid" }`
- bulk edit: `{ "setPrice": true, "price": 4.99, "taxRateId": "uuid", ... }`

For quick add, `priceTotal` remains the subtotal for the whole quantity; the backend derives the
per-unit subtotal before calculating each unit's tax.

## Price read payloads

Inventory units and product history now expose:

```json
{
  "price": 5.43,
  "priceSubtotal": 4.99,
  "taxRate": 0.08875,
  "taxAmount": 0.44,
  "taxRateId": "uuid-or-null",
  "taxLocationId": "uuid-or-null",
  "taxClass": "GROCERY",
  "taxSource": "manual",
  "currency": "USD"
}
```

`price` stays the final amount paid so existing totals and price comparisons remain compatible.
The intake-batch payload returns the same information using `unitPrice`, `unitPriceSubtotal`, and
the tax fields, plus a nested `priceBreakdown` object.

Suggested display under a price field:

`Subtotal $4.99 + tax $0.44 = $5.43 total`

Do not recalculate old purchases in the browser. The backend snapshots the applied rate and amount
at the time the price is saved.
