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

    def test_morning_solar_damping_applied_before_9am(self):
        forecast = [{"time": datetime(2026, 7, 3, 8, 0).astimezone(), "kwh": 2.0}]
        slot = self._slot("2026-07-03T07:30:00+01:00", "2026-07-03T08:00:00+01:00")
        got = optimiser.get_solar_kwh_for_slot(slot["start"], slot["end"], forecast)
        # 2.0 kWh / 2 * 0.65 = 0.65 kWh
        assert abs(got - 0.65) < 1e-4

    def test_no_match_returns_zero(self):
        forecast = [{"time": datetime(2026, 7, 3, 12, 0).astimezone(), "kwh": 2.0}]
        slot = self._slot("2026-07-03T20:00:00+01:00", "2026-07-03T20:30:00+01:00")
        assert optimiser.get_solar_kwh_for_slot(slot["start"], slot["end"], forecast) == 0.0

    def test_empty_forecast_returns_zero(self):
        slot = self._slot("2026-07-03T11:30:00+01:00", "2026-07-03T12:00:00+01:00")
        assert optimiser.get_solar_kwh_for_slot(slot["start"], slot["end"], []) == 0.0


class TestGetLoadKwhForSlot:
    def _slot(self, start_iso, end_iso):
        return {
            "start": datetime.fromisoformat(start_iso.replace("Z", "+00:00")),
            "end": datetime.fromisoformat(end_iso.replace("Z", "+00:00")),
        }

    def test_evening_peak_load_baseline(self):
        slot = self._slot("2026-07-03T18:00:00+01:00", "2026-07-03T18:30:00+01:00")
        got = optimiser.get_load_kwh_for_slot(slot["start"], slot["end"])
        # 1200W * 0.5h / 1000 = 0.60 kWh
        assert got == 0.60

    def test_daytime_load_baseline(self):
        # Weekday (Friday 2026-07-03): (700W + 400W laundry) * 0.5h / 1000 = 0.55 kWh
        slot_weekday = self._slot("2026-07-03T14:00:00+01:00", "2026-07-03T14:30:00+01:00")
        got_weekday = optimiser.get_load_kwh_for_slot(slot_weekday["start"], slot_weekday["end"])
        assert got_weekday == 0.55

        # Weekend (Saturday 2026-07-04): 700W * 0.5h / 1000 = 0.35 kWh
        slot_weekend = self._slot("2026-07-04T14:00:00+01:00", "2026-07-04T14:30:00+01:00")
        got_weekend = optimiser.get_load_kwh_for_slot(slot_weekend["start"], slot_weekend["end"])
        assert got_weekend == 0.35

    def test_overnight_load_baseline(self):
        slot = self._slot("2026-07-03T02:00:00+01:00", "2026-07-03T02:30:00+01:00")
        got = optimiser.get_load_kwh_for_slot(slot["start"], slot["end"])
        # 400W * 0.5h / 1000 = 0.20 kWh
        assert got == 0.20

    def test_uses_historical_rolling_average(self):
        slot = self._slot("2026-07-03T18:00:00+01:00", "2026-07-03T18:30:00+01:00")
        history = {"18:00": [0.50, 0.70, 0.60]}
        got = optimiser.get_load_kwh_for_slot(slot["start"], slot["end"], history)
        assert abs(got - 0.60) < 1e-4

    def test_weekday_partitioned_load_history(self):
        # Friday (weekday) vs Saturday (weekend) with partitioned history
        slot_weekday = self._slot("2026-07-03T14:00:00+01:00", "2026-07-03T14:30:00+01:00")
        slot_weekend = self._slot("2026-07-04T14:00:00+01:00", "2026-07-04T14:30:00+01:00")
        history = {
            "weekday_14:00": [1.15, 1.25],
            "weekend_14:00": [0.35, 0.45],
            "14:00": [0.55]
        }
        got_weekday = optimiser.get_load_kwh_for_slot(slot_weekday["start"], slot_weekday["end"], history)
        got_weekend = optimiser.get_load_kwh_for_slot(slot_weekend["start"], slot_weekend["end"], history)
        assert abs(got_weekday - 1.20) < 1e-4
        assert abs(got_weekend - 0.40) < 1e-4

    def test_tumble_dryer_load_custom_config(self, monkeypatch):
        slot_weekday = self._slot("2026-07-03T14:00:00+01:00", "2026-07-03T14:30:00+01:00")
        import config
        monkeypatch.setattr(config, "TUMBLE_DRYER_LOAD_W", 1000, raising=False)
        # (700W daytime + 400W laundry + 1000W dryer) * 0.5h / 1000 = 1.05 kWh
        got = optimiser.get_load_kwh_for_slot(slot_weekday["start"], slot_weekday["end"])
        assert abs(got - 1.05) < 1e-4


class TestIsPowerDownSlot:
    def _slot(self, start_iso, end_iso):
        return {
            "start": datetime.fromisoformat(start_iso.replace("Z", "+00:00")),
            "end": datetime.fromisoformat(end_iso.replace("Z", "+00:00")),
        }

    def test_matches_power_down_window(self):
        # 18:00 - 19:00 window
        slot = self._slot("2026-07-03T18:00:00+01:00", "2026-07-03T18:30:00+01:00")
        assert optimiser.is_power_down_slot(slot["start"], slot["end"]) is True

    def test_non_power_down_window(self):
        slot = self._slot("2026-07-03T14:00:00+01:00", "2026-07-03T14:30:00+01:00")
        assert optimiser.is_power_down_slot(slot["start"], slot["end"]) is False


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


