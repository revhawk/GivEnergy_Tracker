"""Unit & Contract Tests for Axle Energy Self-Dispatch Module (ADR 0015)."""
import pytest
import responses
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import axle
import config


class TestAxleTokenDiscovery:
    def test_discovers_token_from_config(self, monkeypatch):
        monkeypatch.setattr(config, "AXLE_API_TOKEN", "test_tok_123", raising=False)
        monkeypatch.delenv("AXLE_API_TOKEN", raising=False)
        monkeypatch.delenv("AXLE_TOKEN", raising=False)
        assert axle.get_axle_token() == "test_tok_123"

    def test_strips_bearer_prefix_and_whitespace(self, monkeypatch):
        monkeypatch.setattr(config, "AXLE_API_TOKEN", "  Bearer my_secret_token_abc  ", raising=False)
        monkeypatch.delenv("AXLE_API_TOKEN", raising=False)
        monkeypatch.delenv("AXLE_TOKEN", raising=False)
        assert axle.get_axle_token() == "my_secret_token_abc"

    def test_environment_variable_precedence(self, monkeypatch):
        monkeypatch.setattr(config, "AXLE_API_TOKEN", "config_token", raising=False)
        monkeypatch.setenv("AXLE_API_TOKEN", "env_token_456")
        assert axle.get_axle_token() == "env_token_456"

    def test_returns_none_when_empty(self, monkeypatch):
        monkeypatch.setattr(config, "AXLE_API_TOKEN", "", raising=False)
        monkeypatch.setattr(config, "AXLE_TOKEN", None, raising=False)
        monkeypatch.delenv("AXLE_API_TOKEN", raising=False)
        monkeypatch.delenv("AXLE_TOKEN", raising=False)
        assert axle.get_axle_token() is None


