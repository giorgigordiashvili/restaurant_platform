"""
Terminal providers. ``manual`` needs nothing; ``bog_link`` and ``tbc_tpay``
create a hosted payment (link + QR) with the terminal's own merchant keys;
``ecr_bridge`` hands the amount to a bridge process on the till PC that
talks to the physical terminal.

VERIFY WITH BANK — Bank of Georgia Payment Manager (https://api.bog.ge/docs/en/payments)
* Same ecommerce order API as online checkout: ``POST /payments/v1/ecommerce/orders`` with
  ``callback_url``, ``external_order_id``, ``purchase_units`` → ``id`` + ``_links.redirect.href``.
  Webhook ``Callback-Signature`` (RSA-SHA256 over the raw body, BOG public key), body
  ``{event, body: {order_id, external_order_id, order_status: {key}}}``; keys completed / rejected / refunded.
* ``GET /payments/v1/receipt/{order_id}`` is the poll; ``POST /payments/v1/payment/refund/{order_id}``.
* BOG does not publish an ECR (till ↔ terminal) protocol; ``ecr/bog.py`` is a stub until the bank
  hands over the integration spec.

VERIFY WITH BANK — TBC Checkout / TPAY (https://developers.tbcbank.ge)
* ``POST /v1/tpay/access-token`` (header ``apikey``, form ``client_Id`` / ``client_secret``) → bearer for 1 day.
* ``POST /v1/tpay/payments`` body ``{amount:{currency,total}, returnurl, callbackUrl, extra, expirationMinutes,
  methods:[5,7], language}`` → ``{payId, status, links:[{uri, rel:"approval_url"}]}``.
* ``GET /v1/tpay/payments/{payId}`` → ``status`` in Created / Processing / Succeeded / Failed / Expired /
  WaitingConfirm / CancelPaymentProcessing / Returned; ``POST /v1/tpay/payments/{payId}/cancel`` ``{amount}``.
* Callback: ``POST callbackUrl`` with ``{"PaymentId": ...}`` — never trusted, always re-read via GET.
* TBC ECR: not public; ``ecr/tbc.py`` is a stub.
"""
