"""24-Hour Battery & Solar Simulation Engine.

Simulates 24-hour battery SoC evolution (48 x 30-min slots), evaluates excess solar,
load profile forecasts, iBoost hot water diversion, export pricing, and calculates
optimal grid charge windows and arbitrage opportunities.
"""

import sys
import math
import logging
from datetime import datetime, timezone, timedelta

try:
    import config
except ImportError:
    config = None

import state
import tariffs
import solar
import profiler
import givtcp
import llm
import octoplus
import axle


def _op():
    """Dynamically resolve functions from optimiser module if patched by tests, otherwise default to domain modules."""
    opt_mod = sys.modules.get('optimiser')
    return opt_mod if opt_mod else sys.modules[__name__]


async def run_light_monitor():
    """Light monitor tick: check battery SoC, record load telemetry, and test for plan drift."""
    logging.info("--- Light monitor tick ---")
    try:
        opt = _op()
        get_telemetry_fn = getattr(opt, 'get_inverter_telemetry', givtcp.get_inverter_telemetry)
        get_soc_fn = getattr(opt, 'get_inverter_soc', givtcp.get_inverter_soc)
        load_state_fn = getattr(opt, 'load_state', state.load_state)
        save_state_fn = getattr(opt, 'save_state', state.save_state)

        telemetry = await get_telemetry_fn()
        realtime_soc = None
        if telemetry and 'soc' in telemetry:
            realtime_soc = telemetry['soc']
            if 'load_power' in telemetry and telemetry['load_power'] is not None:
                cur_state = load_state_fn()
                now_local = datetime.now().astimezone()
                slot_key = now_local.strftime("%H:%M")
                day_type = "weekday" if now_local.weekday() < 5 else "weekend"
                part_key = f"{day_type}_{slot_key}"
                slot_kwh = (telemetry['load_power'] / 1000.0) * 0.5
                history_dict = cur_state.setdefault('load_profile_history', {})
                
                # Update partitioned history (retains 14 weekday/weekend readings)
                part_list = history_dict.setdefault(part_key, [])
                part_list.append(round(slot_kwh, 3))
                if len(part_list) > 14:
                    history_dict[part_key] = part_list[-14:]
                    
                # Update legacy non-partitioned key for backward compatibility
                history_list = history_dict.setdefault(slot_key, [])
                history_list.append(round(slot_kwh, 3))
                if len(history_list) > 14:
                    history_dict[slot_key] = history_list[-14:]
                save_state_fn(cur_state)
        else:
            realtime_soc = await get_soc_fn()

        if realtime_soc is None:
            logging.warning("Could not read SoC from GivTCP.")
            return False

        cur_state = load_state_fn()
        planned_schedule = cur_state.get("planned_soc_schedule", [])
        now_utc = datetime.now(timezone.utc)
        drift_threshold = float(getattr(config, 'SOC_DRIFT_THRESHOLD_PCT', 15.0) if config else 15.0)

        current_planned = None
        parse_iso_fn = getattr(opt, 'parse_utc_iso', tariffs.parse_utc_iso)
        for item in planned_schedule:
            p_start = parse_iso_fn(item['start'])
            p_end = parse_iso_fn(item['end'])
            if p_start <= now_utc < p_end:
                current_planned = item.get('soc')
                break

        now_time_str = datetime.now().astimezone().strftime("%H:%M:%S")
        if isinstance(telemetry, dict) and telemetry.get('pv_power') is not None:
            pv_w = telemetry.get('pv_power', 0.0)
            load_w = telemetry.get('load_power', 0.0)
            surplus_w = pv_w - load_w
            if pv_w >= 2000.0 and surplus_w >= 1500.0 and realtime_soc < 98:
                logging.info(
                    f"⏰ [{now_time_str}] ☀️ [SOLAR GUARD 30-MIN TICK] Live PV Power ({pv_w:.0f}W) generates {surplus_w:.0f}W surplus! "
                    f"Battery is charging 100% from free solar power."
                )

        if current_planned is not None:
            drift = abs(realtime_soc - current_planned)
            if drift > drift_threshold:
                logging.warning(
                    f"⏰ [{now_time_str}] ⚡ SoC DRIFT ALERT: real-time SoC ({realtime_soc}%) vs planned SoC ({current_planned:.0f}%) "
                    f"differs by {drift:.1f}% (> threshold {drift_threshold:.1f}%). Triggering re-planning!"
                )
                return True
            else:
                logging.info(
                    f"⏰ [{now_time_str}] Battery SoC: {realtime_soc}% vs planned {current_planned:.0f}% "
                    f"(drift: {drift:.1f}% <= threshold {drift_threshold:.1f}%) — no re-planning needed"
                )
        else:
            logging.info(f"⏰ [{now_time_str}] Battery SoC: {realtime_soc}%  (no active plan slot found for drift comparison)")

        return False
    except Exception as e:
        logging.warning(f"Monitor read failed: {e}")
        return False


