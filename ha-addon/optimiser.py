import os
import sys
import json
import time
import math
import asyncio
import logging
import requests
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta

# Single source of truth for the add-on version (imported from version.py).
from version import __version__

# Import custom configurations
try:
    import config
except ImportError:
    print("Error: config.py not found. Please ensure config.py is in the same directory.")
    sys.exit(1)

# Import composable modules directly
from givtcp import (
    find_key_recursive,
    read_inverter_charge_slots,
    run_startup_write_test,
    get_inverter_telemetry,
    get_inverter_soc,
    set_inverter_charge_slots,
    HAS_MODBUS,
)
from tariffs import (
    parse_utc_iso,
    fetch_export_rate,
    fetch_agile_rates,
    _export_rate_cache,
)
from solar import (
    fetch_solar_forecast,
    get_solar_kwh_for_slot,
    fetch_parallel_solar_forecasts,
)
from profiler import (
    get_load_kwh_for_slot,
    is_power_down_slot,
)
from llm import (
    test_openai_connection,
    get_openai_model,
    chatgpt_veto_plan,
    generate_daily_summary,
)

# ── Octoplus Session Entity Naming (ADR 0004) ───────────────────────────
# HomeAssistant-OctopusEnergy renamed Saving Sessions to Power Down and
# Free Electricity Sessions to Power Up (ADR 0004).
# Legacy entity names remain supported until January 2027.
DEFAULT_POWER_DOWN_SENSOR = "sensor.octopus_energy_power_down_sessions"
DEFAULT_POWER_UP_SENSOR = "sensor.octopus_energy_power_up_sessions"
DEFAULT_POWER_DOWN_EVENT = "event.octopus_energy_octoplus_power_down_events"
DEFAULT_POWER_UP_EVENT = "event.octopus_energy_octoplus_power_up_events"

FALLBACK_SAVING_SESSIONS_SENSOR = "sensor.octopus_energy_saving_sessions"
FALLBACK_FREE_ELECTRICITY_SENSOR = "sensor.octopus_energy_free_electricity_sessions"
FALLBACK_SAVING_SESSIONS_EVENT = "event.octopus_energy_octoplus_saving_sessions_events"
FALLBACK_FREE_ELECTRICITY_EVENT = "event.octopus_energy_octoplus_free_electricity_sessions_events"

def get_octoplus_entity_name(session_type, entity_kind="sensor", prefer_new=True):
    """Return entity name for Octoplus sessions according to ADR 0004,
    supporting legacy fallback for pre-ADR 0004 Home Assistant installations.
    
    session_type: 'power_down' (or legacy 'saving_sessions') | 'power_up' (or legacy 'free_electricity')
    entity_kind:  'sensor' | 'event' | 'calendar'
    prefer_new:   If True, return new ADR 0004 entity name; if False, return legacy name.
    """
    stype = str(session_type).lower().strip()
    kind = str(entity_kind).lower().strip()
    
    is_power_down = stype in ['power_down', 'power_down_sessions', 'saving_sessions', 'saving_session']
    is_power_up = stype in ['power_up', 'power_up_sessions', 'free_electricity', 'free_electricity_sessions']
    
    if is_power_down:
        if kind == 'event':
            return DEFAULT_POWER_DOWN_EVENT if prefer_new else FALLBACK_SAVING_SESSIONS_EVENT
        elif kind == 'calendar':
            return "calendar.octopus_energy_octoplus_power_down_sessions" if prefer_new else "calendar.octopus_energy_octoplus_saving_sessions"
        else:
            return getattr(config, 'OCTOPLUS_POWER_DOWN_ENTITY', DEFAULT_POWER_DOWN_SENSOR) if prefer_new else getattr(config, 'OCTOPLUS_SAVING_SESSIONS_FALLBACK_ENTITY', FALLBACK_SAVING_SESSIONS_SENSOR)
    elif is_power_up:
        if kind == 'event':
            return DEFAULT_POWER_UP_EVENT if prefer_new else FALLBACK_FREE_ELECTRICITY_EVENT
        elif kind == 'calendar':
            return "calendar.octopus_energy_octoplus_power_up_sessions" if prefer_new else "calendar.octopus_energy_octoplus_free_electricity_sessions"
        else:
            return getattr(config, 'OCTOPLUS_POWER_UP_ENTITY', DEFAULT_POWER_UP_SENSOR) if prefer_new else getattr(config, 'OCTOPLUS_FREE_ELECTRICITY_FALLBACK_ENTITY', FALLBACK_FREE_ELECTRICITY_SENSOR)
    else:
        raise ValueError(f"Unknown session_type: {session_type}")


