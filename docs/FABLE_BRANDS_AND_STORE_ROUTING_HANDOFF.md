# Fable handoff: brands and store routing

Status: backend implemented and covered by unit and PostgreSQL integration tests.

This document is the UI contract for canonical brand management and automatic grocery-store
assignment. The UI is not included in this backend change.

## Product goal

Larder can now standardize product brands and use configurable rules to decide where a grocery
item should be purchased. A typical rule is:

> If brand is Kirkland Signature, require Costco.

Assignments are calculated dynamically. Editing or disabling a rule immediately changes matching
grocery items; the derived store is not copied into the grocery row as permanent data.

Routing rules also default the `storeId` of an in-stock inventory unit when no store was explicitly
provided. Scanner context and manual store choices always win. Creating a rule backfills matching
in-stock units whose store is still null, and enrichment performs the same backfill when a newly
identified brand or category first makes a rule match. Existing non-null stores are never
overwritten by a rule.

## Resolution order

The first matching source wins:

1. Manual grocery-item store override
2. Exact product rule
3. Brand rule
4. Category rule
5. Lowest observed price at an active store
6. No store assignment

Rule priority is a tie-breaker only among rules at the same specificity. An exact product rule
always beats a brand rule, even when the brand rule has a higher numeric priority.

Rules have two behaviors:

- `prefer`: use the target store while it is active; if inactive, fall back to price history.
- `require`: retain the target store even when inactive and return a warning for the UI.

A manual selection always wins. If its store is inactive, it is retained with a warning.

For inventory-unit provenance, only active rule stores are used as defaults. Price-history fallback
is a grocery planning feature and does not set an inventory unit's purchase store.

## Canonical brand model

- A brand has an ID, display name, normalized name, active state, aliases, product count, and an
  optional brand-rule ID.
- Normalization is case-insensitive, collapses whitespace and punctuation, applies Unicode NFKC,
  and ignores trademark symbols. For example, `KIRKLAND-Signature™` normalizes to
  `kirkland signature`.
- A product may have multiple brands and one primary brand.
- Open Food Facts brand strings are split on commas, semicolons, and pipes. Existing products are
  backfilled by migration `0007`.
- Enrichment and manual quick-add both resolve aliases before creating a new brand.
- `brand` remains in product responses for compatibility. New UI should use `brandRef` and
  `brands` when it needs IDs.

Example product fields:

```json
{
  "brand": "Kirkland Signature",
  "brandRef": {
    "id": "68df68d1-5bfd-4fde-9f44-fac2af5cb20e",
    "name": "Kirkland Signature"
  },
  "brands": [
    {
      "id": "68df68d1-5bfd-4fde-9f44-fac2af5cb20e",
      "name": "Kirkland Signature"
    }
  ]
}
```

## Brand API

All endpoints are under `/v1/web`.

### List brands

`GET /brands`

```json
{
  "brands": [
    {
      "id": "68df68d1-5bfd-4fde-9f44-fac2af5cb20e",
      "name": "Kirkland Signature",
      "normalizedName": "kirkland signature",
      "aliases": ["Costco Kirkland", "Kirkland"],
      "active": true,
      "productCount": 14,
      "routingRuleId": "97154a59-988b-4b39-92da-45de40e1ee23"
    }
  ]
}
```

### Create a brand

`POST /brands`

```json
{
  "name": "Kirkland Signature",
  "aliases": ["Kirkland", "Costco Kirkland"]
}
```

Returns `201` and a brand object. A name or alias collision returns `409`.

### Edit a brand

`PATCH /brands/{brandId}`

```json
{
  "name": "Kirkland Signature",
  "aliases": ["Kirkland", "Costco Kirkland"],
  "active": true
}
```

All fields are optional. When `aliases` is provided, it replaces the complete alias list. Omitting
`aliases` leaves it unchanged. There is intentionally no hard-delete or merge endpoint in this
version.

### Correct a product's brands

`PUT /products/{barcode}/brands`

