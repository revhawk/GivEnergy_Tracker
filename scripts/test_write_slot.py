#!/usr/bin/env python3
"""End-to-end write-path test for GivTCP.

Sets a charge slot on the GivEnergy inverter via GivTCP REST, prompts you to
verify it in the GivEnergy app, then clears the slot when you press ENTER.

Runs standalone from any machine on your LAN that can reach GivTCP. Requires
only the `requests` package (no add-on rebuild needed).

Usage:
    python3 scripts/test_write_slot.py
    python3 scripts/test_write_slot.py --url http://192.168.1.96:6345
    python3 scripts/test_write_slot.py --start-in-minutes 120 --duration-minutes 30
    python3 scripts/test_write_slot.py --dry-run       # print calls, don't send

Safety:
- Prompts before writing anything.
- Always clears both charge slots on exit (Ctrl-C, normal exit, or exception).
- Idempotent: rerunning after a partial run just re-clears.

This DOES touch your real inverter. If your production tracker is running,
its next tick may re-apply its own plan. Consider pausing the add-on first.
"""
from __future__ import annotations

import argparse
import atexit
import signal
import sys
import time
from datetime import datetime, timedelta

import requests


DEFAULT_URL = "http://192.168.1.96:6345"


def _post(url: str, path: str, payload: dict, dry_run: bool) -> None:
    full = f"{url.rstrip('/')}{path}"
    if dry_run:
        print(f"  [DRY-RUN] POST {full}  {payload}")
        return
    r = requests.post(full, json=payload, timeout=10)
    r.raise_for_status()
    print(f"  ✓ POST {path}  {payload}  → {r.status_code}")


def set_charge_slot(url: str, start_hhmm: str, end_hhmm: str, target: int, dry_run: bool) -> None:
    print(f"Setting charge slot: {start_hhmm} → {end_hhmm} (target {target}%)")
    _post(url, "/setChargeEnable", {"state": "enable"}, dry_run)
    _post(url, "/setChargeTarget", {"chargeToPercent": str(target)}, dry_run)
    _post(url, "/setChargeSlot1",
          {"start": start_hhmm, "finish": end_hhmm, "chargeToPercent": str(target)}, dry_run)
    _post(url, "/setChargeSlot2",
          {"start": "0000", "finish": "0000", "chargeToPercent": "0"}, dry_run)


def clear_charge_slots(url: str, dry_run: bool = False) -> None:
    print("Clearing both charge slots and disabling grid charging...")
    try:
        _post(url, "/setChargeSlot1", {"start": "0000", "finish": "0000", "chargeToPercent": "0"}, dry_run)
        _post(url, "/setChargeSlot2", {"start": "0000", "finish": "0000", "chargeToPercent": "0"}, dry_run)
        _post(url, "/setChargeEnable", {"state": "disable"}, dry_run)
    except Exception as e:
        print(f"  ! clear failed: {e}", file=sys.stderr)


def snap_to_half_hour(dt: datetime) -> datetime:
    """Snap DOWN to the nearest 30-min boundary (inverter operates on half-hour slots)."""
    return dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"GivTCP REST endpoint (default: {DEFAULT_URL})")
    ap.add_argument("--start-in-minutes", type=int, default=60,
                    help="Minutes from now for the test slot to start (default: 60)")
    ap.add_argument("--duration-minutes", type=int, default=30,
                    help="Slot duration in minutes (default: 30)")
    ap.add_argument("--target-percent", type=int, default=100,
                    help="Charge target %% (default: 100)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be sent without actually calling GivTCP")
    ap.add_argument("--auto-clear-after-seconds", type=int, default=0,
                    help="If >0, clear the slot automatically after N seconds instead of waiting for ENTER")
    args = ap.parse_args()

    now = datetime.now()
    start = snap_to_half_hour(now + timedelta(minutes=args.start_in_minutes))
    end = start + timedelta(minutes=args.duration_minutes)
    end = snap_to_half_hour(end)
    if end <= start:
        end = start + timedelta(minutes=30)

    start_hhmm = start.strftime("%H%M")
    end_hhmm = end.strftime("%H%M")

    print("=" * 60)
    print(" GivTCP write-path end-to-end test")
    print("=" * 60)
    print(f"Endpoint:  {args.url}")
    print(f"Now:       {now.strftime('%H:%M:%S')}")
    print(f"Slot:      {start.strftime('%H:%M')} → {end.strftime('%H:%M')}  ({args.duration_minutes} min)")
    print(f"Target:    {args.target_percent}%")
    print(f"Dry-run:   {args.dry_run}")
    print()

    if not args.dry_run:
        print("This will write to your live inverter. If your GivEnergy Tariff")
        print("Optimiser add-on is running, its next tick may overwrite this plan.")
        print("Consider stopping the add-on first.")
        print()
        confirm = input("Proceed? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return

    # Register a clean-up guaranteed to run
    def _cleanup(*_):
        print()
        clear_charge_slots(args.url, dry_run=args.dry_run)
        sys.exit(0)

    atexit.register(clear_charge_slots, args.url, args.dry_run)
    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    print()
    set_charge_slot(args.url, start_hhmm, end_hhmm, args.target_percent, args.dry_run)

    print()
    print("*" * 60)
    print(" Open the GivEnergy app now.")
    print(f" Confirm that Charge Slot 1 shows: {start.strftime('%H:%M')} → {end.strftime('%H:%M')} @ {args.target_percent}%")
    print(f" and Charge Slot 2 shows: 00:00 → 00:00 (cleared)")
    print("*" * 60)
    print()

    if args.auto_clear_after_seconds > 0:
        print(f"Auto-clearing in {args.auto_clear_after_seconds}s...")
        time.sleep(args.auto_clear_after_seconds)
    else:
        input("Press ENTER once you've verified in the app (this clears the slot)... ")

    # atexit will fire clear_charge_slots
    atexit.unregister(clear_charge_slots)
    clear_charge_slots(args.url, dry_run=args.dry_run)

    print()
    print("Done. Verify in the app that both slots are back to 00:00 → 00:00.")


if __name__ == "__main__":
    main()
