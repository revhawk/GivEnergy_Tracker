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

# Single source of truth for the add-on version.
# MUST match `version:` in config.yaml (validated on startup).
__version__ = "1.0.12"


# Import custom configurations
try:
    import config
except ImportError:
    print("Error: config.py not found. Please ensure config.py is in the same directory.")
    sys.exit(1)

# OpenAI client (initialised after connection test)
_openai_client = None

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

# ── OpenAI Startup Connection Test ──────────────────────────────────────────
def test_openai_connection():
    """Test OpenAI API key at startup and initialise the client if valid."""
    global _openai_client
    openai_key = os.environ.get('OPENAI_API_KEY', '').strip() or getattr(config, 'OPENAI_API_KEY', '').strip()

    if not openai_key:
        logging.info("OpenAI API key not configured — ChatGPT audit DISABLED.")
        return False

    try:
        import openai
        client = openai.OpenAI(api_key=openai_key)
        # Minimal test call — lists available models, uses no tokens
        client.models.list()
        _openai_client = client
        logging.info("✓ OpenAI API connected successfully — ChatGPT audit ENABLED.")
        return True
    except Exception as e:
        logging.warning(f"✗ OpenAI API connection test FAILED: {e}")
        logging.warning("  Check your API key in the Configuration tab. ChatGPT audit DISABLED.")
        return False

# ── ChatGPT Veto (structured decision validator) ─────────────────────────────
# Returns (approve: bool, score: int|None, reason: str). Fails open — if the
# LLM is unavailable or misbehaves, approve=True so the deterministic plan wins.
def chatgpt_veto_plan(current_soc, battery_capacity_kwh, solar_total_kwh,
                     export_rate, upcoming_slots, charge_start, charge_end,
                     required_kwh, avg_price):
    if _openai_client is None:
        return True, None, "LLM disabled"

    rates_lines = "\n".join(
        f"  {s['start'].astimezone().strftime('%H:%M')}  {s['price']:.2f}p"
        for s in upcoming_slots[:48]
    )

    if charge_start and charge_end:
        action = (
            f"CHARGE from {charge_start.strftime('%H:%M')} to "
            f"{charge_end.strftime('%H:%M')} — {required_kwh:.1f} kWh at avg {avg_price:.2f}p/kWh"
        )
    else:
        action = "NO CHARGE — rely on solar and existing battery"

    efficiency_break_even = export_rate * 0.90
    system_msg = (
        f"You validate battery charging plans for a UK home.\n"
        f"\n"
        f"HARD FACTS — do not contradict these in your reasoning:\n"
        f"1. Import: Octopus Agile, prices vary every 30 min. Prices can go NEGATIVE (below 0.0p), which means the grid pays us to import energy.\n"
        f"2. Export: Octopus Outgoing at {export_rate:.2f}p/kWh. This rate is "
        f"FLAT and does NOT change during the day. Never suggest 'better export "
        f"rates later' — there are none.\n"
        f"3. Round-trip battery efficiency ≈ 90%. Importing above "
        f"{efficiency_break_even:.1f}p yields NEGATIVE arbitrage profit.\n"
        f"4. Only two profitable reasons to charge: (a) cover a genuine home-load "
        f"deficit later today, or (b) import price is below "
        f"{efficiency_break_even:.1f}p for real arbitrage.\n"
        f"5. IMPORTANT: Negative import rates (e.g. -4.74p) are below 0.0p and are extremely profitable because the grid is paying us to take power. Do not confuse negative numbers as being 'above' the break-even threshold. Always approve charging at negative rates.\n"
        f"\n"
        f"Reply ONLY with valid JSON:\n"
        f"  'approve' (bool) - would you apply this action?\n"
        f"  'score'   (int 1-10) - 1=terrible, 10=optimal for this data\n"
        f"  'reason'  (string ≤ 120 chars) - MUST reference concrete numbers "
        f"from the data. No vague appeals to 'later', 'better times', etc."
    )
    user_msg = (
        f"Battery: {current_soc}% of {battery_capacity_kwh} kWh\n"
        f"Solar forecast today: {solar_total_kwh:.1f} kWh\n"
        f"Export rate (FLAT all day): {export_rate:.2f}p/kWh\n"
        f"Break-even (post 90% efficiency): {efficiency_break_even:.2f}p/kWh\n"
        f"Upcoming Agile rates:\n{rates_lines}\n\n"
        f"Proposed action: {action}\n\n"
        f"Rate 1-10 and approve=true iff you would apply this action. "
        f"If false, cite the specific slot/rate that motivates rejection. Note if prices are negative."
    )

    try:
        response = _openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=150,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip()
        parsed = json.loads(content)
        approve = bool(parsed.get("approve", True))
        score = parsed.get("score")
        if isinstance(score, (int, float)):
            score = max(1, min(10, int(score)))
        else:
            score = None
        reason = str(parsed.get("reason", ""))[:200]
        return approve, score, reason
    except Exception as e:
        logging.warning(f"ChatGPT veto call failed ({e}); defaulting to approve=True")
        return True, None, f"LLM error: {e}"

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

