"""
Bank of Georgia ECR adapter — placeholder.

VERIFY WITH BANK: BOG does not publish the till ↔ terminal protocol. When the
bank supplies the integration kit, implement ``sale(amount, currency)`` /
``refund(amount, rrn)`` in the *bridge* (tools/terminal_bridge/adapters/bog.py)
and describe the expected result shape here. The server never talks to the
device; it only exchanges JSON jobs with the bridge.
"""

JOB_SHAPE = {
    "id": "uuid",
    "kind": "sale | refund",
    "amount": "12.50",
    "currency": "GEL",
    "protocol": "bog",
    "device": {"device": "tcp://192.168.1.60:8000"},
}
RESULT_SHAPE = {"status": "approved | declined | cancelled | failed", "auth_code": "", "card_mask": "", "rrn": ""}
