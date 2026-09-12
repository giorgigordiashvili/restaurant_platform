#!/usr/bin/env python3
"""
AiMenu print bridge.

Runs on any computer next to a receipt / kitchen printer (Windows PC, old
laptop, Raspberry Pi). Polls the AiMenu server for print jobs meant for
this printer and streams the ready-made ESC/POS bytes to the device over
USB, LAN, serial/Bluetooth-SPP or the Windows spooler. No fonts, no menu
data: everything is rendered on the server, so Georgian text prints on any
cheap ESC/POS printer.

    python aimenu_print_bridge.py                # uses ./bridge.toml
    python aimenu_print_bridge.py --config /etc/aimenu/bridge.toml
    python aimenu_print_bridge.py --list-usb     # find the printer's VID:PID
    python aimenu_print_bridge.py --self-test    # print a plain-ASCII page without the server

bridge.toml:

    [bridge]
    server_url = "https://admin.aimenu.ge"
    key = "the bridge key from Dashboard -> Printers"
    printer = "usb://0x04b8:0x0202"   # net://192.168.1.50:9100 | serial:///dev/rfcomm0:9600 | win://POS-80 | file:///tmp/out.bin
    paper = "80"
"""

from __future__ import annotations

import argparse
import base64
import logging
import socket
import sys
import time
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

import requests

LOG = logging.getLogger("aimenu-bridge")
WAIT = 15  # long-poll seconds
RETRY_SLEEP = 3


# ── transports ────────────────────────────────────────────────────────────


class Transport:
    def write(self, data: bytes) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:
        pass


class NetTransport(Transport):
    """Raw TCP port 9100 (every LAN printer)."""

    def __init__(self, host: str, port: int = 9100):
        self.host, self.port = host, port

    def write(self, data: bytes) -> None:
        with socket.create_connection((self.host, self.port), timeout=10) as s:
            s.sendall(data)


class UsbTransport(Transport):
    """USB via python-escpos (needs libusb; on Windows install the WinUSB driver with Zadig)."""

    def __init__(self, vid: int | None, pid: int | None):
        from escpos.printer import Usb  # type: ignore

        if vid is None or pid is None:
            vid, pid = _first_usb_printer()
        self.dev = Usb(vid, pid, timeout=0, profile="default")

    def write(self, data: bytes) -> None:
        self.dev._raw(data)


class SerialTransport(Transport):
    """Serial or Bluetooth SPP (/dev/rfcomm0, COM3)."""

    def __init__(self, port: str, baud: int = 9600):
        import serial  # type: ignore

        self.ser = serial.Serial(port, baud, timeout=5)

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()


class WindowsTransport(Transport):
    """A printer installed in Windows (RAW datatype through the spooler)."""

    def __init__(self, name: str):
        import win32print  # type: ignore

        self.win32print, self.name = win32print, name

    def write(self, data: bytes) -> None:
        h = self.win32print.OpenPrinter(self.name)
        try:
            self.win32print.StartDocPrinter(h, 1, ("AiMenu", None, "RAW"))
            self.win32print.StartPagePrinter(h)
            self.win32print.WritePrinter(h, data)
            self.win32print.EndPagePrinter(h)
            self.win32print.EndDocPrinter(h)
        finally:
            self.win32print.ClosePrinter(h)


class FileTransport(Transport):
    """Append to a file -- for testing without a printer."""

    def __init__(self, path: str):
        self.path = Path(path)

    def write(self, data: bytes) -> None:
        with self.path.open("ab") as f:
            f.write(data)


def _first_usb_printer():
    import usb.core  # type: ignore

    for dev in usb.core.find(find_all=True):
        for cfg in dev:
            for intf in cfg:
                if intf.bInterfaceClass == 7:  # printer class
                    return dev.idVendor, dev.idProduct
    raise SystemExit("No USB printer found; pass usb://VID:PID (see --list-usb).")