def generate_daily_summary(stats):
    """Send previous day's stats to ChatGPT. Log the summary and improvement suggestions."""
    if _openai_client is None:
        logging.info("OpenAI not configured — skipping daily summary.")
        return

    date = stats.get('date', 'unknown')
    windows = stats.get('charge_windows', [])

    if windows:
        windows_desc = "\n".join([
            f"  • {w.get('start','')} → {w.get('end','')} "
            f"at avg {w.get('avg_price', 0):.2f}p/kWh  ({w.get('kwh', 0):.1f} kWh)"
            for w in windows
        ])
    else:
        windows_desc = "  • No grid charging was required all day"

    min_r = stats.get('min_rate_seen', 0)
    max_r = stats.get('max_rate_seen', 0)
    min_r_str = f"{min_r:.2f}" if min_r != float('inf') else "n/a"
    max_r_str = f"{max_r:.2f}" if max_r != float('-inf') else "n/a"

    prompt = (
        f"You are a UK home energy cost optimisation AI reviewing a full day of battery management.\n\n"
        f"DATE: {date}\n\n"
        f"=== BATTERY ===\n"
        f"Starting SoC: {stats.get('start_soc', '?')}%\n"
        f"Ending SoC:   {stats.get('end_soc', '?')}%\n"
        f"Optimiser runs today: {stats.get('runs', 0)} (every 30 min)\n\n"
        f"=== ENERGY ===\n"
        f"Peak solar forecast seen: {stats.get('solar_kwh_forecast', 0):.1f} kWh\n"
        f"Peak iBoost diversion forecast: {stats.get('iboost_kwh_forecast', 0):.1f} kWh\n"
        f"Total kWh charged from grid: {stats.get('total_charged_kwh', 0):.1f} kWh\n"
        f"Of which at negative rates: {stats.get('negative_rate_kwh', 0):.1f} kWh\n\n"
        f"=== OCTOPUS AGILE RATES ===\n"
        f"Cheapest rate seen: {min_r_str}p/kWh\n"
        f"Most expensive rate seen: {max_r_str}p/kWh\n\n"
        f"=== CHARGE WINDOWS SCHEDULED ===\n"
        f"{windows_desc}\n\n"
        f"Please respond with EXACTLY these three sections:\n\n"
        f"**DAILY SUMMARY**\n"
        f"3 sentences: How well did the system perform? Was money saved vs default behaviour? Any concerns?\n\n"
        f"**ESTIMATED SAVING**\n"
        f"Calculate the estimated £ saving today vs charging at the peak rate seen ({max_r_str}p/kWh). Show your working briefly.\n\n"
        f"**OPTIMISATION SUGGESTIONS**\n"
        f"Give 2-3 specific, technical suggestions to improve the Python algorithm based on today's data. "
        f"Be concrete — reference actual logic changes, thresholds, or new data sources. No generic advice."
    )

    try:
        response = _openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.4,
        )
        summary = response.choices[0].message.content.strip()

        border = "=" * 55
        logging.info("")
        logging.info(border)
        logging.info(f"  📊 DAILY SUMMARY — {date}")
        logging.info(border)
        for line in summary.split('\n'):
            logging.info(line)
        logging.info(border)
        logging.info("")

    except Exception as e:
        logging.warning(f"Daily summary ChatGPT call failed: {e}")

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

# Fetch current Octopus export rate. Cached for 6h so we don't hammer the API.
_export_rate_cache = {"rate": None, "fetched_at": None}

