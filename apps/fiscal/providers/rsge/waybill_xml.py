"""
RS.ge WayBillService ``WAYBILL`` element builder.

Field names follow the published WayBillService documentation (``save_waybill``).
UNVERIFIED AGAINST THE LIVE SERVICE: no RS.ge test account was available
when this was written; ``tests/fiscal/fixtures/waybill_expected.xml`` pins
the structure we send.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal

WAYBILL_TYPES = {1: "internal", 2: "with vehicle", 3: "without vehicle", 4: "distribution", 5: "return", 6: "sub"}
VAT_TYPE = {0: "standard", 1: "zero", 2: "exempt"}


def _text(parent, tag, value=""):
    el = ET.SubElement(parent, tag)
    el.text = "" if value is None else str(value)
    return el


def build_waybill_xml(document) -> bytes:
    """``document.payload`` carries the header + goods; returns the WAYBILL element as UTF-8 bytes."""
    p = document.payload or {}
    root = ET.Element("WAYBILL")
    ET.SubElement(root, "SUB_WAYBILLS")
    goods_list = ET.SubElement(root, "GOODS_LIST")
    for g in p.get("goods", []):
        goods = ET.SubElement(goods_list, "GOODS")
        _text(goods, "ID", g.get("id", 0))
        _text(goods, "W_NAME", g.get("name", ""))
        _text(goods, "UNIT_ID", g.get("unit_id", 99))
        _text(goods, "UNIT_TXT", g.get("unit", ""))
        _text(goods, "QUANTITY", Decimal(str(g.get("quantity", 0))))
        _text(goods, "PRICE", Decimal(str(g.get("price", 0))))
        _text(goods, "AMOUNT", Decimal(str(g.get("amount", 0))))
        _text(goods, "BAR_CODE", g.get("bar_code", ""))
        _text(goods, "A_ID", g.get("a_id", ""))
        _text(goods, "VAT_TYPE", g.get("vat_type", 0))
    _text(root, "ID", p.get("id", 0))
    _text(root, "TYPE", p.get("type", 3))
    _text(root, "BUYER_TIN", p.get("buyer_tin", ""))
    _text(root, "CHEK_BUYER_TIN", 1)
    _text(root, "BUYER_NAME", p.get("buyer_name", ""))
    _text(root, "START_ADDRESS", p.get("start_address", ""))
    _text(root, "END_ADDRESS", p.get("end_address", ""))
    _text(root, "DRIVER_TIN", p.get("driver_tin", ""))
    _text(root, "CHEK_DRIVER_TIN", 1 if p.get("driver_tin") else 0)
    _text(root, "DRIVER_NAME", p.get("driver_name", ""))
    _text(root, "TRANSPORT_COAST", p.get("transport_cost", 0))
    _text(root, "RECEPTION_INFO", p.get("reception_info", ""))
    _text(root, "RECEIVER_INFO", p.get("receiver_info", ""))
    _text(root, "DELIVERY_DATE", p.get("delivery_date", ""))
    _text(root, "STATUS", 0)
    _text(root, "SELER_UN_ID", p.get("seller_un_id", ""))
    _text(root, "ACTIVATE_DATE", "")
    _text(root, "PAR_ID", "")
    _text(root, "FULL_AMOUNT", Decimal(str(p.get("full_amount", 0))))
    _text(root, "CAR_NUMBER", p.get("car_number", ""))
    _text(root, "WAYBILL_NUMBER", p.get("waybill_number", ""))
    _text(root, "S_USER_ID", p.get("s_user_id", ""))
    _text(root, "BEGIN_DATE", p.get("begin_date", ""))
    _text(root, "CLOSE_DATE", "")
    _text(root, "TRANS_ID", p.get("trans_id", 2))
    _text(root, "TRANS_TXT", p.get("trans_txt", ""))
    _text(root, "COMMENT", p.get("comment", ""))
    return ET.tostring(root, encoding="utf-8")
