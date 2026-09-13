"""
Bank of Georgia ECR adapter — PLACEHOLDER (VERIFY WITH BANK).

BOG does not publish the till <-> terminal protocol. Once the bank provides
the ECR integration kit (usually a TCP/serial message format or a vendor
DLL), implement ``sale`` / ``refund`` here. Until then every job fails with
a clear message so nothing is silently "approved".
"""


def sale(device, amount, currency, reference):
    return {
        "status": "failed",
        "error": "BOG ECR protocol not configured on this bridge (ask the bank for the ECR kit).",
    }


def refund(device, amount, currency, rrn):
    return {"status": "failed", "error": "BOG ECR protocol not configured on this bridge."}
