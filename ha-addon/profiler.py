"""Load profiling & Power Down session detection module."""
import config


def get_load_kwh_for_slot(slot_start, slot_end, load_profile_history=None):
    """Calculate expected load (kWh) for a 30-minute slot using historical rolling averages or hourly baseline profile."""
    local_start = slot_start.astimezone()
    slot_key = local_start.strftime("%H:%M")

    # 1. Check historical telemetry rolling average if available
    if load_profile_history and isinstance(load_profile_history, dict):
        history = load_profile_history.get(slot_key)
        if history and isinstance(history, list) and len(history) > 0:
            return float(sum(history) / len(history))

    # 2. Hourly baseline profile fallback
    hour = local_start.hour
    weekday = local_start.weekday()  # 0=Monday, 4=Friday, 6=Sunday
    
    if 16 <= hour < 18:
        # Oven & dinner cooking peak boost (16:00 - 18:00)
        watts = getattr(config, 'EVENING_COOKING_LOAD_W', 2200)
    elif 18 <= hour < 20:
        watts = getattr(config, 'EVENING_PEAK_LOAD_W', 1200)
    elif (6 <= hour < 16) or (20 <= hour < 23):
        # Weekday laundry appliance load adjustment (9:00 - 16:00 Mon-Fri)
        if weekday < 5 and (9 <= hour < 16):
            watts = getattr(config, 'DAYTIME_LOAD_W', 700) + 400  # +400W active laundry allocation
        else:
            watts = getattr(config, 'DAYTIME_LOAD_W', 700)
    else:  # Overnight: 23:00 - 06:00
        watts = getattr(config, 'OVERNIGHT_LOAD_W', getattr(config, 'BASE_LOAD_W', 400))

    return (watts / 1000.0) * 0.5


def is_power_down_slot(slot_start, slot_end):
    """Check if a slot falls within any configured Octopus Octoplus Power Down / Saving Session window."""
    local_start = slot_start.astimezone()
    local_end = slot_end.astimezone()
    windows = getattr(config, 'POWER_DOWN_WINDOWS', [])
    if not windows:
        return False
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