def _merge_slot_blocks(slot_list):
    """Merge contiguous half-hour slots into continuous time blocks."""
    if not slot_list:
        return []
    sorted_slots = sorted(slot_list, key=lambda s: s['start'])
    merged = []
    c_start = sorted_slots[0]['start']
    c_end = sorted_slots[0]['end']
    c_prices = [sorted_slots[0]['price']]
    for nxt in sorted_slots[1:]:
        if nxt['start'] == c_end:
            c_end = nxt['end']
            c_prices.append(nxt['price'])
        else:
            merged.append({'start': c_start, 'end': c_end, 'avg_price': sum(c_prices) / len(c_prices)})
            c_start = nxt['start']
            c_end = nxt['end']
            c_prices = [nxt['price']]
    merged.append({'start': c_start, 'end': c_end, 'avg_price': sum(c_prices) / len(c_prices)})
    return merged


def build_plan_candidates(day_classification, current_soc, battery_capacity,
                          upcoming_slots, total_solar_kwh, solar_comparison, export_rate,
                          total_import_kwh=0.0):
    """Generate 3 strategic candidate charging plans (Option B) for LLM evaluation.
    
    Candidate 1: Overnight Defensive Pre-Charge (guarantees defensive buffer e.g. 55%
                 to prevent 4% collapse on overcast days, while leaving 40%-50% headroom
                 for sunny surges).
    Candidate 2: Midday Dip Top-Up (pre-charge during cheap 10:00-15:30 dip before 16:00 peak).
    Candidate 3: Pure Solar Self-Consumption (zero grid draw).
    """
    candidates = []
    max_charge_rate = getattr(config, 'MAX_BATTERY_CHARGE_RATE', 3000) if config else 3000
    max_charge_kwh_per_slot = (max_charge_rate / 1000.0) * 0.5
    arbitrage_margin = getattr(config, 'ARBITRAGE_MARGIN_P', 1.5) if config else 1.5
    arbitrage_threshold = export_rate - arbitrage_margin

    negative_slots = [s for s in upcoming_slots if s['price'] < 0]
    arbitrage_slots = [s for s in upcoming_slots if s['price'] < arbitrage_threshold]

    # Target SoC determination based on user rules
    if day_classification == "MEDIUM_SOLAR_PARTIAL_PRECHARGE":
        # Target 55% defensive floor, leaving ~45% headroom for sunny bursts
        target_soc_opt1 = int(getattr(config, 'DEFENSIVE_HEADROOM_TARGET_SOC', 55))
        target_soc_opt2 = min(80, target_soc_opt1 + 10)
    elif day_classification == "LOW_SOLAR_GRID_PRECHARGE":
        # Overcast/winter: target 95% full pre-charge so battery is never stranded at 4%
        target_soc_opt1 = int(getattr(config, 'LOW_SOLAR_TARGET_SOC', 95))
        target_soc_opt2 = 90
    elif day_classification == "NEGATIVE_RATE_OPPORTUNITY":
        target_soc_opt1 = 100
        target_soc_opt2 = 100
    else:  # HIGH_SOLAR_SELF_CONSUMPTION
        target_soc_opt1 = int(current_soc)
        target_soc_opt2 = min(60, int(current_soc) + 10)

    # Determine pre-peak candidate slots relative to current execution time
    now_dt = upcoming_slots[0]['start'].astimezone() if upcoming_slots else datetime.now().astimezone()
    if now_dt.hour < 7:
        pre_peak_slots = [
            s for s in upcoming_slots
            if s['start'].astimezone().date() == now_dt.date() and s['start'].astimezone().hour < 7
        ]
        if not pre_peak_slots:
            pre_peak_slots = [
                s for s in upcoming_slots
                if s['start'].astimezone().date() == now_dt.date() and s['start'].astimezone().hour < 16
            ]
    elif 7 <= now_dt.hour < 16:
        # Daytime re-plan (e.g. 10:00): candidate slots MUST fall today before the 16:00 peak!
        pre_peak_slots = [
            s for s in upcoming_slots
            if s['start'].astimezone().date() == now_dt.date() and s['start'].astimezone().hour < 16
        ]
    else:
        # Evening (>= 16:00): look for overnight slots before 07:00 tomorrow
        pre_peak_slots = [s for s in upcoming_slots if s['start'].astimezone().hour < 7]

    if not pre_peak_slots:
        pre_peak_slots = [s for s in upcoming_slots if s['start'].astimezone().hour < 16]
    has_axle_export = any(s.get('is_axle_export') for s in upcoming_slots)

    # 1. OPTION 1: Overnight / Early Morning Pre-Charge (or Pre-Peak Defensive Window)
    if negative_slots:
        opt1_slots_raw = negative_slots
        opt1_strategy = "negative_rates"
        opt1_name = "Negative Rate Full Charge (Grid Pays You)"
        target_soc_opt1 = 100
        target_soc_opt2 = 100
        kwh_opt1 = max(0.0, (100.0 - current_soc) / 100.0 * battery_capacity)
    elif has_axle_export:
        opt1_slots_raw = pre_peak_slots or upcoming_slots
        opt1_strategy = "axle_flexibility"
        opt1_name = "Axle Flexibility Pre-Charge (Target 100% for 100p/kWh Export)"
        target_soc_opt1 = 100
        target_soc_opt2 = 100
        kwh_opt1 = max(0.0, (100.0 - current_soc) / 100.0 * battery_capacity)
    elif arbitrage_slots and day_classification != "MEDIUM_SOLAR_PARTIAL_PRECHARGE":
        opt1_slots_raw = arbitrage_slots
        opt1_strategy = "arbitrage"
        opt1_name = f"Arbitrage Pre-Charge (< {arbitrage_threshold:.1f}p/kWh)"
        target_soc_opt1 = max(target_soc_opt1, int(getattr(config, 'ARBITRAGE_TARGET_SOC', 100) if config else 100))
        kwh_opt1 = max(0.0, (target_soc_opt1 - current_soc) / 100.0 * battery_capacity)
    elif total_import_kwh > 0.2:
        opt1_slots_raw = pre_peak_slots
        opt1_strategy = "scheduled"
        opt1_name = f"Optimal Deficit Pre-Charge"
        kwh_opt1 = min(total_import_kwh * 1.10, battery_capacity * (100.0 - current_soc) / 100.0)
        target_soc_opt1 = min(100, int(current_soc + (kwh_opt1 / battery_capacity) * 100))
    else:
        opt1_slots_raw = pre_peak_slots
        opt1_strategy = "midday_defensive" if (7 <= now_dt.hour < 16) else "overnight_defensive"
        opt1_name = f"Defensive Pre-Charge (Target {target_soc_opt1}%)"
        kwh_opt1 = max(0.0, (target_soc_opt1 - current_soc) / 100.0 * battery_capacity)

    slots_needed_opt1 = math.ceil(kwh_opt1 / max_charge_kwh_per_slot) if kwh_opt1 > 0 else 0
    slots_needed_opt1 = min(slots_needed_opt1, len(opt1_slots_raw))

    slots_opt1 = []
    avg_price_opt1 = 0.0
    if slots_needed_opt1 > 0 and opt1_slots_raw:
        if opt1_strategy in ("negative_rates", "arbitrage"):
            sorted_cheap = sorted(opt1_slots_raw, key=lambda s: s['start'])
            blocks_opt1 = _merge_slot_blocks(sorted_cheap)
            avg_price_opt1 = sum(s['price'] for s in opt1_slots_raw) / len(opt1_slots_raw)
        else:
            sorted_cheapest = sorted(opt1_slots_raw, key=lambda s: s['price'])[:slots_needed_opt1]
            sorted_chronological = sorted(sorted_cheapest, key=lambda s: s['start'])
            avg_price_opt1 = sum(s['price'] for s in sorted_chronological) / len(sorted_chronological)
            blocks_opt1 = _merge_slot_blocks(sorted_chronological)

        if len(blocks_opt1) > 10:
            blocks_opt1 = sorted(blocks_opt1, key=lambda b: b['avg_price'])[:10]
            blocks_opt1 = sorted(blocks_opt1, key=lambda b: b['start'])

        slots_opt1 = [(b['start'].astimezone(), b['end'].astimezone()) for b in blocks_opt1]

    headroom_opt1 = max(0.0, battery_capacity * (1.0 - target_soc_opt1 / 100.0))
    desc_opt1 = ', '.join([f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in slots_opt1]) if slots_opt1 else 'None (current SoC meets target)'

    candidates.append({
        'id': 1,
        'name': opt1_name,
        'strategy': opt1_strategy,
        'target_soc': target_soc_opt1,
        'kwh': round(kwh_opt1, 2),
        'avg_price': round(avg_price_opt1, 2),
        'estimated_cost_pence': round(kwh_opt1 * avg_price_opt1, 1),
        'headroom_kwh': round(headroom_opt1, 2),
        'slots': slots_opt1,
        'slots_desc': desc_opt1,
        'rationale': (
            f"Negative rate import pays you to consume power (avg {avg_price_opt1:.2f}p/kWh). "
            f"Fills battery to 100% capacity so subsequent solar generation is 100% exported at {export_rate:.2f}p/kWh for double profit."
            if opt1_strategy == "negative_rates" else
            f"Pre-charges during cheapest window (avg {avg_price_opt1:.2f}p). "
            f"Guarantees {target_soc_opt1}% defensive floor to avoid 4% collapse if overcast, "
            f"while preserving {headroom_opt1:.1f} kWh ({headroom_opt1/max(0.1, battery_capacity)*100:.0f}%) headroom for sunny bursts."
        )
    })

    # 2. OPTION 2: Midday Dip Window (10:00 - 15:30)
    if now_dt.hour < 16:
        daytime_dip_slots = [
            s for s in upcoming_slots
            if s['start'].astimezone().date() == now_dt.date() and 10 <= s['start'].astimezone().hour < 16
        ]
    else:
        tomorrow_date = now_dt.date() + timedelta(days=1)
        daytime_dip_slots = [
            s for s in upcoming_slots
            if s['start'].astimezone().date() == tomorrow_date and 10 <= s['start'].astimezone().hour < 16
        ]
    if not daytime_dip_slots:
        daytime_dip_slots = [s for s in upcoming_slots if 10 <= s['start'].astimezone().hour < 16]
    kwh_opt2 = max(0.0, (target_soc_opt2 - current_soc) / 100.0 * battery_capacity)
    slots_needed_opt2 = math.ceil(kwh_opt2 / max_charge_kwh_per_slot) if kwh_opt2 > 0 else 0
    slots_needed_opt2 = min(slots_needed_opt2, len(daytime_dip_slots))

    slots_opt2 = []
    avg_price_opt2 = 0.0
    if slots_needed_opt2 > 0 and daytime_dip_slots:
        sorted_dip = sorted(daytime_dip_slots, key=lambda s: s['price'])[:slots_needed_opt2]
        sorted_chronological2 = sorted(sorted_dip, key=lambda s: s['start'])
        avg_price_opt2 = sum(s['price'] for s in sorted_chronological2) / len(sorted_chronological2)
        blocks_opt2 = _merge_slot_blocks(sorted_chronological2)
        if len(blocks_opt2) > 10:
            blocks_opt2 = sorted(blocks_opt2, key=lambda b: b['avg_price'])[:10]
            blocks_opt2 = sorted(blocks_opt2, key=lambda b: b['start'])
        slots_opt2 = [(b['start'].astimezone(), b['end'].astimezone()) for b in blocks_opt2]

    headroom_opt2 = max(0.0, battery_capacity * (1.0 - target_soc_opt2 / 100.0))
    desc_opt2 = ', '.join([f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in slots_opt2]) if slots_opt2 else 'None'

    candidates.append({
        'id': 2,
        'name': f"Midday Dip Top-Up (Target {target_soc_opt2}%)",
        'strategy': 'midday_dip',
        'target_soc': target_soc_opt2,
        'kwh': round(kwh_opt2, 2),
        'avg_price': round(avg_price_opt2, 2),
        'estimated_cost_pence': round(kwh_opt2 * avg_price_opt2, 1),
        'headroom_kwh': round(headroom_opt2, 2),
        'slots': slots_opt2,
        'slots_desc': desc_opt2,
        'rationale': (
            f"Delays pre-charge until midday rate dip (10:00-15:30, avg {avg_price_opt2:.2f}p). "
            f"Allows morning solar to charge first; tops up before the 16:00 peak."
        )
    })

    # 3. OPTION 3: Pure Solar Self-Consumption (0p Grid Draw)
    headroom_opt3 = max(0.0, (100.0 - current_soc) / 100.0 * battery_capacity)
    candidates.append({
        'id': 3,
        'name': "Pure Solar Self-Consumption (Zero Grid Draw)",
        'strategy': 'solar_only',
        'target_soc': int(current_soc),
        'kwh': 0.0,
        'avg_price': 0.0,
        'estimated_cost_pence': 0.0,
        'headroom_kwh': round(headroom_opt3, 2),
        'slots': [],
        'slots_desc': "None",
        'rationale': (
            "0p grid import cost. Preserves 100% available headroom for solar. "
            + ("Recommended on clear high solar days." if day_classification == "HIGH_SOLAR_SELF_CONSUMPTION" else
               "HIGH RISK in autumn: if solar underperforms, battery will collapse to 4% and import at 30p-40p peak rates.")
        )
    })

    return candidates


