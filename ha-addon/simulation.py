"""24-Hour Battery & Solar Simulation Engine.

Simulates 24-hour battery SoC evolution (48 x 30-min slots), evaluates excess solar,
load profile forecasts, iBoost hot water diversion, export pricing, and calculates
optimal grid charge windows and arbitrage opportunities.
"""

import sys
import math
import logging
from datetime import datetime, timezone

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
                slot_kwh = (telemetry['load_power'] / 1000.0) * 0.5
                history_dict = cur_state.setdefault('load_profile_history', {})
                history_list = history_dict.setdefault(slot_key, [])
                history_list.append(round(slot_kwh, 3))
                if len(history_list) > 14:  # keep rolling 14 readings per half-hour slot
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
            state_data = load_state_fn()
            load_history = state_data.get('load_profile_history')
            load_kwh = get_load_kwh_fn(slot['start'], slot['end'], load_history)
        net = load_kwh - solar_kwh
        
        iboost_divert = 0.0
        grid_export = 0.0
        import_needed = 0.0
        
        if net < 0:
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

    planned_soc_schedule = [
        {
            'start': imp['slot']['start'].isoformat(),
            'end': imp['slot']['end'].isoformat(),
            'soc': round(imp['batt_soc'], 1)
        }
        for imp in imports
    ]
    cur_state = load_state_fn()
    cur_state['planned_soc_schedule'] = planned_soc_schedule
    save_state_fn(cur_state)
            
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
    total_solar_kwh  = sum(f['kwh'] for f in solar_forecasts)

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
            logging.info(f"   {local_t}  {ns['price']:.2f}p/kWh  ← free money!")
    elif arbitrage_slots:
        logging.info(f"💰 Arbitrage opportunity: {len(arbitrage_slots)} slot(s) below {arbitrage_threshold:.2f}p")
        for a in arbitrage_slots[:6]:
            local_t = a['start'].astimezone().strftime('%m-%d %H:%M')
            profit = export_rate - a['price']
            logging.info(f"   {local_t}  {a['price']:.2f}p/kWh  (profit: {profit:.2f}p/kWh vs export)")

    if negative_slots or arbitrage_slots:
        logging.info(f"   Battery space available: {available_capacity_kwh:.1f} kWh  (SoC: {current_soc}%)")

    force_opportunistic_charge = (
        (len(negative_slots) > 0 or len(arbitrage_slots) > 0)
        and available_capacity_kwh > 0.3
        and current_soc < 98
    )

    if total_import_kwh <= 0.2 and not force_opportunistic_charge:
        logging.info("Battery + solar sufficient AND no profitable import slots. Grid charging not required.")
        approve, score, reason = chatgpt_veto_fn(
            current_soc, battery_capacity, total_solar_kwh, export_rate,
            upcoming_slots, None, None, 0, 0
        )
        score_str = f"{score}/10" if score is not None else "n/a"
        logging.info(f"LLM opinion (no-charge): approve={approve}  score={score_str}  reason={reason}")
        record_plan_fn(action="no_charge", branch="solar_sufficient",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_slots_fn(None, None)
        return

    if force_opportunistic_charge and total_import_kwh <= 0.2:
        required_charge_kwh = available_capacity_kwh
        if negative_slots:
            logging.info(f"⚡ Negative-rate override: filling {required_charge_kwh:.1f} kWh — grid pays us!")
        else:
            logging.info(f"💰 Arbitrage override: filling {required_charge_kwh:.1f} kWh (import < export)")
    else:
        required_charge_kwh = min(total_import_kwh * 1.10, max_energy - current_energy)

    if required_charge_kwh <= 0.2:
        logging.info("Battery is already too full to accept significant grid charge.")
        approve, score, reason = chatgpt_veto_fn(
            current_soc, battery_capacity, total_solar_kwh, export_rate,
            upcoming_slots, None, None, 0, 0
        )
        score_str = f"{score}/10" if score is not None else "n/a"
        logging.info(f"LLM opinion (battery-full): approve={approve}  score={score_str}  reason={reason}")
        record_plan_fn(action="no_charge", branch="battery_full",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_slots_fn(None, None)
        return

    if force_opportunistic_charge:
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

            approve, score, reason = chatgpt_veto_fn(
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
                record_plan_fn(action="no_charge", branch="llm_vetoed",
                             current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                             export_rate=export_rate,
                             min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                             max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                             proposed_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                              "kwh": round(required_charge_kwh, 2), "avg_price": round(total_avg_price, 2)},
                             llm_approve=False, llm_score=score, llm_reason=reason)
                await set_slots_fn(None, None)
                return

            logging.info(f"Arbitrage mode: Programming {len(slots_tuples)} charge block(s)...")
            record_plan_fn(action="charge", branch="arbitrage",
                         current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                         export_rate=export_rate,
                         min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         charge_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                        "kwh": round(required_charge_kwh, 2), "avg_price": round(total_avg_price, 2)},
                         llm_approve=approve, llm_score=score, llm_reason=reason)
            await set_slots_fn(slots_tuples)
            return

    slots_to_charge = math.ceil(required_charge_kwh / max_charge_kwh_per_slot)
    slots_to_charge = max(1, min(slots_to_charge, 8))  # Between 30 mins and 4 hours

    logging.info(f"Target Grid Charge: {required_charge_kwh:.2f} kWh (~{slots_to_charge} half-hour slot(s))")

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

    use_non_contiguous = False
    slots_tuples = []
    min_window_cost = min_contig_cost

    if len(upcoming_slots) >= slots_to_charge:
        cheapest_n = sorted(upcoming_slots, key=lambda s: s['price'])[:slots_to_charge]
        cheapest_n_sorted = sorted(cheapest_n, key=lambda s: s['start'])
        avg_cheapest_n = sum(s['price'] for s in cheapest_n) / len(cheapest_n)

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

        if avg_cheapest_n < (min_contig_cost - 0.01) and len(merged_blocks) <= 10:
            use_non_contiguous = True
            min_window_cost = avg_cheapest_n
            slots_tuples = [(b['start'].astimezone(), b['end'].astimezone()) for b in merged_blocks]

    if use_non_contiguous:
        rate_label = f"{min_window_cost:.2f}p/kWh" if min_window_cost >= 0 else f"{min_window_cost:.2f}p/kWh (NEGATIVE — grid pays us!)"
        logging.info(f"Optimal Deficit Charge: {len(slots_tuples)} non-contiguous block(s)  |  Avg: {rate_label}")
        charge_cost_p = required_charge_kwh * min_window_cost
        logging.info(f"Economics: charge {required_charge_kwh:.1f} kWh × {min_window_cost:.2f}p = {charge_cost_p:.0f}p")

        approve, score, reason = chatgpt_veto_fn(
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
            record_plan_fn(action="no_charge", branch="llm_vetoed",
                         current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                         export_rate=export_rate,
                         min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         proposed_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                          "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                         llm_approve=False, llm_score=score, llm_reason=reason)
            await set_slots_fn(None, None)
            return

        record_plan_fn(action="charge", branch="scheduled",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     charge_window={"multi_slots": [(s.strftime('%H:%M'), e.strftime('%H:%M')) for s, e in slots_tuples],
                                    "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_slots_fn(slots_tuples)
    elif best_contig_start and best_contig_end:
        local_start = best_contig_start.astimezone()
        local_end = best_contig_end.astimezone()
        rate_label = f"{min_window_cost:.2f}p/kWh" if min_window_cost >= 0 else f"{min_window_cost:.2f}p/kWh (NEGATIVE — grid pays us!)"
        logging.info(f"Optimal Deficit Charge Window: {local_start.strftime('%H:%M')} → {local_end.strftime('%H:%M')}  |  Avg: {rate_label}")

        charge_cost_p = required_charge_kwh * min_window_cost
        logging.info(f"Economics: charge {required_charge_kwh:.1f} kWh × {min_window_cost:.2f}p = {charge_cost_p:.0f}p")

        approve, score, reason = chatgpt_veto_fn(
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
            record_plan_fn(action="no_charge", branch="llm_vetoed",
                         current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                         export_rate=export_rate,
                         min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                         proposed_window={"start": local_start.strftime('%H:%M'), "end": local_end.strftime('%H:%M'),
                                          "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                         llm_approve=False, llm_score=score, llm_reason=reason)
            await set_slots_fn(None, None)
            return

        record_plan_fn(action="charge", branch="scheduled",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate,
                     min_rate=min(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     max_rate=max(s['price'] for s in upcoming_slots) if upcoming_slots else None,
                     charge_window={"start": local_start.strftime('%H:%M'), "end": local_end.strftime('%H:%M'),
                                    "kwh": round(required_charge_kwh, 2), "avg_price": round(min_window_cost, 2)},
                     llm_approve=approve, llm_score=score, llm_reason=reason)
        await set_slots_fn(local_start, local_end)
    else:
        logging.info("Could not find a valid charge window. Clearing slots.")
        record_plan_fn(action="no_charge", branch="no_window_found",
                     current_soc=current_soc, solar_forecast_kwh=total_solar_kwh,
                     export_rate=export_rate)
        await set_slots_fn(None, None)
