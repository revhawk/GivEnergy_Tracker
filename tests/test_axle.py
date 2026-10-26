"""Unit & Contract Tests for Axle Energy Self-Dispatch Module (ADR 0015)."""
import pytest
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
