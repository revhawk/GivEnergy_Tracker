"""Tests for external API fetch functions.

We mock the network with `responses` so nothing actually hits Octopus or
Forecast.Solar.
"""
from datetime import datetime, timezone, timedelta

import pytest
import responses

import optimiser


@pytest.fixture(autouse=True)
def clear_export_cache():
    optimiser._export_rate_cache = {"rate": None, "results": [], "fetched_at": None}
    optimiser._account_tariffs_cache = {"tariffs": None, "fetched_at": None}
    yield


class TestFetchAgileRates:
    @responses.activate
    def test_parses_and_sorts_chronologically(self):
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/E-1R-AGILE-24-10-01-E/standard-unit-rates/",
            json={"results": [
                {"valid_from": "2026-07-04T02:00:00Z", "valid_to": "2026-07-04T02:30:00Z", "value_inc_vat": 5.10},
                {"valid_from": "2026-07-04T01:30:00Z", "valid_to": "2026-07-04T02:00:00Z", "value_inc_vat": 4.20},
                {"valid_from": "2026-07-04T02:30:00Z", "valid_to": "2026-07-04T03:00:00Z", "value_inc_vat": 3.30},
            ]},
        )
        slots = optimiser.fetch_agile_rates()
        assert len(slots) == 3
        # Chronological order enforced by fetch_agile_rates
        assert slots[0]["price"] == 4.20
        assert slots[1]["price"] == 5.10
        assert slots[2]["price"] == 3.30

    @responses.activate
    def test_network_error_returns_empty_list(self):
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/E-1R-AGILE-24-10-01-E/standard-unit-rates/",
            status=500,
        )
        assert optimiser.fetch_agile_rates() == []

    @responses.activate
    def test_subdivides_multi_hour_rate_blocks_into_half_hours(self):
        # Flux multi-hour windows: 3 hours off-peak (02:00-05:00) and 11 hours day shoulder (05:00-16:00)
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/E-1R-AGILE-24-10-01-E/standard-unit-rates/",
            json={"results": [
                {"valid_from": "2026-11-10T02:00:00Z", "valid_to": "2026-11-10T05:00:00Z", "value_inc_vat": 15.60},
                {"valid_from": "2026-11-10T05:00:00Z", "valid_to": "2026-11-10T16:00:00Z", "value_inc_vat": 25.80},
            ]},
        )
        slots = optimiser.fetch_agile_rates()
        # 3h = 6 slots of 30 min; 11h = 22 slots of 30 min -> 28 slots total
        assert len(slots) == 28
        assert all(s["price"] == 15.60 for s in slots[:6])
        assert all(s["price"] == 25.80 for s in slots[6:])
        # Check first and last slot boundaries
        assert slots[0]["start"].strftime("%H:%M") == "02:00"
        assert slots[0]["end"].strftime("%H:%M") == "02:30"
        assert slots[5]["start"].strftime("%H:%M") == "04:30"
        assert slots[5]["end"].strftime("%H:%M") == "05:00"
        assert slots[6]["start"].strftime("%H:%M") == "05:00"
        assert slots[-1]["end"].strftime("%H:%M") == "16:00"



