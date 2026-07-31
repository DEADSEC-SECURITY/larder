# Fable handoff: interchangeable stock groups

## What changed

Larder can now keep a generic household need in stock without requiring one exact barcode.
For example, a `Strawberries` stock group can contain Driscoll's, Wegmans, and future compatible
strawberry products. Inventory and restock calculations use the total quantity across every
accepted member.

The backend calls this a **stock group**. In user-facing copy, prefer:

- **Any compatible brand or size** for the scope.
- **Stock group** only on management/settings screens.
- **Exact product** for the existing barcode-specific behavior.

Stock groups are only created intentionally from products the user chooses to keep stocked.
After that one-time choice, matching future products is automated.

## Matching behavior

The backend owns all matching. Do not duplicate name-matching logic in Angular.

It normalizes case and plurals, ignores brand, package size, quantity, and marketing words, then
scores product kind, identity terms, category hierarchy, and physical form. Product kind is a
hard boundary, so a food item cannot satisfy a household or medical need.

Default confidence behavior:

| Confidence | Backend action | UI treatment |
| --- | --- | --- |
| `>= 0.90` and unambiguous | Automatically accepts the product | Show `Matched automatically` |
| `0.65` through `0.8999` | Creates a review candidate | Show in the review queue |
| `< 0.65` | Does nothing | Do not surface as a match |

The top candidate must lead the second candidate by at least `0.05` before automatic acceptance.
Products with an existing exact-product stock policy are never moved automatically; they become
review candidates instead.

Examples:

- `Driscoll's Strawberries 16 Oz` and `Wegmans Organic Strawberries 32 Oz` auto-match.
- `Frozen Strawberries` is sent to review when the group currently represents fresh fruit.
- `Strawberry Greek Yogurt` is rejected by category/identity evidence.
- Accepting a candidate teaches compatible categories/forms to the group.
- Rejecting a candidate remembers that barcode and learns only its distinguishing terms/forms.

## Product payload additions

Every product returned by inventory, products, and product detail now includes `stockGroup`.

```json
{
  "barcode": "0715756200023",
  "name": "Strawberries",
  "policy": {
    "reorderPoint": 2,
    "targetStock": 3,
    "scope": "stock_group",
    "stockGroupId": "2cb5d264-00fd-43b7-a6df-a55e4a8da4d4"
  },
  "stockGroup": {
    "id": "2cb5d264-00fd-43b7-a6df-a55e4a8da4d4",
    "name": "Strawberries",
    "active": true,
    "source": "automatic",
    "confidence": 0.97,
    "scopeLabel": "Any compatible brand or size",
    "memberCount": 3,
    "inStock": 4,
    "policy": {
      "reorderPoint": 2,
      "targetStock": 3
    }
  }
}
```

`inStock` at the product root remains the exact barcode's count. `stockGroup.inStock` is the
combined count that determines whether the generic need is satisfied.

When `stockGroup` is null, the product is not an accepted member. Use the match endpoint below to
load pending suggestions.

## Product-detail stock controls

In the existing Keep Stocked section, add a scope choice:

1. **Exact product** — preserve the existing barcode policy.
2. **Any compatible brand or size** — create/use a stock group.

When converting an exact product to the generic scope, call:

`POST /v1/web/stock-groups`

```json
{
  "seedBarcode": "0715756200023",
  "name": "Strawberries",
  "reorderPoint": 2,
  "targetStock": 3
}
```

`name` and the policy fields are optional. If the product already has an exact policy, creating
the group migrates that policy and its shopping-list demand automatically.

Response:

```json
{
  "created": true,
  "stockGroup": {
    "id": "2cb5d264-00fd-43b7-a6df-a55e4a8da4d4",
    "name": "Strawberries",
    "canonicalKey": "strawberry",
    "productKind": "food",
    "representativeProductBarcode": "0715756200023",
    "requiredTerms": ["strawberry"],
    "excludedTerms": [],
    "allowedForms": ["fresh"],
    "categoryIds": ["en:berries", "en:fruits"],
    "ignoreBrand": true,
    "ignorePackageSize": true,
    "autoMatchThreshold": 0.9,
    "reviewThreshold": 0.65,
    "active": true,
    "memberCount": 3,
    "inStock": 4,
    "pendingCandidateCount": 1,
    "policy": {
      "reorderPoint": 2,
      "targetStock": 3
    }
  }
}
```