```json
{
  "brandIds": ["68df68d1-5bfd-4fde-9f44-fac2af5cb20e"],
  "primaryBrandId": "68df68d1-5bfd-4fde-9f44-fac2af5cb20e"
}
```

`primaryBrandId` must be included in `brandIds`. If omitted, the first brand becomes primary. Send
an empty `brandIds` array to clear the product's brand assignment.

## Store-routing API

### List rules

`GET /store-routing-rules`

```json
{
  "rules": [
    {
      "id": "97154a59-988b-4b39-92da-45de40e1ee23",
      "name": "Kirkland Signature to Costco",
      "matchType": "brand",
      "match": {
        "id": "68df68d1-5bfd-4fde-9f44-fac2af5cb20e",
        "label": "Kirkland Signature",
        "productCount": 14
      },
      "store": {
        "id": "8ee9404d-f579-4c69-98be-2f021ac9ecfd",
        "code": "COSTCO",
        "name": "Costco",
        "active": true
      },
      "behavior": "require",
      "priority": 0,
      "enabled": true,
      "createdAt": "2026-07-17T20:00:00+00:00",
      "updatedAt": "2026-07-17T20:00:00+00:00"
    }
  ],
  "precedence": ["manual", "product", "brand", "category", "price_history"]
}
```

`match.productCount` is the rule preview count Fable should show.

### Create a rule

`POST /store-routing-rules`

Brand rule:

```json
{
  "name": "Kirkland Signature to Costco",
  "matchType": "brand",
  "brandId": "68df68d1-5bfd-4fde-9f44-fac2af5cb20e",
  "storeId": "8ee9404d-f579-4c69-98be-2f021ac9ecfd",
  "behavior": "require",
  "priority": 0,
  "enabled": true
}
```

Product and category rules use exactly one of these shapes:

```json
{ "matchType": "product", "productBarcode": "096295131338" }
```

```json
{ "matchType": "category", "categoryId": "en:beverages" }
```

Include the common `storeId`, `behavior`, `priority`, and `enabled` fields with either shape. Name is
optional; the API generates one. Enum input is case-insensitive. Only one rule may exist for each
exact product, brand, or category matcher; duplicates return `409`.

### Edit or delete a rule

`PATCH /store-routing-rules/{ruleId}` accepts:

```json
{
  "name": "House brands at Costco",
  "storeId": "8ee9404d-f579-4c69-98be-2f021ac9ecfd",
  "behavior": "require",
  "priority": 10,
  "enabled": true
}
```

The matcher is immutable in this version. To change the matcher, delete and recreate the rule.

`DELETE /store-routing-rules/{ruleId}` returns `204`.

### Preview one product's derived assignment

`GET /products/{barcode}/store-assignment`

```json
{
  "barcode": "096295131338",
  "assignment": {
    "storeId": "8ee9404d-f579-4c69-98be-2f021ac9ecfd",
    "storeName": "Costco",
    "storeActive": true,
    "source": "brand_rule",
    "reason": "Brand rule: Kirkland Signature",
    "behavior": "require",
    "ruleId": "97154a59-988b-4b39-92da-45de40e1ee23",
    "warning": null
  }
}
```

Possible `source` values are `manual`, `product_rule`, `brand_rule`, `category_rule`,
`price_history`, and `none`.

## Grocery API changes

Every item from `GET /grocery` still has the compatibility fields `storeId` and `storeName`, and now
also has `storeAssignment` with provenance:

```json
{
  "storeId": "8ee9404d-f579-4c69-98be-2f021ac9ecfd",
  "storeName": "Costco",
  "storeAssignment": {
    "storeId": "8ee9404d-f579-4c69-98be-2f021ac9ecfd",
    "storeName": "Costco",
    "storeActive": true,
    "source": "brand_rule",
    "reason": "Brand rule: Kirkland Signature",
    "behavior": "require",
    "ruleId": "97154a59-988b-4b39-92da-45de40e1ee23",
    "warning": null
  }
}
```

Set a manual override with:

`PATCH /grocery/{barcode}`

