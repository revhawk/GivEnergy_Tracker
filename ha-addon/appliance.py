"""Appliance Telemetry & Washing Machine Usage Dashboard Module.

Tracks smart plug power sensors (e.g. sensor.washing_machine_power), detects active wash
cycle states ('idle', 'washing', 'spinning', 'complete'), calculates wash cycle costs in pence,
recommends optimal cheap Agile wash slots, and renders washing machine usage statistics.
"""

import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

try:
    import config
except ImportError:
    config = None

import state


def get_ha_headers():
    """Build Authorization headers for Home Assistant REST API."""
    import os
    token = os.environ.get('SUPERVISOR_TOKEN', '')
    if token:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    return None


def get_washing_machine_telemetry() -> Dict[str, Any]:
    """Fetch current washing machine power sensor state from Home Assistant."""
    headers = get_ha_headers()
    entity_id = getattr(config, 'WASHING_MACHINE_POWER_SENSOR', 'sensor.washing_machine_power') if config else 'sensor.washing_machine_power'
    
    if not headers:
        logging.debug("Appliance: Home Assistant Supervisor token unavailable; operating in simulation mode.")
        return {"entity_id": entity_id, "power_w": 0.0, "state": "idle"}

    url = f"http://supervisor/core/api/states/{entity_id}"
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            raw_state = data.get('state', '0')
            try:
                power_w = float(raw_state)
            except ValueError:
                power_w = 0.0
            return {
                "entity_id": entity_id,
                "power_w": power_w,
                "attributes": data.get("attributes", {})
            }
        return {"entity_id": entity_id, "power_w": 0.0, "state": "idle"}
    except Exception as e:
        logging.debug(f"Appliance: Could not read {entity_id}: {e}")
        return {"entity_id": entity_id, "power_w": 0.0, "state": "idle"}


def detect_wash_cycle_state(power_w: float) -> str:
    """Evaluate wash cycle operational state based on power draw in Watts."""
    if power_w > 1200.0:
        return "heating"
    elif power_w > 300.0:
        return "spinning"
    elif power_w > 10.0:
        return "washing"
    else:
        return "idle"


def calculate_cycle_cost(kwh: float, rate_p_per_kwh: float) -> float:
    """Calculate wash cycle cost in pence."""
    return round(kwh * rate_p_per_kwh, 2)


def recommend_cheap_wash_slots(upcoming_slots: List[Dict[str, Any]], cycle_duration_hours: float = 1.5) -> List[Dict[str, Any]]:
    """Identify top 3 cheapest contiguous time windows for running washing machine today."""
    if not upcoming_slots:
        return []

    needed_slots = int(cycle_duration_hours * 2)  # 1.5 hours = 3 x 30-min slots
    needed_slots = max(1, min(needed_slots, len(upcoming_slots)))

    windows = []
    for idx in range(len(upcoming_slots) - needed_slots + 1):
        window = upcoming_slots[idx : idx + needed_slots]
        avg_price = sum(s['price'] for s in window) / len(window)
        windows.append({
            'start': window[0]['start'],
            'end': window[-1]['end'],
            'avg_price': avg_price
        })

    windows.sort(key=lambda w: w['avg_price'])
    return windows[:3]


def update_washing_machine_stats(power_w: float, current_rate_p: float = 25.0) -> Dict[str, Any]:
    """Track wash cycles, accumulate daily kWh, and persist to state."""
    cur_state = state.load_state()
    wm_stats = cur_state.setdefault('washing_machine_stats', {
        'date': datetime.now().astimezone().date().isoformat(),
        'cycles_today': 0,
        'total_kwh_today': 0.0,
        'total_cost_today_p': 0.0,
        'is_cycle_active': False,
        'cycle_start_time': None
    })

    today_str = datetime.now().astimezone().date().isoformat()
    if wm_stats.get('date') != today_str:
        wm_stats = {
            'date': today_str,
            'cycles_today': 0,
            'total_kwh_today': 0.0,
            'total_cost_today_p': 0.0,
            'is_cycle_active': False,
            'cycle_start_time': None
        }

    cycle_state = detect_wash_cycle_state(power_w)
    
    if power_w > 15.0:
        if not wm_stats['is_cycle_active']:
            wm_stats['is_cycle_active'] = True
            wm_stats['cycles_today'] += 1
            wm_stats['cycle_start_time'] = datetime.now().astimezone().strftime("%H:%M")
            logging.info(f"🧺 Washing Machine: New wash cycle detected! Total cycles today: {wm_stats['cycles_today']}")
        
        # Accumulate estimated 30-min kWh chunk for active reading
        slot_kwh = (power_w / 1000.0) * (30.0 / 3600.0)  # power for tick
        wm_stats['total_kwh_today'] = round(wm_stats['total_kwh_today'] + slot_kwh, 3)
        cost_p = calculate_cycle_cost(slot_kwh, current_rate_p)
        wm_stats['total_cost_today_p'] = round(wm_stats['total_cost_today_p'] + cost_p, 2)
    else:
        if wm_stats['is_cycle_active']:
            wm_stats['is_cycle_active'] = False
            logging.info(f"🧺 Washing Machine: Wash cycle finished. Total kWh today: {wm_stats['total_kwh_today']:.2f} kWh")

    cur_state['washing_machine_stats'] = wm_stats
    state.save_state(cur_state)
    return wm_stats


def generate_appliance_dashboard_summary(upcoming_slots: List[Dict[str, Any]] = None) -> str:
    """Generate plain-English summary of washing machine usage & optimal wash windows."""
    cur_state = state.load_state()
    wm_stats = cur_state.get('washing_machine_stats', {})
    cycles = wm_stats.get('cycles_today', 0)
    kwh = wm_stats.get('total_kwh_today', 0.0)
    cost_p = wm_stats.get('total_cost_today_p', 0.0)

    recs = recommend_cheap_wash_slots(upcoming_slots) if upcoming_slots else []
    
    rec_str = "n/a"
    if recs:
        best = recs[0]
        start_t = best['start'].astimezone().strftime('%H:%M')
        end_t = best['end'].astimezone().strftime('%H:%M')
        rec_str = f"{start_t} → {end_t} (avg {best['avg_price']:.1f}p/kWh)"

    lines = [
        "🧺 WASHING MACHINE USAGE DASHBOARD",
        "----------------------------------",
        f"• Cycles Completed Today : {cycles} cycle(s)",
        f"• Energy Consumed Today   : {kwh:.2f} kWh",
        f"• Electricity Cost Today  : {cost_p:.1f}p",
        f"• Best Recommended Slot   : {rec_str}"
    ]
    return "\n".join(lines)
