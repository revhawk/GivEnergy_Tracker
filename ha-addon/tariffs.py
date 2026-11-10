"""Octopus Energy tariff integration module (Agile & Flux import, Outgoing export rates, Account auto-discovery)."""
import os
import logging
import requests
from datetime import datetime, timezone

try:
    import config
except ImportError:
    config = None

_export_rate_cache = {"rate": None, "results": [], "fetched_at": None}
_account_tariffs_cache = {"tariffs": None, "fetched_at": None}


def is_auto_detect_enabled():
    """Check if tariff auto-discovery is enabled via env var or config."""
    val = os.environ.get('AUTO_DETECT_TARIFFS')
    if val is not None:
        return val.lower() in ('true', '1', 'yes')
    return getattr(config, 'AUTO_DETECT_TARIFFS', False) if config else False


def parse_utc_iso(iso_str):
    """Parse UTC ISO timestamps from Octopus API."""
    iso_str = iso_str.replace('Z', '+00:00')
    return datetime.fromisoformat(iso_str).astimezone(timezone.utc)


def extract_product_code_from_tariff(tariff_code):
    """Extract Octopus product code from a full tariff code string.
    Example: E-1R-FLUX-IMPORT-23-02-14-E -> FLUX-IMPORT-23-02-14
             E-1R-AGILE-24-10-01-E      -> AGILE-24-10-01
             E-1R-OUTGOING-VAR-24-10-26-E -> OUTGOING-VAR-24-10-26
    """
    if not tariff_code:
        return ""
    parts = tariff_code.strip().split('-')
    if len(parts) >= 4 and parts[0] in ('E', 'G') and parts[1] == '1R':
        return '-'.join(parts[2:-1])
    elif len(parts) >= 2:
        return '-'.join(parts[:-1])
    return tariff_code


def discover_account_tariffs(api_key=None, account_id=None):
    """Discover active electricity import, export, and gas tariffs directly from Octopus Account API.
    Caches results for 1 hour.
    """
    global _account_tariffs_cache
    now = datetime.now(timezone.utc)
    if _account_tariffs_cache["fetched_at"]:
        age = (now - _account_tariffs_cache["fetched_at"]).total_seconds()
        if _account_tariffs_cache["tariffs"] is not None and age < 3600:
            return _account_tariffs_cache["tariffs"]

    key = api_key or os.environ.get('OCTOPUS_API_KEY', '') or (getattr(config, 'OCTOPUS_API_KEY', '') if config else '')
    acc_id = (
        account_id
        or os.environ.get('OCTOPUS_ACCOUNT_ID', '')
        or (getattr(config, 'OCTOPUS_ACCOUNT_ID', '') or getattr(config, 'OCTOPUS_ACCOUNT_NUMBER', '') if config else '')
    )

    if not key or not acc_id:
        return None

    url = f"https://api.octopus.energy/v1/accounts/{acc_id}/"
    try:
        resp = requests.get(url, auth=(key, ""), timeout=15)
        resp.raise_for_status()
        acc_data = resp.json()
        now_iso = now.isoformat().replace('+00:00', 'Z')

        discovered = {
            "import": None,
            "export": None,
            "gas": None,
        }

        properties = acc_data.get('properties', [])
        if properties:
            prop = properties[0]
            for mp in prop.get('electricity_meter_points', []):
                is_export = mp.get('is_export', False)
                key_type = "export" if is_export else "import"
                agreements = mp.get('agreements', [])
                active = next(
                    (a for a in agreements if a.get('valid_from', '') <= now_iso <= (a.get('valid_to') or '9999')),
                    None
                )
                if not active and agreements:
                    agreements_sorted = sorted(agreements, key=lambda a: a.get('valid_from', ''), reverse=True)
                    active = agreements_sorted[0]
                if active:
                    t_code = active.get('tariff_code', '')
                    p_code = extract_product_code_from_tariff(t_code)
                    discovered[key_type] = {
                        "mpan": mp.get('mpan'),
                        "tariff_code": t_code,
                        "product_code": p_code,
                        "valid_from": active.get('valid_from'),
                        "valid_to": active.get('valid_to'),
                    }

            for gp in prop.get('gas_meter_points', []):
                agreements = gp.get('agreements', [])
                active = next(
                    (a for a in agreements if a.get('valid_from', '') <= now_iso <= (a.get('valid_to') or '9999')),
                    None
                )
                if not active and agreements:
                    agreements_sorted = sorted(agreements, key=lambda a: a.get('valid_from', ''), reverse=True)
                    active = agreements_sorted[0]
                if active:
                    t_code = active.get('tariff_code', '')
                    p_code = extract_product_code_from_tariff(t_code)
                    discovered["gas"] = {
                        "mprn": gp.get('mprn'),
                        "tariff_code": t_code,
                        "product_code": p_code,
                    }

        _account_tariffs_cache["tariffs"] = discovered
        _account_tariffs_cache["fetched_at"] = now
        if discovered.get("import"):
            logging.info(f"✓ [TARIFF AUTO-DETECT] Active import agreement: {discovered['import']['product_code']} ({discovered['import']['tariff_code']})")
        if discovered.get("export"):
            logging.info(f"✓ [TARIFF AUTO-DETECT] Active export agreement: {discovered['export']['product_code']} ({discovered['export']['tariff_code']})")
        return discovered
    except Exception as e:
        logging.debug(f"Tariff auto-detect: could not query Octopus Account API ({e})")
        _account_tariffs_cache["tariffs"] = None
        _account_tariffs_cache["fetched_at"] = now
        return None