```json
{ "preferredStoreId": "some-store-uuid" }
```

Clear the override and return to automatic routing with:

```json
{ "preferredStoreId": null }
```

The overview grocery preview now also returns `storeName` and `storeAssignmentSource`.

## Recommended UI

### Shopping rules screen

Add a `Shopping rules` or `Store routing` area under settings. The primary list row should show:

- Rule name and enabled toggle
- `Brand · Kirkland Signature` style matcher label
- Destination store
- `Preferred` or `Required` badge
- `14 matching products` preview count
- Warning state if the destination store is inactive
- Edit and delete actions

The create/edit drawer should use this order:

1. Match type: Product, Brand, or Category
2. Searchable matcher selector
3. Destination store selector
4. Behavior: Prefer or Require, with short explanatory copy
5. Optional priority under an advanced disclosure
6. Enabled toggle

Do not expose a free-form rule language in this version.

### Brands screen

Add a searchable brand table with:

- Canonical display name
- Alias chips
- Product count
- Linked routing rule, if present
- Active/inactive status
- Edit action

The edit drawer needs display name, alias-chip input, active toggle, and a link to create or edit the
brand's store rule. Explain that aliases are alternate source spellings, not additional display
names.

### Product detail

Show the canonical brand as a linked entity. An edit action should open a multi-select brand picker
with one primary brand. Also show a `Buy at` card using the product assignment endpoint:

- Store name
- Assignment reason
- Preferred/Required badge when rule-driven
- Warning message when present
- Link to the matching rule when `ruleId` is non-null

### Grocery list

Continue grouping by `storeName`. Beneath or beside the store, use compact provenance copy:

- `Kirkland Signature rule` for `brand_rule`
- `Product rule` for `product_rule`
- `Category rule` for `category_rule`
- `Lowest observed price` for `price_history`
- `Selected manually` for `manual`

Each item needs a store-override action and a `Use automatic assignment` action. Display
`storeAssignment.warning` prominently; do not silently hide required inactive stores.

## UI states and validation

- Empty rules: explain the precedence and offer `Create first rule`.
- Empty brands: explain that brands are automatically populated during product enrichment.
- No assignment: group under `Any store` or `Unassigned` and show no error styling.
- Inactive preferred store: automatic price fallback may occur, so show the returned assignment.
- Inactive required/manual store: show the returned store and warning.
- `409` on brand save: keep the form open and explain that the name or alias is already in use.
- `409` on rule creation: offer to open the existing rule.
- `404` during editing: refresh the collection; the target was removed in another session.
- Use the existing live-update event stream; writes to brands, aliases, product-brand associations,
  and routing rules now publish Larder change notifications.

## Fable acceptance checklist

- Brand list and editor consume canonical IDs rather than comparing display strings.
- Product detail supports correcting brands and choosing the primary brand.
- Rules support product, brand, and category matchers plus prefer/require behavior.
- Rule list shows matcher product count and inactive-store warnings.
- Grocery items show assignment provenance and allow setting/clearing manual overrides.
- No UI code duplicates the resolver; `storeAssignment` from the API is the source of truth.
- Loading, empty, validation, conflict, stale-target, and inactive-store states are designed.
- Existing responsive behavior and live refresh are preserved.

## Backend implementation references

- Migration: `migrations/versions/0007_brands_and_store_routing.py`
- Models: `src/larder/models/brand.py`
- Brand normalization: `src/larder/services/brands.py`
- Routing resolver: `src/larder/services/store_routing.py`
- Brand API: `src/larder/webapi/brands.py`
- Rule API: `src/larder/webapi/routing.py`
- Grocery integration: `src/larder/webapi/grocery.py`

Verification result after default-store routing and standardized naming support: `81 passed`.

All user-facing product, brand, brand-alias, store, category, routing-rule, and
scanner display names are returned in title case. The backend normalizes new
writes and Open Food Facts enrichment; migration `0009` updates existing rows.
Identifiers, barcodes, codes, URLs, ingredients, and raw source data are not
case-normalized.
