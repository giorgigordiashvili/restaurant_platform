"""
TBC Bank ECR adapter — PLACEHOLDER (VERIFY WITH BANK). See bog.py.
"""


def sale(device, amount, currency, reference):
    return {
        "status": "failed",
        "error": "TBC ECR protocol not configured on this bridge (ask the bank for the ECR kit).",
    }


def refund(device, amount, currency, rrn):
    return {"status": "failed", "error": "TBC ECR protocol not configured on this bridge."}
