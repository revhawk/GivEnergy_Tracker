"""Unit & Mutation-Killing Tests for Hive Hot Water Controller (hive.py)."""
import pytest
import responses

import hive


class TestHiveHotWater:
    def test_evaluate_morning_shower_safety_exact_boundary(self):
        # Missing sensor -> SAFE mode (False = keep gas schedule)
        assert hive.evaluate_morning_shower_safety(None) is False
        # Below threshold -> False
        assert hive.evaluate_morning_shower_safety(44.9, min_required_c=45.0) is False
        # Exact threshold (45.0 >= 45.0) -> True (kills >= -> > mutant!)
        assert hive.evaluate_morning_shower_safety(45.0, min_required_c=45.0) is True
        # Above threshold -> True
        assert hive.evaluate_morning_shower_safety(52.0, min_required_c=45.0) is True

    def test_should_suppress_gas_hot_water_exact_boundary(self):
        assert hive.should_suppress_gas_hot_water(0.0, grid_charged_overnight=True) is True
        assert hive.should_suppress_gas_hot_water(14.9, grid_charged_overnight=False, min_solar_kwh=15.0) is False
        # Exact boundary (15.0 >= 15.0) -> True (kills >= -> > mutant!)
        assert hive.should_suppress_gas_hot_water(15.0, grid_charged_overnight=False, min_solar_kwh=15.0) is True

    @responses.activate
    def test_get_hive_hot_water_state_200_vs_500(self, monkeypatch):
        monkeypatch.setattr(hive, "SUPERVISOR_TOKEN", "sup_tok")
        url = "http://supervisor/core/api/states/water_heater.hive_hot_water"
        responses.add(responses.GET, url, json={"state": "eco"}, status=200)
        res = hive.get_hive_hot_water_state()
        assert res == {"state": "eco"}

        responses.replace(responses.GET, url, status=500)
        assert hive.get_hive_hot_water_state() is None

        monkeypatch.setattr(hive, "SUPERVISOR_TOKEN", "")
        assert hive.get_hive_hot_water_state() is None
