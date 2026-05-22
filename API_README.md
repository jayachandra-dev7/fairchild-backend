# API Error and Retry Behavior

This document describes the standardized error envelope, retry behavior, and validation rules used by pipeline endpoints.

## Standardized Error Envelope

All handled API failures use this shape:

```json
{
  "success": false,
  "error": {
    "code": "MACHINE_CODE",
    "message": "User safe error message",
    "details": {},
    "retryable": false,
    "step": "pipeline_step_name"
  }
}
```

- `success`: always `false` on errors.
- `error.code`: stable machine-readable code for frontend handling.
- `error.message`: safe text for user display.
- `error.details`: optional diagnostics for debugging (no secrets/tokens).
- `error.retryable`: whether frontend should show retry.
- `error.step`: failing pipeline step identifier.

## Pipeline Progress Metadata

When available, progress metadata is returned in `error.details.progress`:

```json
{
  "completedSteps": ["renderform_render", "wordpress_media_upload"],
  "failedStep": "wordpress_create_product",
  "canRetryFromStep": "wordpress_create_product"
}
```

## Error Codes

- `RENDER_TIMEOUT`: RenderForm network/upstream timeout/failure.
- `MEDIA_UPLOAD_INVALID_INPUT`: invalid media input or upload failure.
- `WORDPRESS_CREATE_FAILED`: WordPress product creation failed.
- `METRICOOL_SCHEDULE_FAILED`: Metricool scheduling failed.
- `IMAGE_INVALID_OR_BLANK`: image is invalid/corrupt/blank/near-black.
- `UPSTREAM_RATE_LIMITED`: upstream returned `429`.
- `INVALID_KEYWORD_FORMAT`: keyword was URL-like or empty after normalization.
- `VALIDATION_ERROR`: request schema validation failure.
- `INTERNAL_SERVER_ERROR`: unhandled server failure.

## Retry Policy

Server-side retries are applied only to transient failures:

- timeouts and transport/network errors
- HTTP `429`
- HTTP `5xx` (`500`, `502`, `503`, `504`)

Default policy:

- `max_attempts = 3`
- backoff with jitter: `0.5s`, `1.0s`, `2.0s` (+ small jitter)

No retry for:

- `400`, `401`, `403`, `404`, `422`

## Keyword Guard (CJ/Impact Search)

Keyword search rejects URL-like or blank keywords with `422`:

- `code`: `INVALID_KEYWORD_FORMAT`
- `message`: `Enter related keyword (2-3 words). URLs are not supported for keyword search.`

Applied to:

- `POST /api/v1/cj/ads/products/query` (`keywords[]`)
- `GET /api/v1/impact/catalogs/{catalog_id}/items` (`keyword`)
- `GET /api/v1/impact/catalogs/item-search` (`keyword`)
