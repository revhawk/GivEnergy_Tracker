#!/usr/bin/env python3
"""Check live Octopus Energy Account Tariffs (Import, Export, Gas).

Connects to Octopus Energy REST API using account credentials from config.py or environment:
1. Queries /v1/accounts/{account_id}/ for active meter points and tariff agreements.
2. Fetches current unit rates for Electricity Import, Electricity Export, and Gas.
3. Compares live account tariffs with local config.py to verify synchronization.
"""

import os
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

# Add ha-addon directory to sys.path to access config.py and tariffs.py
ROOT = Path(__file__).resolve().parent.parent
HA_ADDON = ROOT / "ha-addon"
sys.path.insert(0, str(HA_ADDON))

try:
    import config
except ImportError:
    config = None


def main():
    print("=" * 70)
    print(" 🐙 OCTOPUS ENERGY ACCOUNT TARIFF AUDIT")
    print("=" * 70)

    api_key = (
        os.environ.get("OCTOPUS_API_KEY", "")
        or (getattr(config, "OCTOPUS_API_KEY", "") if config else "")
    )
    account_id = (
        os.environ.get("OCTOPUS_ACCOUNT_ID", "")
        or (getattr(config, "OCTOPUS_ACCOUNT_ID", "") if config else "")
        or (getattr(config, "OCTOPUS_ACCOUNT_NUMBER", "") if config else "")
    )

    if not api_key or not account_id:
        print("✗ ERROR: OCTOPUS_API_KEY and OCTOPUS_ACCOUNT_ID must be set in config.py or env.")
        sys.exit(1)

    print(f"Account ID:  {account_id}")
    masked_key = api_key[:7] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
    print(f"API Key:     {masked_key}\n")

    account_url = f"https://api.octopus.energy/v1/accounts/{account_id}/"
    print(f"Querying Octopus Account API: {account_url}")

    try:
        resp = requests.get(account_url, auth=(api_key, ""), timeout=15)
        resp.raise_for_status()
        acc_data = resp.json()
    except Exception as e:
        print(f"\n✗ Failed to fetch account data from Octopus API: {e}")
        print("\nChecking public rate endpoints directly using config.py settings...")
        check_direct_rates()
        return

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")

    properties = acc_data.get("properties", [])
    if not properties:
        print("No properties found in account response.")
        return

    prop = properties[0]
    elec_points = prop.get("electricity_meter_points", [])
    gas_points = prop.get("gas_meter_points", [])

    print("\n" + "─" * 70)
    print(" ⚡ ELECTRICITY TARIFFS")
    print("─" * 70)

    for mp in elec_points:
        mpan = mp.get("mpan")
        is_export = mp.get("is_export", False)
        point_type = "EXPORT" if is_export else "IMPORT"

        print(f"\n[{point_type}] MPAN: {mpan}")
        agreements = mp.get("agreements", [])
        active_agreements = [
            a for a in agreements
            if a.get("valid_from", "") <= now_iso <= (a.get("valid_to") or "9999")
        ]

        if not active_agreements and agreements:
            # Sort by valid_from descending to show the newest
            agreements.sort(key=lambda x: x.get("valid_from", ""), reverse=True)
            active_agreements = [agreements[0]]
            print("  (Showing newest agreement on record):")

        for a in active_agreements:
            tariff_code = a.get("tariff_code", "")
            valid_from = a.get("valid_from", "")
            valid_to = a.get("valid_to") or "Open-ended (Active)"
            print(f"  • Tariff Code: {tariff_code}")
            print(f"  • Agreement:   {valid_from} to {valid_to}")

            # Derive product code: strip prefix (E-1R-) and trailing region (-E)
            parts = tariff_code.split("-")
            if len(parts) >= 4:
                product_code = "-".join(parts[2:-1]) if parts[0] in ("E", "G") and parts[1] == "1R" else "-".join(parts[:-1])
            else:
                product_code = tariff_code

            # Fetch live unit rates for this tariff
            rates_url = f"https://api.octopus.energy/v1/products/{product_code}/electricity-tariffs/{tariff_code}/standard-unit-rates/"
            try:
                r_resp = requests.get(rates_url, timeout=10)
                if r_resp.status_code == 200:
                    r_data = r_resp.json().get("results", [])
                    if r_data:
                        # Find currently active rate slot
                        cur_slot = next(
                            (s for s in r_data if s["valid_from"] <= now_iso <= (s.get("valid_to") or "9999")),
                            r_data[0]
                        )
                        print(f"  • Current Rate: {cur_slot['value_inc_vat']:.2f}p / kWh")
                        if len(r_data) > 1:
                            rates_set = {s["value_inc_vat"] for s in r_data}
                            min_r = min(rates_set)
                            max_r = max(rates_set)
                            print(f"  • Rate Range:   {min_r:.2f}p (off-peak) to {max_r:.2f}p (peak)")
                else:
                    print(f"  • Live Rates:  Could not query endpoint (status {r_resp.status_code})")
            except Exception as re_err:
                print(f"  • Live Rates:  {re_err}")

    print("\n" + "─" * 70)
    print(" 🔥 GAS TARIFFS")
    print("─" * 70)

    for gp in gas_points:
        mprn = gp.get("mprn")
        print(f"\n[GAS] MPRN: {mprn}")
        agreements = gp.get("agreements", [])
        active_agreements = [
            a for a in agreements
            if a.get("valid_from", "") <= now_iso <= (a.get("valid_to") or "9999")
        ]
        if not active_agreements and agreements:
            agreements.sort(key=lambda x: x.get("valid_from", ""), reverse=True)
            active_agreements = [agreements[0]]
            print("  (Showing newest agreement on record):")

        for a in active_agreements:
            tariff_code = a.get("tariff_code", "")
            valid_from = a.get("valid_from", "")
            valid_to = a.get("valid_to") or "Open-ended (Active)"
            print(f"  • Tariff Code: {tariff_code}")
            print(f"  • Agreement:   {valid_from} to {valid_to}")

            parts = tariff_code.split("-")
            product_code = "-".join(parts[2:-1]) if len(parts) >= 4 and parts[0] in ("G", "E") else tariff_code
            rates_url = f"https://api.octopus.energy/v1/products/{product_code}/gas-tariffs/{tariff_code}/standard-unit-rates/"
            try:
                r_resp = requests.get(rates_url, timeout=10)
                if r_resp.status_code == 200:
                    r_data = r_resp.json().get("results", [])
                    if r_data:
                        print(f"  • Unit Rate:    {r_data[0]['value_inc_vat']:.2f}p / kWh")
            except Exception:
                pass

    print("\n" + "=" * 70)
    print(" 🛠️  CONFIG.PY SYNCHRONIZATION AUDIT")
    print("=" * 70)
    if config:
        cfg_import_prod = getattr(config, "AGILE_PRODUCT_CODE", "not set")
        cfg_import_tariff = getattr(config, "AGILE_TARIFF_CODE", "not set")
        cfg_export_prod = getattr(config, "EXPORT_PRODUCT_CODE", "not set")
        cfg_export_tariff = getattr(config, "EXPORT_TARIFF_CODE", "not set")
        print(f"  Config Import: {cfg_import_prod} ({cfg_import_tariff})")
        print(f"  Config Export: {cfg_export_prod} ({cfg_export_tariff})")
    print("=" * 70)