def fetch_export_rate():
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
        _export_rate_cache = {"rate": rate, "fetched_at": now}
        logging.info(f"Export rate: {rate:.2f}p/kWh (from Octopus)")
        return rate
    except Exception as e:
        logging.warning(f"Failed to fetch export rate ({e}); using fallback {fallback}p/kWh")
        return fallback

# Fetch Octopus Agile Rates
def fetch_agile_rates():
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
        
        # Sort chronologically (earliest first)
        slots.sort(key=lambda s: s['start'])
        return slots
    except Exception as e:
        logging.error(f"Error fetching Octopus Agile rates: {e}")
        return []

# Fetch Solar Forecast from Forecast.Solar (free tier API)
def fetch_solar_forecast():
    url = f"https://api.forecast.solar/estimate/{config.LATITUDE}/{config.LONGITUDE}/{config.SOLAR_DECLINATION}/{config.SOLAR_AZIMUTH}/{config.SOLAR_KWP}"
    logging.info(f"Fetching Solar Forecast from Forecast.Solar: {url}")
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 429:
            logging.warning("Forecast.Solar API rate-limited (too many requests). Using empty solar forecast.")
            return []
        response.raise_for_status()
        data = response.json()
        
        wh_period = data.get('result', {}).get('watt_hours_period', {})
        forecasts = []
        for time_str, wh in wh_period.items():
            dt_naive = datetime.fromisoformat(time_str)
            dt_local = dt_naive.astimezone()
            forecasts.append({
                'time': dt_local,
                'kwh': wh / 1000.0
            })
        forecasts.sort(key=lambda f: f['time'])
        return forecasts
    except Exception as e:
        logging.error(f"Error fetching solar forecast: {e}. Assuming 0 solar generation.")
        return []

# Map hourly solar forecast to half-hourly Octopus slots
def get_solar_kwh_for_slot(slot_start, slot_end, solar_forecasts):
    local_end = slot_end.astimezone()
    for f in solar_forecasts:
        f_time = f['time']
        if (f_time.year == local_end.year and 
            f_time.month == local_end.month and 
            f_time.day == local_end.day and 
            f_time.hour == local_end.hour):
            return f['kwh'] / 2.0
    return 0.0

# Read current charge-slot configuration from GivTCP (for the startup self-test).
# Returns dict with slot1_start/end and slot2_start/end (values may be None if
# GivTCP didn't return the expected field names — degrades gracefully).
def read_inverter_charge_slots():
    givtcp_url = getattr(config, 'GIVTCP_URL', None)
    if not givtcp_url:
        return None
    url = f"{givtcp_url.rstrip('/')}/getCache"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # GivTCP field naming varies between versions — try common variants
        # In v3, we also have nested objects like raw.invertor.charge_slot_1 = {"start": "00:00", "end": "00:00"}
        # Check raw.invertor.charge_slot_1 first
        def _get_slot_v3(slot_num, field):
            val = find_key_recursive(data, f"charge_slot_{slot_num}")
            if isinstance(val, dict):
                return val.get(field)
            return None

        s1 = _get_slot_v3(1, 'start')
        e1 = _get_slot_v3(1, 'end')
        s2 = _get_slot_v3(2, 'start')
        e2 = _get_slot_v3(2, 'end')

        if s1 is not None or e1 is not None:
            return {
                'slot1_start': s1,
                'slot1_end': e1,
                'slot2_start': s2,
                'slot2_end': e2,
            }

        candidates_start1 = ["Charge_start_time_slot_1", "Charge_Start_Time_1",
                             "Timeslots.Charge_start_time_slot_1", "charge_start_time_slot_1"]
        candidates_end1 = ["Charge_end_time_slot_1", "Charge_End_Time_1",
                           "charge_end_time_slot_1"]
        candidates_start2 = ["Charge_start_time_slot_2", "Charge_Start_Time_2",
                             "charge_start_time_slot_2"]
        candidates_end2 = ["Charge_end_time_slot_2", "Charge_End_Time_2",
                           "charge_end_time_slot_2"]
        def _first_found(keys):
            for k in keys:
                v = find_key_recursive(data, k)
                if v is not None:
                    return v
            return None
        return {
            'slot1_start': _first_found(candidates_start1),
            'slot1_end': _first_found(candidates_end1),
            'slot2_start': _first_found(candidates_start2),
            'slot2_end': _first_found(candidates_end2),
        }
    except Exception as e:
        logging.warning(f"Failed to read charge slots from GivTCP: {e}")
        return None

