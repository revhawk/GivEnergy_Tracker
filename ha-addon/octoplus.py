"""Octoplus Session Entity Naming, Payload Parsing & Live GraphQL Client (ADR 0004 & ADR 0013).

Supports automated detection of:
- Power Down Sessions (formerly Saving Sessions / TURN_DOWN)
- Power Up Sessions (Free Electricity / TURN_UP / WEEKEND_HAPPY_HOUR)
via Octopus Energy's backend Kraken GraphQL API.
"""

import sys
import logging
from datetime import datetime, timezone, timedelta
import requests

from tariffs import parse_utc_iso

try:
    import config
except ImportError:
    config = None

DEFAULT_POWER_DOWN_SENSOR = "sensor.octopus_energy_power_down_sessions"
DEFAULT_POWER_UP_SENSOR = "sensor.octopus_energy_power_up_sessions"
DEFAULT_POWER_DOWN_EVENT = "event.octopus_energy_octoplus_power_down_events"
DEFAULT_POWER_UP_EVENT = "event.octopus_energy_octoplus_power_up_events"

FALLBACK_SAVING_SESSIONS_SENSOR = "sensor.octopus_energy_saving_sessions"
FALLBACK_FREE_ELECTRICITY_SENSOR = "sensor.octopus_energy_free_electricity_sessions"
FALLBACK_SAVING_SESSIONS_EVENT = "event.octopus_energy_octoplus_saving_sessions_events"
FALLBACK_FREE_ELECTRICITY_EVENT = "event.octopus_energy_octoplus_free_electricity_sessions_events"

OCTOPUS_AUTH_GRAPHQL_URL = "https://api.octopus.energy/v1/graphql/"
OCTOPUS_BACKEND_GRAPHQL_URL = "https://api.backend.octopus.energy/v1/graphql/"

# In-memory caches for token (1h) and sessions (15m)
_kraken_token_cache = {"token": None, "fetched_at": None}
_octoplus_sessions_cache = {"sessions": [], "fetched_at": None}


def clear_octoplus_cache():
    """Clear in-memory caches (used for tests or forced re-fetch)."""
    global _kraken_token_cache, _octoplus_sessions_cache
    _kraken_token_cache = {"token": None, "fetched_at": None}
    _octoplus_sessions_cache = {"sessions": [], "fetched_at": None}


