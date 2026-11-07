"""Octopus Energy Rewards & Gamification Module (Wheel of Fortune & Monday Scratchcard).

Encapsulates automated reward claiming and user notifications:
1. Wheel of Fortune: Automated spin-to-win for electricity and gas via
   Octopus backend Kraken GraphQL mutations or Home Assistant service calls.
2. Octoplus Monday Scratchcard: Availability monitoring via Home Assistant sensors
   and actionable notifications with direct dashboard claim URLs.
"""

import os
import sys
import logging
from datetime import datetime, timezone
import requests

try:
    import config
except ImportError:
    config = None

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_URL = "http://supervisor/core/api"

DEFAULT_SCRATCHCARD_SENSOR = "binary_sensor.octoplus_scratchcard_available"
FALLBACK_SCRATCHCARD_SENSOR = "binary_sensor.octopus_energy_octoplus_scratchcard_available"

DEFAULT_WHEEL_SPINS_ELEC = "sensor.octopus_energy_wheel_of_fortune_spins_electricity"
DEFAULT_WHEEL_SPINS_GAS = "sensor.octopus_energy_wheel_of_fortune_spins_gas"

OCTOPUS_BACKEND_GRAPHQL_URL = "https://api.backend.octopus.energy/v1/graphql/"


def is_wheel_spins_enabled():
    """Check whether automated Wheel of Fortune spinning is enabled via config or env."""
    val = os.environ.get("ENABLE_WHEEL_SPINS", "")
    if val:
        return val.lower() in ("true", "1", "yes")
    # Backwards compatibility check
    val_legacy = os.environ.get("AUTO_SPIN_WHEEL_OF_FORTUNE", "")
    if val_legacy:
        return val_legacy.lower() in ("true", "1", "yes")
    if config:
        return bool(
            getattr(config, "ENABLE_WHEEL_SPINS", None)
            if hasattr(config, "ENABLE_WHEEL_SPINS")
            else getattr(config, "AUTO_SPIN_WHEEL_OF_FORTUNE", True)
        )
    return True


def is_scratchcard_alerts_enabled():
    """Check whether Monday scratchcard notifications are enabled via config or env."""
    val = os.environ.get("ENABLE_SCRATCHCARD_ALERTS", "")
    if val:
        return val.lower() in ("true", "1", "yes")
    # Backwards compatibility check
    val_legacy = os.environ.get("NOTIFY_OCTOPLUS_SCRATCHCARD", "")
    if val_legacy:
        return val_legacy.lower() in ("true", "1", "yes")
    if config:
        return bool(
            getattr(config, "ENABLE_SCRATCHCARD_ALERTS", None)
            if hasattr(config, "ENABLE_SCRATCHCARD_ALERTS")
            else getattr(config, "NOTIFY_OCTOPLUS_SCRATCHCARD", True)
        )
    return True


def send_ha_notification(title, message, notification_id=None):
    """Send a persistent notification to Home Assistant via Supervisor API."""
    if not SUPERVISOR_TOKEN:
        return False
    url = f"{HA_URL}/services/persistent_notification/create"
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "title": title,
        "message": message,
    }
    if notification_id:
        payload["notification_id"] = str(notification_id)
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def fetch_ha_sensor(entity_id):
    """Fetch entity state from Home Assistant REST API."""
    if not SUPERVISOR_TOKEN:
        return None
    url = f"{HA_URL}/states/{entity_id}"
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


def _get_account_id(account_id=None):
    """Resolve Octopus account number from argument, env, or config."""
    return account_id or os.environ.get('OCTOPUS_ACCOUNT_ID', '') or (
        getattr(config, 'OCTOPUS_ACCOUNT_ID', '') or getattr(config, 'OCTOPUS_ACCOUNT_NUMBER', '') if config else ''
    )