def parse_octoplus_session(session_data):
    """Parse session data dict into a standardized session metadata dictionary.
    Supports both ADR 0004 (power_down, power_up) and legacy names.
    """
    if not isinstance(session_data, dict):
        return None
        
    session_type_raw = str(session_data.get('type') or session_data.get('session_type') or session_data.get('event_type') or '').lower()
    if any(k in session_type_raw for k in ['power_down', 'saving']):
        session_type = 'power_down'
        display_name = 'Power Down Session'
    elif any(k in session_type_raw for k in ['power_up', 'free_electricity', 'free']):
        session_type = 'power_up'
        display_name = 'Power Up Session'
    else:
        session_type = 'unknown'
        display_name = 'Octoplus Session'
        
    start_str = session_data.get('start') or session_data.get('valid_from') or session_data.get('start_time')
    end_str = session_data.get('end') or session_data.get('valid_to') or session_data.get('end_time')
    
    start_dt = parse_utc_iso(start_str) if isinstance(start_str, str) else start_str
    end_dt = parse_utc_iso(end_str) if isinstance(end_str, str) else end_str
    
    code = session_data.get('code') or session_data.get('id') or ''
    
    return {
        'session_type': session_type,
        'display_name': display_name,
        'start': start_dt,
        'end': end_dt,
        'code': code,
        'entity_name': get_octoplus_entity_name(session_type, entity_kind='sensor')
    }


