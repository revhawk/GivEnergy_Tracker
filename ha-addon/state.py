"""State Persistence & Daily Statistics Tracking.

Manages persistent JSON state files (/share/nas_logs/givenergy_state.json)
and daily execution metrics (/share/nas_logs/givenergy_daily_stats.json).
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone

try:
    import config
except ImportError:
    config = None

STATE_FILE = "/share/nas_logs/givenergy_state.json"
STATS_FILE = "/share/nas_logs/givenergy_daily_stats.json"

_last_plan = {}


def setup_logging():
    """Setup console and optional NAS rotating file loggers."""
    log_level_str = getattr(config, 'LOG_LEVEL', 'INFO').upper() if config else 'INFO'
    log_level = getattr(logging, log_level_str, logging.INFO)
    log_file = getattr(config, 'LOG_FILE_PATH', None) if config else None
    
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
    
    if log_file and not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
                
            file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
            file_handler.setLevel(log_level)
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
            logging.info(f"File logging successfully directed to: {log_file}")
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

    stats['solar_kwh_forecast'] = max(
        stats.get('solar_kwh_forecast', 0), run_data.get('solar_kwh', 0))
    stats['iboost_kwh_forecast'] = max(
        stats.get('iboost_kwh_forecast', 0), run_data.get('iboost_kwh', 0))

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

    return stats
