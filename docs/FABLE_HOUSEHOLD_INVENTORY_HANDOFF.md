# Fable handoff: household inventory generalization

## Goal

Larder is now a household inventory and shopping system, not a food-only app. Existing food,
nutrition, expiration, scanner, pricing, tax, store-routing, and grocery-list behavior remains
compatible.

The UI should use **Shopping** as the visible name. The old `/grocery` routes remain aliases so the
current UI does not break, but new UI code should call `/shopping`.

All routes below are under `/v1/web`.

## Product kinds

Requests send uppercase enum values; responses return lowercase values.

| Request value | Response value | Suggested label |
| --- | --- | --- |
| `FOOD` | `food` | Food & Beverage |
| `HOUSEHOLD` | `household` | Household |
| `PERSONAL_CARE` | `personal_care` | Personal Care |
| `MEDICAL` | `medical` | Health & Medical |
| `PET` | `pet` | Pet Supplies |
| `OFFICE` | `office` | Office |
| `OTHER` | `other` | Other |

Do not hide nutrition for food products. For non-food products, omit the nutrition section when
`nutrition` is `null`. Expiration is optional for every kind.

## Product fields

Product list rows and product detail now expose:

```json
{
  "productKind": "household",
  "description": "Disposable cleaning gloves",
  "manufacturer": "Acme Industries",
  "packageSize": "100 Count",
  "material": "Nitrile",
  "variant": "Large",
  "identificationSource": "UPCITEMDB",
  "defaultStorageLocationId": "uuid-or-null"
}
```

Product detail additionally exposes provider provenance in `providerMetadata`, `fieldSources`, and
`providers`. A field whose source is `USER` is protected from later provider enrichment. Suggested
UI: place provider data in a collapsed “Identification” section; it is provenance/debug information,
not primary product copy.

### Edit product metadata

`PATCH /products/{barcode}` accepts any subset of:

```json
{
  "name": "Nitrile Gloves",
  "brand": "Acme",
  "productKind": "HOUSEHOLD",
  "description": "Disposable cleaning gloves",
  "manufacturer": "Acme Industries",
  "packageSize": "100 Count",
  "material": "Nitrile",
  "variant": "Large",
  "categoryId": "local:cleaning-supplies",
  "defaultStorageLocationId": "uuid-or-null"
}
```

Explicit `null` clears optional text fields and the default storage location. `categoryId: null`
sets the product back to Uncategorized. Names, brands, manufacturers, materials, and variants are
standardized to title case by the backend.

Suggested UI: replace the food-specific edit affordance with an **Edit product** drawer or modal
containing Identity, Classification, Package details, and Defaults sections.

## Barcode identification behavior

No UI orchestration is required. A scan queues enrichment as before. The backend checks:

1. Open Facts universal lookup (`product_type=all`), which can return Food, Beauty, Pet Food, or
   general Products Facts data.
2. UPCitemdb when Open Facts explicitly returns not found.
3. Manual identification when neither provider recognizes the barcode.

Temporary Open Facts errors retry instead of silently falling through. UPCitemdb free access is
rate-limited, so retain the existing pending/retry/not-found enrichment states.

## Storage locations

Purchase store and storage location are separate concepts: store is where a unit was bought;
storage location is where it is kept. The migration seeds Pantry, Refrigerator, Freezer, Bathroom,
Medicine Cabinet, Laundry Room, Garage, and Office.

### API

- `GET /storage-locations`
- `POST /storage-locations` with `{ "name": "Utility Closet" }`
- `PATCH /storage-locations/{id}` with `{ "name": "Hall Closet", "active": true }`

List response:

```json
{
  "storageLocations": [
    {
      "id": "uuid",
      "code": "utility-closet",
      "name": "Utility Closet",
      "active": true,
      "inStockUnits": 12
    }
  ]
}
```

Use archive (`active: false`) instead of delete. Archived locations remain valid on historical
inventory units but should be hidden from new-assignment selectors.

Inventory unit payloads now include `storageLocationId` and `storageLocationName`. Supported writes:

- `PATCH /units/{id}` with `{ "storageLocationId": "uuid-or-null" }`;
- `POST /units/bulk` with `setStorageLocation: true` and `storageLocationId`;
- `POST /quick-add` with `storageLocationId`;
- `POST /intake-batches/{id}/confirm` with `storageLocationId`.

Explicit `null` clears the assignment. A new inventory unit receives its product default first,
then its category default, unless the user explicitly chooses another location.

## Category changes

Category list/tree nodes now include:

```json
{
  "productKind": "household",
  "defaultStorageLocationId": "uuid-or-null"
}
```

`POST /categories` and `PATCH /categories/{id}` accept uppercase `productKind` and
`defaultStorageLocationId`. Existing `days` remains optional; `null` means this category has no
expiration default and may inherit one from a parent.

Suggested category settings UI: keep the parent/child tree, add a product-kind badge/filter, add
Default storage and Default expiration controls, and treat “No expiration default” as valid.

## Shopping API

Use these routes for new code:

- `GET /shopping`
- `POST /shopping`
- `PATCH /shopping/{barcode}`
- `POST /shopping/clear-checks`

The response contract matches the old grocery routes and each item now includes `productKind`.
The scanner command remains `CMD:MODE:LIST`; Settings can keep the label **Add To List**.

Manual shopping additions can include a product kind:

```json
{
  "name": "AA Batteries",
  "quantity": 2,
  "productKind": "HOUSEHOLD"
}
```

The legacy `/grocery` routes return the same data and should not be removed yet.

## Quick add changes

`POST /quick-add` still accepts the existing fields and additionally accepts:

```json
{
  "productKind": "HOUSEHOLD",
  "description": "Optional description",
  "manufacturer": "Acme Industries",
  "packageSize": "100 Count",
  "material": "Nitrile",
  "variant": "Large",
  "storageLocationId": "uuid-or-null"
}
```

Existing clients that omit these fields continue to work. The compatibility default for manually
created quick-add products is `FOOD`; the redesigned UI should always show and submit the kind.

## Intake popup changes

Keep the popup behavior Fable already built. Add one selector next to Store:

- label: **Stored in**;
- options: active storage locations;
- initial value: the scan-time product/category default;
- clear option: **No location**;
- submit as `storageLocationId` in the existing confirm request.

Repeated scans of the same product continue to update quantity in the same open intake batch.
Confirming applies the chosen storage location to every editable unit in that batch.

## Recommended navigation and terminology

- **Inventory** stays Inventory.
- Rename **Grocery List** to **Shopping**.
- Settings adds **Storage locations**.
- Product kind is a top-level inventory filter.
- Use **Product** or **Item**, not **Food**, in generic empty states and buttons.
- Keep food-only panels such as nutrition contextual instead of deleting them.

## Compatibility and rollout notes

- Existing Angular routes do not have to change in the same deployment.
- Existing food products are backfilled as kind `food`.
- Existing internal table names such as `food_categories` and `grocery_list_items` are deliberately
  retained for migration safety; do not mirror those names in visible UI copy.
- Existing `/grocery`, nutrition, category, tax, store-routing, barcode-printing, and scanner APIs
  remain supported.
