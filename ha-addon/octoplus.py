"""Octoplus Session Entity Naming, Payload Parsing & Live GraphQL Client (ADR 0004 & ADR 0013).

Supports automated detection of:
- Power Down Sessions (formerly Saving Sessions / TURN_DOWN)
- Power Up Sessions (Free Electricity / TURN_UP / WEEKEND_HAPPY_HOUR)
via Octopus Energy's backend Kraken GraphQL API.
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
import requests

from tariffs import parse_utc_iso

try:
    import config
except ImportError:
    config = None

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_URL = "http://supervisor/core/api"

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

    key = api_key or os.environ.get('OCTOPUS_API_KEY', '') or (getattr(config, 'OCTOPUS_API_KEY', '') if config else '')
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


def join_octoplus_saving_session(account_id=None, event_code=None, event_id=None, token=None):
    """Automatically enroll/join an upcoming Octoplus Saving Session (Power Down).
    Tries Kraken GraphQL mutation first, then falls back to Home Assistant service calls.
    """
    code = str(event_code or event_id or '').strip()
    if not code:
        return False, "Missing event code"

    acc_id = account_id or os.environ.get('OCTOPUS_ACCOUNT_ID', '') or (getattr(config, 'OCTOPUS_ACCOUNT_ID', '') or getattr(config, 'OCTOPUS_ACCOUNT_NUMBER', '') if config else '')
    auth_token = token or obtain_kraken_token()

    # 1. Try Kraken GraphQL mutation if credentials available
    if auth_token and acc_id:
        join_mutation = """
        mutation JoinSavingSession($accountNumber: String!, $eventCode: String!) {
          joinSavingSessionsEvent(
            input: {
              accountNumber: $accountNumber
              eventCode: $eventCode
            }
          ) {
            joinedEventCodes
          }
        }
        """
        try:
            headers = {"Authorization": auth_token}
            resp = requests.post(
                OCTOPUS_BACKEND_GRAPHQL_URL,
                json={"query": join_mutation, "variables": {"accountNumber": acc_id, "eventCode": code}},
                headers=headers,
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if 'errors' not in data and data.get('data', {}).get('joinSavingSessionsEvent'):
                logging.info(f"✓ [OCTOPLUS AUTO-JOIN] Successfully enrolled in Saving Session {code} via Octopus API!")
                return True, "Enrolled via GraphQL"
            else:
                logging.warning(f"Octoplus join mutation response: {data}")
        except Exception as e:
            logging.warning(f"Octoplus join mutation failed ({e})")

    # 2. Try Home Assistant service calls
    if SUPERVISOR_TOKEN:
        services_to_try = [
            "join_octoplus_saving_session_event",
            "join_saving_session_event",
            "join_octoplus_power_down_session_event",
        ]
        headers = {
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json"
        }
        for svc in services_to_try:
            url = f"{HA_URL}/services/octopus_energy/{svc}"
            payloads = [{"event_code": code}, {"code": code}, {"event_id": code}]
            for p in payloads:
                try:
                    resp = requests.post(url, json=p, headers=headers, timeout=8)
                    if resp.status_code == 200:
                        logging.info(f"✓ [OCTOPLUS AUTO-JOIN] Successfully enrolled in Saving Session {code} via Home Assistant service {svc}!")
                        return True, f"Enrolled via Home Assistant service {svc}"
                except Exception:
                    pass

    return False, "Could not enroll into session"


def fetch_ha_octoplus_sensor(entity_id):
    """Fetch Octoplus entity state from Home Assistant REST API."""
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


def fetch_ha_octoplus_sessions():
    """Poll Home Assistant Octoplus event and sensor entities for active/upcoming sessions."""
    entities_to_check = [
        DEFAULT_POWER_UP_EVENT,
        DEFAULT_POWER_DOWN_EVENT,
        DEFAULT_POWER_UP_SENSOR,
        DEFAULT_POWER_DOWN_SENSOR,
        FALLBACK_FREE_ELECTRICITY_EVENT,
        FALLBACK_SAVING_SESSIONS_EVENT,
        FALLBACK_FREE_ELECTRICITY_SENSOR,
        FALLBACK_SAVING_SESSIONS_SENSOR,
    ]
    sessions = []
    seen = set()
    for ent in entities_to_check:
        data = fetch_ha_octoplus_sensor(ent)
        if data:
            parsed = parse_octoplus_session(data)
            if parsed and parsed.get('start') and parsed.get('end'):
                key = (parsed['session_type'], parsed['start'].isoformat())
                if key not in seen:
                    seen.add(key)
                    sessions.append(parsed)
    return sessions


def fetch_live_octoplus_sessions(api_key=None, account_id=None, lookback_hours=24):
    """Fetch active, upcoming, and recent Octoplus Saving Sessions (Power Down)
    and Free Electricity Sessions (Power Up) via Octopus backend GraphQL or HA sensors.
    Caches results for 15 minutes.
    """
    global _octoplus_sessions_cache
    now = datetime.now(timezone.utc)
    if _octoplus_sessions_cache["sessions"] and _octoplus_sessions_cache["fetched_at"]:
        age = (now - _octoplus_sessions_cache["fetched_at"]).total_seconds()
        if age < 900:  # 15 minutes
            return _octoplus_sessions_cache["sessions"]

    acc_id = account_id or getattr(config, 'OCTOPUS_ACCOUNT_ID', '') or getattr(config, 'OCTOPUS_ACCOUNT_NUMBER', '') if config else ''
    token = obtain_kraken_token(api_key)
    if not token or not acc_id:
        ha_sessions = fetch_ha_octoplus_sessions()
        if ha_sessions:
            _octoplus_sessions_cache["sessions"] = ha_sessions
            _octoplus_sessions_cache["fetched_at"] = now
            return ha_sessions
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
            ha_sessions = fetch_ha_octoplus_sessions()
            return ha_sessions or _octoplus_sessions_cache["sessions"] or []

        saving_data = data.get('data', {}).get('savingSessions', {})
        events = saving_data.get('events', [])
        joined_events = saving_data.get('account', {}).get('joinedEvents', []) or []
        joined_ids = {j.get('eventId') for j in joined_events if j.get('eventId')}

        auto_join = os.environ.get('AUTO_JOIN_SAVING_SESSIONS', 'true').lower() in ('true', '1', 'yes')
        cutoff_start = now - timedelta(hours=lookback_hours)
        parsed_sessions = []
        for ev in events:
            ev_copy = dict(ev)
            ev_copy['is_joined'] = ev.get('id') in joined_ids
            parsed = parse_octoplus_session(ev_copy)
            if parsed and parsed['end'] and parsed['end'] >= cutoff_start:
                if auto_join and parsed['session_type'] == 'power_down' and not ev_copy['is_joined'] and parsed['end'] > now:
                    success, msg = join_octoplus_saving_session(
                        account_id=acc_id,
                        event_code=parsed.get('code'),
                        event_id=parsed.get('event_id'),
                        token=token
                    )
                    if success:
                        parsed['is_joined'] = True
                parsed_sessions.append(parsed)

        # Merge with any HA entity sessions if found
        ha_sessions = fetch_ha_octoplus_sessions()
        for hs in ha_sessions:
            if not any(ps['start'] == hs['start'] and ps['session_type'] == hs['session_type'] for ps in parsed_sessions):
                if auto_join and hs['session_type'] == 'power_down' and not hs.get('is_joined') and hs['end'] > now:
                    success, msg = join_octoplus_saving_session(
                        account_id=acc_id,
                        event_code=hs.get('code'),
                        event_id=hs.get('event_id'),
                        token=token
                    )
                    if success:
                        hs['is_joined'] = True
                parsed_sessions.append(hs)

        # Sort chronologically by start time
        parsed_sessions.sort(key=lambda s: s['start'])
        _octoplus_sessions_cache["sessions"] = parsed_sessions
        _octoplus_sessions_cache["fetched_at"] = now
        return parsed_sessions
    except Exception as e:
        logging.warning(f"Octoplus: Failed to fetch sessions from Octopus backend ({e})")
        ha_sessions = fetch_ha_octoplus_sessions()
        return ha_sessions or _octoplus_sessions_cache["sessions"] or []


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
    Also normalizes Home Assistant entity state payloads.
    """
    if not isinstance(session_data, dict):
        return None

    # Handle nested attributes if fetched from Home Assistant REST sensor state
    if "attributes" in session_data and isinstance(session_data["attributes"], dict):
        attrs = session_data["attributes"]
        session_type_raw = str(
            attrs.get('event_type')
            or attrs.get('eventType')
            or attrs.get('session_type')
            or session_data.get('entity_id')
            or ''
        ).lower()
        start_raw = attrs.get('start') or attrs.get('start_time') or attrs.get('startAt') or session_data.get('state')
        end_raw = attrs.get('end') or attrs.get('end_time') or attrs.get('endAt')
        code = attrs.get('code') or session_data.get('entity_id', '')
        event_id = attrs.get('id') or attrs.get('event_id')
        reward_points = attrs.get('reward_points') or attrs.get('rewardPerKwhInOctoPoints') or 0
        is_joined = bool(attrs.get('is_joined', True))
    else:
        session_type_raw = str(
            session_data.get('type')
            or session_data.get('session_type')
            or session_data.get('event_type')
            or session_data.get('eventType')
            or ''
        ).lower()
        start_raw = (
            session_data.get('start')
            or session_data.get('startAt')
            or session_data.get('valid_from')
            or session_data.get('start_time')
        )
        end_raw = (
            session_data.get('end')
            or session_data.get('endAt')
            or session_data.get('valid_to')
            or session_data.get('end_time')
        )
        code = session_data.get('code') or str(session_data.get('id', ''))
        event_id = session_data.get('id') or session_data.get('eventId')
        reward_points = session_data.get('rewardPerKwhInOctoPoints') or session_data.get('reward_points') or 0
        is_joined = bool(session_data.get('is_joined', False))

    if any(k in session_type_raw for k in ['turn_down', 'power_down', 'saving']):
        session_type = 'power_down'
        display_name = 'Power Down Session'
    elif any(k in session_type_raw for k in ['turn_up', 'weekend_happy_hour', 'happy_hour', 'power_up', 'free_electricity', 'free']):
        session_type = 'power_up'
        display_name = 'Power Up Session'
    else:
        session_type = 'unknown'
        display_name = 'Octoplus Session'

    if not start_raw or str(start_raw).lower() in ("none", "null", "unavailable", "unknown", ""):
        return None

    try:
        start_dt = parse_utc_iso(start_raw) if isinstance(start_raw, str) else start_raw
        end_dt = parse_utc_iso(end_raw) if isinstance(end_raw, str) and end_raw else None
        if start_dt and not end_dt:
            end_dt = start_dt + timedelta(hours=1)

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
    except Exception as e:
        logging.warning(f"Octoplus: Failed to parse session fields ({e}): {session_data}")
        return None


def is_power_down_slot_dt(slot_start, slot_end, live_sessions=None):
    """Check if a datetime slot overlaps with any active Octopus Octoplus Power Down session.
    Checks live GraphQL/HA sessions first, then falls back to static config.POWER_DOWN_WINDOWS.
    """
    sessions = live_sessions if live_sessions is not None else fetch_live_octoplus_sessions()
    for s in sessions:
        if s.get('session_type') == 'power_down':
            s_start = s['start']
            s_end = s['end']
            if slot_start < s_end and slot_end > s_start:
                return True

    # Fallback to static windows in config.py only if no live sessions were found
    if sessions:
        return False

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
    Checks live GraphQL/HA sessions first, then falls back to static config.POWER_UP_WINDOWS.
    """
    sessions = live_sessions if live_sessions is not None else fetch_live_octoplus_sessions()
    for s in sessions:
        if s.get('session_type') == 'power_up':
            s_start = s['start']
            s_end = s['end']
            if slot_start < s_end and slot_end > s_start:
                return True

    # Fallback to static windows in config.py only if no live sessions were found
    if sessions:
        return False

    windows = getattr(config, 'POWER_UP_WINDOWS', []) if config else []
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
