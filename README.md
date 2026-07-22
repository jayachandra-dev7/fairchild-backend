# Affiliate Automation Backend

Fresh FastAPI scaffold for multi-platform affiliate integrations (CJ, Impact, WordPress, Metricool).

## Folder Structure

```text
app/
  main.py
  api/
    v1/
      router.py
      endpoints/
        cj/
        impact/
        wordpress/
        metricool/
  core/
  schemas/
    cj/
    impact/
    wordpress/
    metricool/
  services/
    cj/
    impact/
    wordpress/
    metricool/
  models/
  workers/
  utils/
```

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Local Env Credentials
- For local testing, credentials can be loaded from `.env` at app startup.
- Supported keys: `CJ_TOKEN`, `IMPACT_ACCOUNT_SID`, `IMPACT_AUTH_TOKEN`, `WORDPRESS_DOMAIN`, `WORDPRESS_WC_CONSUMER_KEY`, `WORDPRESS_WC_CONSUMER_SECRET`, `METRICOOL_TOKEN`, `METRICOOL_USER_ID`, `METRICOOL_BLOG_ID`, `RENDERFORM_API_KEY`, `CLAUDE_API_KEY`.
- You can still call each platform `authorize` endpoint later to override in-memory values for the running process.

## Docs
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- API error/retry guide: `API_README.md`

## Current Starter Endpoints
- `GET /api/v1/health`
- `GET /api/v1/platforms`
- `GET /api/v1/cj/health`
- `POST /api/v1/cj/authorize`
- `GET /api/v1/claude/health`
- `POST /api/v1/claude/generate`
- `GET /api/v1/impact/health`
- `POST /api/v1/impact/authorize`
- `GET /api/v1/impact/campaigns`
- `GET /api/v1/impact/campaigns/{campaign_id}`
- `GET /api/v1/impact/campaigns/{campaign_id}/deals`
- `GET /api/v1/impact/campaigns/{campaign_id}/deals/{deal_id}`
- `GET /api/v1/impact/catalogs`
- `GET /api/v1/impact/catalogs/{catalog_id}/items`
- `GET /api/v1/impact/catalogs/item-search`
- `GET /api/v1/impact/media-properties`
- `POST /api/v1/impact/programs/{program_id}/tracking-links`
- `GET /api/v1/wordpress/health`
- `POST /api/v1/wordpress/authorize`
- `POST /api/v1/wordpress/media/upload`
- `POST /api/v1/wordpress/products`
- `GET /api/v1/metricool/health`
- `POST /api/v1/metricool/authorize`
- `GET /api/v1/metricool/profiles`
- `POST /api/v1/metricool/upload`
- `GET /api/v1/metricool/scheduler/posts`
- `GET /api/v1/metricool/scheduler/boards/pinterest`
- `POST /api/v1/metricool/scheduler/posts`
- `GET /api/v1/renderform/health`
- `POST /api/v1/renderform/authorize`
- `GET /api/v1/renderform/templates`
- `POST /api/v1/renderform/render`
- `POST /api/v1/renderform/render/upload`

Next, we can add each platform router from your cURL commands one-by-one.

## CJ: Advertiser Lookup
- Backend route: `GET /api/v1/cj/advertisers/lookup`
- Required query params:
  - `requestor-cid` (example: `6947255`)
  - `advertiser-ids` optional (default: `joined`, example: `1,2`)
  - `response-format` optional (`json` or `raw`, default: `json`)
- Auth options:
  - Set once via `POST /api/v1/cj/authorize` with body `{"token":"<CJ_TOKEN>"}`
  - Or pass header `Authorization: Bearer <token>` directly on lookup call

## CJ: Ads Product Query
- Backend route: `POST /api/v1/cj/ads/products/query`
- Upstream URL: `https://ads.api.cj.com/query`
- Uses token saved from `POST /api/v1/cj/authorize`
- Content type sent upstream: `application/graphql`

