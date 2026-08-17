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
    """The mapping is done in *local* time (via .astimezone() with no tz arg).
    The add-on runs with TZ=Europe/London. Tests assume the same — CI sets it
    on the runner. If you run pytest locally in a different tz, use TZ=Europe/London.
    """

    def _slot(self, start_iso, end_iso):
        return {
            "start": datetime.fromisoformat(start_iso.replace("Z", "+00:00")),
            "end": datetime.fromisoformat(end_iso.replace("Z", "+00:00")),
        }

    def test_hour_match_halves_the_kwh(self):
        # Forecast comes back as local-time entries. With TZ=Europe/London,
        # the .astimezone() call produces Europe/London-tz datetimes.
        forecast = [{"time": datetime(2026, 7, 3, 12, 0).astimezone(), "kwh": 2.0}]
        slot = self._slot("2026-07-03T11:30:00+01:00", "2026-07-03T12:00:00+01:00")
        got = optimiser.get_solar_kwh_for_slot(slot["start"], slot["end"], forecast)
        # Hourly 2.0 kWh split across two half-hour slots → 1.0 kWh each
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


class TestGetOctoplusEntityName:
    def test_power_down_new_and_fallback(self):
        assert optimiser.get_octoplus_entity_name("power_down", prefer_new=True) == "sensor.octopus_energy_power_down_sessions"
        assert optimiser.get_octoplus_entity_name("saving_sessions", prefer_new=False) == "sensor.octopus_energy_saving_sessions"

    def test_power_up_new_and_fallback(self):
        assert optimiser.get_octoplus_entity_name("power_up", prefer_new=True) == "sensor.octopus_energy_power_up_sessions"
        assert optimiser.get_octoplus_entity_name("free_electricity", prefer_new=False) == "sensor.octopus_energy_free_electricity_sessions"

    def test_events_and_calendars(self):
        assert optimiser.get_octoplus_entity_name("power_down", entity_kind="event", prefer_new=True) == "event.octopus_energy_octoplus_power_down_events"
        assert optimiser.get_octoplus_entity_name("power_up", entity_kind="calendar", prefer_new=True) == "calendar.octopus_energy_octoplus_power_up_sessions"


class TestParseOctoplusSession:
    def test_parses_power_down_session(self):
        data = {
            "type": "power_down",
            "start": "2026-08-17T18:00:00Z",
            "end": "2026-08-17T19:00:00Z",
            "code": "PD123"
        }
        parsed = optimiser.parse_octoplus_session(data)
        assert parsed["session_type"] == "power_down"
        assert parsed["display_name"] == "Power Down Session"
        assert parsed["code"] == "PD123"

    def test_parses_power_up_legacy_free_electricity_session(self):
        data = {
            "session_type": "free_electricity",
            "start": "2026-08-17T13:00:00Z",
            "end": "2026-08-17T14:00:00Z",
            "code": "PU456"
        }
        parsed = optimiser.parse_octoplus_session(data)
        assert parsed["session_type"] == "power_up"
        assert parsed["display_name"] == "Power Up Session"


class TestGetOpenAIModel:
    def test_default_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        assert optimiser.get_openai_model() == "gpt-4o-mini"

    def test_environment_variable_override(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
        assert optimiser.get_openai_model() == "gpt-4o"