def check_direct_rates():
    """Fallback rate check using parameters from config.py."""
    if not config:
        return
    import_prod = getattr(config, "AGILE_PRODUCT_CODE", "")
    import_tariff = getattr(config, "AGILE_TARIFF_CODE", "")
    export_prod = getattr(config, "EXPORT_PRODUCT_CODE", "")
    export_tariff = getattr(config, "EXPORT_TARIFF_CODE", "")

    if import_prod and import_tariff:
        print(f"\n[Import Tariff] {import_prod} / {import_tariff}")
        url = f"https://api.octopus.energy/v1/products/{import_prod}/electricity-tariffs/{import_tariff}/standard-unit-rates/"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                slots = r.json().get("results", [])
                print(f"  ✓ Retrieved {len(slots)} rate slots.")
                if slots:
                    rates = {s['value_inc_vat'] for s in slots}
                    print(f"  • Min: {min(rates):.2f}p | Max: {max(rates):.2f}p | Current: {slots[0]['value_inc_vat']:.2f}p")
        except Exception as e:
            print(f"  ✗ Error: {e}")

    if export_prod and export_tariff:
        print(f"\n[Export Tariff] {export_prod} / {export_tariff}")
        url = f"https://api.octopus.energy/v1/products/{export_prod}/electricity-tariffs/{export_tariff}/standard-unit-rates/"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                slots = r.json().get("results", [])
                print(f"  ✓ Retrieved {len(slots)} export rate slots.")
                if slots:
                    rates = {s['value_inc_vat'] for s in slots}
                    print(f"  • Rates: {sorted(rates)}")
        except Exception as e:
            print(f"  ✗ Error: {e}")


if __name__ == "__main__":
    main()