def obtain_kraken_token(api_key=None):
    """Authenticate with Octopus GraphQL and obtain a short-lived Kraken JWT token.
    Caches token for 1 hour.
    """
    global _kraken_token_cache
    now = datetime.now(timezone.utc)
    if _kraken_token_cache["token"] and _kraken_token_cache["fetched_at"]:
        age = (now - _kraken_token_cache["fetched_at"]).total_seconds()
        if age < 3600:
            return _kraken_token_cache["token"]

    key = api_key or getattr(config, 'OCTOPUS_API_KEY', '') if config else ''
    if not key:
        logging.warning("Octoplus: OCTOPUS_API_KEY is not configured; cannot obtain Kraken token.")
        return None

    auth_mutation = """
    mutation ObtainKrakenToken($input: ObtainJSONWebTokenInput!) {
      obtainKrakenToken(input: $input) {
        token
      }
    }
    """
    try:
        resp = requests.post(
            OCTOPUS_AUTH_GRAPHQL_URL,
            json={"query": auth_mutation, "variables": {"input": {"APIKey": key}}},
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get('data', {}).get('obtainKrakenToken', {}).get('token')
        if token:
            _kraken_token_cache["token"] = token
            _kraken_token_cache["fetched_at"] = now
            return token
        logging.warning(f"Octoplus: Failed to retrieve Kraken token from payload: {data}")
        return None
    except Exception as e:
        logging.warning(f"Octoplus: Error authenticating with Octopus GraphQL ({e})")
        return None


def fetch_live_octoplus_sessions(api_key=None, account_id=None, lookback_hours=24):
    """Fetch active, upcoming, and recent Octoplus Saving Sessions (Power Down)
    and Free Electricity Sessions (Power Up) via Octopus backend GraphQL.
    Caches results for 15 minutes.
    """
    global _octoplus_sessions_cache
    now = datetime.now(timezone.utc)
    if _octoplus_sessions_cache["sessions"] and _octoplus_sessions_cache["fetched_at"]:
        age = (now - _octoplus_sessions_cache["fetched_at"]).total_seconds()
        if age < 900:  # 15 minutes
            return _octoplus_sessions_cache["sessions"]

    acc_id = account_id or getattr(config, 'OCTOPUS_ACCOUNT_ID', '') if config else ''
    token = obtain_kraken_token(api_key)
    if not token:
        return _octoplus_sessions_cache["sessions"] or []

    sessions_query = """
    query savingSessionInfo($accountNumber: String!) {
      savingSessions {
        events {
          id
          code
          startAt
          endAt
          eventType
          rewardPerKwhInOctoPoints
        }
        account(accountNumber: $accountNumber) {
          joinedEvents {
            eventId
            startAt
            endAt
          }
        }
      }
    }
    """
    try:
        headers = {"Authorization": token}
        resp = requests.post(
            OCTOPUS_BACKEND_GRAPHQL_URL,
            json={"query": sessions_query, "variables": {"accountNumber": acc_id}},
            headers=headers,
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if 'errors' in data:
            logging.warning(f"Octoplus: GraphQL returned errors: {data['errors']}")
            return _octoplus_sessions_cache["sessions"] or []

        saving_data = data.get('data', {}).get('savingSessions', {})
        events = saving_data.get('events', [])
        joined_events = saving_data.get('account', {}).get('joinedEvents', []) or []
        joined_ids = {j.get('eventId') for j in joined_events if j.get('eventId')}

        cutoff_start = now - timedelta(hours=lookback_hours)
        parsed_sessions = []
        for ev in events:
            ev_copy = dict(ev)
            ev_copy['is_joined'] = ev.get('id') in joined_ids
            parsed = parse_octoplus_session(ev_copy)
            if parsed and parsed['end'] and parsed['end'] >= cutoff_start:
                parsed_sessions.append(parsed)

        # Sort chronologically by start time
        parsed_sessions.sort(key=lambda s: s['start'])
        _octoplus_sessions_cache["sessions"] = parsed_sessions
        _octoplus_sessions_cache["fetched_at"] = now
        return parsed_sessions
    except Exception as e:
        logging.warning(f"Octoplus: Failed to fetch sessions from Octopus backend ({e})")
        return _octoplus_sessions_cache["sessions"] or []


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
            return getattr(config, 'OCTOPLUS_POWER_DOWN_ENTITY', DEFAULT_POWER_DOWN_SENSOR) if prefer_new and config else getattr(config, 'OCTOPLUS_SAVING_SESSIONS_FALLBACK_ENTITY', FALLBACK_SAVING_SESSIONS_SENSOR) if config else DEFAULT_POWER_DOWN_SENSOR
    elif is_power_up:
        if kind == 'event':
            return DEFAULT_POWER_UP_EVENT if prefer_new else FALLBACK_FREE_ELECTRICITY_EVENT
        elif kind == 'calendar':
            return "calendar.octopus_energy_octoplus_power_up_sessions" if prefer_new else "calendar.octopus_energy_octoplus_free_electricity_sessions"
        else:
            return getattr(config, 'OCTOPLUS_POWER_UP_ENTITY', DEFAULT_POWER_UP_SENSOR) if prefer_new and config else getattr(config, 'OCTOPLUS_FREE_ELECTRICITY_FALLBACK_ENTITY', FALLBACK_FREE_ELECTRICITY_SENSOR) if config else DEFAULT_POWER_UP_SENSOR
    else:
        raise ValueError(f"Unknown session_type: {session_type}")


def parse_octoplus_session(session_data):
    """Parse session data dict into a standardized session metadata dictionary.
    Supports both ADR 0004 (power_down, power_up), legacy names, and Kraken GraphQL types:
    - TURN_DOWN -> power_down
    - TURN_UP / WEEKEND_HAPPY_HOUR -> power_up
    """
    if not isinstance(session_data, dict):
        return None
        
    session_type_raw = str(
        session_data.get('type')
        or session_data.get('session_type')
        or session_data.get('event_type')
        or session_data.get('eventType')
        or ''
    ).lower()

    if any(k in session_type_raw for k in ['turn_down', 'power_down', 'saving']):
        session_type = 'power_down'
        display_name = 'Power Down Session'
    elif any(k in session_type_raw for k in ['turn_up', 'weekend_happy_hour', 'happy_hour', 'power_up', 'free_electricity', 'free']):
        session_type = 'power_up'
        display_name = 'Power Up Session'
    else:
        session_type = 'unknown'
        display_name = 'Octoplus Session'
        
    start_str = (
        session_data.get('start')
        or session_data.get('startAt')
        or session_data.get('valid_from')
        or session_data.get('start_time')
    )
    end_str = (
        session_data.get('end')
        or session_data.get('endAt')
        or session_data.get('valid_to')
        or session_data.get('end_time')
    )
    
    start_dt = parse_utc_iso(start_str) if isinstance(start_str, str) else start_str
    end_dt = parse_utc_iso(end_str) if isinstance(end_str, str) else end_str
    
    code = session_data.get('code') or str(session_data.get('id', ''))
    event_id = session_data.get('id') or session_data.get('eventId')
    reward_points = session_data.get('rewardPerKwhInOctoPoints') or session_data.get('reward_points') or 0
    is_joined = bool(session_data.get('is_joined', False))
    
    return {
        'session_type': session_type,
        'display_name': display_name,
        'start': start_dt,
        'end': end_dt,
        'code': code,
        'event_id': event_id,
        'event_type': session_data.get('eventType') or session_data.get('event_type', session_type),
        'reward_points': reward_points,
        'is_joined': is_joined,
        'entity_name': get_octoplus_entity_name(session_type, entity_kind='sensor')
    }


def is_power_down_slot_dt(slot_start, slot_end, live_sessions=None):
    """Check if a datetime slot overlaps with any active Octopus Octoplus Power Down session.
    Checks live GraphQL sessions first, then falls back to static config.POWER_DOWN_WINDOWS.
    """
    sessions = live_sessions if live_sessions is not None else fetch_live_octoplus_sessions()
    for s in sessions:
        if s.get('session_type') == 'power_down':
            s_start = s['start']
            s_end = s['end']
            if slot_start < s_end and slot_end > s_start:
                return True

    # Fallback to static windows in config.py
    windows = getattr(config, 'POWER_DOWN_WINDOWS', []) if config else []
    if windows:
        local_start = slot_start.astimezone()
        local_end = slot_end.astimezone()
        for start_str, end_str in windows:
            try:
                s_hour, s_min = map(int, start_str.split(':'))
                e_hour, e_min = map(int, end_str.split(':'))
                w_start_min = s_hour * 60 + s_min
                w_end_min = e_hour * 60 + e_min

                slot_s_min = local_start.hour * 60 + local_start.minute
                slot_e_min = local_end.hour * 60 + local_end.minute
                if slot_e_min == 0 and local_end.day != local_start.day:
                    slot_e_min = 24 * 60

                if slot_s_min < w_end_min and slot_e_min > w_start_min:
                    return True
            except Exception:
                pass
    return False


def is_power_up_slot_dt(slot_start, slot_end, live_sessions=None):
    """Check if a datetime slot overlaps with any active Octopus Octoplus Power Up
    (Free Electricity / Weekend Happy Hour) session.
    """
    sessions = live_sessions if live_sessions is not None else fetch_live_octoplus_sessions()
    for s in sessions:
        if s.get('session_type') == 'power_up':
            s_start = s['start']
            s_end = s['end']
            if slot_start < s_end and slot_end > s_start:
                return True
    return False