class TestFetchExportRate:
    @responses.activate
    def test_picks_currently_active_rate(self):
        # Two rate periods — one expired, one current-and-open-ended
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/OUTGOING-VAR-24-10-26/electricity-tariffs/E-1R-OUTGOING-VAR-24-10-26-E/standard-unit-rates/",
            json={"results": [
                {"valid_from": "2026-03-01T00:00:00Z", "valid_to": None, "value_inc_vat": 12.00},
                {"valid_from": "2024-10-25T23:00:00Z", "valid_to": "2026-03-01T00:00:00Z", "value_inc_vat": 15.00},
            ]},
        )
        assert optimiser.fetch_export_rate() == 12.00

    @responses.activate
    def test_caches_for_6h(self):
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/OUTGOING-VAR-24-10-26/electricity-tariffs/E-1R-OUTGOING-VAR-24-10-26-E/standard-unit-rates/",
            json={"results": [
                {"valid_from": "2026-03-01T00:00:00Z", "valid_to": None, "value_inc_vat": 12.00},
            ]},
        )
        first = optimiser.fetch_export_rate()
        second = optimiser.fetch_export_rate()
        assert first == second == 12.00
        # Only one HTTP request should have been made
        assert len(responses.calls) == 1

    @responses.activate
    def test_fallback_on_network_error(self):
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/OUTGOING-VAR-24-10-26/electricity-tariffs/E-1R-OUTGOING-VAR-24-10-26-E/standard-unit-rates/",
            status=500,
        )
        assert optimiser.fetch_export_rate() == 12.0  # matches config.EXPORT_RATE_P_FALLBACK

    @responses.activate
    def test_stale_cache_refreshes_after_ttl(self):
        # First response
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/OUTGOING-VAR-24-10-26/electricity-tariffs/E-1R-OUTGOING-VAR-24-10-26-E/standard-unit-rates/",
            json={"results": [{"valid_from": "2026-03-01T00:00:00Z", "valid_to": None, "value_inc_vat": 12.00}]},
        )
        # First call populates cache
        assert optimiser.fetch_export_rate() == 12.00
        # Second response with new rate
        responses.replace(
            responses.GET,
            "https://api.octopus.energy/v1/products/OUTGOING-VAR-24-10-26/electricity-tariffs/E-1R-OUTGOING-VAR-24-10-26-E/standard-unit-rates/",
            json={"results": [{"valid_from": "2026-03-01T00:00:00Z", "valid_to": None, "value_inc_vat": 10.50}]},
        )
        # Manually expire cache: set fetched_at to 7h ago
        import tariffs
        tariffs._export_rate_cache["fetched_at"] = datetime.now(timezone.utc) - timedelta(hours=7)
        # Next call should re-fetch and return the updated rate
        assert optimiser.fetch_export_rate() == 10.50


class TestFetchSolarForecast:
    @responses.activate
    def test_parses_wh_period_dict_to_kwh_list(self):
        responses.add(
            responses.GET,
            "https://api.forecast.solar/estimate/52.0/-2.0/35/0/10.0",
            json={"result": {"watt_hours_period": {
                "2026-07-04 11:00:00": 500,
                "2026-07-04 12:00:00": 2000,
                "2026-07-04 13:00:00": 1500,
            }}},
        )
        forecast = optimiser.fetch_solar_forecast()
        assert len(forecast) == 3
        # Sorted chronologically, kWh = wh/1000
        assert forecast[0]["kwh"] == 0.5
        assert forecast[1]["kwh"] == 2.0
        assert forecast[2]["kwh"] == 1.5

    @responses.activate
    def test_rate_limit_returns_empty(self):
        responses.add(
            responses.GET,
            "https://api.forecast.solar/estimate/52.0/-2.0/35/0/10.0",
            status=429,
        )
        assert optimiser.fetch_solar_forecast() == []


class TestExtractProductCode:
    def test_extract_standard_codes(self):
        assert optimiser.extract_product_code_from_tariff("E-1R-FLUX-IMPORT-23-02-14-E") == "FLUX-IMPORT-23-02-14"
        assert optimiser.extract_product_code_from_tariff("E-1R-AGILE-24-10-01-E") == "AGILE-24-10-01"
        assert optimiser.extract_product_code_from_tariff("E-1R-OUTGOING-VAR-24-10-26-E") == "OUTGOING-VAR-24-10-26"
        assert optimiser.extract_product_code_from_tariff("G-1R-OE-FIX-12M-25-11-24-E") == "OE-FIX-12M-25-11-24"

    def test_fallback_or_empty(self):
        assert optimiser.extract_product_code_from_tariff("") == ""
        assert optimiser.extract_product_code_from_tariff(None) == ""
        assert optimiser.extract_product_code_from_tariff("PRODUCT-E") == "PRODUCT"


