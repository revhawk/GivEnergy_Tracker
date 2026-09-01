"""Open-Meteo Solar API integration module (Free, open-source solar irradiance model)."""
import logging
import requests
from datetime import datetime, timezone

import config


def fetch_openmeteo_solar_forecast():
    """Fetch 48-hour solar generation forecast from Open-Meteo Solar API."""
    lat = getattr(config, 'LATITUDE', 52.0)
    lon = getattr(config, 'LONGITUDE', -2.0)
    tilt = getattr(config, 'SOLAR_DECLINATION', 35)
    azimuth = getattr(config, 'SOLAR_AZIMUTH', 0)
    kwp = getattr(config, 'SOLAR_KWP', 10.0)

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&hourly=global_tilted_irradiance&"
        f"tilt={tilt}&azimuth={azimuth}&forecast_days=2"
    )
    logging.info(f"Fetching parallel solar forecast from Open-Meteo: {url}")
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()

        hourly = data.get('hourly', {})
        times = hourly.get('time', [])
        irradiance = hourly.get('global_tilted_irradiance', [])

        forecasts = []
        for time_str, gti in zip(times, irradiance):
            if gti is None:
                gti = 0.0
            dt_naive = datetime.fromisoformat(time_str)
            dt_utc = dt_naive.replace(tzinfo=timezone.utc)
            dt_local = dt_utc.astimezone()
            # GTI is W/m²; for 1kWp panel array at STC (1000 W/m²), kWh per hour ≈ (gti / 1000.0) * kwp
            kwh_hour = (float(gti) / 1000.0) * float(kwp)
            forecasts.append({
                'time': dt_local,
                'kwh': max(0.0, kwh_hour)
            })
        forecasts.sort(key=lambda f: f['time'])
        return forecasts
    except Exception as e:
        logging.warning(f"Failed to fetch parallel Open-Meteo solar forecast: {e}")
        return []