class TestAxleConnectionAndParsing:
    def test_test_axle_connection_success(self, monkeypatch):
        class MockResp:
            status_code = 200
            text = '{"start_time": "2026-10-27T17:00:00Z", "end_time": "2026-10-27T18:00:00Z", "import_export": "export"}'
            def json(self):
                return {
                    "start_time": "2026-10-27T17:00:00Z",
                    "end_time": "2026-10-27T18:00:00Z",
                    "import_export": "export"
                }

        import requests
        monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: MockResp())

        success, msg, data = axle.test_axle_connection("valid_token")
        assert success is True
        assert "Connected" in msg
        assert data["start_time"] == "2026-10-27T17:00:00Z"

    def test_test_axle_connection_unauthorized(self, monkeypatch):
        class MockResp:
            status_code = 401
            text = "Unauthorized"

        import requests
        monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: MockResp())

        success, msg, data = axle.test_axle_connection("bad_token")
        assert success is False
        assert "Authentication failed" in msg
        assert data is None

    def test_parse_axle_payload_direct_format(self):
        payload = {
            "start_time": "2026-10-27T17:30:00Z",
            "end_time": "2026-10-27T18:30:00Z",
            "import_export": "export",
            "updated_at": "2026-10-27T12:00:00Z"
        }
        event = axle.parse_axle_payload(payload)
        assert event is not None
        assert event["start"] == datetime(2026, 10, 27, 17, 30, tzinfo=timezone.utc)
        assert event["end"] == datetime(2026, 10, 27, 18, 30, tzinfo=timezone.utc)
        assert event["import_export"] == "export"

    def test_parse_axle_payload_ha_sensor_format(self):
        payload = {
            "state": "2026-10-27T17:30:00Z",
            "attributes": {
                "start_time": "2026-10-27T17:30:00Z",
                "end_time": "2026-10-27T18:30:00Z",
                "import_export": "export",
                "updated_at": "2026-10-27T12:00:00Z"
            }
        }
        event = axle.parse_axle_payload(payload)
        assert event is not None
        assert event["start"] == datetime(2026, 10, 27, 17, 30, tzinfo=timezone.utc)
        assert event["end"] == datetime(2026, 10, 27, 18, 30, tzinfo=timezone.utc)

    def test_parse_axle_payload_none_when_empty(self):
        assert axle.parse_axle_payload({}) is None
        assert axle.parse_axle_payload({"start_time": None}) is None
        assert axle.parse_axle_payload({"start_time": "unavailable"}) is None
        assert axle.parse_axle_payload([]) is None

    def test_parse_axle_payload_list_format(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = [
            # Past event
            {
                "start_time": (now - timedelta(hours=3)).isoformat(),
                "end_time": (now - timedelta(hours=2)).isoformat(),
                "import_export": "export",
            },
            # Upcoming event
            {
                "start_time": (now + timedelta(hours=2)).isoformat(),
                "end_time": (now + timedelta(hours=3)).isoformat(),
                "import_export": "export",
            },
        ]
        event = axle.parse_axle_payload(payload)
        assert event is not None
        assert event["start"] == now + timedelta(hours=2)
        assert event["end"] == now + timedelta(hours=3)

    def test_parse_axle_payload_events_wrapper_dict(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = {
            "events": [
                {
                    "start": (now + timedelta(hours=1)).isoformat(),
                    "finish": (now + timedelta(hours=2)).isoformat(),
                    "direction": "export",
                }
            ]
        }
        event = axle.parse_axle_payload(payload)
        assert event is not None
        assert event["import_export"] == "export"
        assert event["start"] == now + timedelta(hours=1)
        assert event["end"] == now + timedelta(hours=2)

    def test_parse_axle_payload_alternative_field_names(self):
        payload = {
            "start": "2026-11-17T17:00:00Z",
            "finish": "2026-11-17T18:00:00Z",
            "direction": "export",
            "updated": "2026-11-17T10:00:00Z"
        }
        event = axle.parse_axle_payload(payload)
        assert event is not None
        assert event["start"] == datetime(2026, 11, 17, 17, 0, tzinfo=timezone.utc)
        assert event["end"] == datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
        assert event["import_export"] == "export"


class TestAxleTimingAndWindows:
    def test_is_axle_event_active(self):
        now = datetime.now(timezone.utc)
        event = {
            "start": now - timedelta(minutes=15),
            "end": now + timedelta(minutes=45),
            "import_export": "export"
        }
        assert axle.is_axle_event_active(event, now) is True

        future_event = {
            "start": now + timedelta(hours=2),
            "end": now + timedelta(hours=3),
            "import_export": "export"
        }
        assert axle.is_axle_event_active(future_event, now) is False

    def test_is_axle_precharge_window(self):
        now = datetime.now(timezone.utc)
        # Event is in 1 hour -> inside 2-hour precharge window
        event = {
            "start": now + timedelta(hours=1),
            "end": now + timedelta(hours=2),
            "import_export": "export"
        }
        assert axle.is_axle_precharge_window(event, now, lead_hours=2.0) is True

        # Event is in 5 hours -> outside precharge window
        distant_event = {
            "start": now + timedelta(hours=5),
            "end": now + timedelta(hours=6),
            "import_export": "export"
        }
        assert axle.is_axle_precharge_window(distant_event, now, lead_hours=2.0) is False

    def test_axle_event_triggers_100_percent_precharge(self):
        from simulation import build_plan_candidates
        now = datetime.now(timezone.utc)
        slots = [
            {"start": now + timedelta(hours=i), "end": now + timedelta(hours=i, minutes=30), "price": 15.0}
            for i in range(6)
        ]
        # Tag slot 4 as Axle flexibility export
        slots[4]["is_axle_export"] = True
        slots[4]["export_rate"] = 100.0

        candidates = build_plan_candidates(
            day_classification="WEATHER_ADAPTIVE",
            current_soc=40.0,
            battery_capacity=9.5,
            upcoming_slots=slots,
            total_solar_kwh=10.0,
            solar_comparison={},
            export_rate=12.0,
            total_import_kwh=0.0
        )
        c1 = candidates[0]
        assert c1["target_soc"] == 100
        assert "Axle Flexibility" in c1["name"]
        assert c1["strategy"] == "axle_flexibility"

    def test_is_axle_enabled_each_source_independently(self, monkeypatch):
        """Kills Or->And mutants in is_axle_enabled across env, config, and token sources."""
        class EmptyCfg:
            ENABLE_AXLE_DISPATCH = False
            AXLE_API_TOKEN = ""

        monkeypatch.setattr(axle, "config", EmptyCfg)
        monkeypatch.delenv("ENABLE_AXLE_DISPATCH", raising=False)
        monkeypatch.delenv("AXLE_API_TOKEN", raising=False)
        assert axle.is_axle_enabled() is False

        # Only env flag
        monkeypatch.setenv("ENABLE_AXLE_DISPATCH", "true")
        assert axle.is_axle_enabled() is True
        monkeypatch.delenv("ENABLE_AXLE_DISPATCH")

        # Only token
        monkeypatch.setenv("AXLE_API_TOKEN", "tok_123")
        assert axle.is_axle_enabled() is True
        monkeypatch.delenv("AXLE_API_TOKEN")

        # Only config flag
        EmptyCfg.ENABLE_AXLE_DISPATCH = True
        assert axle.is_axle_enabled() is True

    def test_parse_axle_payload_missing_end_defaults_plus_one_hour_and_alt_keys(self):
        """Kills Add->Sub (+1h default) and alternate-key Or->And mutants in parse_axle_payload."""
        # Missing end_time -> must default to start + 1 hour (NOT start - 1 hour!)
        parsed = axle.parse_axle_payload({"start_time": "2026-11-24T18:00:00Z"})
        assert parsed is not None
        assert parsed["end"] == parsed["start"] + timedelta(hours=1)
        assert parsed["import_export"] == "export"

        # Alternate keys in flat dict & HA attributes
        alt = axle.parse_axle_payload({"start": "2026-11-24T18:00:00Z", "finish": "2026-11-24T19:30:00Z", "direction": "import"})
        assert alt["end"] == alt["start"] + timedelta(minutes=90)
        assert alt["import_export"] == "import"

        ha_alt = axle.parse_axle_payload({
            "state": "2026-11-24T18:00:00Z",
            "attributes": {"finish": "2026-11-24T19:00:00Z", "direction": "export", "updated": "2026-11-24T12:00:00Z"}
        })
        assert ha_alt["end"] == ha_alt["start"] + timedelta(hours=1)
        assert ha_alt["updated_at"] is not None

        # Unavailable / null start strings must return None
        assert axle.parse_axle_payload({"start_time": "unavailable"}) is None
        assert axle.parse_axle_payload({"start_time": ""}) is None

    @responses.activate
    def test_fetch_ha_axle_sensor_200_vs_404(self, monkeypatch):
        """Kills status_code == 200 -> != 200 and entity_id fallback mutants."""
        monkeypatch.setattr(axle, "SUPERVISOR_TOKEN", "sup_tok")
        responses.add(
            responses.GET,
            "http://supervisor/core/api/states/sensor.axle_event",
            json={"state": "2026-11-24T20:00:00Z", "attributes": {"end_time": "2026-11-24T21:00:00Z"}},
            status=200,
        )
        ev = axle.fetch_ha_axle_sensor("sensor.axle_event")
        assert ev is not None
        assert ev["state"] == "2026-11-24T20:00:00Z"

        responses.replace(
            responses.GET,
            "http://supervisor/core/api/states/sensor.axle_event",
            status=404,
        )
        assert axle.fetch_ha_axle_sensor("sensor.axle_event") is None

    def test_fetch_axle_event_cache_hit_and_expiry(self, monkeypatch):
        """Kills cache TTL (age < 300, now - fetched_at) and force=True mutants."""
        axle.clear_axle_cache()
        now = datetime.now(timezone.utc)
        cached_ev = {"start": now + timedelta(hours=1), "end": now + timedelta(hours=2), "import_export": "export"}
        axle._axle_cache["event"] = cached_ev
        axle._axle_cache["fetched_at"] = now - timedelta(seconds=60)  # Fresh (< 300s)

        # Should return cached event without calling API
        with patch("axle.test_axle_connection") as mock_api:
            assert axle.fetch_axle_event(token="tok", force=False) == cached_ev
            assert not mock_api.called

        # Expired (> 300s) -> must re-fetch from API
        axle._axle_cache["fetched_at"] = now - timedelta(seconds=400)
        with patch("axle.test_axle_connection", return_value=(True, "ok", {"start_time": (now + timedelta(hours=2)).isoformat(), "end_time": (now + timedelta(hours=3)).isoformat()})) as mock_api:
            fresh = axle.fetch_axle_event(token="tok", force=False)
            assert mock_api.called
            assert fresh is not None

    def test_axle_window_helpers_guard_none_and_default_dt(self):
        """Kills Or->And mutants on None/missing fields and default dt in window helpers."""
        assert axle.is_axle_event_active(None) is False
        assert axle.is_axle_event_active({"start": datetime.now(timezone.utc)}) is False
        assert axle.is_axle_precharge_window(None) is False
        assert axle.is_axle_precharge_window({}) is False

        # Default dt=None works live
        now = datetime.now(timezone.utc)
        live_ev = {"start": now - timedelta(minutes=5), "end": now + timedelta(minutes=55)}
        assert axle.is_axle_event_active(live_ev) is True
        upcoming_ev = {"start": now + timedelta(minutes=30), "end": now + timedelta(minutes=90)}
        assert axle.is_axle_precharge_window(upcoming_ev, lead_hours=2.0) is True

    def test_axle_fallback_paths_and_edge_boundaries(self, monkeypatch):
        """Kills remaining fallback/boundary mutants in axle.py (HA sensor fallback, force=True,
        alternate attribute keys, default entity_id, and exact cache boundary).
        """
        # 1. HA attributes fallback with 'end' and default direction='export'
        ha_ev = axle.parse_axle_payload({
            "state": "2026-11-24T18:00:00Z",
            "attributes": {"end": "2026-11-24T19:00:00Z"},
        })
        assert ha_ev["end"] == ha_ev["start"] + timedelta(hours=1)
        assert ha_ev["import_export"] == "export"

        # 2. Flat dict with updated_at vs updated
        flat_ev = axle.parse_axle_payload({
            "start_time": "2026-11-24T18:00:00Z",
            "end_time": "2026-11-24T19:00:00Z",
            "updated": "2026-11-24T10:00:00Z",
        })
        assert flat_ev["updated_at"] is not None

        # 3. fetch_axle_event falls back to HA sensor when API returns no event and SUPERVISOR_TOKEN is set
        axle.clear_axle_cache()
        monkeypatch.setattr(axle, "SUPERVISOR_TOKEN", "sup_tok")
        now = datetime.now(timezone.utc)
        ha_payload = {
            "state": (now + timedelta(hours=1)).isoformat(),
            "attributes": {"end_time": (now + timedelta(hours=2)).isoformat()},
        }
        with patch("axle.test_axle_connection", return_value=(False, "err", None)), \
             patch("axle.fetch_ha_axle_sensor", return_value=ha_payload) as mock_ha:
            ev = axle.fetch_axle_event(token="tok", force=True)
            assert mock_ha.called
            assert ev is not None

        # 4. When API succeeds with event, HA sensor is NOT called
        axle.clear_axle_cache()
        api_payload = {
            "start_time": (now + timedelta(hours=1)).isoformat(),
            "end_time": (now + timedelta(hours=2)).isoformat(),
        }
        with patch("axle.test_axle_connection", return_value=(True, "ok", api_payload)), \
             patch("axle.fetch_ha_axle_sensor") as mock_ha:
            ev2 = axle.fetch_axle_event(token="tok", force=True)
            assert not mock_ha.called
            assert ev2 is not None

        # 5. force=True bypasses fresh cache
        with patch("axle.test_axle_connection", return_value=(True, "ok", api_payload)) as mock_api:
            axle.fetch_axle_event(token="tok", force=True)
            assert mock_api.called

        # 6. parse_axle_payload list filtering skips invalid/past events and selects upcoming
        past = (now - timedelta(hours=2)).isoformat()
        past_end = (now - timedelta(hours=1)).isoformat()
        fut = (now + timedelta(hours=3)).isoformat()
        fut_end = (now + timedelta(hours=4)).isoformat()
        picked = axle.parse_axle_payload([
            {"start_time": "invalid"},
            {"start_time": past, "end_time": past_end},
            {"start_time": fut, "end_time": fut_end},
        ])
        assert picked is not None
        assert picked["start"].hour == (now + timedelta(hours=3)).hour

