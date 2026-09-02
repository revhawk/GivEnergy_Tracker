"""Unit test suite for appliance.py (Washing Machine Telemetry & Dashboard Module)."""

import pytest
from datetime import datetime, timezone

import appliance
import contracts


def test_detect_wash_cycle_state():
    assert appliance.detect_wash_cycle_state(0.0) == "idle"
    assert appliance.detect_wash_cycle_state(5.0) == "idle"
    assert appliance.detect_wash_cycle_state(50.0) == "washing"
    assert appliance.detect_wash_cycle_state(450.0) == "spinning"
    assert appliance.detect_wash_cycle_state(1800.0) == "heating"


def test_calculate_cycle_cost():
    # 1.2 kWh at 25.0p/kWh = 30.0p
    cost = appliance.calculate_cycle_cost(1.2, 25.0)
    assert cost == 30.0

    # 0.8 kWh at 15.5p/kWh = 12.4p
    cost_cheap = appliance.calculate_cycle_cost(0.8, 15.5)
    assert cost_cheap == 12.4


def test_recommend_cheap_wash_slots():
    slots = [
        {'start': datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc), 'end': datetime(2026, 9, 2, 12, 30, tzinfo=timezone.utc), 'price': 30.0},
        {'start': datetime(2026, 9, 2, 12, 30, tzinfo=timezone.utc), 'end': datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc), 'price': 25.0},
        {'start': datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc), 'end': datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc), 'price': 15.0},
        {'start': datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc), 'end': datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc), 'price': 12.0},
        {'start': datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc), 'end': datetime(2026, 9, 2, 14, 30, tzinfo=timezone.utc), 'price': 10.0},
        {'start': datetime(2026, 9, 2, 14, 30, tzinfo=timezone.utc), 'end': datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc), 'price': 35.0},
    ]

    recs = appliance.recommend_cheap_wash_slots(slots, cycle_duration_hours=1.5)
    assert len(recs) == 3
    # Cheapest 3 contiguous slots: 13:00 (15.0), 13:30 (12.0), 14:00 (10.0) -> avg 12.33p
    assert recs[0]['avg_price'] == pytest.approx(12.33, rel=1e-2)


def test_washing_machine_telemetry_pydantic_contract():
    telemetry = contracts.WashingMachineTelemetry(
        state="washing",
        current_power_w=450.0,
        cycles_today=2,
        total_kwh_today=2.4,
        estimated_cost_today_p=38.5
    )
    assert telemetry.state == "washing"
    assert telemetry.current_power_w == 450.0
    assert telemetry.cycles_today == 2


def test_generate_appliance_dashboard_summary():
    summary = appliance.generate_appliance_dashboard_summary()
    assert "WASHING MACHINE USAGE DASHBOARD" in summary
    assert "Cycles Completed Today" in summary
