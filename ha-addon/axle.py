"""Axle Energy Self-Dispatch Virtual Power Plant (VPP) Integration (ADR 0015).

Manages direct REST API polling and Home Assistant entity fallback for Axle Energy
Self-Dispatch mode events. Coordinates pre-event battery top-ups and force-discharge
export windows during grid demand-flexibility sessions.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
import requests

from tariffs import parse_utc_iso

try:
    import config
except ImportError:
    config = None

AXLE_API_URL = "https://api.axle.energy/vpp/home-assistant/event"
DEFAULT_AXLE_SENSOR = "sensor.axle_event"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_URL = "http://supervisor/core/api"

# In-memory cache for Axle event data (5 minutes)
_axle_cache = {"event": None, "fetched_at": None, "last_status": None}


def clear_axle_cache():
    """Clear in-memory cache for tests or forced re-fetch."""
    global _axle_cache
    _axle_cache = {"event": None, "fetched_at": None, "last_status": None}


def get_axle_token():
    """Discover Axle authentication token from config or environment.
    Strips whitespace and any leading 'Bearer ' prefix if present.
    """
    raw_token = (
        os.environ.get("AXLE_API_TOKEN")
        or os.environ.get("AXLE_TOKEN")
        or getattr(config, "AXLE_API_TOKEN", None)
        or getattr(config, "AXLE_TOKEN", None)
        or getattr(config, "AXLE_AUTH_TOKEN", None)
        or ""
    )
    token = str(raw_token).strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def is_axle_enabled():
    """Determine if Axle Self-Dispatch integration is active."""
    env_enabled = os.environ.get("ENABLE_AXLE_DISPATCH", "").lower() in ("true", "1", "yes")
    config_enabled = getattr(config, "ENABLE_AXLE_DISPATCH", False) or getattr(config, "AXLE_ENABLED", False) if config else False
    has_token = bool(get_axle_token())
    return env_enabled or config_enabled or has_token


def test_axle_connection(token=None):
    """Test Axle Energy API connectivity and token authentication.
    Returns (success: bool, message: str, event_data: dict | None).
    """
    tok = token or get_axle_token()
    if not tok:
        return False, "Axle token not configured.", None

    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.get(AXLE_API_URL, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json() if resp.text else {}
            return True, "Connected successfully", data
        elif resp.status_code in (401, 403):
            return False, f"Authentication failed (HTTP {resp.status_code}): Invalid or expired token.", None
        else:
            return False, f"Unexpected response from Axle API (HTTP {resp.status_code})", None
    except Exception as e:
        return False, f"Network error connecting to Axle API: {e}", None


def parse_axle_payload(data):
    """Normalize raw Axle JSON payload into standardized event dictionary."""
    if not isinstance(data, dict):
        return None

    # Handle nested attributes if fetched from Home Assistant REST sensor state
    if "attributes" in data and isinstance(data["attributes"], dict):
        attrs = data["attributes"]
        start_raw = attrs.get("start_time") or data.get("state")
        end_raw = attrs.get("end_time")
        direction = attrs.get("import_export", "export")
        updated_raw = attrs.get("updated_at")
    else:
        start_raw = data.get("start_time")
        end_raw = data.get("end_time")
        direction = data.get("import_export", "export")
        updated_raw = data.get("updated_at")

    if not start_raw or str(start_raw).lower() in ("none", "null", "unavailable", "unknown", ""):
        return None

    try:
        start_dt = parse_utc_iso(start_raw) if isinstance(start_raw, str) else start_raw
        end_dt = parse_utc_iso(end_raw) if isinstance(end_raw, str) and end_raw else None
        updated_dt = parse_utc_iso(updated_raw) if isinstance(updated_raw, str) and updated_raw else None

        # Fallback default duration: 1 hour if end_time is not provided
        if start_dt and not end_dt:
            end_dt = start_dt + timedelta(hours=1)

        return {
            "start": start_dt,
            "end": end_dt,
            "import_export": str(direction).lower().strip(),
            "updated_at": updated_dt,
            "raw": data
        }
    except Exception as e:
        logging.warning(f"Axle: Failed to parse event datetime fields ({e}): {data}")
        return None


def fetch_ha_axle_sensor(entity_id=None):
    """Fallback: Fetch Axle event entity state from Home Assistant REST API."""
    if not SUPERVISOR_TOKEN:
        return None

    target_entity = entity_id or getattr(config, "AXLE_EVENT_ENTITY", DEFAULT_AXLE_SENSOR) if config else DEFAULT_AXLE_SENSOR
    url = f"{HA_URL}/states/{target_entity}"
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def fetch_axle_event(token=None, force=False):
    """Fetch upcoming or active Axle Energy event with 5-minute caching.
    Tries direct Axle REST API first, falling back to HA sensor.
    """
    global _axle_cache
    now = datetime.now(timezone.utc)
    if not force and _axle_cache["fetched_at"]:
        age = (now - _axle_cache["fetched_at"]).total_seconds()
        if age < 300:  # 5 minutes
            return _axle_cache["event"]

    tok = token or get_axle_token()
    event = None

    # Path A: Direct Axle REST API
    if tok:
        success, msg, data = test_axle_connection(tok)
        if success and data:
            event = parse_axle_payload(data)
            _axle_cache["last_status"] = "ok"
        else:
            _axle_cache["last_status"] = msg
            if not success:
                logging.warning(f"Axle: {msg}")

    # Path B: Fallback to Home Assistant sensor
    if not event and SUPERVISOR_TOKEN:
        ha_data = fetch_ha_axle_sensor()
        if ha_data:
            event = parse_axle_payload(ha_data)

    _axle_cache["event"] = event
    _axle_cache["fetched_at"] = now
    return event


def is_axle_event_active(event, dt=None):
    """Check if an Axle event is currently active at the given time."""
    if not event or not event.get("start") or not event.get("end"):
        return False
    check_dt = dt or datetime.now(timezone.utc)
    return event["start"] <= check_dt < event["end"]


def is_axle_precharge_window(event, dt=None, lead_hours=2.0):
    """Check if the current time falls within the pre-event battery charge top-up window."""
    if not event or not event.get("start"):
        return False
    check_dt = dt or datetime.now(timezone.utc)
    precharge_start = event["start"] - timedelta(hours=lead_hours)
    return precharge_start <= check_dt < event["start"]

