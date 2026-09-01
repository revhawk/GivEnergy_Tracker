"""Hive Water Heater integration module (Decoupled gas hot water controller for v1.0.21+)."""
import os
import logging
import requests

import config

SUPERVISOR_TOKEN = os.environ.get('SUPERVISOR_TOKEN', '')
HA_URL = "http://supervisor/core/api"


def get_ha_headers():
    """Build Authorization headers for Home Assistant REST API."""
    if SUPERVISOR_TOKEN:
        return {
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        }
    return None


def get_hive_hot_water_state():
    """Fetch state of water_heater.hive_hot_water from Home Assistant."""
    headers = get_ha_headers()
    if not headers:
        logging.warning("Hive: Home Assistant Supervisor token unavailable; cannot read Hive state.")
        return None

    entity_id = getattr(config, 'HIVE_WATER_HEATER_ENTITY', 'water_heater.hive_hot_water')
    url = f"{HA_URL}/states/{entity_id}"
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()
        logging.warning(f"Hive: Failed to fetch state for {entity_id} (HTTP {r.status_code})")
        return None
    except Exception as e:
        logging.warning(f"Hive: Error reading state ({e})")
        return None


def set_hive_hot_water_mode(mode="schedule"):
    """Set Hive hot water operation mode ('schedule', 'off', 'on')."""
    headers = get_ha_headers()
    if not headers:
        logging.warning("Hive: Home Assistant Supervisor token unavailable; skipping Hive mode update.")
        return False

    entity_id = getattr(config, 'HIVE_WATER_HEATER_ENTITY', 'water_heater.hive_hot_water')
    url = f"{HA_URL}/services/water_heater/set_operation_mode"
    payload = {
        "entity_id": entity_id,
        "operation_mode": mode
    }
    try:
        logging.info(f"🐝 Hive: Setting {entity_id} operation mode to '{mode}'")
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"Hive: Failed to set operation mode to '{mode}': {e}")
        return False


def evaluate_morning_shower_safety(tank_temp_c=None, min_required_c=45.0):
    """Morning shower safety check — ensures warm shower at 6am.
    
    Returns:
        is_safe (bool): True if tank is warm enough to disable gas, False if gas backup is needed.
    """
    if tank_temp_c is None:
        # If no temperature sensor is installed yet, default to SAFE mode (do NOT disable gas)
        logging.info("🐝 Hive Safety: No numerical tank temperature sensor detected — maintaining gas schedule for 6am shower.")
        return False

    if float(tank_temp_c) >= float(min_required_c):
        logging.info(f"🐝 Hive Safety: Tank temperature is {tank_temp_c:.1f}°C (>= {min_required_c}°C threshold) — safe to disable gas.")
        return True
    else:
        logging.warning(f"🐝 Hive Safety: Tank temperature is {tank_temp_c:.1f}°C (< {min_required_c}°C threshold) — enabling gas backup for morning shower.")
        return False


def should_suppress_gas_hot_water(solar_today_kwh=0.0, grid_charged_overnight=False, min_solar_kwh=15.0):
    """Determine if gas hot water can be safely paused to save money today without cold shower risk."""
    if grid_charged_overnight:
        logging.info("🐝 Hive Gas Control: Overnight electric pre-charge occurred — tank is hot. Gas can be paused.")
        return True

    if solar_today_kwh >= min_solar_kwh:
        logging.info(f"🐝 Hive Gas Control: Strong solar forecast ({solar_today_kwh:.1f} kWh >= {min_solar_kwh} kWh) — iBoost will heat water. Gas can be paused.")
        return True

    logging.info(f"🐝 Hive Gas Control: Low solar ({solar_today_kwh:.1f} kWh) and no pre-charge — maintaining Hive gas schedule for hot shower guarantee.")
    return False