def spin_wheel_of_fortune(fuel_type="ELECTRICITY", account_id=None, token=None):
    """Spin Octopus Energy Wheel of Fortune for the given fuel type (ELECTRICITY or GAS).
    Tries Kraken GraphQL mutation first, then falls back to Home Assistant service call.
    Returns (success: bool, result: dict or str).
    """
    fuel = str(fuel_type or "ELECTRICITY").strip().upper()
    if fuel not in ("ELECTRICITY", "GAS"):
        fuel = "ELECTRICITY"

    acc_id = _get_account_id(account_id)
    auth_token = token
    if not auth_token:
        try:
            import octoplus
            auth_token = octoplus.obtain_kraken_token()
        except Exception:
            auth_token = None

    # 1. Try Kraken GraphQL mutation if credentials available
    if auth_token and acc_id:
        spin_mutation = f"""
        mutation SpinWheelOfFortune {{
          spinWheelOfFortune(input: {{
            accountNumber: "{acc_id}",
            fuelType: {fuel}
          }}) {{
            prize {{
              value
            }}
          }}
        }}
        """
        try:
            headers = {"Authorization": auth_token}
            resp = requests.post(
                OCTOPUS_BACKEND_GRAPHQL_URL,
                json={"query": spin_mutation},
                headers=headers,
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if 'errors' not in data and data.get('data', {}).get('spinWheelOfFortune'):
                prize_data = data['data']['spinWheelOfFortune'].get('prize', {}) or {}
                prize_val = prize_data.get('value', 'won')
                logging.info(f"✓ [OCTOPUS REWARDS] Successfully spun Wheel of Fortune for {fuel} via GraphQL! Prize: {prize_val}")
                return True, {"fuel_type": fuel, "prize": prize_val, "method": "graphql"}
            else:
                logging.debug(f"Octopus Rewards: GraphQL spin response: {data}")
        except Exception as e:
            logging.debug(f"Octopus Rewards: GraphQL spin failed: {e}")

    # 2. Try Home Assistant service call (octopus_energy.spin_wheel_of_fortune)
    if SUPERVISOR_TOKEN:
        url = f"{HA_URL}/services/octopus_energy/spin_wheel_of_fortune"
        headers = {
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json"
        }
        acc_clean = acc_id.lower().replace("-", "_") if acc_id else ""
        entity_candidates = [
            f"sensor.octopus_energy_wheel_of_fortune_spins_{fuel.lower()}",
        ]
        if acc_clean:
            entity_candidates.insert(0, f"sensor.octopus_energy_{acc_clean}_wheel_of_fortune_spins_{fuel.lower()}")

        for ent in entity_candidates:
            payload = {"entity_id": ent}
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=10)
                if resp.status_code == 200:
                    logging.info(f"✓ [OCTOPUS REWARDS] Successfully spun Wheel of Fortune for {fuel} via Home Assistant service targeting {ent}!")
                    prize_info = "spun"
                    try:
                        res_json = resp.json()
                        if isinstance(res_json, dict) and "points" in res_json:
                            prize_info = f"{res_json.get('points')} points"
                    except Exception:
                        pass
                    return True, {"fuel_type": fuel, "prize": prize_info, "method": "home_assistant", "entity_id": ent}
            except Exception:
                pass

        # Also try direct data payload with account_number and fuelType
        if acc_id:
            try:
                resp = requests.post(url, json={"account_number": acc_id, "fuelType": fuel}, headers=headers, timeout=10)
                if resp.status_code == 200:
                    logging.info(f"✓ [OCTOPUS REWARDS] Successfully spun Wheel of Fortune for {fuel} via Home Assistant service (account_number)!")
                    return True, {"fuel_type": fuel, "prize": "spun", "method": "home_assistant"}
            except Exception:
                pass

    return False, f"Could not execute Wheel of Fortune spin for {fuel}"


def get_available_wheel_spins(account_id=None):
    """Retrieve remaining Wheel of Fortune spins for electricity and gas from Home Assistant sensors.
    Returns dict mapping 'ELECTRICITY' and 'GAS' to int count (or None if sensor unavailable).
    """
    acc_id = _get_account_id(account_id)
    acc_clean = acc_id.lower().replace("-", "_") if acc_id else ""

    result = {"ELECTRICITY": None, "GAS": None}
    for fuel in ("ELECTRICITY", "GAS"):
        f_lower = fuel.lower()
        candidates = [
            f"sensor.octopus_energy_wheel_of_fortune_spins_{f_lower}",
        ]
        if acc_clean:
            candidates.insert(0, f"sensor.octopus_energy_{acc_clean}_wheel_of_fortune_spins_{f_lower}")

        for ent in candidates:
            sensor_data = fetch_ha_sensor(ent)
            if sensor_data and "state" in sensor_data:
                try:
                    val = int(sensor_data["state"])
                    result[fuel] = val
                    break
                except (ValueError, TypeError):
                    pass

    return result