class TestDiscoverAccountTariffs:
    @responses.activate
    def test_parses_import_export_and_gas(self):
        account_data = {
            "properties": [
                {
                    "electricity_meter_points": [
                        {
                            "mpan": "1419585700009",
                            "is_export": False,
                            "agreements": [
                                {
                                    "tariff_code": "E-1R-FLUX-IMPORT-23-02-14-E",
                                    "valid_from": "2026-11-10T00:00:00Z",
                                    "valid_to": None,
                                }
                            ],
                        },
                        {
                            "mpan": "1470001817590",
                            "is_export": True,
                            "agreements": [
                                {
                                    "tariff_code": "E-1R-OUTGOING-VAR-24-10-26-E",
                                    "valid_from": "2024-11-08T00:00:00Z",
                                    "valid_to": None,
                                }
                            ],
                        },
                    ],
                    "gas_meter_points": [
                        {
                            "mprn": "2488354103",
                            "agreements": [
                                {
                                    "tariff_code": "G-1R-OE-FIX-12M-25-11-24-E",
                                    "valid_from": "2025-12-09T00:00:00Z",
                                    "valid_to": "2026-12-09T00:00:00Z",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/accounts/A-TEST0000/",
            json=account_data,
        )

        discovered = optimiser.discover_account_tariffs(api_key="sk_test", account_id="A-TEST0000")
        assert discovered is not None
        assert discovered["import"]["product_code"] == "FLUX-IMPORT-23-02-14"
        assert discovered["import"]["tariff_code"] == "E-1R-FLUX-IMPORT-23-02-14-E"
        assert discovered["export"]["product_code"] == "OUTGOING-VAR-24-10-26"
        assert discovered["export"]["tariff_code"] == "E-1R-OUTGOING-VAR-24-10-26-E"
        assert discovered["gas"]["product_code"] == "OE-FIX-12M-25-11-24"

    def test_missing_credentials_returns_none(self):
        import tariffs
        assert tariffs.discover_account_tariffs(api_key="", account_id="") is None

    @responses.activate
    def test_account_api_error_returns_none(self):
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/accounts/A-TEST0000/",
            status=500,
        )
        import tariffs
        assert tariffs.discover_account_tariffs(api_key="sk_test", account_id="A-TEST0000") is None


class TestTimeOfUseExport:
    @responses.activate
    def test_slot_specific_rate_matching(self):
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/FLUX-EXPORT-23-02-14/electricity-tariffs/E-1R-FLUX-EXPORT-23-02-14-E/standard-unit-rates/",
            json={"results": [
                {"valid_from": "2026-11-10T02:00:00Z", "valid_to": "2026-11-10T05:00:00Z", "value_inc_vat": 5.40},
                {"valid_from": "2026-11-10T16:00:00Z", "valid_to": "2026-11-10T19:00:00Z", "value_inc_vat": 27.20},
                {"valid_from": "2026-11-10T05:00:00Z", "valid_to": "2026-11-10T16:00:00Z", "value_inc_vat": 15.10},
            ]},
        )
        import tariffs
        import config
        config.EXPORT_PRODUCT_CODE = "FLUX-EXPORT-23-02-14"
        config.EXPORT_TARIFF_CODE = "E-1R-FLUX-EXPORT-23-02-14-E"

        dt_night = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
        dt_peak = datetime(2026, 11, 10, 17, 30, tzinfo=timezone.utc)
        dt_day = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

        assert tariffs.fetch_export_rate(dt_night) == 5.40
        assert tariffs.fetch_export_rate(dt_peak) == 27.20
        assert tariffs.fetch_export_rate(dt_day) == 15.10

        # Reset config to original
        config.EXPORT_PRODUCT_CODE = "OUTGOING-VAR-24-10-26"
        config.EXPORT_TARIFF_CODE = "E-1R-OUTGOING-VAR-24-10-26-E"

