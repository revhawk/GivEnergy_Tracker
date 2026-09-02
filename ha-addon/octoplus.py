"""Octoplus Session Entity Naming & Payload Parsing (ADR 0004).

HomeAssistant-OctopusEnergy renamed Saving Sessions to Power Down and
Free Electricity Sessions to Power Up (ADR 0004).
Legacy entity names remain supported until January 2027.
"""

import sys
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