### CJ: Product Filtering and Sorting
Additional body fields on `POST /api/v1/cj/ads/products/query`:

- `discount_percentage` (`0`–`100`) — a **minimum** threshold, not an exact match.
- `low_price`, `high_price` — price band.
- `brand` — single brand name.
- `sort_by` — `LAST_UPDATED` or `PRICE`. CJ's enum has no discount sort; use `discount_percentage` to filter, then sort the returned page client-side.
- `sort_order` — `ASC` or `DESC`.

Sorting and cursor pagination are mutually exclusive: CJ rejects any query whose *selection set* contains `nextPage` once sorting is requested, so setting `sort_by` drops `nextPage` from the selection and the response reports `nextPage: null`.

`offset` must stay `0` when paging — CJ only supports cursor pagination through the `page` token.

## Impact: Campaigns
- Backend route: `GET /api/v1/impact/campaigns`
- Backend route: `GET /api/v1/impact/campaigns/{campaign_id}`
- Backend route: `GET /api/v1/impact/campaigns/{campaign_id}/deals`
- Backend route: `GET /api/v1/impact/campaigns/{campaign_id}/deals/{deal_id}`
- Upstream URL: `https://api.impact.com/Mediapartners/<Account-SID>/Campaigns`
- Upstream URL: `https://api.impact.com/Mediapartners/<Account-SID>/Campaigns/<ID>`
- Upstream URL: `https://api.impact.com/Mediapartners/<Account-SID>/Campaigns/<CampaignID>/Deals`
- Upstream URL: `https://api.impact.com/Mediapartners/<Account-SID>/Campaigns/<CampaignID>/Deals/<ID>`
- Auth can be stored via `POST /api/v1/impact/authorize`
- Auth format: Basic Auth using `account_sid` as username and `auth_token` as password
- If direct Basic Auth is sent on the request, it overrides stored credentials
- Header sent upstream: `Accept: application/json`
- Pagination params supported on list endpoints: `limit` (default `20`), `offset` (default `0`)

## Impact: Catalogs and Properties
- Backend route: `GET /api/v1/impact/catalogs`
- Backend route: `GET /api/v1/impact/catalogs/{catalog_id}/items`
- Optional query param: `keyword`
- Pagination params supported: `limit` (default `20`), `offset` (default `0`)
- Backend route: `GET /api/v1/impact/catalogs/item-search`
- Optional query param: `keyword`
- Pagination params supported: `limit` (default `20`), `offset` (default `0`)
- Backend route: `GET /api/v1/impact/media-properties`

### Impact: Server-side Filtering and Sorting
Available on `GET /catalogs/{catalog_id}/items`, `GET /catalogs/{catalog_id}/items/by-keyword` and `GET /catalogs/item-search`:

- `sortBy` — one of `DiscountPercentage`, `CurrentPrice`, `Name`, `CatalogItemId`, `Category`, `Manufacturer`. Any other value is rejected with `422` (`INVALID_SORT_FIELD`) and never forwarded, because Impact answers unknown sort fields with an opaque `400`.
- `sortOrder` — `ASC` or `DESC`.
- `minDiscount`, `minPrice`, `maxPrice` — combined into upstream `Query` as AND-joined numeric conditions, e.g. `DiscountPercentage>30 AND CurrentPrice>20 AND CurrentPrice<50`.

Impact's `Query` parser accepts **numeric conditions only**. String conditions such as `Name~boots` or `StockAvailability=InStock` fail upstream with `Failed to parse expression`, so text matching continues to go through `keyword`.

There is no promotions filter: `PromotionIds=!=null` returns `400 Invalid search param(s): PromotionIds` on `ItemSearch` and is silently ignored on `Catalogs/{id}/Items`, while `Query=PromotionIds!=null` returns zero rows with `@total=-1`.

## Impact: Tracking Links
- Backend route: `POST /api/v1/impact/programs/{program_id}/tracking-links`
- Upstream URL: `https://api.impact.com/Mediapartners/<Account-SID>/Programs/<PROGRAM_ID>/TrackingLinks`
- Optional body fields: `deeplink`, `shared_id`, `sub_id1`, `sub_id2`, `sub_id3`, `sub_id4`, `media_partner_property_id`

