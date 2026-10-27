"""State Persistence & Daily Statistics Tracking.

Manages persistent JSON state files (/share/nas_logs/givenergy_state.json)
and daily execution metrics (/share/nas_logs/givenergy_daily_stats.json).
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime, timezone

try:
    import config
except ImportError:
    config = None

STATE_FILE = "/share/nas_logs/givenergy_state.json"
STATS_FILE = "/share/nas_logs/givenergy_daily_stats.json"
HISTORY_DIR = "/share/nas_logs/history"

_last_plan = {}


def setup_logging():
    """Setup console and daily midnight rotating file loggers (retaining 90 days of history)."""
    log_level_str = getattr(config, 'LOG_LEVEL', 'INFO').upper() if config else 'INFO'
    log_level = getattr(logging, log_level_str, logging.INFO)
    env_log_file = os.getenv('LOG_FILE_PATH', '').strip()
    log_file = env_log_file if env_log_file else (getattr(config, 'LOG_FILE_PATH', '/share/nas_logs/givenergy_tracker.log') if config else '/share/nas_logs/givenergy_tracker.log')
    
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Root captures everything, handlers filter
    
    # Clear default root handlers to enforce timestamp formatting
    for h in list(logger.handlers):
        logger.removeHandler(h)
        
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    if log_file and not any(isinstance(h, (RotatingFileHandler, TimedRotatingFileHandler)) for h in logger.handlers):
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
                
            # Midnight rotation: creates daily log files (e.g. givenergy_tracker.log.2026-09-02) keeping 90 days
            file_handler = TimedRotatingFileHandler(log_file, when='midnight', interval=1, backupCount=90, encoding='utf-8')
            file_handler.setLevel(log_level)
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
            logging.info(f"Daily logging active (retaining 90 days of logs at: {log_file})")
        except Exception as e:
            print(f"Error initializing file logger at {log_file}: {e}", file=sys.stderr)


def _record_plan(**fields):
    """Record current optimization run details into in-memory _last_plan."""
    _last_plan.clear()
    _last_plan.update({
        "at": datetime.now(timezone.utc).isoformat(),
        **fields,
    })


def load_state():
    """Load persistent daemon state dictionary from disk."""
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    """Save persistent daemon state dictionary to disk."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logging.warning(f"Could not save state: {e}")


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
    now_iso = datetime.now().astimezone().isoformat()
    return {
        'date': date_str,
        'timestamp': now_iso,
        'created_at': now_iso,
        'updated_at': now_iso,
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
    now_iso = datetime.now().astimezone().isoformat()
    stats['updated_at'] = now_iso
    stats['timestamp'] = now_iso
    stats['runs'] = stats.get('runs', 0) + 1
    stats['end_soc'] = run_data.get('soc', stats.get('end_soc', 0))

    stats['solar_kwh_forecast'] = max(
        stats.get('solar_kwh_forecast', 0), run_data.get('solar_kwh', 0))
    stats['iboost_kwh_forecast'] = max(
        stats.get('iboost_kwh_forecast', 0), run_data.get('iboost_kwh', 0))

    if run_data.get('day_classification'):
        val = run_data['day_classification']
        stats['day_classification'] = val.value if hasattr(val, 'value') else str(val)
    if run_data.get('weather_risk_commentary'):
        stats['weather_risk_commentary'] = str(run_data['weather_risk_commentary'])

    if run_data.get('min_rate') is not None:
        stats['min_rate_seen'] = min(stats.get('min_rate_seen', float('inf')), run_data['min_rate'])
    if run_data.get('max_rate') is not None:
        stats['max_rate_seen'] = max(stats.get('max_rate_seen', float('-inf')), run_data['max_rate'])

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

    if run_data.get('candidate_options'):
        stats['candidate_options'] = run_data['candidate_options']
    if run_data.get('selected_candidate'):
        stats['selected_candidate'] = run_data['selected_candidate']
    if run_data.get('selected_candidate_id'):
        stats['selected_candidate_id'] = run_data['selected_candidate_id']
    if run_data.get('llm_reason'):
        stats['llm_reason'] = str(run_data['llm_reason'])
    if run_data.get('llm_score') is not None:
        stats['llm_score'] = run_data['llm_score']

    return stats


def archive_daily_stats_history(stats, date_str=None):
    """Archive complete daily performance summary, solar generation, and ChatGPT audits into date-and-time-stamped JSON."""
    try:
        now_dt = datetime.now().astimezone()
        now_iso = now_dt.isoformat()
        dt = date_str or stats.get('date') or now_dt.strftime("%Y-%m-%d")
        time_str = now_dt.strftime("%H%M%S")

        # Ensure timestamp fields are present inside stats JSON
        if not stats.get('timestamp'):
            stats['timestamp'] = now_iso
        stats['archived_at'] = now_iso

        history_dir = getattr(config, 'HISTORY_DIR', HISTORY_DIR) if config else HISTORY_DIR
        os.makedirs(history_dir, exist_ok=True)

        # 1. Primary date-stamped file for backward compatibility & contract checks
        archive_path = os.path.join(history_dir, f"daily_stats_{dt}.json")
        with open(archive_path, 'w') as f:
            json.dump(stats, f, indent=2, default=str)

        # 2. Date-and-time-stamped snapshot file
        ts_archive_path = os.path.join(history_dir, f"daily_stats_{dt}_{time_str}.json")
        with open(ts_archive_path, 'w') as f:
            json.dump(stats, f, indent=2, default=str)

        logging.info(f"✓ Daily stats archived to {archive_path} and {ts_archive_path}")
        return archive_path
    except Exception as e:
        logging.warning(f"Could not archive daily stats history: {e}")
        return None

