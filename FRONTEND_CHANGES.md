# Frontend Handoff — Server-side Discount / Price / Sort Filtering

Backend now forwards discount, price, and sort filters to CJ and Impact instead of dropping them.

**Nothing breaks.** Every new parameter is optional, and omitting all of them reproduces today's exact behaviour. No existing field changed name, type, or meaning. There is one behavioural change, described under "Breaking-ish" below.

All parameters are visible in Swagger at `/docs` with descriptions and examples, and can be exercised from "Try it out".

---

## 1. Impact — new query parameters

Added to all three catalog endpoints:

- `GET /api/v1/impact/catalogs/{catalog_id}/items`
- `GET /api/v1/impact/catalogs/{catalog_id}/items/by-keyword`
- `GET /api/v1/impact/catalogs/item-search`

| Param | Type | Range | Meaning |
|---|---|---|---|
| `sortBy` | string | see allowlist below | Field to sort by, applied upstream |
| `sortOrder` | string | `ASC` \| `DESC` | Sort direction; only meaningful with `sortBy` |
| `minDiscount` | number | `0`–`100` | Minimum discount percentage |
| `minPrice` | number | `>= 0` | Minimum current price |
| `maxPrice` | number | `>= 0` | Maximum current price |

**`sortBy` allowlist** — exactly these six values:

```
DiscountPercentage, CurrentPrice, Name, CatalogItemId, Category, Manufacturer
```

Matching is case-insensitive (`discountpercentage` works). Anything else is rejected with `422` and never sent upstream. Build the sort dropdown from this list — don't let free text reach it.

### Examples

```
GET /api/v1/impact/catalogs/item-search?keyword=shoes&sortBy=DiscountPercentage&sortOrder=DESC&limit=5
GET /api/v1/impact/catalogs/item-search?keyword=shoes&minDiscount=30&limit=5
GET /api/v1/impact/catalogs/895/items?minDiscount=20&maxPrice=50&limit=5
```

Filters compose — `minDiscount` + `minPrice` + `maxPrice` + `sortBy` can all be sent together.

### Verified effect on result counts

| Query | `@total` |
|---|---|
| `item-search?keyword=shoes` (baseline) | 42,676 |
| `item-search?keyword=shoes&minDiscount=30` | 1,530 |
| `catalogs/895/items` (baseline) | 222,712 |
| `catalogs/895/items?minDiscount=20` | 25,246 |

### Important: text filtering must stay on `keyword`

Impact's filter parser accepts **numeric conditions only**. Do not expect to filter by name, category, brand, or stock status through these params — the backend deliberately never constructs such conditions because they fail upstream. Text matching continues to go through the existing `keyword` param, unchanged.

### No promotions filter

There is **no `promotionsOnly` parameter.** It was specified but could not be implemented: Impact rejects it outright on `item-search`, silently ignores it on `catalogs/{id}/items` (identical total to a deliberately fake param name), and the alternative form returns zero rows. Do not build a "promotions only" toggle against this API — there is nothing behind it. Ask backend before designing that chip.

---

## 2. CJ — new request body fields

`POST /api/v1/cj/ads/products/query`

| Field | Type | Range | Meaning |
|---|---|---|---|
| `discount_percentage` | number | `0`–`100` | **Minimum** discount, not an exact match |
| `low_price` | number | `>= 0` | Minimum price |
| `high_price` | number | `>= 0` | Maximum price |
| `brand` | string | — | Single brand name |
| `sort_by` | string | `LAST_UPDATED` \| `PRICE` | Sort field |
| `sort_order` | string | `ASC` \| `DESC` | Sort direction |

`sort_by` / `sort_order` are accepted case-insensitively and normalised to uppercase.

```jsonc
{
  "company_id": "6947255",
  "pid": "6947255",
  "keywords": ["shoes"],
  "limit": 20,
  "discount_percentage": 30,
  "low_price": 50,
  "high_price": 150,
  "brand": "Nike",
  "sort_by": "PRICE",
  "sort_order": "ASC"
}
```

### Verified effect on result counts

| Query | `totalCount` |
|---|---|
| `keywords: ["shoes"]` (baseline) | 124,743 |
| `+ discount_percentage: 30` | 41,475 |
| `+ discount_percentage: 60` | 3,045 |

At `discount_percentage: 60`, every sampled row's `salePrice` was at least 60% below `price`.

### There is no discount sort on CJ

CJ's sort enum contains only `LAST_UPDATED` and `PRICE`. A "sort by discount" option **cannot** be sent to CJ. The intended pattern is: filter with `discount_percentage`, then sort the returned page client-side. If the UI offers "biggest discount first" for CJ, it has to be a client-side sort of the current page.

### Breaking-ish: sorting disables pagination

CJ rejects any sorted query that also asks for a page cursor. So **when `sort_by` is set, the response always returns `nextPage: null`** — there is no second page. This is enforced by the backend; you'll get a valid sorted first page, not an error.

Frontend impact: when a CJ sort is active, hide or disable "load more" / infinite scroll. Treat `nextPage: null` as authoritative — that logic already works, it just now triggers whenever sorting is on.

Unsorted requests are unaffected and still return a `nextPage` cursor as before.

### Pagination reminder (unchanged)

`offset` must stay `0`. CJ only supports cursor pagination via the `page` token taken from the previous response's `nextPage`.

---

## 3. New error responses

Standard error envelope, unchanged shape.

**Invalid Impact sort field** — HTTP `422`:

```json
{
  "success": false,
  "error": {
    "code": "INVALID_SORT_FIELD",
    "message": "Unsupported sortBy value. Allowed: DiscountPercentage, CurrentPrice, Name, CatalogItemId, Category, Manufacturer.",
    "retryable": false,
    "step": "impact_catalog_items_search"
  }
}
```

**Invalid Impact sort order** — HTTP `422`, code `INVALID_SORT_ORDER`, same shape.

**Invalid CJ `sort_by` / `sort_order`** — HTTP `422`, code `VALIDATION_ERROR`, with the offending field in `error.details[].loc`.

Both new codes are `retryable: false` — show a message, don't offer retry.

### Bug fix worth knowing about

Request-body validation failures on CJ previously returned **HTTP 500 `INTERNAL_SERVER_ERROR`** instead of a 422. This was a latent backend bug affecting any validated body field. It's fixed — you now reliably get `422` + `VALIDATION_ERROR`. If any frontend code special-cases a 500 from this endpoint as "bad input", it can be removed.

---

## Summary for the ticket

- Impact: 5 new optional query params on 3 endpoints, all filtering server-side.
- CJ: 6 new optional body fields.
- 2 new 422 error codes: `INVALID_SORT_FIELD`, `INVALID_SORT_ORDER`.
- CJ sorting and pagination are mutually exclusive — `nextPage` is `null` while sorting.
- No promotions filter exists; no discount sort exists on CJ.
- Everything is in `/docs` with examples.
