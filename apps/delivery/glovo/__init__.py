"""
Glovo Partners API integration.

Written against the public Partners documentation; NOT exercised against
Glovo's stage environment yet (no credentials at the time of writing).
Everything below is pinned by fixtures in tests/delivery/fixtures/glovo.

VERIFY ON STAGE
* ``Authorization`` header: raw token (default) or ``Bearer`` -- ``GlovoConfig.auth_scheme``.
* Whether ``ACCEPTED`` is accepted for the store (order acceptance must be enabled in Glovo Manager).
* Menu JSON field names (``price_impact``, ``attributes_groups``, ``sections``) -- table-driven in menu.py.
* ``GET /webhook/stores/{storeId}/menu/{transactionId}`` path for the upload status.
* Amounts in order notifications: minor units (``GLOVO_PRICES_IN_MINOR_UNITS``).
* Webhook payload drift: the parser is tolerant and keeps the raw body in ``platform_data``.
"""