async def run_optimization():
    """Main Optimization Engine run: fetch tariffs, simulate battery evolution, and program GivTCP slots."""
    logging.info(f"===== ENERGY OPTIMIZATION RUN =====")
    
    opt = _op()
    fetch_agile_fn = getattr(opt, 'fetch_agile_rates', tariffs.fetch_agile_rates)
    fetch_solar_fn = getattr(opt, 'fetch_parallel_solar_forecasts', solar.fetch_parallel_solar_forecasts)
    get_solar_kwh_fn = getattr(opt, 'get_solar_kwh_for_slot', solar.get_solar_kwh_for_slot)
    get_load_kwh_fn = getattr(opt, 'get_load_kwh_for_slot', profiler.get_load_kwh_for_slot)
    get_telemetry_fn = getattr(opt, 'get_inverter_telemetry', givtcp.get_inverter_telemetry)
    get_soc_fn = getattr(opt, 'get_inverter_soc', givtcp.get_inverter_soc)
    fetch_export_fn = getattr(opt, 'fetch_export_rate', tariffs.fetch_export_rate)
    chatgpt_veto_fn = getattr(opt, 'chatgpt_veto_plan', llm.chatgpt_veto_plan)
    chatgpt_select_fn = getattr(opt, 'chatgpt_select_candidate', llm.chatgpt_select_candidate)
    record_plan_fn = getattr(opt, '_record_plan', state._record_plan)
    set_slots_fn = getattr(opt, 'set_inverter_charge_slots', givtcp.set_inverter_charge_slots)
    load_state_fn = getattr(opt, 'load_state', state.load_state)
    save_state_fn = getattr(opt, 'save_state', state.save_state)

    # 1. Fetch Octopus Agile prices
    rates = fetch_agile_fn()
    if not rates:
        logging.error("Could not retrieve Agile rates. Aborting optimization.")
        return
        
    # Filter for future rates only
    now_utc = datetime.now(timezone.utc)
    upcoming_slots = [r for r in rates if r['end'] > now_utc][:48] # Next 24 hours (48 slots)
    if not upcoming_slots:
        logging.error("No upcoming Agile rate slots available. Aborting.")
        return

    # 1b. Check for live Octoplus Power Down & Power Up sessions
    has_octo_creds = bool(getattr(config, 'OCTOPUS_ACCOUNT_NUMBER', None) and getattr(config, 'OCTOPUS_API_KEY', None)) if config else False
    if has_octo_creds:
        logging.info("Checking Octopus Octoplus sessions from: https://api.backend.octopus.energy/v1/graphql/")
    fetch_octoplus_fn = getattr(opt, 'fetch_live_octoplus_sessions', octoplus.fetch_live_octoplus_sessions)
    live_sessions = fetch_octoplus_fn() if fetch_octoplus_fn else []
    if live_sessions:
        for sess in live_sessions:
            if sess.get('end') and sess['end'] > now_utc:
                stype = sess.get('session_type')
                s_str = sess['start'].astimezone().strftime('%m-%d %H:%M')
                e_str = sess['end'].astimezone().strftime('%H:%M')
                if stype == 'power_down':
                    pts = sess.get('reward_points', 0)
                    logging.info(
                        f"⚡ [OCTOPLUS] Detected Power Down session: {sess.get('code')} "
                        f"({s_str} - {e_str}, {pts} OctoPoints/kWh). Enforcing zero grid import."
                    )
                elif stype == 'power_up':
                    logging.info(
                        f"⚡ [OCTOPLUS] Detected Power Up session: {sess.get('code')} "
                        f"({s_str} - {e_str}, Free Electricity). Prioritizing 100% battery pre-charge."
                    )

        # Apply Power Up and Power Down overrides to upcoming slots
        for s in upcoming_slots:
            if octoplus.is_power_up_slot_dt(s['start'], s['end'], live_sessions):
                s['price'] = 0.0
                s['is_power_up'] = True
            if octoplus.is_power_down_slot_dt(s['start'], s['end'], live_sessions):
                s['is_power_down'] = True
    elif has_octo_creds:
        logging.info("Octopus Octoplus: Polled successfully — no active or upcoming sessions scheduled.")

    # 1c. Check for active or upcoming Axle Energy Self-Dispatch events (ADR 0015)
    axle_event = None
    if axle.is_axle_enabled():
        logging.info("Checking Axle Energy Self-Dispatch events from: https://api.axle.energy/vpp/home-assistant/event")
        fetch_axle_fn = getattr(opt, 'fetch_axle_event', axle.fetch_axle_event)
        axle_event = fetch_axle_fn() if fetch_axle_fn else None
        if axle_event and axle_event.get('start') and axle_event.get('end'):
            a_start_str = axle_event['start'].astimezone().strftime('%m-%d %H:%M')
            a_end_str = axle_event['end'].astimezone().strftime('%H:%M')
            direction = axle_event.get('import_export', 'export')
            logging.info(
                f"⚡ [AXLE VPP] Active/Upcoming Axle Self-Dispatch Event: {a_start_str} - {a_end_str} ({direction.upper()})."
            )
            if direction == 'export':
                reward_p = float(getattr(config, 'AXLE_EXPORT_REWARD_P', 100.0) if config else 100.0)
                for s in upcoming_slots:
                    if axle_event['start'] <= s['start'] < axle_event['end']:
                        s['is_axle_export'] = True
                        s['export_rate'] = reward_p
        else:
            logging.info("Axle Energy VPP: Polled successfully — no active or upcoming flexibility events scheduled.")
        
    # 2. Fetch primary & shadow solar forecasts in parallel
    solar_forecasts, solar_comparison = fetch_solar_fn()
    
    # 3. Get current battery SoC & telemetry
    telemetry = await get_telemetry_fn()
    if telemetry and 'soc' in telemetry:
        current_soc = telemetry['soc']
    else:
        current_soc = await get_soc_fn()
        
    if current_soc is None:
        logging.error(
            "Cannot plan without a valid battery SoC reading. Aborting this optimization run. "
            "The tracker will retry on the next tick. Check GivTCP is running at "
            f"{getattr(config, 'GIVTCP_URL', '(not set)') if config else '(not set)'}."
        )
        return

    # 4. Simulate battery SoC evolution if we DO NOT grid-charge
    battery_capacity = getattr(config, 'BATTERY_CAPACITY_KWH', 9.5) if config else 9.5
    max_charge_rate = getattr(config, 'MAX_BATTERY_CHARGE_RATE', 3000) if config else 3000
    max_charge_kwh_per_slot = (max_charge_rate / 1000.0) * 0.5 # 3kW * 0.5h = 1.5kWh
    
    min_soc = 10.0 # Standard minimum reserve (10%)
    min_energy = battery_capacity * (min_soc / 100.0)
    max_energy = battery_capacity
    
    current_energy = battery_capacity * (current_soc / 100.0)
    
    energy = current_energy
    import_needed_slots = []
    imports = []
    
    state_data = load_state_fn()
    load_history = state_data.get('load_profile_history')

    # Run physical priority simulation: Solar -> Home Load -> Battery Charge -> iBoost Divert -> Export
    for idx, slot in enumerate(upcoming_slots):
        if idx == 0 and telemetry and 'pv_power' in telemetry and 'load_power' in telemetry:
            solar_kwh = (telemetry['pv_power'] / 1000.0) * 0.5
            load_kwh = (telemetry['load_power'] / 1000.0) * 0.5
            logging.info(
                f"Using live inverter telemetry for slot 0: "
                f"PV={telemetry['pv_power']:.0f}W ({solar_kwh:.2f}kWh), "
                f"Load={telemetry['load_power']:.0f}W ({load_kwh:.2f}kWh)"
            )
        else:
            solar_kwh = get_solar_kwh_fn(slot['start'], slot['end'], solar_forecasts)
            load_kwh = get_load_kwh_fn(slot['start'], slot['end'], load_history)
        net = load_kwh - solar_kwh
        
        iboost_divert = 0.0
        grid_export = 0.0
        import_needed = 0.0
        
        if slot.get('is_axle_export'):
            # Axle Flexibility Force Export at max discharge rate (3 kW = 1.5 kWh per slot)
            force_discharge = min(max_charge_kwh_per_slot, max(0.0, energy - min_energy))
            energy -= force_discharge
            if net < 0:
                grid_export = (-net) + force_discharge
                import_needed = 0.0
            else:
                if force_discharge >= net:
                    grid_export = force_discharge - net
                    import_needed = 0.0
                else:
                    grid_export = 0.0
                    import_needed = net - force_discharge
        elif net < 0:
            excess_solar = -net
            solar_charge = min(excess_solar, max_charge_kwh_per_slot, max_energy - energy)
            energy += solar_charge
            
            remaining_excess = excess_solar - solar_charge
            max_iboost_kwh = (getattr(config, 'IBOOST_MAX_DIVERT_RATE', 3000) / 1000.0 if config else 3.0) * 0.5
            iboost_divert = min(remaining_excess, max_iboost_kwh)
            grid_export = remaining_excess - iboost_divert
        else:
            discharge = min(net, energy - min_energy)
            energy -= discharge
            import_needed = net - discharge
            
        batt_soc = (energy / battery_capacity) * 100.0
        
        imports.append({
            'slot': slot,
            'import_needed': import_needed,
            'solar': solar_kwh,
            'load': load_kwh,
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
            
    logging.info("--- 24-Hour Base Simulation (No Grid Charge) ---")
    logging.info(f"{'Date/Time':<10} | {'Price':<6} | {'Solar':<6} | {'Load':<6} | {'Battery':<7} | {'iBoost':<6} | {'Export':<6} | {'Import':<6}")
    logging.info("-" * 75)
    for imp in imports:
        time_str = imp['slot']['start'].astimezone().strftime('%m-%d %H:%M')
        price = f"{imp['slot']['price']:.1f}p"
        solar_str = f"{imp['solar']:.2f}"
        load_str = f"{imp['load']:.2f}"
        batt = f"{imp['batt_soc']:.0f}%"
        iboost = f"{imp['iboost']:.2f}" if imp['iboost'] > 0 else "-"
        export = f"{imp['export']:.2f}" if imp['export'] > 0 else "-"
        imp_val = f"{imp['import_needed']:.2f}" if imp['import_needed'] > 0 else "-"
        
        logging.info(f"{time_str:<10} | {price:<6} | {solar_str:<6} | {load_str:<6} | {batt:<7} | {iboost:<6} | {export:<6} | {imp_val:<6}")
        
    total_import_kwh = sum(i['kwh'] for i in import_needed_slots)
    total_iboost_kwh = sum(i['iboost'] for i in imports)
    total_export_kwh = sum(i['export'] for i in imports)
    
    # Restrict total solar calculation to the upcoming slots horizon (prevents summing 48h across 2 days)
    if upcoming_slots:
        sim_start = upcoming_slots[0]['start']
        sim_end = upcoming_slots[-1]['end']
        total_solar_kwh = sum(
            f['kwh'] for f in solar_forecasts
            if sim_start <= f.get('time', f.get('start', sim_start)) <= sim_end
        )
    else:
        total_solar_kwh = sum(f['kwh'] for f in solar_forecasts)

    logging.info("--- Simulation Summary ---")
    logging.info(f"Total Grid Import Needed:  {total_import_kwh:.2f} kWh")
    logging.info(f"Expected iBoost Diversion: {total_iboost_kwh:.2f} kWh")
    logging.info(f"Expected Grid Export:      {total_export_kwh:.2f} kWh")

    export_rate = fetch_export_fn()
    margin = getattr(config, 'ARBITRAGE_MARGIN_P', 1.5) if config else 1.5
    arbitrage_threshold = export_rate - margin

    negative_slots = [s for s in upcoming_slots if s['price'] < 0]
    arbitrage_slots = [s for s in upcoming_slots if s['price'] < arbitrage_threshold]
    available_capacity_kwh = max_energy - current_energy

    logging.info(f"Export rate now: {export_rate:.2f}p/kWh  |  Arbitrage threshold: <{arbitrage_threshold:.2f}p")

    if negative_slots:
        logging.info(f"⚡ NEGATIVE RATE ALERT: {len(negative_slots)} slot(s) — grid pays YOU!")
        for ns in negative_slots[:8]:
            local_t = ns['start'].astimezone().strftime('%m-%d %H:%M')
            logging.info(f"  • {local_t} @ {ns['price']:.2f}p/kWh")
    # Weather-Adaptive Day Classification
    battery_capacity = float(getattr(config, 'BATTERY_CAPACITY_KWH', 9.5))
    available_capacity_kwh = max(0.0, (100.0 - current_soc) / 100.0 * battery_capacity)
    
    daytime_solar_surplus_kwh = sum(
        max(0.0, imp['solar'] - imp['load'])
        for imp in imports if imp['slot']['start'].astimezone().hour < 16
    )

    solar_self_sufficient = (
        daytime_solar_surplus_kwh >= available_capacity_kwh
        and available_capacity_kwh > 0.3
        and total_solar_kwh >= 15.0
    )

    medium_solar_day = (
        (0.35 * available_capacity_kwh <= daytime_solar_surplus_kwh or total_solar_kwh < 15.0)
        and total_solar_kwh >= 4.0
        and not solar_self_sufficient
    )

    if negative_slots:
        day_classification = "NEGATIVE_RATE_OPPORTUNITY"
    elif solar_self_sufficient:
        day_classification = "HIGH_SOLAR_SELF_CONSUMPTION"
    elif medium_solar_day:
        day_classification = "MEDIUM_SOLAR_PARTIAL_PRECHARGE"
    else:
        day_classification = "LOW_SOLAR_GRID_PRECHARGE"

    logging.info(
        f"☀️ [DAY CLASSIFICATION] {day_classification} "
        f"(Forecast Surplus: {daytime_solar_surplus_kwh:.2f} kWh | Deficit: {available_capacity_kwh:.2f} kWh | Solar Total: {total_solar_kwh:.2f} kWh)"
    )

    # 5. Generate Candidate Plan Options (Option B) for AI Co-Pilot Evaluation
    candidates = build_plan_candidates(
        day_classification=day_classification,
        current_soc=current_soc,
        battery_capacity=battery_capacity,
        upcoming_slots=upcoming_slots,
        total_solar_kwh=total_solar_kwh,
        solar_comparison=solar_comparison,
        export_rate=export_rate,
        total_import_kwh=total_import_kwh,
    )

    logging.info(f"Generated {len(candidates)} Candidate Charging Strategies for AI Evaluation (Option B):")
    for c in candidates:
        logging.info(f"  • Option {c['id']}: [{c['name']}] Target {c['target_soc']}% | {c['kwh']} kWh | Headroom: {c['headroom_kwh']} kWh | Slots: {c['slots_desc']}")

    # 6. Evaluate Candidate Strategies via ChatGPT AI Co-Pilot
    approve, sel_id, score, reason, weather_commentary = chatgpt_select_fn(
        day_classification=day_classification,
        current_soc=current_soc,
        battery_capacity_kwh=battery_capacity,
        total_solar_kwh=total_solar_kwh,
        solar_comparison=solar_comparison,
        candidates=candidates,
        upcoming_slots=upcoming_slots,
        export_rate=export_rate,
        live_sessions=live_sessions,
        axle_event=axle_event,
    )

    # 7. Select Candidate and Handle Disagreement / Fallbacks
    selected_candidate = next((c for c in candidates if c['id'] == sel_id), candidates[0])

    # If LLM disapproves but proposed cost is negative, grid pays us — override veto
    if not approve and selected_candidate.get('avg_price', 0.0) < 0.0:
        logging.info(f"⚡ OVERRIDING LLM VETO: candidate cost is negative ({selected_candidate['avg_price']:.2f}p/kWh). Proceeding with plan.")
        approve = True
    elif not approve:
        if day_classification in ("LOW_SOLAR_DEFENSIVE_PRECHARGE", "MEDIUM_SOLAR_PARTIAL_PRECHARGE") or total_import_kwh > 0.2:
            logging.warning(
                f"LLM vetoed candidate {selected_candidate['id']}, but day classification is {day_classification} "
                f"(import deficit {total_import_kwh:.2f} kWh). Falling back to deterministic Option 1 safety plan."
            )
            selected_candidate = candidates[0]
            approve = True
        else:
            logging.info(f"LLM vetoed candidate {selected_candidate['id']} on high solar day — falling back to Option 3 (Solar self-consumption).")
            selected_candidate = candidates[-1]

    slots_to_apply = selected_candidate.get('slots', [])
    target_soc_to_apply = selected_candidate.get('target_soc', 100)
    if selected_candidate.get('strategy') == "negative_rates" or selected_candidate.get('avg_price', 0.0) < 0.0:
        target_soc_to_apply = 100

    score_str = f"{score}/10" if score is not None else "n/a"
    logging.info(
        f"Selected Strategy: Option {selected_candidate['id']} [{selected_candidate['name']}] "
        f"| approve={approve} | score={score_str} | reason={reason}"
    )
    if weather_commentary and weather_commentary != "N/A":
        logging.info(f"LLM weather commentary: {weather_commentary}")

    if slots_to_apply and approve:
        logging.info(f"Programming {len(slots_to_apply)} charge block(s) with target {target_soc_to_apply}% SoC...")
        action = "charge"
        await set_slots_fn(slots_to_apply, charge_target=target_soc_to_apply)
    else:
        logging.info("Zero grid charge programmed (relying on solar PV and existing battery).")
        action = "no_charge"
        await set_slots_fn(None, None)

    # Calculate and persist planned SoC trajectory accounting for applied charge blocks
    # (Prevents light monitor false drift triggers that otherwise re-plan in a loop)
    sim_energy = (current_soc / 100.0) * battery_capacity
    planned_soc_schedule = []
    target_energy = (target_soc_to_apply / 100.0) * battery_capacity

    for imp in imports:
        slot_start = imp['slot']['start']
        # Check if slot falls within any charge block
        is_charge_slot = False
        if approve and slots_to_apply:
            for c_start, c_end in slots_to_apply:
                if c_start <= slot_start < c_end:
                    is_charge_slot = True
                    break

        solar_kwh = imp['solar']
        load_kwh = imp['load']

        if is_charge_slot:
            grid_charge = min(max_charge_kwh_per_slot, max(0.0, target_energy - sim_energy))
            sim_energy += grid_charge
            net = load_kwh - solar_kwh
            if net > 0:
                discharge = min(net, max(0.0, sim_energy - min_energy))
                sim_energy -= discharge
            else:
                excess_solar = -net
                solar_charge = min(excess_solar, max_charge_kwh_per_slot, max_energy - sim_energy)
                sim_energy += solar_charge
        else:
            net = load_kwh - solar_kwh
            if net < 0:
                excess_solar = -net
                solar_charge = min(excess_solar, max_charge_kwh_per_slot, max_energy - sim_energy)
                sim_energy += solar_charge
            else:
                discharge = min(net, max(0.0, sim_energy - min_energy))
                sim_energy -= discharge

        batt_soc = (sim_energy / battery_capacity) * 100.0
        planned_soc_schedule.append({
            'start': slot_start.isoformat(),
            'end': imp['slot']['end'].isoformat(),
            'soc': round(batt_soc, 1)
        })

    cur_state = load_state_fn()
    cur_state['planned_soc_schedule'] = planned_soc_schedule
    save_state_fn(cur_state)

    # 8. Log and persist to NAS state & daily stats
    record_plan_fn(
        action=action,
        branch=selected_candidate.get('strategy', 'candidate_selection'),
        day_classification=day_classification,
        weather_risk_commentary=weather_commentary,
        current_soc=current_soc,
        solar_forecast_kwh=total_solar_kwh,
        export_rate=export_rate,
        candidate_options=candidates,
        selected_candidate_id=selected_candidate.get('id', 1),
        selected_candidate=selected_candidate,
        min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
        max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
        charge_window={
            "multi_slots": [f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in slots_to_apply],
            "kwh": selected_candidate.get('kwh', 0.0),
            "avg_price": selected_candidate.get('avg_price', 0.0),
            "target_soc": target_soc_to_apply,
            "headroom_kwh": selected_candidate.get('headroom_kwh', 0.0),
        } if slots_to_apply and approve else None,
        llm_approve=approve,
        llm_score=score,
        llm_reason=reason,
    )
    return
