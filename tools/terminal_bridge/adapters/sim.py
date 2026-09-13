"""Simulator: approves every sale after a short pause (training / demos, no device)."""

import time


def sale(device, amount, currency, reference):
    time.sleep(2)
    return {"status": "approved", "auth_code": "SIM123", "card_mask": "**** 0000", "rrn": f"SIM{int(time.time())}"}


def refund(device, amount, currency, rrn):
    time.sleep(1)
    return {"status": "approved", "auth_code": "SIMREF", "rrn": rrn or ""}
