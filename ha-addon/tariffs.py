"""Octopus Energy tariff integration module (Agile import & Outgoing export rates)."""
import logging
import requests
from datetime import datetime, timezone

import config

_export_rate_cache = {"rate": None, "fetched_at": None}


def parse_utc_iso(iso_str):
    """Parse UTC ISO timestamps from Octopus API."""
    iso_str = iso_str.replace('Z', '+00:00')
    return datetime.fromisoformat(iso_str).astimezone(timezone.utc)


def fetch_export_rate():
    """Fetch current Octopus export rate. Cached for 6h."""
    global _export_rate_cache
    now = datetime.now(timezone.utc)
    if _export_rate_cache["fetched_at"]:
        age = (now - _export_rate_cache["fetched_at"]).total_seconds()
        if _export_rate_cache["rate"] is not None and age < 6 * 3600:
            return _export_rate_cache["rate"]

    product = getattr(config, 'EXPORT_PRODUCT_CODE', 'OUTGOING-VAR-24-10-26')
    tariff = getattr(config, 'EXPORT_TARIFF_CODE', 'E-1R-OUTGOING-VAR-24-10-26-E')
    fallback = getattr(config, 'EXPORT_RATE_P_FALLBACK', 12.0)
    url = f"https://api.octopus.energy/v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        results = response.json().get('results', [])
        now_iso = now.isoformat().replace('+00:00', 'Z')
        active = next((r for r in results
                       if r['valid_from'] <= now_iso <= (r.get('valid_to') or '9999')), None)
        rate = active['value_inc_vat'] if active else (results[0]['value_inc_vat'] if results else fallback)
        _export_rate_cache["rate"] = rate
        _export_rate_cache["fetched_at"] = now
        logging.info(f"Export rate: {rate:.2f}p/kWh (from Octopus)")
        return rate
    except Exception as e:
        logging.warning(f"Failed to fetch export rate ({e}); using fallback {fallback}p/kWh")
        return fallback


def fetch_agile_rates():
    """Fetch Octopus Agile Rates."""
    url = f"https://api.octopus.energy/v1/products/{config.AGILE_PRODUCT_CODE}/electricity-tariffs/{config.AGILE_TARIFF_CODE}/standard-unit-rates/"
    logging.info(f"Fetching Octopus Agile pricing from: {url}")
    try:
        response = requests.get(url, auth=(config.OCTOPUS_API_KEY, ""), timeout=15)
        response.raise_for_status()
        data = response.json()

        slots = []
        for r in data.get('results', []):
            start = parse_utc_iso(r['valid_from'])
            end = parse_utc_iso(r['valid_to'])
            price = r['value_inc_vat']
            slots.append({
                'start': start,
                'end': end,
                'price': price
            })

        slots.sort(key=lambda s: s['start'])
        return slots
    except Exception as e:
        logging.error(f"Error fetching Octopus Agile rates: {e}")
        return []
