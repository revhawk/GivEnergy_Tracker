"""Solar forecast integration module (Forecast.Solar API primary + Open-Meteo parallel comparison)."""
import logging
import requests
from datetime import datetime

import config
from solar_openmeteo import fetch_openmeteo_solar_forecast


def fetch_solar_forecast():
    """Fetch Solar Forecast from Forecast.Solar (free tier API)."""
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


def get_solar_kwh_for_slot(slot_start, slot_end, solar_forecasts):
    """Map hourly solar forecast to half-hourly Octopus slots (with morning damping)."""
    local_start = slot_start.astimezone()
    local_end = slot_end.astimezone()
    for f in solar_forecasts:
        f_time = f['time']
        if (f_time.year == local_end.year and 
            f_time.month == local_end.month and 
            f_time.day == local_end.day and 
            f_time.hour == local_end.hour):
            raw_kwh = f['kwh'] / 2.0
            if local_start.hour < 9:
                multiplier = getattr(config, 'MORNING_SOLAR_DAMPING', 0.65)
                return raw_kwh * multiplier
            return raw_kwh
    return 0.0


def fetch_parallel_solar_forecasts():
    """Fetch primary (Forecast.Solar) and secondary (Open-Meteo) forecasts in parallel and log comparison metrics."""
    primary_forecasts = fetch_solar_forecast()
    secondary_forecasts = fetch_openmeteo_solar_forecast()

    primary_total_kwh = sum(f['kwh'] for f in primary_forecasts) if primary_forecasts else 0.0
    secondary_total_kwh = sum(f['kwh'] for f in secondary_forecasts) if secondary_forecasts else 0.0

    diff_kwh = secondary_total_kwh - primary_total_kwh
    logging.info(
        f"☀️ [PARALLEL SOLAR COMPARISON] "
        f"Primary (Forecast.Solar): {primary_total_kwh:.2f} kWh | "
        f"Secondary (Open-Meteo): {secondary_total_kwh:.2f} kWh | "
        f"Diff: {diff_kwh:+.2f} kWh"
    )

    comparison = {
        'primary_total_kwh': primary_total_kwh,
        'secondary_total_kwh': secondary_total_kwh,
        'diff_kwh': diff_kwh,
        'primary_count': len(primary_forecasts),
        'secondary_count': len(secondary_forecasts),
    }

    return primary_forecasts, comparison
