"""GivEnergy Tariff Optimiser Daemon (Main Entrypoint).

Orchestrates daily planning runs, battery state monitoring, end-of-day financial audits,
and GivTCP slot programming by composing modular domain services.
Re-exports domain functions to preserve single facade interface across test suites.
"""

import os
import sys
import asyncio
import logging
from datetime import datetime

# Single source of truth for the add-on version
from version import __version__

# Import custom configurations
try:
    import config
except ImportError:
    print("Error: config.py not found. Please ensure config.py is in the same directory.")
    sys.exit(1)

# Import domain contracts
from contracts import (
    InverterTelemetry,
    OctopusRateSlot,
    GivTCPWriteSlot,
    LLMVetoDecision,
)

# Re-export state persistence functions
from state import (
    setup_logging,
    load_state,
    save_state,
    load_daily_stats,
    save_daily_stats,
    init_daily_stats,
    update_daily_stats,
    _record_plan,
    _last_plan,
)

# Re-export Octoplus ADR 0004 helpers
from octoplus import (
    get_octoplus_entity_name,
    parse_octoplus_session,
)

# Re-export simulation engine math
from simulation import (
    run_optimization,
    run_light_monitor,
)

# Re-export tariff API operations
from tariffs import (
    parse_utc_iso,
    fetch_export_rate,
    fetch_agile_rates,
    _export_rate_cache,
)

# Re-export solar API operations
from solar import (
    fetch_solar_forecast,
    get_solar_kwh_for_slot,
    fetch_parallel_solar_forecasts,
)

# Re-export profile helpers
from profiler import (
    get_load_kwh_for_slot,
    is_power_down_slot,
)

# Re-export GivTCP REST & Modbus operations
from givtcp import (
    find_key_recursive,
    read_inverter_charge_slots,
    get_inverter_telemetry,
    get_inverter_soc,
    set_inverter_charge_slots,
    HAS_MODBUS,
)

# Re-export LLM ChatGPT veto operations
from llm import (
    test_openai_connection,
    get_openai_model,
    chatgpt_veto_plan,
    generate_daily_summary,
)

# Initialize logging system
setup_logging()


# ── End-of-day audit: summarise the day using persisted state + daily stats ──
async def run_end_of_day_audit():
    """Run midnight audit summarizing daily performance & financial savings via ChatGPT."""
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


# ── Main Daemon Loop ─────────────────────────────────────────────────────────
async def main():
    """Daemon execution loop managing daily plan triggers, drift checks, and sleep intervals."""
    run_once = os.environ.get('RUN_ONCE', 'false').lower() in ('true', '1', 'yes')
    interval = int(os.environ.get('INTERVAL_MINUTES', 30))
    debug_enabled = os.environ.get('DEBUG_LOGGING', 'false').lower() in ('true', '1', 'yes')

    # Configure logging level & silence third-party HTTP debug logs when debug_logging is false
    if debug_enabled:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.info("🔊 DEBUG LOGGING IS ENABLED (verbose HTTP and socket traces ON).")
    else:
        logging.getLogger().setLevel(logging.INFO)
        for _logger_name in (
            "urllib3", "httpx", "httpx2", "httpcore", "httpcore2",
            "openai", "openai._base_client", "requests"
        ):
            logging.getLogger(_logger_name).setLevel(logging.WARNING)

    # Startup checks: validate version sync
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
