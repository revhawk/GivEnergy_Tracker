"""Contract tests for API data schemas, payload interfaces, and component boundaries."""
import json
import pytest
import responses
from pathlib import Path
from datetime import datetime, timezone

import version
import givtcp
import tariffs
import solar
import solar_openmeteo
import llm


class TestVersionContract:
    def test_version_py_matches_config_yaml(self):
        config_yaml_path = Path(__file__).resolve().parent.parent / "ha-addon" / "config.yaml"
        assert config_yaml_path.exists(), "ha-addon/config.yaml missing"
        with open(config_yaml_path, "r") as f:
            content = f.read()
        
        yaml_ver = None
        for line in content.splitlines():
            if line.startswith("version:"):
                yaml_ver = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
        
        assert yaml_ver is not None, "Could not find version: in config.yaml"
        assert version.__version__ == yaml_ver, f"Version mismatch: version.py={version.__version__} vs config.yaml={yaml_ver}"


class TestGivTCPContract:
    @pytest.mark.asyncio
    @responses.activate
    async def test_set_charge_slot_payload_contract(self):
        for _ in range(10):
            responses.add(
                responses.POST,
                "http://192.0.2.1:6345/setChargeSlot",
                json={"status": "success"},
                status=200,
            )
        responses.add(
            responses.POST,
            "http://192.0.2.1:6345/setChargeTarget",
            json={"status": "success"},
            status=200,
        )
        responses.add(
            responses.POST,
            "http://192.0.2.1:6345/setBatteryMode",
            json={"status": "success"},
            status=200,
        )
        responses.add(
            responses.POST,
            "http://192.0.2.1:6345/enableChargeTarget",
            json={"status": "success"},
            status=200,
        )
        responses.add(
            responses.POST,
            "http://192.0.2.1:6345/enableChargeSchedule",
            json={"status": "success"},
            status=200,
        )

        start = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
        end = datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc)
        
        res = await givtcp.set_inverter_charge_slots(start, end, charge_target=100)
        assert res is True

        # Verify POST payload contract for /setChargeSlot
        slot_calls = [c for c in responses.calls if "/setChargeSlot" in c.request.url]
        assert len(slot_calls) == 10
        payload1 = json.loads(slot_calls[0].request.body)
        assert payload1["start"] == "02:30"
        assert payload1["finish"] == "04:00"
        assert payload1["slot"] == "1"
        assert payload1["chargeToPercent"] == 100

        # Verify POST payload contract for /setBatteryMode
        mode_calls = [c for c in responses.calls if "/setBatteryMode" in c.request.url]
        assert len(mode_calls) >= 1
        mode_payload = json.loads(mode_calls[0].request.body)
        assert mode_payload["mode"] == "Timed Demand"


class TestOctopusTariffContract:
    @responses.activate
    def test_agile_tariff_schema_contract(self):
        mock_response = {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "value_exc_vat": 10.0,
                    "value_inc_vat": 12.0,
                    "valid_from": "2026-09-02T02:00:00Z",
                    "valid_to": "2026-09-02T02:30:00Z",
                    "payment_method": None
                }
            ]
        }
        responses.add(
            responses.GET,
            "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/E-1R-AGILE-24-10-01-E/standard-unit-rates/",
            json=mock_response,
            status=200,
        )

        rates = tariffs.fetch_agile_rates()
        assert len(rates) == 1
        assert "start" in rates[0]
        assert "end" in rates[0]
        assert "price" in rates[0]
        assert rates[0]["price"] == 12.0


class TestSolarForecastContract:
    @responses.activate
    def test_forecast_solar_schema_contract(self):
        mock_response = {
            "result": {
                "watt_hours_period": {
                    "2026-09-02 12:00:00": 1500,
                    "2026-09-02 13:00:00": 2000
                }
            },
            "message": {"code": 0, "type": "success"}
        }
        responses.add(
            responses.GET,
            "https://api.forecast.solar/estimate/52.0/-2.0/35/0/10.0",
            json=mock_response,
            status=200,
        )

        fc = solar.fetch_solar_forecast()
        assert len(fc) == 2
        assert fc[0]["kwh"] == 1.5
        assert fc[1]["kwh"] == 2.0

    @responses.activate
    def test_openmeteo_schema_contract(self):
        mock_response = {
            "latitude": 52.0,
            "longitude": -2.0,
            "hourly": {
                "time": ["2026-09-02T12:00", "2026-09-02T13:00"],
                "global_tilted_irradiance": [500.0, 600.0]
            }
        }
        responses.add(
            responses.GET,
            "https://api.open-meteo.com/v1/forecast?latitude=52.0&longitude=-2.0&hourly=global_tilted_irradiance&tilt=35&azimuth=0&forecast_days=2",
            json=mock_response,
            status=200,
        )

        fc = solar_openmeteo.fetch_openmeteo_solar_forecast()
        assert len(fc) == 2
        # (500 W/m² / 1000) * 10.0 kWp = 5.0 kWh
        assert fc[0]["kwh"] == 5.0
        assert fc[1]["kwh"] == 6.0


class TestLLMVetoSchemaContract:
    def test_veto_response_schema_validation(self):
        json_response = '{"approve": true, "score": 9, "reason": "Pre-charging saves money vs 50p peak."}'
        parsed = json.loads(json_response)
        
        assert "approve" in parsed and isinstance(parsed["approve"], bool)
        assert "score" in parsed and isinstance(parsed["score"], int)
        assert "reason" in parsed and isinstance(parsed["reason"], str)
        assert 1 <= parsed["score"] <= 10


class TestHiveContract:
    @responses.activate
    def test_hive_set_operation_mode_payload_contract(self, monkeypatch):
        import hive
        monkeypatch.setattr(hive, "SUPERVISOR_TOKEN", "dummy_token")
        
        responses.add(
            responses.POST,
            "http://supervisor/core/api/services/water_heater/set_operation_mode",
            json={"result": "ok"},
            status=200,
        )

        res = hive.set_hive_hot_water_mode("off")
        assert res is True
        assert len(responses.calls) == 1
        payload = json.loads(responses.calls[0].request.body)
        assert payload["entity_id"] == "water_heater.hive_hot_water"
        assert payload["operation_mode"] == "off"

    def test_evaluate_morning_shower_safety(self):
        import hive
        # Without sensor: default to False (keep gas on for safety)
        assert hive.evaluate_morning_shower_safety(None) is False
        # Cold tank (< 45C): False (keep gas on)
        assert hive.evaluate_morning_shower_safety(38.0, min_required_c=45.0) is False
        # Warm tank (>= 45C): True (safe to disable gas)
        assert hive.evaluate_morning_shower_safety(52.0, min_required_c=45.0) is True

    def test_should_suppress_gas_hot_water(self):
        import hive
        # Low solar & no pre-charge -> False (keep gas on, no cold shower)
        assert hive.should_suppress_gas_hot_water(solar_today_kwh=5.0, grid_charged_overnight=False) is False
        # High solar (>= 15 kWh) -> True (safe to pause gas)
        assert hive.should_suppress_gas_hot_water(solar_today_kwh=22.0, grid_charged_overnight=False) is True
        # Electric pre-charge -> True (safe to pause gas)
        assert hive.should_suppress_gas_hot_water(solar_today_kwh=0.0, grid_charged_overnight=True) is True