def auto_spin_available_wheels(account_id=None, force=False):
    """Automatically execute any available Wheel of Fortune spins.
    If remaining spin counts are known via HA sensors, spins whenever count > 0.
    If spin counts cannot be queried from sensors, only attempts spins if force=True or on Mondays.
    """
    acc_id = _get_account_id(account_id)
    available_spins = get_available_wheel_spins(acc_id)

    now_local = datetime.now().astimezone()
    is_monday = now_local.weekday() == 0

    spins_executed = 0
    prizes = []
    results = []

    for fuel in ("ELECTRICITY", "GAS"):
        count = available_spins.get(fuel)
        should_spin = False
        spins_to_run = 1

        if count is not None:
            if count > 0:
                should_spin = True
                spins_to_run = min(count, 5)  # Cap at 5 consecutive spins per check
        elif force or is_monday:
            should_spin = True
            spins_to_run = 1

        if should_spin:
            for _ in range(spins_to_run):
                success, res = spin_wheel_of_fortune(fuel_type=fuel, account_id=acc_id)
                results.append(res)
                if success:
                    spins_executed += 1
                    prize_str = res.get('prize', 'unknown') if isinstance(res, dict) else str(res)
                    prizes.append(f"{fuel}: {prize_str}")
                else:
                    break

    if spins_executed > 0:
        prize_summary = ", ".join(prizes)
        send_ha_notification(
            title="🎡 Octopus Wheel Spun!",
            message=f"Automatically executed {spins_executed} Wheel of Fortune spin(s)!\n\nResults: {prize_summary}",
            notification_id="octopus_wheel_of_fortune_spun"
        )

    return {
        "spins_executed": spins_executed,
        "prizes": prizes,
        "results": results,
    }


def check_octoplus_scratchcard_status(account_id=None, notify=True):
    """Check if the weekly Octoplus scratchcard is ready to play.
    Reads Home Assistant sensor introduced in HomeAssistant-OctopusEnergy v19.3.0.
    If available and notify=True, sends a persistent notification in Home Assistant with a direct link.
    """
    acc_id = _get_account_id(account_id)
    acc_clean = acc_id.lower().replace("-", "_") if acc_id else ""

    candidates = [
        DEFAULT_SCRATCHCARD_SENSOR,
        FALLBACK_SCRATCHCARD_SENSOR,
    ]
    if acc_clean:
        candidates.insert(0, f"binary_sensor.octopus_energy_{acc_clean}_octoplus_scratchcard_available")
        candidates.append(f"sensor.octopus_energy_{acc_clean}_octoplus_scratchcard_available")

    is_available = False
    found_entity = None
    for ent in candidates:
        sensor_data = fetch_ha_sensor(ent)
        if sensor_data and "state" in sensor_data:
            state_val = str(sensor_data.get("state", "")).lower().strip()
            found_entity = ent
            if state_val in ("on", "true", "available"):
                is_available = True
                break

    notified = False
    if is_available:
        logging.info(f"🎟️ [OCTOPUS REWARDS] Scratchcard is available (sensor: {found_entity})!")
        if notify:
            dash_url = f"https://octopus.energy/dashboard/new/accounts/{acc_id}/octoplus" if acc_id else "https://octopus.energy/dashboard/new/octoplus"
            notified = send_ha_notification(
                title="🎟️ Octoplus Scratchcard Ready!",
                message=f"Your weekly Monday Octoplus scratchcard is ready to play.\n\n[Play Scratchcard in Octoplus Dashboard]({dash_url})",
                notification_id="octoplus_scratchcard_available"
            )

    return {
        "available": is_available,
        "entity": found_entity,
        "notified": notified
    }

