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
    optimiser._export_rate_cache = {"rate": None, "fetched_at": None}
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
        # Second response with new rate
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/OUTGOING-VAR-24-10-26/electricity-tariffs/E-1R-OUTGOING-VAR-24-10-26-E/standard-unit-rates/",
            json={"results": [{"valid_from": "2026-03-01T00:00:00Z", "valid_to": None, "value_inc_vat": 10.50}]},
        )
        # First call populates cache
        assert optimiser.fetch_export_rate() == 12.00
        # Manually expire cache: set fetched_at to 7h ago
        optimiser._export_rate_cache["fetched_at"] = datetime.now(timezone.utc) - timedelta(hours=7)
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