# Startup self-test — verifies the GivTCP write path by adding, reading back,
# and clearing a test charge slot. Runs once on daemon startup when enabled.
async def run_startup_write_test():
    logging.info("=" * 40)
    logging.info(" STARTUP WRITE-PATH SELF-TEST")
    logging.info("=" * 40)

    now_local = datetime.now().astimezone()
    # Test slot: 2 hours in the future (won't collide with an active plan since
    # planning runs are once daily at DAILY_PLAN_HOUR). 30 min duration.
    test_start = (now_local + timedelta(hours=2)).replace(second=0, microsecond=0)
    # Snap DOWN to nearest 30-min boundary
    test_start = test_start.replace(minute=(test_start.minute // 30) * 30)
    test_end = test_start + timedelta(minutes=30)
    expected_start_hhmm = test_start.strftime("%H%M")
    expected_end_hhmm = test_end.strftime("%H%M")

    logging.info(f"Test slot: {test_start.strftime('%H:%M')} → {test_end.strftime('%H:%M')} (100%)")

    try:
        # Step 1: write the test slot
        logging.info("[1/4] Writing test slot via GivTCP...")
        ok = await set_inverter_charge_slots(test_start, test_end, charge_target=100)
        if not ok:
            logging.error("[1/4] FAIL — set_inverter_charge_slots returned False")
            return False
        logging.info("[1/4] PASS — write returned success")

        await asyncio.sleep(8)  # let GivTCP cache propagate

        # Step 2: read back and verify
        logging.info("[2/4] Reading back charge slots from GivTCP...")
        slots = read_inverter_charge_slots()
        if slots is None:
            logging.warning("[2/4] SKIP — could not read back (GivTCP fields not found or unreachable)")
        else:
            logging.info(f"[2/4] Read: slot1={slots.get('slot1_start')} → {slots.get('slot1_end')}, "
                          f"slot2={slots.get('slot2_start')} → {slots.get('slot2_end')}")
             # GivTCP returns times as "HH:MM:SS" or "HH:MM" — normalize to "HH:MM"
            def _norm(v):
                if v is None: return "00:00"
                parts = str(v).split(':')
                if len(parts) >= 2:
                    return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
                # If no colon, try to parse HHMM
                s = str(v).replace(":", "")
                if len(s) >= 4:
                    return f"{s[:2]}:{s[2:4]}"
                return "00:00"

            expected_start_hh_mm = test_start.strftime("%H:%M")
            expected_end_hh_mm = test_end.strftime("%H:%M")

            if _norm(slots.get('slot1_start')) == expected_start_hh_mm and \
               _norm(slots.get('slot1_end')) == expected_end_hh_mm:
                logging.info(f"[2/4] PASS — slot 1 matches expected {expected_start_hh_mm} → {expected_end_hh_mm}")
            else:
                logging.warning(
                    f"[2/4] MISMATCH — expected slot1={expected_start_hh_mm} → {expected_end_hh_mm}, "
                    f"got {_norm(slots.get('slot1_start'))} → {_norm(slots.get('slot1_end'))}. "
                    f"(May be a field-name mismatch — check via GivEnergy app manually.)"
                )

        # Step 3: clear
        logging.info("[3/4] Clearing test slot via GivTCP...")
        ok = await set_inverter_charge_slots(None, None)
        if not ok:
            logging.error("[3/4] FAIL — clear returned False")
            return False
        logging.info("[3/4] PASS — clear returned success")

        await asyncio.sleep(8)  # let GivTCP cache propagate clearing

        # Step 4: read back and verify cleared
        logging.info("[4/4] Reading back to verify slot cleared...")
        slots = read_inverter_charge_slots()
        if slots is None:
            logging.warning("[4/4] SKIP — could not read back after clear")
        else:
            def _norm(v):
                if v is None: return "00:00"
                parts = str(v).split(':')
                if len(parts) >= 2:
                    return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
                return "00:00"
            s1 = _norm(slots.get('slot1_start'))
            e1 = _norm(slots.get('slot1_end'))
            if s1 == "00:00" and e1 == "00:00":
                logging.info("[4/4] PASS — slot 1 is cleared (00:00 → 00:00)")
            else:
                logging.warning(f"[4/4] slot 1 not cleared as expected: {s1} → {e1}")

        logging.info("=" * 40)
        logging.info(" WRITE-PATH SELF-TEST COMPLETE")
        logging.info("=" * 40)
        return True
    except Exception as e:
        logging.error(f"Startup write-test crashed: {e}", exc_info=True)
        # Best-effort clear so we don't leave a stray slot behind
        try:
            await set_inverter_charge_slots(None, None)
        except Exception:
            pass
        return False

# Connect to Inverter and get State of Charge (SoC)
async def get_inverter_soc():
    # 1. Try GivTCP REST API if configured
    givtcp_url = getattr(config, 'GIVTCP_URL', None)
    if givtcp_url:
        url = f"{givtcp_url.rstrip('/')}/getCache"
        logging.info(f"Connecting to GivTCP REST API at {url} to fetch current SoC...")
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            soc = find_key_recursive(data, "SOC")
            if soc is not None:
                logging.info(f"GivTCP: Current battery State of Charge (SoC): {soc}%")
                return int(soc)
            else:
                logging.warning("Could not find 'SOC' key in GivTCP cache response. Trying Modbus fallback...")
        except Exception as e:
            logging.warning(f"GivTCP API error: {e}. Trying Modbus fallback...")

    # 2. Fall back to local Modbus TCP
    if not HAS_MODBUS:
        logging.error(
            "SoC read FAILED — GivTCP unreachable AND Modbus package unavailable. "
            "Cannot fetch battery state; aborting this run. Returning None."
        )
        return None


    port = getattr(config, 'INVERTER_PORT', 8899)
    logging.info(f"Connecting to GivEnergy Inverter at {config.INVERTER_IP}:{port} via Modbus TCP...")
    client = Client(host=config.INVERTER_IP, port=port)
    try:
        await client.connect()
        await client.refresh_plant(full_refresh=True)
        soc = client.plant.inverter.battery_state_of_charge
        logging.info(f"Modbus: Current battery State of Charge (SoC): {soc}%")
        await client.close()
        return soc
    except Exception as e:
        logging.error(f"Error communicating via Modbus: {e}. Falling back to 25% SoC.")
        return 25

# Write charge slots to Inverter
async def set_inverter_charge_slots(start_time, end_time, charge_target=100):
    # 1. Try GivTCP REST API if configured
    givtcp_url = getattr(config, 'GIVTCP_URL', None)
    if givtcp_url:
        base_url = givtcp_url.rstrip('/')
        try:
            if start_time and end_time:
                # GivTCP v3 requires HH:MM format (not HHMM) and integer chargeToPercent.
                if start_time.date() == end_time.date():
                    s_str = start_time.strftime("%H:%M")
                    e_str = end_time.strftime("%H:%M")
                    logging.info(f"GivTCP: Setting slot 1: {s_str} to {e_str}")
                    r = requests.post(f"{base_url}/setChargeSlot1", json={
                        "start": s_str,
                        "finish": e_str,
                        "chargeToPercent": charge_target
                    }, timeout=10)
                    r.raise_for_status()
                    r2 = requests.post(f"{base_url}/setChargeSlot2", json={"start": "00:00", "finish": "00:00", "chargeToPercent": 0}, timeout=10)
                    r2.raise_for_status()
                else:
                    s_str1 = start_time.strftime("%H:%M")
                    e_str1 = "23:59"
                    s_str2 = "00:00"
                    e_str2 = end_time.strftime("%H:%M")

                    logging.info(f"GivTCP: Spanning midnight. Slot 1: {s_str1}-{e_str1}, Slot 2: {s_str2}-{e_str2}")
                    r1 = requests.post(f"{base_url}/setChargeSlot1", json={
                        "start": s_str1,
                        "finish": e_str1,
                        "chargeToPercent": charge_target
                    }, timeout=10)
                    r1.raise_for_status()

                    r2 = requests.post(f"{base_url}/setChargeSlot2", json={
                        "start": s_str2,
                        "finish": e_str2,
                        "chargeToPercent": charge_target
                    }, timeout=10)
                    r2.raise_for_status()

                # Enable charging AFTER slots are written.
                # These endpoints are best-effort — some GivTCP versions don't expose
                # all of them. A 404 is logged as a warning but does NOT abort.
                logging.info(f"GivTCP: Enabling grid charge and setting target to {charge_target}%...")
                for _path, _payload in [
                    ("/setChargeTarget",     {"chargeToPercent": charge_target}),
                    ("/enableChargeTarget",  {"state": "enable"}),
                    ("/setChargeEnable",     {"state": "enable"}),
                ]:
                    try:
                        _r = requests.post(f"{base_url}{_path}", json=_payload, timeout=10)
                        _r.raise_for_status()
                    except Exception as _e:
                        logging.warning(f"GivTCP: {_path} unavailable ({_e}) — skipping.")
            else:
                logging.info("GivTCP: Disabling grid charging (clearing slots)...")
                # GivTCP v3: use HH:MM format, integer chargeToPercent
                r1 = requests.post(f"{base_url}/setChargeSlot1", json={"start": "00:00", "finish": "00:00", "chargeToPercent": 0}, timeout=10)
                r1.raise_for_status()
                r2 = requests.post(f"{base_url}/setChargeSlot2", json={"start": "00:00", "finish": "00:00", "chargeToPercent": 0}, timeout=10)
                r2.raise_for_status()
                # Best-effort disable.
                try:
                    requests.post(f"{base_url}/setChargeEnable", json={"state": "disable"}, timeout=10)
                except Exception:
                    pass

            logging.info("GivTCP: Configuration applied successfully.")
            return True
        except Exception as e:
            logging.error(f"GivTCP REST API write failed: {e}. Trying direct Modbus fallback...")

    # 2. Fall back to local Modbus TCP
    if not HAS_MODBUS:
        logging.error(
            "Inverter write FAILED — GivTCP unreachable AND Modbus package unavailable. "
            "Charge slot was NOT applied to the inverter."
        )
        return False

    port = getattr(config, 'INVERTER_PORT', 8899)
    client = Client(host=config.INVERTER_IP, port=port)
    try:
        await client.connect()
        # Wrap full_refresh in its own try/except: on ARM hardware certain
        # inverter register layouts can trigger a SIGBUS inside the native
        # givenergy-modbus Cython decoder if an unaligned read is attempted.
        # A failed refresh is non-fatal — we still attempt the write commands.
        try:
            await client.refresh_plant(full_refresh=True)
        except Exception as refresh_err:
            logging.warning(f"Modbus: refresh_plant failed ({refresh_err}); continuing with write commands anyway.")

        logging.info(f"Modbus: Setting charge target to {charge_target}%...")
        await client.one_shot_command(commands.set_charge_target(charge_target))

        # Helper: call set_charge_slot with slot_map if the attribute exists,
        # otherwise fall back to the older 2-arg signature.
        def _slot_map():
            try:
                return client.plant.inverter.slot_map
            except AttributeError:
                return None

        async def _write_slot(slot_num, ts):
            sm = _slot_map()
            if sm is not None:
                try:
                    await client.one_shot_command(commands.set_charge_slot(slot_num, ts, sm))
                    return
                except Exception:
                    pass  # fall through to 2-arg form
            await client.one_shot_command(commands.set_charge_slot(slot_num, ts))

        if start_time and end_time:
            logging.info(f"Modbus: Programming charge slots: {start_time.strftime('%H:%M')} to {end_time.strftime('%H:%M')}...")
            if start_time.date() == end_time.date():
                ts1 = TimeSlot.from_components(start_time.hour, start_time.minute, end_time.hour, end_time.minute)
                ts2 = TimeSlot.from_components(0, 0, 0, 0)
                await _write_slot(1, ts1)
                await _write_slot(2, ts2)
            else:
                ts1 = TimeSlot.from_components(start_time.hour, start_time.minute, 23, 59)
                ts2 = TimeSlot.from_components(0, 0, end_time.hour, end_time.minute)
                logging.info("Modbus: Splitting charge slot across midnight.")
                await _write_slot(1, ts1)
                await _write_slot(2, ts2)
        else:
            logging.info("Modbus: Clearing all charge slots...")
            ts_clear = TimeSlot.from_components(0, 0, 0, 0)
            await _write_slot(1, ts_clear)
            await _write_slot(2, ts_clear)

        logging.info("Modbus: Inverter configuration complete.")
        await client.close()
        return True
    except Exception as e:
        logging.error(f"Failed to configure inverter via Modbus: {e}")
        try:
            await client.close()
        except Exception:
            pass
        return False

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
        
    # 2. Fetch solar forecast
    solar_forecasts = fetch_solar_forecast()
    
    # 3. Get current battery SoC
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
    for slot in upcoming_slots:
        solar = get_solar_kwh_for_slot(slot['start'], slot['end'], solar_forecasts)
        load = (getattr(config, 'BASE_LOAD_W', 1000) / 1000.0) * 0.5 # 300W baseline -> 0.15kWh
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
            
    # Print a beautiful simulation timeline
    logging.info("--- 24-Hour Base Simulation (No Grid Charge) ---")
    logging.info(f"{'Time':<5} | {'Price':<6} | {'Solar':<6} | {'Load':<6} | {'Battery':<7} | {'iBoost':<6} | {'Export':<6} | {'Import':<6}")
    logging.info("-" * 70)
    for imp in imports:
        time_str = imp['slot']['start'].astimezone().strftime('%H:%M')
        price = f"{imp['slot']['price']:.1f}p"
        solar = f"{imp['solar']:.2f}"
        load = f"{imp['load']:.2f}"
        batt = f"{imp['batt_soc']:.0f}%"
        iboost = f"{imp['iboost']:.2f}" if imp['iboost'] > 0 else "-"
        export = f"{imp['export']:.2f}" if imp['export'] > 0 else "-"
        imp_val = f"{imp['import_needed']:.2f}" if imp['import_needed'] > 0 else "-"
        
        logging.info(f"{time_str:<5} | {price:<6} | {solar:<6} | {load:<6} | {batt:<7} | {iboost:<6} | {export:<6} | {imp_val:<6}")
        
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
            local_t = ns['start'].astimezone().strftime('%H:%M')
            logging.info(f"   {local_t}  {ns['price']:.2f}p/kWh  ← free money!")
    elif arbitrage_slots:
        logging.info(f"💰 Arbitrage opportunity: {len(arbitrage_slots)} slot(s) below {arbitrage_threshold:.2f}p")
        for a in arbitrage_slots[:6]:
            local_t = a['start'].astimezone().strftime('%H:%M')
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

    slots_to_charge = math.ceil(required_charge_kwh / max_charge_kwh_per_slot)
    slots_to_charge = max(1, min(slots_to_charge, 8))  # Between 30 mins and 4 hours

    logging.info(f"Target Grid Charge: {required_charge_kwh:.2f} kWh (~{slots_to_charge} half-hour slot(s))")

    # Slide a window to find the cheapest contiguous block
    # Negative rate slots will naturally be selected as they have the lowest prices
    best_window_start = None
    best_window_end = None
    min_window_cost = float('inf')

    for start_idx in range(len(upcoming_slots) - slots_to_charge + 1):
        window = upcoming_slots[start_idx : start_idx + slots_to_charge]
        avg_price = sum(s['price'] for s in window) / len(window)

        if avg_price < min_window_cost:
            min_window_cost = avg_price
            best_window_start = window[0]['start']
            best_window_end = window[-1]['end']

    if best_window_start and best_window_end:
        # If we're doing pure arbitrage (no home load deficit), require the window
        # average to actually beat the export rate — otherwise it's not profitable.
        is_pure_arbitrage = force_opportunistic_charge and total_import_kwh <= 0.2
        if is_pure_arbitrage and min_window_cost >= arbitrage_threshold:
            logging.info(f"Cheapest window avg {min_window_cost:.2f}p is not profitable vs export {export_rate:.2f}p — skipping charge.")
            approve, score, reason = chatgpt_veto_plan(
                current_soc, battery_capacity, total_solar_kwh, export_rate,
                upcoming_slots, None, None, 0, 0
            )
            score_str = f"{score}/10" if score is not None else "n/a"
            logging.info(f"LLM opinion (unprofitable-window): approve={approve}  score={score_str}  reason={reason}")
            _record_plan(action="no_charge", branch="unprofitable_window",
                         current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                         export_rate=export_rate,
                         min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         cheapest_window_avg=min_window_cost,
                         llm_approve=approve, llm_score=score, llm_reason=reason)
            await set_inverter_charge_slots(None, None)
            return

        local_start = best_window_start.astimezone()
        local_end = best_window_end.astimezone()
        rate_label = f"{min_window_cost:.2f}p/kWh" if min_window_cost >= 0 else f"{min_window_cost:.2f}p/kWh (NEGATIVE — grid pays us!)"
        logging.info(f"Optimal Charge Window: {local_start.strftime('%H:%M')} → {local_end.strftime('%H:%M')}  |  Avg: {rate_label}")

        # Report economics
        charge_cost_p = required_charge_kwh * min_window_cost
        if min_window_cost < export_rate:
            profit_per_kwh = export_rate - min_window_cost
            estimated_profit_p = required_charge_kwh * profit_per_kwh
            logging.info(
                f"Economics: charge {required_charge_kwh:.1f} kWh × {min_window_cost:.2f}p = {charge_cost_p:.0f}p cost  "
                f"|  vs {export_rate:.2f}p export = {profit_per_kwh:.2f}p/kWh profit  "
                f"|  est. daily gain £{estimated_profit_p/100:.2f}"
            )
        else:
            logging.info(f"Economics: charge {required_charge_kwh:.1f} kWh × {min_window_cost:.2f}p = {charge_cost_p:.0f}p  (deficit charge, above export rate)")

        # LLM veto: if the model rejects the plan, fall back to clearing slots.
        # Bypass the veto if the average cost is negative — grid is paying us to charge,
        # so any LLM reject is a mathematical hallucination.
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
            logging.info("LLM VETOED the charge plan — clearing slots as fallback (deterministic plan overridden).")
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

# ── Light monitor: cheap SoC check, no LLM, no inverter writes ──────────────
async def run_light_monitor():
    logging.info("--- Light monitor tick ---")
    try:
        soc = await get_inverter_soc()
        if soc is not None:
            logging.info(f"Battery SoC: {soc}%  (no re-planning — next plan run scheduled per DAILY_PLAN_HOUR)")
        else:
            logging.warning("Could not read SoC from GivTCP.")
    except Exception as e:
        logging.warning(f"Monitor read failed: {e}")

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
    logging.info(f"  BASE_LOAD_W            = {getattr(config, 'BASE_LOAD_W', 1000)} W")
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
    logging.info(f"Scheduling: daily plan at {plan_hour:02d}:00, audit at {audit_hour:02d}:00 (local time).")

    # Optional startup self-test — verifies the GivTCP write path is working by
    # briefly setting and clearing a test slot. Runs once per daemon startup.
    if os.environ.get('STARTUP_WRITE_TEST', 'false').lower() in ('true', '1', 'yes'):
        try:
            await run_startup_write_test()
        except Exception as e:
            logging.error(f"Startup write-test errored (continuing anyway): {e}", exc_info=True)

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

            # 2. Daily plan — fire if we've never planned, OR today's plan is stale.
            last_plan_date = state.get('last_plan_date')
            need_first_plan = last_plan_date is None
            need_new_day_plan = (last_plan_date != today_str)

            if need_first_plan or need_new_day_plan:
                if run_once:
                    logging.info("===== PLANNING RUN (RUN_ONCE) =====")
                else:
                    reason = "first plan since startup" if need_first_plan else "new calendar day detected"
                    logging.info(f"===== DAILY PLANNING RUN ({reason}) =====")
                _last_plan.clear()
                await run_optimization()
                if _last_plan:
                    state = load_state()  # reload in case audit modified it
                    state['last_plan'] = dict(_last_plan)
                    state['last_plan_at'] = _last_plan.get('at')
                    state['last_plan_date'] = today_str
                    save_state(state)
            else:
                await run_light_monitor()

        except Exception as e:
            logging.error(f"Unhandled exception in main loop: {e}", exc_info=True)

        if run_once:
            logging.info("RUN_ONCE is enabled. Exiting.")
            break

        logging.info(f"Sleeping for {interval} minutes...")
        await asyncio.sleep(interval * 60)

if __name__ == "__main__":
    asyncio.run(main())