The existing endpoint remains backward compatible:

`PUT /v1/web/products/{barcode}/stock-policy`

If the product is grouped, the backend updates the group policy and returns
`scope: "stock_group"` plus `stockGroupId`. Fable should prefer the explicit group endpoint once
the new UI is in place:

`PUT /v1/web/stock-groups/{groupId}/stock-policy`

```json
{
  "reorderPoint": 2,
  "targetStock": 3
}
```

Sending both values as zero removes the policy.

## Stock-group management APIs

### List groups

`GET /v1/web/stock-groups`

Returns:

```json
{
  "stockGroups": [
    {
      "id": "2cb5d264-00fd-43b7-a6df-a55e4a8da4d4",
      "name": "Strawberries",
      "productKind": "food",
      "memberCount": 3,
      "inStock": 4,
      "pendingCandidateCount": 1,
      "autoMatchThreshold": 0.9,
      "reviewThreshold": 0.65,
      "active": true,
      "policy": {"reorderPoint": 2, "targetStock": 3}
    }
  ]
}
```

Pass `?includeArchived=true` to include inactive groups.

### Group detail

`GET /v1/web/stock-groups/{groupId}`

This returns all summary fields plus members and candidates:

```json
{
  "id": "2cb5d264-00fd-43b7-a6df-a55e4a8da4d4",
  "name": "Strawberries",
  "members": [
    {
      "barcode": "0715756200023",
      "name": "Strawberries",
      "brand": "Driscoll's",
      "source": "user",
      "confidence": 1.0,
      "evidence": {"seedProduct": true}
    }
  ],
  "candidates": [
    {
      "barcode": "0000000000102",
      "name": "Frozen Strawberries",
      "brand": null,
      "confidence": 0.79,
      "status": "pending",
      "evidence": {
        "requiredCoverage": 1.0,
        "termPrecision": 1.0,
        "categoryOverlap": ["en:fruits"],
        "productForms": ["frozen"],
        "formConflict": true
      }
    }
  ]
}
```

Candidates with `status: "rejected"` are history, not actionable review cards.

### Edit matching configuration

`PATCH /v1/web/stock-groups/{groupId}`

Supported fields:

```json
{
  "name": "Fresh Strawberries",
  "requiredTerms": ["strawberry"],
  "excludedTerms": ["frozen", "yogurt"],
  "allowedForms": ["fresh"],
  "categoryIds": ["en:berries", "en:fruits"],
  "autoMatchThreshold": 0.9,
  "reviewThreshold": 0.65,
  "active": true
}
```

Saving automatically reruns matching against ungrouped products. Require
`autoMatchThreshold >= reviewThreshold` in the form.

Do not expose brand or package-size matching toggles. Those are deliberately ignored for this
feature's generic scope.

### Accept or reject a candidate

`POST /v1/web/stock-groups/{groupId}/candidates/{barcode}/decision`

Accept and learn:

```json
{
  "accepted": true,
  "learn": true,
  "migratePolicy": true
}
```

Reject and learn:

```json
{
  "accepted": false,
  "learn": true
}
```

The response is refreshed group detail. Use optimistic loading, not optimistic membership changes,
because acceptance can migrate shopping demand and policy state transactionally.

### Manually add or remove members

Add:

`POST /v1/web/stock-groups/{groupId}/members`

```json
{
  "barcode": "0715756200023",
  "learn": true,
  "migratePolicy": true
}
```

Remove and remember the rejection:

`DELETE /v1/web/stock-groups/{groupId}/members/{barcode}?remember=true`

If a product is moved from another group, the backend recalculates both groups.

### Product match state

Read persisted state without making changes:

`GET /v1/web/products/{barcode}/stock-group-matches`

Force a fresh backend evaluation:

`POST /v1/web/products/{barcode}/stock-group-matches/refresh`

Possible top-level `status` values are `matched`, `review`, and `none`. The refresh response also
contains `evaluation.status`, which may be `matched`, `review`, `no_match`, or `no_profile`.

