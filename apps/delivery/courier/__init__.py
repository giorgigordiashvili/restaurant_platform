"""
Courier hand-off: the restaurant keeps the order (taken on its own page) and
only buys the ride from Wolt Drive or Glovo On-Demand (Delivery Hero ODR),
or dispatches one of its own couriers.

Written against the public docs; NOT exercised against either sandbox (no
merchant keys at the time of writing). Fixtures under tests/ordering/fixtures.

VERIFY ON STAGE — Wolt Drive (https://developer.wolt.com/docs/drive)
* Base ``https://daas-public-api.wolt.com`` (dev ``https://daas-public-api.development.dev.woltapi.com``),
  ``Authorization: Bearer <merchant api key>``.
* ``POST /v1/venues/{venue_id}/shipment-promises`` → ``{id, price{amount(minor), currency}, pickup{eta}, dropoff{eta},
  valid_until}``; ``POST /v1/venues/{venue_id}/deliveries`` with ``shipment_promise_id`` → ``{id, status, tracking{url},
  price, pickup{eta}, dropoff{eta}, wolt_order_reference_id}``; ``PATCH /order/{wolt_order_reference_id}/status/cancel``.
* ``order_number`` is at most 5 characters (we send the tail of our order number).
* Webhooks are a JWT (HS256, the merchant ``client_secret``) in the body ``{token}`` or raw; ``type`` in
  ``order.received / order.rejected / order.pickup_eta_updated / order.pickup_started / order.picked_up /
  order.dropoff_completed / order.delivered / order.dropoff_eta_updated / order.cancelled``.

VERIFY ON STAGE — Glovo On-Demand (Delivery Hero ODR, https://ondemand-api-glovoapp.deliveryhero.io)
* ``/{country_code}/api/v1`` (``ge``); staging ``https://api-infra-eu-central-1.stg.ondemandrider.net``.
* OAuth ``https://sts.deliveryhero.io/oauth2/token`` ``grant_type=client_credentials`` with
  ``client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`` and a JWT assertion signed with
  the merchant secret (HS256, ``iss``/``sub`` = client id, ``aud`` = the STS url).
* ``POST /quotes`` → ``{quoteId, quotePrice{amount, currencyCode}, estimatedTimeOfArrival..}``;
  ``POST /quotes/{quoteId}`` creates the order → ``{trackingNumber, orderCode, state, ...}``;
  ``GET /orders/{id}``, ``DELETE /orders/{id}``, ``GET /orders/{id}/coordinates``.
* Callbacks carry ``X-Signature-SHA256`` = hex HMAC-SHA256 of the raw body with the callback secret;
  ``state`` in NEW / SCHEDULED / ACTIVE / PICKED_UP / DELIVERED / CANCELLED / RETURNED.
"""
