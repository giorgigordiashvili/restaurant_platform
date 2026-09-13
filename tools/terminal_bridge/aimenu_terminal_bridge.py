#!/usr/bin/env python3
"""
AiMenu terminal bridge.

Runs on the till PC next to a bank ECR terminal. Long-polls the AiMenu server
for card-payment jobs meant for this terminal, drives the device through a
bank adapter, and posts the result back. The server never touches the device.

    python aimenu_terminal_bridge.py                 # uses ./bridge.toml
    python aimenu_terminal_bridge.py --config /etc/aimenu/terminal.toml
    python aimenu_terminal_bridge.py --simulate      # approve every job (demo / training)

bridge.toml:

    [bridge]
    server_url = "https://admin.aimenu.ge"
    key = "the bridge key from Dashboard -> Card terminals"
    protocol = "bog"            # bog | tbc | sim
    device = "tcp://192.168.1.60:8000"   # or serial:///dev/ttyUSB0:115200

Adapters live in ./adapters/<protocol>.py and expose
``sale(device, amount, currency, reference) -> dict`` and
``refund(device, amount, currency, rrn) -> dict`` returning
``{"status": "approved|declined|cancelled|failed", "auth_code": "", "card_mask": "", "rrn": "", "error": ""}``.
The BOG and TBC adapters are placeholders until the banks hand over their ECR kits.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

import requests

LOG = logging.getLogger("aimenu-terminal")
WAIT = 15
RETRY_SLEEP = 3


def load_config(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)["bridge"]


def load_adapter(protocol: str):
    sys.path.insert(0, str(Path(__file__).parent))
    return importlib.import_module(f"adapters.{protocol}")


def run(cfg: dict, *, simulate: bool = False) -> None:
    server = cfg["server_url"].rstrip("/")
    headers = {"X-Bridge-Key": cfg["key"]}
    adapter = load_adapter("sim" if simulate else cfg.get("protocol", "sim"))
    device = cfg.get("device", "")
    ping = requests.get(f"{server}/api/v1/terminal-bridge/ping/", headers=headers, timeout=10)
    ping.raise_for_status()
    LOG.info("connected: %s", ping.json())
    while True:
        try:
            r = requests.get(
                f"{server}/api/v1/terminal-bridge/jobs/next/", headers=headers, params={"wait": WAIT}, timeout=WAIT + 10
            )
            if r.status_code == 204:
                continue
            r.raise_for_status()
            job = r.json()
        except requests.RequestException as exc:
            LOG.warning("poll failed: %s", exc)
            time.sleep(RETRY_SLEEP)
            continue
        LOG.info("job %s: %s %s %s", job["id"], job["kind"], job["amount"], job["currency"])
        try:
            if job["kind"] == "refund":
                result = adapter.refund(device, job["amount"], job["currency"], job.get("rrn", ""))
            else:
                result = adapter.sale(device, job["amount"], job["currency"], job.get("reference", ""))
        except Exception as exc:  # noqa: BLE001 - the terminal must never take the bridge down
            LOG.exception("adapter failed")
            result = {"status": "failed", "error": str(exc)[:300]}
        try:
            requests.post(
                f"{server}/api/v1/terminal-bridge/jobs/{job['id']}/result/", headers=headers, json=result, timeout=15
            ).raise_for_status()
        except requests.RequestException as exc:
            LOG.error("could not report result for %s: %s (%s)", job["id"], exc, result)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AiMenu terminal bridge")
    parser.add_argument("--config", default="bridge.toml")
    parser.add_argument("--simulate", action="store_true", help="approve every job without a device")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    cfg = load_config(Path(args.config))
    while True:
        try:
            run(cfg, simulate=args.simulate)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001
            LOG.error("bridge loop crashed: %s -- restarting in %ss", exc, RETRY_SLEEP)
            time.sleep(RETRY_SLEEP)


if __name__ == "__main__":
    sys.exit(main())