# setup python logger
def setup_logging():
    log_level_str = getattr(config, 'LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    log_file = getattr(config, 'LOG_FILE_PATH', None)
    
    # Root logger config
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Root captures everything, handlers filter
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler (saves to NAS)
    if log_file:
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
                
            # Limit file size to 5MB, keep 3 backup logs
            file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
            file_handler.setLevel(log_level)
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
            logging.info(f"File logging successfully directed to: {log_file}")
        except Exception as e:
            # Output error to console if file logging cannot be initialized
            print(f"Error initializing file logger at {log_file}: {e}", file=sys.stderr)

setup_logging()


# ── Plan snapshotting: run_optimization populates this via _record_plan ─────
# It's read by run_daily_plan() and persisted to state for the audit.
_last_plan = {}

def _record_plan(**fields):
    _last_plan.clear()
    _last_plan.update({
        "at": datetime.now(timezone.utc).isoformat(),
        **fields,
    })

# ── Persistent state (last plan/audit dates, latest plan snapshot) ──────────
STATE_FILE = "/share/nas_logs/givenergy_state.json"

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logging.warning(f"Could not save state: {e}")

# ── Daily Stats Accumulation ───────────────────────────────────────────────────────────
STATS_FILE = "/share/nas_logs/givenergy_daily_stats.json"

def load_daily_stats():
    """Load today's accumulated stats from disk."""
    try:
        with open(STATS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_daily_stats(stats):
    """Persist stats to disk after every run."""
    try:
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        with open(STATS_FILE, 'w') as f:
            json.dump(stats, f, indent=2, default=str)
    except Exception as e:
        logging.warning(f"Could not save daily stats: {e}")

def init_daily_stats(date_str, first_soc):
    """Initialise a fresh stats dict for a new day."""
    return {
        'date': date_str,
        'start_soc': first_soc,
        'end_soc': first_soc,
        'runs': 0,
        'charge_windows': [],
        'total_charged_kwh': 0.0,
        'negative_rate_kwh': 0.0,
        'solar_kwh_forecast': 0.0,
        'iboost_kwh_forecast': 0.0,
        'min_rate_seen': float('inf'),
        'max_rate_seen': float('-inf'),
        'no_charge_runs': 0,
    }

def update_daily_stats(stats, run_data):
    """Merge one optimisation run's data into the rolling daily stats."""
    stats['runs'] = stats.get('runs', 0) + 1
    stats['end_soc'] = run_data.get('soc', stats.get('end_soc', 0))

    # Peak forecast seen during the day (best proxy for daily solar total)
    stats['solar_kwh_forecast'] = max(
        stats.get('solar_kwh_forecast', 0), run_data.get('solar_kwh', 0))
    stats['iboost_kwh_forecast'] = max(
        stats.get('iboost_kwh_forecast', 0), run_data.get('iboost_kwh', 0))

    # Rate extremes
    if run_data.get('min_rate') is not None:
        stats['min_rate_seen'] = min(stats.get('min_rate_seen', float('inf')), run_data['min_rate'])
    if run_data.get('max_rate') is not None:
        stats['max_rate_seen'] = max(stats.get('max_rate_seen', float('-inf')), run_data['max_rate'])

    # Charge windows (deduplicate by start time)
    window = run_data.get('charge_window')
    if window:
        existing_starts = [w['start'] for w in stats.get('charge_windows', [])]
        if window['start'] not in existing_starts:
            stats.setdefault('charge_windows', []).append(window)
            stats['total_charged_kwh'] = stats.get('total_charged_kwh', 0) + window.get('kwh', 0)
            if window.get('avg_price', 0) < 0:
                stats['negative_rate_kwh'] = stats.get('negative_rate_kwh', 0) + window.get('kwh', 0)
    else:
        stats['no_charge_runs'] = stats.get('no_charge_runs', 0) + 1

    return stats

# GivEnergy Modbus imports (for direct Modbus fallback if GivTCP fails).
# The package name on PyPI is `givenergy-modbus`; the newer 2.x releases
# restructured internal modules so the specific submodule imports below
# may fail even when the package itself is installed. Log the full error
# so the difference between "not installed" and "API mismatch" is visible.
try:
    from givenergy_modbus.client.client import Client
    from givenergy_modbus.client import commands
    from givenergy_modbus.model.plant import TimeSlot
    HAS_MODBUS = True
except ImportError as _e:
    logging.warning(
        f"Modbus fallback DISABLED — could not import from givenergy_modbus: {_e}. "
        f"GivTCP will be the ONLY write path. If GivTCP fails, plans will not be applied."
    )
    HAS_MODBUS = False

# Helper: Parse UTC ISO timestamps from Octopus API
def parse_utc_iso(iso_str):
    iso_str = iso_str.replace('Z', '+00:00')
    return datetime.fromisoformat(iso_str).astimezone(timezone.utc)

# Helper: Recursively search nested dict/list for a specific key
def find_key_recursive(data, target_key):
    target_lower = target_key.lower()
    if isinstance(data, dict):
        for key, val in data.items():
            if key.lower() == target_lower:
                return val
            result = find_key_recursive(val, target_key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_key_recursive(item, target_key)
            if result is not None:
                return result
    return None

# Optimization Engine
async def run_optimization():
    logging.info(f"===== ENERGY OPTIMIZATION RUN =====")
    
    # 1. Fetch Octopus Agile prices
    rates = fetch_agile_rates()
    if not rates:
        logging.error("Could not retrieve Agile rates. Aborting optimization.")
        return
        
    # Filter for future rates only
    now_utc = datetime.now(timezone.utc)
    upcoming_slots = [r for r in rates if r['end'] > now_utc][:48] # Next 24 hours (48 slots)
    if not upcoming_slots:
        logging.error("No upcoming Agile rate slots available. Aborting.")
        return
        
    # 2. Fetch primary & shadow solar forecasts in parallel
    solar_forecasts, solar_comparison = fetch_parallel_solar_forecasts()
    
    # 3. Get current battery SoC & telemetry
    telemetry = await get_inverter_telemetry()
    if telemetry and 'soc' in telemetry:
        current_soc = telemetry['soc']
    else:
        current_soc = await get_inverter_soc()
        
    if current_soc is None:
        logging.error(
            "Cannot plan without a valid battery SoC reading. Aborting this optimization run. "
            "The tracker will retry on the next tick. Check GivTCP is running at "
            f"{getattr(config, 'GIVTCP_URL', '(not set)')}."
        )
        return

    # 4. Simulate battery SoC evolution if we DO NOT grid-charge
    battery_capacity = getattr(config, 'BATTERY_CAPACITY_KWH', 9.5)
    max_charge_rate = getattr(config, 'MAX_BATTERY_CHARGE_RATE', 3000)
    max_charge_kwh_per_slot = (max_charge_rate / 1000.0) * 0.5 # 3kW * 0.5h = 1.5kWh
    
    min_soc = 10.0 # Standard minimum reserve (10%)
    min_energy = battery_capacity * (min_soc / 100.0)
    max_energy = battery_capacity
    
    current_energy = battery_capacity * (current_soc / 100.0)
    
    energy = current_energy
    import_needed_slots = []
    imports = []
    
    # Run the physical priority simulation: Solar -> Home Load -> Battery Charge -> iBoost Divert -> Export
    for idx, slot in enumerate(upcoming_slots):
        if idx == 0 and telemetry and 'pv_power' in telemetry and 'load_power' in telemetry:
            solar = (telemetry['pv_power'] / 1000.0) * 0.5
            load = (telemetry['load_power'] / 1000.0) * 0.5
            logging.info(
                f"Using live inverter telemetry for slot 0: "
                f"PV={telemetry['pv_power']:.0f}W ({solar:.2f}kWh), "
                f"Load={telemetry['load_power']:.0f}W ({load:.2f}kWh)"
            )
        else:
            solar = get_solar_kwh_for_slot(slot['start'], slot['end'], solar_forecasts)
            state_data = load_state()
            load_history = state_data.get('load_profile_history')
            load = get_load_kwh_for_slot(slot['start'], slot['end'], load_history)
        net = load - solar
        
        iboost_divert = 0.0
        grid_export = 0.0
        import_needed = 0.0
        
        if net < 0:
            # Excess solar
            excess_solar = -net
            # First priority: Charge battery
            solar_charge = min(excess_solar, max_charge_kwh_per_slot, max_energy - energy)
            energy += solar_charge
            
            # Second priority: iBoost hot water diversion
            remaining_excess = excess_solar - solar_charge
            max_iboost_kwh = (getattr(config, 'IBOOST_MAX_DIVERT_RATE', 3000) / 1000.0) * 0.5
            iboost_divert = min(remaining_excess, max_iboost_kwh)
            
            # Third priority: Export to Grid
            grid_export = remaining_excess - iboost_divert
        else:
            # Solar deficit - cover from battery first
            discharge = min(net, energy - min_energy)
            energy -= discharge
            
            # Remainder is imported from Grid
            import_needed = net - discharge
            
        batt_soc = (energy / battery_capacity) * 100.0
        
        imports.append({
            'slot': slot,
            'import_needed': import_needed,
            'solar': solar,
            'load': load,
            'batt_soc': batt_soc,
            'iboost': iboost_divert,
            'export': grid_export
        })
        
        if import_needed > 0:
            import_needed_slots.append({
                'slot': slot,
                'kwh': import_needed,
                'price': slot['price']
            })

    # Save simulated SoC schedule for drift checks in monitor tick
    planned_soc_schedule = [
        {
            'start': imp['slot']['start'].isoformat(),
            'end': imp['slot']['end'].isoformat(),
            'soc': round(imp['batt_soc'], 1)
        }
        for imp in imports
    ]
    cur_state = load_state()
    cur_state['planned_soc_schedule'] = planned_soc_schedule
    save_state(cur_state)
            
    # Print a beautiful simulation timeline
    logging.info("--- 24-Hour Base Simulation (No Grid Charge) ---")
    logging.info(f"{'Date/Time':<10} | {'Price':<6} | {'Solar':<6} | {'Load':<6} | {'Battery':<7} | {'iBoost':<6} | {'Export':<6} | {'Import':<6}")
    logging.info("-" * 75)
    for imp in imports:
        time_str = imp['slot']['start'].astimezone().strftime('%m-%d %H:%M')
        price = f"{imp['slot']['price']:.1f}p"
        solar = f"{imp['solar']:.2f}"
        load = f"{imp['load']:.2f}"
        batt = f"{imp['batt_soc']:.0f}%"
        iboost = f"{imp['iboost']:.2f}" if imp['iboost'] > 0 else "-"
        export = f"{imp['export']:.2f}" if imp['export'] > 0 else "-"
        imp_val = f"{imp['import_needed']:.2f}" if imp['import_needed'] > 0 else "-"
        
        logging.info(f"{time_str:<10} | {price:<6} | {solar:<6} | {load:<6} | {batt:<7} | {iboost:<6} | {export:<6} | {imp_val:<6}")
        
    total_import_kwh = sum(i['kwh'] for i in import_needed_slots)
    total_iboost_kwh = sum(i['iboost'] for i in imports)
    total_export_kwh = sum(i['export'] for i in imports)
    total_solar_kwh  = sum(f['kwh'] for f in solar_forecasts)

    logging.info("--- Simulation Summary ---")
    logging.info(f"Total Grid Import Needed:  {total_import_kwh:.2f} kWh")
    logging.info(f"Expected iBoost Diversion: {total_iboost_kwh:.2f} kWh")
    logging.info(f"Expected Grid Export:      {total_export_kwh:.2f} kWh")

    # 5. Fetch live export rate and identify arbitrage opportunities
    export_rate = fetch_export_rate()
    margin = getattr(config, 'ARBITRAGE_MARGIN_P', 1.5)
    arbitrage_threshold = export_rate - margin

    negative_slots = [s for s in upcoming_slots if s['price'] < 0]
    # Slots where import is cheaper than export → arbitrage is profitable
    arbitrage_slots = [s for s in upcoming_slots if s['price'] < arbitrage_threshold]
    available_capacity_kwh = max_energy - current_energy

    logging.info(f"Export rate now: {export_rate:.2f}p/kWh  |  Arbitrage threshold: <{arbitrage_threshold:.2f}p")

    if negative_slots:
        logging.info(f"⚡ NEGATIVE RATE ALERT: {len(negative_slots)} slot(s) — grid pays YOU!")
        for ns in negative_slots[:8]:
            local_t = ns['start'].astimezone().strftime('%m-%d %H:%M')
            logging.info(f"   {local_t}  {ns['price']:.2f}p/kWh  ← free money!")
    elif arbitrage_slots:
        logging.info(f"💰 Arbitrage opportunity: {len(arbitrage_slots)} slot(s) below {arbitrage_threshold:.2f}p")
        for a in arbitrage_slots[:6]:
            local_t = a['start'].astimezone().strftime('%m-%d %H:%M')
            profit = export_rate - a['price']
            logging.info(f"   {local_t}  {a['price']:.2f}p/kWh  (profit: {profit:.2f}p/kWh vs export)")

    if negative_slots or arbitrage_slots:
        logging.info(f"   Battery space available: {available_capacity_kwh:.1f} kWh  (SoC: {current_soc}%)")

    # Force charging if we have profitable opportunities and battery headroom
    force_opportunistic_charge = (
        (len(negative_slots) > 0 or len(arbitrage_slots) > 0)
        and available_capacity_kwh > 0.3
        and current_soc < 98
    )

    # 6. Decide Grid Charging Slots
    if total_import_kwh <= 0.2 and not force_opportunistic_charge:
        logging.info("Battery + solar sufficient AND no profitable import slots. Grid charging not required.")
        # LLM opinion on the "no charge" decision (for scoring/telemetry only — not acted on)
        approve, score, reason = chatgpt_veto_plan(
            current_soc, battery_capacity, total_solar_kwh, export_rate,
            upcoming_slots, None, None, 0, 0
        )
        score_str = f"{score}/10" if score is not None else "n/a"
        logging.info(f"LLM opinion (no-charge): approve={approve}  score={score_str}  reason={reason}")
        _record_plan(action="no_charge", branch="solar_sufficient",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_inverter_charge_slots(None, None)
        return

    # Calculate required charge energy
    if force_opportunistic_charge and total_import_kwh <= 0.2:
        # Pure arbitrage — fill available battery capacity
        required_charge_kwh = available_capacity_kwh
        if negative_slots:
            logging.info(f"⚡ Negative-rate override: filling {required_charge_kwh:.1f} kWh — grid pays us!")
        else:
            logging.info(f"💰 Arbitrage override: filling {required_charge_kwh:.1f} kWh (import < export)")
    else:
        required_charge_kwh = min(total_import_kwh * 1.10, max_energy - current_energy)

    if required_charge_kwh <= 0.2:
        logging.info("Battery is already too full to accept significant grid charge.")
        approve, score, reason = chatgpt_veto_plan(
            current_soc, battery_capacity, total_solar_kwh, export_rate,
            upcoming_slots, None, None, 0, 0
        )
        score_str = f"{score}/10" if score is not None else "n/a"
        logging.info(f"LLM opinion (battery-full): approve={approve}  score={score_str}  reason={reason}")
        _record_plan(action="no_charge", branch="battery_full",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_inverter_charge_slots(None, None)
        return

    if force_opportunistic_charge:
        # Group and merge contiguous sub-threshold slots
        cheap_slots = arbitrage_slots if arbitrage_slots else negative_slots
        merged_blocks = []
        if cheap_slots:
            sorted_cheap = sorted(cheap_slots, key=lambda s: s['start'])
            current_start = sorted_cheap[0]['start']
            current_end = sorted_cheap[0]['end']
            current_prices = [sorted_cheap[0]['price']]
            
            for next_slot in sorted_cheap[1:]:
                if next_slot['start'] == current_end:
                    current_end = next_slot['end']
                    current_prices.append(next_slot['price'])
                else:
                    merged_blocks.append({
                        'start': current_start,
                        'end': current_end,
                        'avg_price': sum(current_prices) / len(current_prices)
                    })
                    current_start = next_slot['start']
                    current_end = next_slot['end']
                    current_prices = [next_slot['price']]
            merged_blocks.append({
                'start': current_start,
                'end': current_end,
                'avg_price': sum(current_prices) / len(current_prices)
            })

        if merged_blocks:
            if len(merged_blocks) > 10:
                logging.warning(f"Found {len(merged_blocks)} cheap blocks. Limiting to 10 cheapest.")
                merged_blocks = sorted(merged_blocks, key=lambda b: b['avg_price'])[:10]
                merged_blocks = sorted(merged_blocks, key=lambda b: b['start'])

            total_avg_price = sum(b['avg_price'] for b in merged_blocks) / len(merged_blocks)
            slots_tuples = [(b['start'].astimezone(), b['end'].astimezone()) for b in merged_blocks]

            approve, score, reason = chatgpt_veto_plan(
                current_soc, battery_capacity, total_solar_kwh, export_rate,
                upcoming_slots, slots_tuples, None, required_charge_kwh, total_avg_price
            )
            score_str = f"{score}/10" if score is not None else "n/a"

            if total_avg_price < 0.0:
                logging.info(f"LLM veto response: approve={approve}  score={score_str}  reason={reason}")
                if not approve:
                    logging.info(f"⚡ OVERRIDING LLM VETO: proposed average cost is negative ({total_avg_price:.2f}p/kWh). Proceeding with plan.")
                    approve = True
            else:
                logging.info(f"LLM veto: approve={approve}  score={score_str}  reason={reason}")

            if not approve:
                logging.info("LLM VETOED the charge plan — clearing slots as fallback.")
                _record_plan(action="no_charge", branch="llm_vetoed",
                             current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                             export_rate=export_rate,
                             min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                             max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                             proposed_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                              "kwh": round(required_charge_kwh, 2), "avg_price": round(total_avg_price, 2)},
                             llm_approve=False, llm_score=score, llm_reason=reason)
                await set_inverter_charge_slots(None, None)
                return

            logging.info(f"Arbitrage mode: Programming {len(slots_tuples)} charge block(s)...")
            _record_plan(action="charge", branch="arbitrage",
                         current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                         export_rate=export_rate,
                         min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         charge_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                        "kwh": round(required_charge_kwh, 2), "avg_price": round(total_avg_price, 2)},
                         llm_approve=approve, llm_score=score, llm_reason=reason)
            await set_inverter_charge_slots(slots_tuples)
            return

    slots_to_charge = math.ceil(required_charge_kwh / max_charge_kwh_per_slot)
    slots_to_charge = max(1, min(slots_to_charge, 8))  # Between 30 mins and 4 hours

    logging.info(f"Target Grid Charge: {required_charge_kwh:.2f} kWh (~{slots_to_charge} half-hour slot(s))")

    # 1. Contiguous window search
    best_contig_start = None
    best_contig_end = None
    min_contig_cost = float('inf')

    for start_idx in range(len(upcoming_slots) - slots_to_charge + 1):
        window = upcoming_slots[start_idx : start_idx + slots_to_charge]
        avg_price = sum(s['price'] for s in window) / len(window)

        if avg_price < min_contig_cost:
            min_contig_cost = avg_price
            best_contig_start = window[0]['start']
            best_contig_end = window[-1]['end']

    # 2. Non-contiguous cheapest N slots search (if multiple sessions are available)
    use_non_contiguous = False
    slots_tuples = []
    min_window_cost = min_contig_cost

    if len(upcoming_slots) >= slots_to_charge:
        cheapest_n = sorted(upcoming_slots, key=lambda s: s['price'])[:slots_to_charge]
        cheapest_n_sorted = sorted(cheapest_n, key=lambda s: s['start'])
        avg_cheapest_n = sum(s['price'] for s in cheapest_n) / len(cheapest_n)

        # Merge adjacent slots
        merged_blocks = []
        if cheapest_n_sorted:
            c_start = cheapest_n_sorted[0]['start']
            c_end = cheapest_n_sorted[0]['end']
            c_prices = [cheapest_n_sorted[0]['price']]

            for nxt in cheapest_n_sorted[1:]:
                if nxt['start'] == c_end:
                    c_end = nxt['end']
                    c_prices.append(nxt['price'])
                else:
                    merged_blocks.append({'start': c_start, 'end': c_end, 'avg_price': sum(c_prices) / len(c_prices)})
                    c_start = nxt['start']
                    c_end = nxt['end']
                    c_prices = [nxt['price']]
            merged_blocks.append({'start': c_start, 'end': c_end, 'avg_price': sum(c_prices) / len(c_prices)})

        # Use non-contiguous if cheaper than contiguous window and fits in 10 slots
        if avg_cheapest_n < (min_contig_cost - 0.01) and len(merged_blocks) <= 10:
            use_non_contiguous = True
            min_window_cost = avg_cheapest_n
            slots_tuples = [(b['start'].astimezone(), b['end'].astimezone()) for b in merged_blocks]

    if use_non_contiguous:
        rate_label = f"{min_window_cost:.2f}p/kWh" if min_window_cost >= 0 else f"{min_window_cost:.2f}p/kWh (NEGATIVE — grid pays us!)"
        logging.info(f"Optimal Deficit Charge: {len(slots_tuples)} non-contiguous block(s)  |  Avg: {rate_label}")
        charge_cost_p = required_charge_kwh * min_window_cost
        logging.info(f"Economics: charge {required_charge_kwh:.1f} kWh × {min_window_cost:.2f}p = {charge_cost_p:.0f}p")

        approve, score, reason = chatgpt_veto_plan(
            current_soc, battery_capacity, total_solar_kwh, export_rate,
            upcoming_slots, slots_tuples, None, required_charge_kwh, min_window_cost
        )
        score_str = f"{score}/10" if score is not None else "n/a"
        if min_window_cost < 0.0:
            logging.info(f"LLM veto response: approve={approve}  score={score_str}  reason={reason}")
            if not approve:
                logging.info(f"⚡ OVERRIDING LLM VETO: proposed window cost is negative ({min_window_cost:.2f}p/kWh). Proceeding with plan.")
                approve = True
        else:
            logging.info(f"LLM veto: approve={approve}  score={score_str}  reason={reason}")

        if not approve:
            logging.info("LLM VETOED the charge plan — clearing slots as fallback.")
            _record_plan(action="no_charge", branch="llm_vetoed",
                         current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                         export_rate=export_rate,
                         min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         proposed_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                          "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                         llm_approve=False, llm_score=score, llm_reason=reason)
            await set_inverter_charge_slots(None, None)
            return

        _record_plan(action="charge", branch="scheduled",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     charge_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                    "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_inverter_charge_slots(slots_tuples)
    elif best_contig_start and best_contig_end:
        local_start = best_contig_start.astimezone()
        local_end = best_contig_end.astimezone()
        rate_label = f"{min_window_cost:.2f}p/kWh" if min_window_cost >= 0 else f"{min_window_cost:.2f}p/kWh (NEGATIVE — grid pays us!)"
        logging.info(f"Optimal Deficit Charge Window: {local_start.strftime('%H:%M')} → {local_end.strftime('%H:%M')}  |  Avg: {rate_label}")

        # Report economics
        charge_cost_p = required_charge_kwh * min_window_cost
        logging.info(f"Economics: charge {required_charge_kwh:.1f} kWh × {min_window_cost:.2f}p = {charge_cost_p:.0f}p")

        approve, score, reason = chatgpt_veto_plan(
            current_soc, battery_capacity, total_solar_kwh, export_rate,
            upcoming_slots, local_start, local_end, required_charge_kwh, min_window_cost
        )
        score_str = f"{score}/10" if score is not None else "n/a"
        
        if min_window_cost < 0.0:
            logging.info(f"LLM veto response: approve={approve}  score={score_str}  reason={reason}")
            if not approve:
                logging.info(f"⚡ OVERRIDING LLM VETO: proposed window cost is negative ({min_window_cost:.2f}p/kWh). Proceeding with plan.")
                approve = True
        else:
            logging.info(f"LLM veto: approve={approve}  score={score_str}  reason={reason}")

        if not approve:
            logging.info("LLM VETOED the charge plan — clearing slots as fallback.")
            _record_plan(action="no_charge", branch="llm_vetoed",
                         current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                         export_rate=export_rate,
                         min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         proposed_window={"start": local_start.strftime('%H:%M'), "end": local_end.strftime('%H:%M'),
                                          "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                         llm_approve=False, llm_score=score, llm_reason=reason)
            await set_inverter_charge_slots(None, None)
            return

        _record_plan(action="charge", branch="scheduled",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     charge_window={"start": local_start.strftime('%H:%M'), "end": local_end.strftime('%H:%M'),
                                    "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_inverter_charge_slots(local_start, local_end)
    else:
        logging.info("Could not find a valid charge window. Clearing slots.")
        _record_plan(action="no_charge", branch="no_window_found",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate)
        await set_inverter_charge_slots(None, None)

# ── Light monitor: cheap SoC check + load profiling + SoC drift check ──────
async def run_light_monitor():
    logging.info("--- Light monitor tick ---")
    try:
        telemetry = await get_inverter_telemetry()
        realtime_soc = None
        if telemetry and 'soc' in telemetry:
            realtime_soc = telemetry['soc']
            if 'load_power' in telemetry and telemetry['load_power'] is not None:
                state = load_state()
                now_local = datetime.now().astimezone()
                slot_key = now_local.strftime("%H:%M")
                slot_kwh = (telemetry['load_power'] / 1000.0) * 0.5
                history_dict = state.setdefault('load_profile_history', {})
                history_list = history_dict.setdefault(slot_key, [])
                history_list.append(round(slot_kwh, 3))
                if len(history_list) > 14:  # keep rolling 14 readings per half-hour slot
                    history_dict[slot_key] = history_list[-14:]
                save_state(state)
        else:
            realtime_soc = await get_inverter_soc()

        if realtime_soc is None:
            logging.warning("Could not read SoC from GivTCP.")
            return False

        state = load_state()
        planned_schedule = state.get("planned_soc_schedule", [])
        now_utc = datetime.now(timezone.utc)
        drift_threshold = float(getattr(config, 'SOC_DRIFT_THRESHOLD_PCT', 15.0))

        current_planned = None
        for item in planned_schedule:
            p_start = parse_utc_iso(item['start'])
            p_end = parse_utc_iso(item['end'])
            if p_start <= now_utc < p_end:
                current_planned = item.get('soc')
                break

        if current_planned is not None:
            drift = abs(realtime_soc - current_planned)
            if drift > drift_threshold:
                logging.warning(
                    f"⚡ SoC DRIFT ALERT: real-time SoC ({realtime_soc}%) vs planned SoC ({current_planned:.0f}%) "
                    f"differs by {drift:.1f}% (> threshold {drift_threshold:.1f}%). Triggering re-planning!"
                )
                return True
            else:
                logging.info(
                    f"Battery SoC: {realtime_soc}% vs planned {current_planned:.0f}% "
                    f"(drift: {drift:.1f}% <= threshold {drift_threshold:.1f}%) — no re-planning needed"
                )
        else:
            logging.info(f"Battery SoC: {realtime_soc}%  (no active plan slot found for drift comparison)")

        return False
    except Exception as e:
        logging.warning(f"Monitor read failed: {e}")
        return False

# ── End-of-day audit: summarise the day using persisted state + daily stats ──
async def run_end_of_day_audit():
    logging.info("========================================")
    logging.info("     END-OF-DAY AUDIT")
    logging.info("========================================")

    state = load_state()
    stats = load_daily_stats()
    today = datetime.now().astimezone().date().isoformat()

    if stats.get('date') != today:
        logging.info(f"No stats accumulated for today ({today}); daily stats file has date={stats.get('date')}")

    last_plan = state.get('last_plan')
    if last_plan:
        logging.info(f"Today's plan (generated at {state.get('last_plan_at', 'unknown')}):")
        for k, v in last_plan.items():
            logging.info(f"  {k}: {v}")
    else:
        logging.info("No plan on record for today.")

    # Feed the day's data to ChatGPT for an English-language summary.
    # generate_daily_summary() is already defined and calls OpenAI.
    if stats.get('date') == today or last_plan:
        generate_daily_summary(stats if stats.get('date') == today else {
            'date': today,
            'start_soc': (last_plan or {}).get('current_soc_at_plan', '?'),
            'end_soc': '?',
            'runs': 1,
            'charge_windows': [last_plan['charge_window']] if last_plan and last_plan.get('charge_window') else [],
            'total_charged_kwh': (last_plan or {}).get('charge_window', {}).get('kwh', 0) if last_plan else 0,
            'negative_rate_kwh': 0,
            'solar_kwh_forecast': (last_plan or {}).get('solar_forecast_kwh', 0),
            'iboost_kwh_forecast': 0,
            'min_rate_seen': (last_plan or {}).get('min_rate', 0),
            'max_rate_seen': (last_plan or {}).get('max_rate', 0),
            'no_charge_runs': 0,
        })
    logging.info("========================================")

# Main Daemon loop
async def main():
    run_once = os.environ.get('RUN_ONCE', 'false').lower() in ('true', '1', 'yes')
    interval = int(os.environ.get('INTERVAL_MINUTES', 30))

    # Silence noisy third-party HTTP debug logs
    for _logger_name in ("urllib3", "httpx", "httpcore", "openai", "openai._base_client"):
        logging.getLogger(_logger_name).setLevel(logging.WARNING)

    # ── Startup checks ───────────────────────────────────────────────────────
    # Fail loudly if config.yaml and __version__ disagree — prevents silent
    # version drift where HA reports one version and the running code is another.
    try:
        with open(os.path.join(os.path.dirname(__file__), 'config.yaml')) as _f:
            for _line in _f:
                if _line.strip().startswith('version:'):
                    _yaml_ver = _line.split(':', 1)[1].strip().strip('"').strip("'")
                    if _yaml_ver != __version__:
                        logging.warning(
                            f"VERSION MISMATCH: config.yaml says '{_yaml_ver}' "
                            f"but code says '{__version__}'. Fix before releasing."
                        )
                    break
    except FileNotFoundError:
        pass  # container may not have config.yaml at runtime path

    logging.info("========================================")
    logging.info(f"  GivEnergy Tariff Optimiser v{__version__}")
    logging.info("========================================")
    logging.info("--- Effective config (config.py) ---")
    logging.info(f"  BASE_LOAD_W            = {getattr(config, 'BASE_LOAD_W', 400)} W")
    logging.info(f"  BATTERY_CAPACITY_KWH   = {getattr(config, 'BATTERY_CAPACITY_KWH', 9.5)} kWh")
    logging.info(f"  MAX_BATTERY_CHARGE_RATE= {getattr(config, 'MAX_BATTERY_CHARGE_RATE', 3000)} W")
    logging.info(f"  IBOOST_MAX_DIVERT_RATE = {getattr(config, 'IBOOST_MAX_DIVERT_RATE', 3000)} W")
    logging.info(f"  SOLAR_KWP              = {getattr(config, 'SOLAR_KWP', 10.0)} kWp")
    logging.info(f"  IMPORT_TARIFF          = {getattr(config, 'AGILE_TARIFF_CODE', '?')}")
    logging.info(f"  EXPORT_TARIFF          = {getattr(config, 'EXPORT_TARIFF_CODE', '?')}")
    live_export = fetch_export_rate()
    fallback_export = getattr(config, 'EXPORT_RATE_P_FALLBACK', 12.0)
    logging.info(f"  EXPORT_RATE (live)     = {live_export:.2f}p/kWh  (fallback: {fallback_export:.2f}p)")
    logging.info(f"  ARBITRAGE_MARGIN_P     = {getattr(config, 'ARBITRAGE_MARGIN_P', 1.5)}p")
    logging.info(f"  GIVTCP_URL             = {getattr(config, 'GIVTCP_URL', None) or 'not set (Modbus fallback)'}")
    logging.info(f"  INTERVAL_MINUTES       = {interval}")
    logging.info(f"  RUN_ONCE               = {run_once}")
    logging.info("------------------------------------")
    test_openai_connection()
    logging.info("========================================")

    # Time-of-day thresholds (overridable via env)
    plan_hour = int(os.environ.get('DAILY_PLAN_HOUR', '17'))
    audit_hour = int(os.environ.get('DAILY_AUDIT_HOUR', '23'))
    is_startup = True
    logging.info(f"Scheduling: daily plan at {plan_hour:02d}:00, audit at {audit_hour:02d}:00 (local time).")

    while True:
        try:
            state = load_state()
            now_local = datetime.now().astimezone()
            today_str = now_local.date().isoformat()

            # 1. End-of-day audit — once per day at/after audit_hour
            if now_local.hour >= audit_hour and state.get('last_audit_date') != today_str:
                try:
                    await run_end_of_day_audit()
                    state['last_audit_date'] = today_str
                    save_state(state)
                except Exception as e:
                    logging.error(f"Audit failed: {e}", exc_info=True)

            # 2. Daily plan — fire if startup, OR we've never planned, OR calendar day has changed.
            last_plan_date = state.get('last_plan_date')
            need_first_plan = last_plan_date is None
            need_new_day_plan = (last_plan_date != today_str)

            if is_startup or need_first_plan or need_new_day_plan:
                if run_once:
                    logging.info("===== PLANNING RUN (RUN_ONCE) =====")
                else:
                    if is_startup:
                        reason = "add-on startup"
                    elif need_first_plan:
                        reason = "first plan since startup"
                    else:
                        reason = "new calendar day detected"
                    logging.info(f"===== DAILY PLANNING RUN ({reason}) =====")
                _last_plan.clear()
                await run_optimization()
                if _last_plan:
                    state = load_state()  # reload in case audit modified it
                    state['last_plan'] = dict(_last_plan)
                    state['last_plan_at'] = _last_plan.get('at')
                    state['last_plan_date'] = today_str
                    save_state(state)
                is_startup = False
            else:
                replan_needed = await run_light_monitor()
                if replan_needed:
                    logging.info("===== RE-PLANNING RUN (triggered by SoC drift) =====")
                    _last_plan.clear()
                    await run_optimization()
                    if _last_plan:
                        state = load_state()
                        state['last_plan'] = dict(_last_plan)
                        state['last_plan_at'] = _last_plan.get('at')
                        state['last_plan_date'] = today_str
                        save_state(state)

        except Exception as e:
            logging.error(f"Unhandled exception in main loop: {e}", exc_info=True)

        if run_once:
            logging.info("RUN_ONCE is enabled. Exiting.")
            break

        logging.info(f"Sleeping for {interval} minutes...")
        await asyncio.sleep(interval * 60)

if __name__ == "__main__":
    asyncio.run(main())
