"""
TBC Bank ECR adapter — placeholder (see ecr/bog.py). VERIFY WITH BANK.
"""

JOB_SHAPE = {
    "id": "uuid",
    "kind": "sale | refund",
    "amount": "12.50",
    "currency": "GEL",
    "protocol": "tbc",
    "device": {"device": "serial:///dev/ttyUSB0:115200"},
}
RESULT_SHAPE = {"status": "approved | declined | cancelled | failed", "auth_code": "", "card_mask": "", "rrn": ""}