def fetch_export_rate(dt=None):
    """Fetch Octopus export rate in p/kWh for the given datetime (or now).
    Auto-detects active export tariff from Octopus account if available, falling back to config.
    Cached for 6 hours.
    """
    global _export_rate_cache
    now = datetime.now(timezone.utc)
    target_dt = dt or now
    target_iso = target_dt.isoformat().replace('+00:00', 'Z')

    if _export_rate_cache["fetched_at"]:
        age = (now - _export_rate_cache["fetched_at"]).total_seconds()
        if _export_rate_cache.get("results") and age < 6 * 3600:
            active = next(
                (r for r in _export_rate_cache["results"]
                 if r['valid_from'] <= target_iso <= (r.get('valid_to') or '9999')),
                None
            )
            if active:
                return active['value_inc_vat']
            elif _export_rate_cache.get("rate") is not None:
                return _export_rate_cache["rate"]
        elif _export_rate_cache.get("rate") is not None and age < 6 * 3600:
            return _export_rate_cache["rate"]

    auto_detect = is_auto_detect_enabled()
    product = None
    tariff = None

    if auto_detect:
        discovered = discover_account_tariffs()
        if discovered and discovered.get("export"):
            product = discovered["export"].get("product_code")
            tariff = discovered["export"].get("tariff_code")

    if not product or not tariff:
        product = getattr(config, 'EXPORT_PRODUCT_CODE', 'OUTGOING-VAR-24-10-26') if config else 'OUTGOING-VAR-24-10-26'
        tariff = getattr(config, 'EXPORT_TARIFF_CODE', 'E-1R-OUTGOING-VAR-24-10-26-E') if config else 'E-1R-OUTGOING-VAR-24-10-26-E'

    fallback = getattr(config, 'EXPORT_RATE_P_FALLBACK', 12.0) if config else 12.0
    url = f"https://api.octopus.energy/v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        results = response.json().get('results', [])
        active = next((r for r in results
                       if r['valid_from'] <= target_iso <= (r.get('valid_to') or '9999')), None)
        rate = active['value_inc_vat'] if active else (results[0]['value_inc_vat'] if results else fallback)
        _export_rate_cache["rate"] = rate
        _export_rate_cache["results"] = results
        _export_rate_cache["fetched_at"] = now
        logging.info(f"Export rate ({product}): {rate:.2f}p/kWh (from Octopus)")
        return rate
    except Exception as e:
        logging.warning(f"Failed to fetch export rate ({e}); using fallback {fallback}p/kWh")
        return fallback


def fetch_agile_rates():
    """Fetch Octopus Time-of-Use Import Rates (Agile, Flux, Tracker, etc.).
    Auto-detects live import tariff from Octopus account if available, falling back to config.
    """
    auto_detect = is_auto_detect_enabled()
    product = None
    tariff = None

    if auto_detect:
        discovered = discover_account_tariffs()
        if discovered and discovered.get("import"):
            product = discovered["import"].get("product_code")
            tariff = discovered["import"].get("tariff_code")

    if not product or not tariff:
        product = (
            getattr(config, 'IMPORT_PRODUCT_CODE', None)
            or getattr(config, 'AGILE_PRODUCT_CODE', 'AGILE-24-10-01') if config else 'AGILE-24-10-01'
        )
        tariff = (
            getattr(config, 'IMPORT_TARIFF_CODE', None)
            or getattr(config, 'AGILE_TARIFF_CODE', 'E-1R-AGILE-24-10-01-E') if config else 'E-1R-AGILE-24-10-01-E'
        )

    url = f"https://api.octopus.energy/v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/"
    logging.info(f"Fetching Octopus import pricing ({product}) from: {url}")
    try:
        api_key = getattr(config, 'OCTOPUS_API_KEY', '') if config else ''
        response = requests.get(url, auth=(api_key, ""), timeout=15)
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
        logging.error(f"Error fetching Octopus import rates: {e}")
        return []