class TestOctoplusLiveGraphQL:
    def test_parses_kraken_graphql_event_types(self):
        # Power Down (TURN_DOWN)
        pd_data = {
            "id": 7865,
            "code": "EVENT_122_261026",
            "startAt": "2026-10-26T18:00:00+00:00",
            "endAt": "2026-10-26T19:00:00+00:00",
            "eventType": "TURN_DOWN",
            "rewardPerKwhInOctoPoints": 75,
            "is_joined": True
        }
        parsed_pd = optimiser.parse_octoplus_session(pd_data)
        assert parsed_pd["session_type"] == "power_down"
        assert parsed_pd["display_name"] == "Power Down Session"
        assert parsed_pd["reward_points"] == 75
        assert parsed_pd["is_joined"] is True
        assert parsed_pd["event_id"] == 7865

        # Power Up (WEEKEND_HAPPY_HOUR)
        pu_data = {
            "id": 7829,
            "code": "EVENT_118_251026",
            "startAt": "2026-10-25T07:00:00+00:00",
            "endAt": "2026-10-25T08:00:00+00:00",
            "eventType": "WEEKEND_HAPPY_HOUR",
            "rewardPerKwhInOctoPoints": 0,
            "is_joined": False
        }
        parsed_pu = optimiser.parse_octoplus_session(pu_data)
        assert parsed_pu["session_type"] == "power_up"
        assert parsed_pu["display_name"] == "Power Up Session"
        assert parsed_pu["reward_points"] == 0
        assert parsed_pu["is_joined"] is False

    def test_obtain_kraken_token_and_cache(self, monkeypatch):
        optimiser.clear_octoplus_cache()
        call_count = 0

        def mock_post(url, json=None, timeout=None, headers=None):
            nonlocal call_count
            call_count += 1
            class MockResp:
                def raise_for_status(self): pass
                def json(self):
                    return {"data": {"obtainKrakenToken": {"token": "test_jwt_token_123"}}}
            return MockResp()

        import requests
        monkeypatch.setattr(requests, "post", mock_post)

        token1 = optimiser.obtain_kraken_token(api_key="sk_test")
        assert token1 == "test_jwt_token_123"
        assert call_count == 1

        # Second call should use cache
        token2 = optimiser.obtain_kraken_token(api_key="sk_test")
        assert token2 == "test_jwt_token_123"
        assert call_count == 1

    def test_fetch_live_octoplus_sessions_success(self, monkeypatch):
        optimiser.clear_octoplus_cache()
        import octoplus
        octoplus._kraken_token_cache["token"] = "mock_jwt"
        octoplus._kraken_token_cache["fetched_at"] = datetime.now(timezone.utc)

        now = datetime.now(timezone.utc)
        start1 = (now + timedelta(hours=1)).isoformat()
        end1 = (now + timedelta(hours=2)).isoformat()
        start2 = (now + timedelta(hours=3)).isoformat()
        end2 = (now + timedelta(hours=4)).isoformat()

        mock_graphql_data = {
            "data": {
                "savingSessions": {
                    "events": [
                        {
                            "id": 101,
                            "code": "EVENT_DOWN_1",
                            "startAt": start1,
                            "endAt": end1,
                            "eventType": "TURN_DOWN",
                            "rewardPerKwhInOctoPoints": 80
                        },
                        {
                            "id": 102,
                            "code": "EVENT_UP_1",
                            "startAt": start2,
                            "endAt": end2,
                            "eventType": "WEEKEND_HAPPY_HOUR",
                            "rewardPerKwhInOctoPoints": 0
                        }
                    ],
                    "account": {
                        "joinedEvents": [
                            {"eventId": 101, "startAt": start1, "endAt": end1}
                        ]
                    }
                }
            }
        }

        import requests
        class MockResp:
            ok = True
            def raise_for_status(self): pass
            def json(self): return mock_graphql_data

        monkeypatch.setattr(requests, "post", lambda url, json=None, headers=None, timeout=None: MockResp())

        sessions = optimiser.fetch_live_octoplus_sessions(api_key="sk_test", account_id="A-123", lookback_hours=48)
        assert len(sessions) == 2
        assert sessions[0]["session_type"] == "power_down"
        assert sessions[0]["is_joined"] is True
        assert sessions[1]["session_type"] == "power_up"
        assert sessions[1]["is_joined"] is False

    def test_is_power_down_and_power_up_slot_dt(self):
        now = datetime.now(timezone.utc)
        live_sessions = [
            {
                "session_type": "power_down",
                "start": now + timedelta(hours=1),
                "end": now + timedelta(hours=2)
            },
            {
                "session_type": "power_up",
                "start": now + timedelta(hours=3),
                "end": now + timedelta(hours=4)
            }
        ]

        # Slot inside Power Down
        s1_start = now + timedelta(hours=1, minutes=15)
        s1_end = now + timedelta(hours=1, minutes=45)
        assert optimiser.is_power_down_slot_dt(s1_start, s1_end, live_sessions=live_sessions) is True
        assert optimiser.is_power_up_slot_dt(s1_start, s1_end, live_sessions=live_sessions) is False

        # Slot inside Power Up
        s2_start = now + timedelta(hours=3, minutes=15)
        s2_end = now + timedelta(hours=3, minutes=45)
        assert optimiser.is_power_up_slot_dt(s2_start, s2_end, live_sessions=live_sessions) is True
        assert optimiser.is_power_down_slot_dt(s2_start, s2_end, live_sessions=live_sessions) is False