Global/manual rescan:

`POST /v1/web/stock-groups/auto-match`

```json
{
  "evaluated": 41,
  "matched": 8,
  "review": 3,
  "unmatched": 30
}
```

## Shopping-list changes

`GET /v1/web/shopping` can now return exact product rows and stock-group rows together.

```json
{
  "itemType": "stock_group",
  "barcode": "group:2cb5d264-00fd-43b7-a6df-a55e4a8da4d4",
  "stockGroupId": "2cb5d264-00fd-43b7-a6df-a55e4a8da4d4",
  "name": "Strawberries",
  "brand": null,
  "scopeLabel": "Any compatible brand or size",
  "productKind": "food",
  "quantity": 2,
  "checked": false,
  "reason": "Below minimum · 1 of 2",
  "memberBarcodes": ["0715756200023", "0778901032890"],
  "memberCount": 2,
  "inStock": 1,
  "storeId": null,
  "storeName": null,
  "unitPrice": null,
  "priceKnown": false,
  "storeAssignment": {
    "storeId": null,
    "storeName": null,
    "storeActive": null,
    "source": "none",
    "reason": null,
    "behavior": null,
    "ruleId": null,
    "warning": null
  }
}
```

The pseudo-barcode is intentional compatibility plumbing. Existing check/quantity/store actions can
use it unchanged:

`PATCH /v1/web/shopping/group:{groupId}`

To add generic manual demand, prefer:

`POST /v1/web/shopping`

```json
{
  "stockGroupId": "2cb5d264-00fd-43b7-a6df-a55e4a8da4d4",
  "quantity": 1
}
```

`POST /v1/web/shopping/clear-checks` clears both exact and group rows.

Shopping-card design:

- Branch on `itemType`, not the `group:` prefix.
- For a group, replace the brand line with `Any compatible brand or size`.
- Optionally show `N accepted products` and `M currently in stock`.
- Keep quantity, checked, preferred-store, and totals behavior unchanged.
- The backend picks the lowest observed compatible-product price unless the group has a manual
  preferred store or every member consistently routes to one store.

## Scanner and intake behavior

- Scanner **LIST** mode adds demand to the stock group when the barcode is already a member.
- If the barcode has not been identified yet, it is temporarily added as an exact item. Enrichment
  moves that demand into the group if the product later auto-matches.
- Scanner **ADD**, **REMOVE**, and **TRASH** recalculate the combined group stock automatically.
- Manual quick-add and enrichment both trigger product matching.

In the intake popup, add a passive line when `product.stockGroup` is present:

> Counts toward Strawberries · Any compatible brand or size

Do not interrupt intake for an automatic match. A pending review should be handled in the stock
group review queue, not as a blocking scanner popup.

## Recommended settings UI

Add **Stock groups** under Settings.

List card:

- Group name and product-kind badge.
- Combined on-hand count.
- Target/reorder summary.
- Accepted product count.
- Pending review badge.
- Active/inactive state.

Detail page or drawer:

1. **Keep stocked** — reorder point and target.
2. **Accepted products** — image/name/brand/barcode, source, confidence, remove action.
3. **Needs review** — candidate evidence, Accept, Reject.
4. **Matching rules** — required terms, excluded terms, allowed forms, categories, thresholds.
5. **Danger zone** — deactivate group. Deactivation preserves history and clears automatic demand.

Confidence is supporting information, not the primary label. Translate evidence into concise copy,
for example `Same product name`, `Same fruit category`, or `Different form: frozen`.

## Fable acceptance checklist

- A product can switch from exact stock to `Any compatible brand or size`.
- Creating the generic scope preserves its existing policy and shopping demand.
- Product detail shows exact barcode stock separately from combined group stock.
- Group members and pending candidates are manageable from Settings.
- Accept and Reject use backend endpoints and refresh from the response.
- Shopping rows branch on `itemType` and support `group:{id}` patch routes.
- Scanner intake shows a passive group membership line without blocking.
- The UI never implements its own product-equivalence matcher.
- Exact-product behavior remains unchanged when `stockGroup` is null.
