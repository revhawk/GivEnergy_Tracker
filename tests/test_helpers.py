"""Tests for pure helper functions in optimiser.py."""
from datetime import datetime, timezone, timedelta

import optimiser


class TestParseUtcIso:
    def test_z_suffix_is_utc(self):
        got = optimiser.parse_utc_iso("2026-07-03T02:30:00Z")
        assert got == datetime(2026, 7, 3, 2, 30, tzinfo=timezone.utc)

    def test_offset_suffix_converts_to_utc(self):
        # BST is UTC+1
        got = optimiser.parse_utc_iso("2026-07-03T03:30:00+01:00")
        assert got == datetime(2026, 7, 3, 2, 30, tzinfo=timezone.utc)

    def test_returns_utc_tz(self):
        got = optimiser.parse_utc_iso("2026-01-15T12:00:00Z")
        assert got.tzinfo == timezone.utc


class TestFindKeyRecursive:
    def test_top_level_key(self):
        assert optimiser.find_key_recursive({"SOC": 42}, "SOC") == 42

    def test_nested_dict(self):
        data = {"Level1": {"Level2": {"SOC": 55}}}
        assert optimiser.find_key_recursive(data, "SOC") == 55

    def test_inside_list(self):
        data = {"items": [{"other": 1}, {"SOC": 30}]}
        assert optimiser.find_key_recursive(data, "SOC") == 30

    def test_case_insensitive_match(self):
        assert optimiser.find_key_recursive({"soc": 25}, "SOC") == 25
        assert optimiser.find_key_recursive({"Soc": 25}, "soc") == 25

    def test_missing_returns_none(self):
        assert optimiser.find_key_recursive({"foo": "bar"}, "SOC") is None

    def test_empty_returns_none(self):
        assert optimiser.find_key_recursive({}, "SOC") is None
        assert optimiser.find_key_recursive([], "SOC") is None


class TestGetSolarKwhForSlot:
    def _slot(self, start_iso, end_iso):
        return {
            "start": datetime.fromisoformat(start_iso.replace("Z", "+00:00")),
            "end": datetime.fromisoformat(end_iso.replace("Z", "+00:00")),
        }

    def test_hour_match_halves_the_kwh(self):
        # An hourly forecast of 2.0 kWh should map to 1.0 kWh per half-hour slot
        forecast = [{"time": datetime(2026, 7, 3, 12, 0).astimezone(), "kwh": 2.0}]
        slot = self._slot("2026-07-03T11:30:00+01:00", "2026-07-03T12:00:00+01:00")
        got = optimiser.get_solar_kwh_for_slot(slot["start"], slot["end"], forecast)
        assert got == 1.0

    def test_no_match_returns_zero(self):
        forecast = [{"time": datetime(2026, 7, 3, 12, 0).astimezone(), "kwh": 2.0}]
        slot = self._slot("2026-07-03T20:00:00+01:00", "2026-07-03T20:30:00+01:00")
        assert optimiser.get_solar_kwh_for_slot(slot["start"], slot["end"], forecast) == 0.0

    def test_empty_forecast_returns_zero(self):
        slot = self._slot("2026-07-03T11:30:00+01:00", "2026-07-03T12:00:00+01:00")
        assert optimiser.get_solar_kwh_for_slot(slot["start"], slot["end"], []) == 0.0


class TestRecordPlan:
    def test_populates_module_global(self):
        optimiser._last_plan.clear()
        optimiser._record_plan(action="charge", branch="scheduled", current_soc=50)
        assert optimiser._last_plan["action"] == "charge"
        assert optimiser._last_plan["current_soc"] == 50
        assert "at" in optimiser._last_plan

    def test_clears_previous_plan(self):
        optimiser._record_plan(action="charge", stale=True)
        optimiser._record_plan(action="no_charge")
        assert "stale" not in optimiser._last_plan
        assert optimiser._last_plan["action"] == "no_charge"