## Metricool
- Backend route: `POST /api/v1/metricool/authorize`
- Required body fields: `token`
- Backend route: `GET /api/v1/metricool/profiles`
- Upstream URL: `https://app.metricool.com/api/admin/simpleProfiles`
- Backend route: `POST /api/v1/metricool/upload`
- Required query params: `userId`, `blogId`
- Upload field: `picture` as multipart file
- Upstream URL: `https://app.metricool.com/api/utils/upload?userId=<USER_ID>&blogId=<BLOG_ID>`
- Backend route: `GET /api/v1/metricool/scheduler/posts`
- Required query params: `start`, `end`
- `userId` and `blogId` are optional in request if `METRICOOL_USER_ID` and `METRICOOL_BLOG_ID` are set in `.env`
- Optional query params: `timezone` default `America/Denver`, `extendedRange` default `true`
- Backend route: `GET /api/v1/metricool/scheduler/boards/pinterest`
- `userId` and `blogId` are optional in request if `METRICOOL_USER_ID` and `METRICOOL_BLOG_ID` are set in `.env`
- Upstream URL: `https://app.metricool.com/api/v2/scheduler/boards/pinterest?userId=<USER_ID>&blogId=<BLOG_ID>`
- Backend route: `POST /api/v1/metricool/scheduler/posts`
- Required query params: `userId`, `blogId`
- Upstream URL: `https://app.metricool.com/api/v2/scheduler/posts?userId=<USER_ID>&blogId=<BLOG_ID>`
- Header sent upstream: `X-Mc-Auth: <token>`

## WordPress
- Backend route: `POST /api/v1/wordpress/authorize`
- Required body fields: `domain`, `wc_consumer_key`, `wc_consumer_secret`
- Backend route: `POST /api/v1/wordpress/media/upload`
- Upload field: `file` as multipart form-data
- Upstream URL: `https://<domain>/wp-json/wp/v2/media`
- Auth format: Basic Auth using `wc_consumer_key:wc_consumer_secret`
- Backend route: `POST /api/v1/wordpress/products`
- Upstream URL: `https://<domain>/wp-json/wc/v3/products`
- Auth format: Basic Auth using `wc_consumer_key:wc_consumer_secret`
- Product body supports sample keys: `name`, `type`, `status`, `featured`, `catalog_visibility`, `description`, `short_description`, `external_url`, `button_text`, `regular_price`, `sale_price`, `images`, `meta_data`
- Product body also supports `categories` as WooCommerce expects: `[{ "id": <category_id> }]`
- Backend route: `GET /api/v1/wordpress/products/categories`
- Upstream URL: `https://<domain>/wp-json/wc/v3/products/categories`
- Auth format: Basic Auth using `wc_consumer_key:wc_consumer_secret`
- Pagination params: `per_page` default `100`, `page` default `1`

## RenderForm
- Backend route: `POST /api/v1/renderform/authorize`
- Required body fields: `api_key`
- Backend route: `GET /api/v1/renderform/templates`
- Upstream URL: `https://get.renderform.io/api/v2/my-templates`
- Header sent upstream: `X-API-KEY: <API_KEY>`
- Backend route: `POST /api/v1/renderform/render`
- Upstream URL: `https://get.renderform.io/api/v2/render`
- Sample body fields handled: `template`, `titleText`, `imageSrc` and optional `extraData`
- Backend route: `POST /api/v1/renderform/render/upload`
- Upload field: `image` (multipart)
- The backend converts uploaded image to data URL and sends it as `image.src`

## Claude
- Backend route: `POST /api/v1/claude/generate`
- Requires `CLAUDE_API_KEY` in `.env`
- Upstream URL: `https://api.anthropic.com/v1/messages`
- Supports model fallback via `modelCandidates`
