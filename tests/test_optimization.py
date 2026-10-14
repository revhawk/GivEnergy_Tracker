import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import optimiser
import config

# Mock slot rates
def _make_slot(hour, minute, price):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return {
        'start': start,
        'end': start + timedelta(minutes=30),
        'price': price
    }

@pytest.mark.asyncio
async def test_get_inverter_telemetry_success():
    with patch('requests.get') as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "Power": {
                "Power": {
                    "SOC": 85,
                    "PV_Power": 1500,
                    "Load_Power": 650
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        telemetry = await optimiser.get_inverter_telemetry()
        assert telemetry is not None
        assert telemetry['soc'] == 85
        assert telemetry['pv_power'] == 1500.0
        assert telemetry['load_power'] == 650.0

@pytest.mark.asyncio
async def test_get_inverter_telemetry_missing():
    with patch('requests.get') as mock_get:
        mock_get.side_effect = Exception("network error")
        telemetry = await optimiser.get_inverter_telemetry()
        assert telemetry is None

@pytest.mark.asyncio
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_veto_plan')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_optimization_arbitrage_multi_slots(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_veto, mock_set_slots
):
    # Setup: 3 cheap rate blocks, 2 contiguous, 1 separate
    # 02:00-02:30 -> 8p, 02:30-03:00 -> 7p (should merge to 02:00-03:00)
    # 04:00-04:30 -> 5p (separate block)
    slots = [
        _make_slot(2, 0, 8.0),
        _make_slot(2, 30, 7.0),
        _make_slot(4, 0, 5.0)
    ]
    # Rest of rates are expensive
    for h in range(5, 15):
        slots.append(_make_slot(h, 0, 15.0))
        
    mock_telemetry.return_value = {"soc": 50, "pv_power": 0.0, "load_power": 400.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 12.0 # Arbitrage threshold will be 12.0 - 1.5 = 10.5p
    mock_veto.return_value = (True, 10, "arbitrage")
    mock_set_slots.return_value = True

    await optimiser.run_optimization()

    # Verify set_inverter_charge_slots was called with the two merged blocks
    assert mock_set_slots.called
    called_slots = mock_set_slots.call_args[0][0]
    assert len(called_slots) == 2
    
    # Block 1: 02:00 to 03:00 UTC
    assert called_slots[0][0].astimezone(timezone.utc).hour == 2
    assert called_slots[0][0].astimezone(timezone.utc).minute == 0
    assert called_slots[0][1].astimezone(timezone.utc).hour == 3
    assert called_slots[0][1].astimezone(timezone.utc).minute == 0
    
    # Block 2: 04:00 to 04:30 UTC
    assert called_slots[1][0].astimezone(timezone.utc).hour == 4
    assert called_slots[1][0].astimezone(timezone.utc).minute == 0
    assert called_slots[1][1].astimezone(timezone.utc).hour == 4
    assert called_slots[1][1].astimezone(timezone.utc).minute == 30


@pytest.mark.asyncio
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_veto_plan')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_optimization_deficit_non_contiguous_cheapest_slots(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_veto, mock_set_slots
):
    # Setup 2 cheap slots separated by expensive slots (01:00 @ 11p, 02:00 @ 30p, 03:00 @ 11p)
    slots = [
        _make_slot(1, 0, 11.0),
        _make_slot(2, 0, 30.0),
        _make_slot(3, 0, 11.0),
    ]
    for h in range(4, 15):
        slots.append(_make_slot(h, 0, 25.0))

    mock_telemetry.return_value = {"soc": 55, "pv_power": 0.0, "load_power": 800.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 10.0  # Export threshold = 8.5p, so Agile rates (11p) don't trigger arbitrage
    mock_veto.return_value = (True, 10, "ok")
    mock_set_slots.return_value = True

    await optimiser.run_optimization()

    assert mock_set_slots.called
    called_slots = mock_set_slots.call_args[0][0]
    # Should pick non-contiguous slots (01:00 and 03:00)
    assert len(called_slots) == 2
    assert called_slots[0][0].astimezone(timezone.utc).hour == 1


@pytest.mark.asyncio
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.load_state')
async def test_light_monitor_soc_drift_triggers_replan(mock_load_state, mock_telemetry):
    now_utc = datetime.now(timezone.utc)
    slot_start = now_utc - timedelta(minutes=10)
    slot_end = now_utc + timedelta(minutes=20)

    # Realtime SoC is 30%, planned SoC was 80% (drift = 50% > threshold 15%)
    mock_telemetry.return_value = {"soc": 30, "load_power": 500.0}
    mock_load_state.return_value = {
        "planned_soc_schedule": [
            {"start": slot_start.isoformat(), "end": slot_end.isoformat(), "soc": 80.0}
        ]
    }

    replan_needed = await optimiser.run_light_monitor()
    assert replan_needed is True


@pytest.mark.asyncio
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.load_state')
async def test_light_monitor_soc_drift_within_threshold_no_replan(mock_load_state, mock_telemetry):
    now_utc = datetime.now(timezone.utc)
    slot_start = now_utc - timedelta(minutes=10)
    slot_end = now_utc + timedelta(minutes=20)

    # Realtime SoC is 75%, planned SoC was 80% (drift = 5% <= threshold 15%)
    mock_telemetry.return_value = {"soc": 75, "load_power": 500.0}
    mock_load_state.return_value = {
        "planned_soc_schedule": [
            {"start": slot_start.isoformat(), "end": slot_end.isoformat(), "soc": 80.0}
        ]
    }

    replan_needed = await optimiser.run_light_monitor()
    assert replan_needed is False


@pytest.mark.asyncio
async def test_day_classification_contract_and_history_logging(tmp_path):
    from contracts import DayClassification, LLMPlannerRecommendation
    import state

    rec = LLMPlannerRecommendation(
        approve=True,
        score=9,
        recommended_action="no_charge",
        day_classification=DayClassification.HIGH_SOLAR_SELF_CONSUMPTION,
        weather_risk_commentary="Solar surplus exceeds battery space. Free solar charging approved.",
        reasoning="High solar generation forecast."
    )

    assert rec.day_classification == DayClassification.HIGH_SOLAR_SELF_CONSUMPTION
    assert "Free solar" in rec.weather_risk_commentary

    stats = state.init_daily_stats("2026-09-03", 50.0)
    updated = state.update_daily_stats(stats, {
        "soc": 50.0,
        "day_classification": rec.day_classification,
        "weather_risk_commentary": rec.weather_risk_commentary
    })

    assert updated["day_classification"] == "HIGH_SOLAR_SELF_CONSUMPTION"
    assert "Free solar" in updated["weather_risk_commentary"]

    with patch.object(state, 'HISTORY_DIR', str(tmp_path)):
        path = state.archive_daily_stats_history(updated, "2026-09-03")
        assert path is not None
        assert "2026-09-03" in path


@pytest.mark.asyncio
async def test_build_plan_candidates_medium_solar_autumn_headroom():
    from simulation import build_plan_candidates
    # Mock upcoming slots: 02:00 @ 11p, 11:00 @ 14p, 17:00 @ 35p
    slots = [
        _make_slot(2, 0, 11.0),
        _make_slot(2, 30, 10.5),
        _make_slot(11, 0, 14.0),
        _make_slot(11, 30, 13.5),
        _make_slot(17, 0, 35.0),
    ]
    # Current battery at 20% on a Medium Solar autumn day (9.5 kWh capacity)
    candidates = build_plan_candidates(
        day_classification="MEDIUM_SOLAR_PARTIAL_PRECHARGE",
        current_soc=20.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=8.0,
        solar_comparison={'diff_kwh': 1.5},
        export_rate=12.0,
        total_import_kwh=0.0
    )

    assert len(candidates) == 3
    # Candidate 1: Overnight defensive floor to 55%
    c1 = candidates[0]
    assert c1['id'] == 1
    assert c1['target_soc'] == 55
    # (55 - 20)% of 9.5 kWh = 3.325 kWh (~3.32 kWh)
    assert 3.2 <= c1['kwh'] <= 3.4
    # Headroom left for solar surges: 45% of 9.5 kWh = 4.28 kWh
    assert 4.2 <= c1['headroom_kwh'] <= 4.35
    assert len(c1['slots']) > 0

    # Candidate 2: Midday dip top-up (target 65%)
    c2 = candidates[1]
    assert c2['id'] == 2
    assert c2['target_soc'] == 65
    assert len(c2['slots']) > 0

    # Candidate 3: Solar self-consumption
    c3 = candidates[2]
    assert c3['id'] == 3
    assert c3['kwh'] == 0.0
    assert len(c3['slots']) == 0


@pytest.mark.asyncio
async def test_build_plan_candidates_low_solar_overcast_safety():
    from simulation import build_plan_candidates
    slots = [_make_slot(2, 0, 10.0), _make_slot(3, 0, 10.5)]
    # Current battery at 4% on an overcast winter/autumn day
    candidates = build_plan_candidates(
        day_classification="LOW_SOLAR_GRID_PRECHARGE",
        current_soc=4.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=1.5,
        solar_comparison={'diff_kwh': 0.2},
        export_rate=12.0,
        total_import_kwh=0.0
    )

    c1 = candidates[0]
    # Guarantees full pre-charge (95%) so battery is never stranded at 4%
    assert c1['target_soc'] == 95
    assert c1['kwh'] > 8.0


@pytest.mark.asyncio
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_candidate_selection_execution_and_nas_logging(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots
):
    import state
    slots = [_make_slot(2, 0, 11.0), _make_slot(2, 30, 10.5), _make_slot(11, 0, 14.0)]
    mock_telemetry.return_value = {"soc": 20.0, "pv_power": 0.0, "load_power": 400.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 12.0
    mock_set_slots.return_value = True

    # Mock LLM selecting Candidate 1 (Overnight defensive floor)
    mock_select.return_value = (
        True,  # approve
        1,     # selected_candidate_id
        9,     # score
        "Option 1 secures 55% defensive floor before morning, leaving 45% headroom for solar.",
        "Open-Meteo confirms morning cloud cover risk."
    )

    await optimiser.run_optimization()

    assert mock_set_slots.called
    # Check that charge target was passed to set_inverter_charge_slots
    _, kwargs = mock_set_slots.call_args
    assert kwargs.get('charge_target') in (55, 95)

    last_plan = state._last_plan
    assert 'candidate_options' in last_plan
    assert len(last_plan['candidate_options']) == 3
    assert last_plan['selected_candidate_id'] == 1
    assert "defensive floor" in last_plan['llm_reason']
    assert last_plan['llm_score'] == 9