def list_usb() -> None:
    import usb.core  # type: ignore

    for dev in usb.core.find(find_all=True):
        cls = {cfg_intf.bInterfaceClass for cfg in dev for cfg_intf in cfg}
        tag = " <- printer" if 7 in cls else ""
        print(f"usb://0x{dev.idVendor:04x}:0x{dev.idProduct:04x}{tag}")


def make_transport(uri: str) -> Transport:
    scheme, _, rest = uri.partition("://")
    if scheme == "net":
        host, _, port = rest.partition(":")
        return NetTransport(host, int(port or 9100))
    if scheme == "usb":
        if not rest:
            return UsbTransport(None, None)
        vid, _, pid = rest.partition(":")
        return UsbTransport(int(vid, 16), int(pid, 16))
    if scheme == "serial":
        port, _, baud = rest.rpartition(":") if rest.count(":") else (rest, "", "")
        if not port:
            port, baud = rest, ""
        return SerialTransport(port, int(baud or 9600))
    if scheme == "win":
        return WindowsTransport(rest)
    if scheme == "file":
        return FileTransport(rest)
    raise SystemExit(f"Unknown printer URI: {uri}")


# ── bridge loop ───────────────────────────────────────────────────────────


class Bridge:
    def __init__(self, server_url: str, key: str, printer_uri: str):
        self.base = server_url.rstrip("/") + "/api/v1/print-bridge"
        self.session = requests.Session()
        self.session.headers["X-Bridge-Key"] = key
        self.printer_uri = printer_uri
        self.transport: Transport | None = None

    def _transport(self) -> Transport:
        if self.transport is None:
            self.transport = make_transport(self.printer_uri)
        return self.transport

    def ping(self) -> dict:
        r = self.session.get(f"{self.base}/ping/", timeout=15)
        r.raise_for_status()
        return r.json()

    def run(self) -> None:
        info = self.ping()
        LOG.info(
            "Connected: %s / %s (%s mm) at %s", info["restaurant"], info["printer"], info["paper"], self.printer_uri
        )
        while True:
            try:
                r = self.session.get(f"{self.base}/jobs/next/", params={"wait": WAIT}, timeout=WAIT + 15)
                if r.status_code == 204:
                    continue
                if r.status_code == 401:
                    LOG.error("Bridge key rejected; check bridge.toml")
                    time.sleep(30)
                    continue
                r.raise_for_status()
                job = r.json()
                self.print_job(job)
            except requests.RequestException as exc:
                LOG.warning("Server unreachable (%s); retrying", exc)
                time.sleep(RETRY_SLEEP)
            except KeyboardInterrupt:
                LOG.info("Stopping")
                return

    def print_job(self, job: dict) -> None:
        data = base64.b64decode(job["escpos_b64"])
        copies = int(job.get("copies") or 1)
        try:
            for _ in range(copies):
                self._transport().write(data)
        except Exception as exc:  # noqa: BLE001 - report whatever the device said
            LOG.error("Print failed for %s: %s", job.get("title"), exc)
            self.transport = None  # reconnect next time
            self.session.post(f"{self.base}/jobs/{job['id']}/failed/", json={"error": str(exc)[:300]}, timeout=15)
            time.sleep(RETRY_SLEEP)
            return
        LOG.info("Printed %s (%s)", job.get("title"), job.get("kind"))
        self.session.post(f"{self.base}/jobs/{job['id']}/done/", timeout=15)


def self_test(printer_uri: str) -> None:
    esc = b"\x1b@AiMenu print bridge\nself-test OK\n\n\n\n\x1dV\x01"
    make_transport(printer_uri).write(esc)
    print("Sent a self-test page.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AiMenu print bridge")
    parser.add_argument("--config", default="bridge.toml")
    parser.add_argument("--list-usb", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if args.list_usb:
        list_usb()
        return 0
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path} (copy bridge.example.toml)", file=sys.stderr)
        return 2
    cfg = tomllib.loads(cfg_path.read_text())["bridge"]
    if args.self_test:
        self_test(cfg["printer"])
        return 0
    Bridge(cfg["server_url"], cfg["key"], cfg["printer"]).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
