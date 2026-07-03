"""Pytest configuration.

Adds the add-on directory to sys.path so tests can `import optimiser` directly,
and stubs the `config` module the optimiser imports at load time.
"""
import sys
import types
from pathlib import Path

# Make the add-on importable
_ADDON_DIR = Path(__file__).resolve().parent.parent / "ha-addon"
sys.path.insert(0, str(_ADDON_DIR))

# The optimiser does `import config` at import time. Provide a stub with
# realistic defaults so tests don't need a real config.py.
if "config" not in sys.modules:
    config = types.ModuleType("config")
    config.INVERTER_IP = "192.0.2.1"
    config.INVERTER_PORT = 8899
    config.GIVTCP_URL = "http://192.0.2.1:6345"
    config.OCTOPUS_API_KEY = "sk_test_dummy"
    config.OCTOPUS_ACCOUNT_ID = "A-TEST0000"
    config.AGILE_PRODUCT_CODE = "AGILE-24-10-01"
    config.AGILE_TARIFF_CODE = "E-1R-AGILE-24-10-01-E"
    config.EXPORT_PRODUCT_CODE = "OUTGOING-VAR-24-10-26"
    config.EXPORT_TARIFF_CODE = "E-1R-OUTGOING-VAR-24-10-26-E"
    config.EXPORT_RATE_P_FALLBACK = 12.0
    config.ARBITRAGE_MARGIN_P = 0.5
    config.LATITUDE = 52.0
    config.LONGITUDE = -2.0
    config.SOLAR_DECLINATION = 35
    config.SOLAR_AZIMUTH = 0
    config.SOLAR_KWP = 10.0
    config.BATTERY_CAPACITY_KWH = 9.5
    config.MAX_BATTERY_CHARGE_RATE = 3000
    config.IBOOST_MAX_DIVERT_RATE = 3000
    config.BASE_LOAD_W = 1000
    config.LOG_FILE_PATH = None  # No file logging in tests
    config.LOG_LEVEL = "WARNING"
    config.OPENAI_API_KEY = ""
    sys.modules["config"] = config
