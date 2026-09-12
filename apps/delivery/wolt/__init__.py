"""
Wolt POS integration (pos-integration-service).

Written against the public developer.wolt.com documentation; NOT exercised
against Wolt's development environment yet (no client credentials at the
time of writing). Everything below is pinned by fixtures in
tests/delivery/fixtures/wolt.

VERIFY ON DEV
* OAuth: ``grant_type=refresh_token`` with Basic auth (client_id:client_secret); refresh tokens are
  single-use -- the rotated token is persisted on the link. First-time bootstrap uses the one-time
  ``authorization_code`` Wolt hands over (``redirect_uri`` may be required; ``WOLT_OAUTH_REDIRECT_URI``).
* Legacy ``WOLT-API-KEY`` header is kept as an alternative for venues onboarded before OAuth.
* Order payload amounts are minor units (``price.amount`` / ``base_price.amount``); options carry a
  unit ``price`` and ``count``. ``pos_id`` / ``value_pos_id`` echo the ``external_data`` we upload.
* Menu upload: ``POST /v1/restaurants/{venueId}/menu`` -- ``price`` in major units; ``external_data``
  carries our ids; response is 202 with no transaction id (no status endpoint -> marked success).
* Item / option updates: ``PATCH /venues/{venueId}/items`` with ``{"data": [{"external_id", "enabled",
  "in_stock", "price"}]}`` and ``PATCH /venues/{venueId}/options/values`` (same shape, minor-unit price).
* ``PATCH /venues/{venueId}/online`` body ``{"status": "ONLINE" | "OFFLINE", "until": ISO-8601 | null}``.
* Webhook: ``WOLT-SIGNATURE`` hex HMAC-SHA256 of the raw body with the secret we hand Wolt;
  body ``{"id", "type": "order.notification", "order": {"id", "venue_id", "status", "resource_url"}, "created_at"}``;
  statuses CREATED / ACKNOWLEDGED / PRODUCTION / READY / REJECTED / DELIVERED.
* ``PUT /orders/{id}/delivered`` only for takeaway / eat-in / self-delivery orders; courier orders are
  marked delivered by Wolt.
* Pre-orders: ``confirm-preorder`` on receipt, then the PRODUCTION notification starts the kitchen.
"""
