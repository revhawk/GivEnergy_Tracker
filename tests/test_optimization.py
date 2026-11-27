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
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_optimization_arbitrage_multi_slots(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots
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
    mock_select.return_value = (True, 1, 10, "arbitrage", "clear skies")
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
@patch('optimiser.fetch_live_octoplus_sessions', return_value=[])
@patch('simulation.is_winter_full_charge_enabled', return_value=False)
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_optimization_deficit_non_contiguous_cheapest_slots(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots, mock_winter, mock_octoplus
):
    # Setup 2 cheap slots separated by expensive slots (01:00 @ 11p, 02:00 @ 30p, 03:00 @ 11p)
    slots = [
        _make_slot(1, 0, 11.0),
        _make_slot(2, 0, 30.0),
        _make_slot(3, 0, 11.0),
    ]
    for h in range(4, 15):
        slots.append(_make_slot(h, 0, 25.0))

    mock_telemetry.return_value = {"soc": 35, "pv_power": 0.0, "load_power": 800.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 10.0  # Export threshold = 8.5p, so Agile rates (11p) don't trigger arbitrage
    mock_select.return_value = (True, 1, 10, "ok", "clear skies")
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
@patch('optimiser.save_state')
@patch('optimiser.is_axle_enabled', return_value=True)
@patch('optimiser.fetch_axle_event')
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.load_state')
async def test_light_monitor_detects_new_axle_event_and_triggers_replan(
    mock_load_state, mock_telemetry, mock_fetch_axle, mock_axle_enabled, mock_save_state
):
    now_utc = datetime.now(timezone.utc)
    slot_start = now_utc - timedelta(minutes=10)
    slot_end = now_utc + timedelta(minutes=20)

    mock_telemetry.return_value = {"soc": 75, "load_power": 500.0}
    mock_load_state.return_value = {
        "planned_soc_schedule": [
            {"start": slot_start.isoformat(), "end": slot_end.isoformat(), "soc": 75.0}
        ]
    }

    # New upcoming Axle event in 4 hours
    event_start = now_utc + timedelta(hours=4)
    event_end = now_utc + timedelta(hours=5)
    mock_fetch_axle.return_value = {
        "start": event_start,
        "end": event_end,
        "import_export": "export"
    }

    replan_needed = await optimiser.run_light_monitor()
    assert replan_needed is True
    assert mock_save_state.called
    saved_state = mock_save_state.call_args[0][0]
    assert saved_state["tracked_axle_event_start"] == event_start.isoformat()
    assert saved_state["active_axle_event_end"] == event_end.isoformat()


@pytest.mark.asyncio
@patch('optimiser.save_state')
@patch('optimiser.is_axle_enabled', return_value=True)
@patch('optimiser.fetch_axle_event')
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.load_state')
async def test_light_monitor_entering_axle_precharge_window_triggers_replan(
    mock_load_state, mock_telemetry, mock_fetch_axle, mock_axle_enabled, mock_save_state
):
    now_utc = datetime.now(timezone.utc)
    slot_start = now_utc - timedelta(minutes=10)
    slot_end = now_utc + timedelta(minutes=20)

    # Event starts in 90 minutes (inside 2h precharge window)
    event_start = now_utc + timedelta(minutes=90)
    event_end = now_utc + timedelta(minutes=150)
    mock_fetch_axle.return_value = {
        "start": event_start,
        "end": event_end,
        "import_export": "export"
    }

    mock_telemetry.return_value = {"soc": 60, "load_power": 450.0}
    mock_load_state.return_value = {
        "planned_soc_schedule": [
            {"start": slot_start.isoformat(), "end": slot_end.isoformat(), "soc": 60.0}
        ],
        "tracked_axle_event_start": event_start.isoformat(),
        "active_axle_event_end": event_end.isoformat(),
        # axle_precharge_triggered is NOT set yet
    }

    replan_needed = await optimiser.run_light_monitor()
    assert replan_needed is True
    assert mock_save_state.called
    saved_state = mock_save_state.call_args[0][0]
    assert saved_state["axle_precharge_triggered"] == event_start.isoformat()


@pytest.mark.asyncio
@patch('optimiser.save_state')
@patch('optimiser.set_inverter_discharge_slot')
@patch('optimiser.is_axle_enabled', return_value=True)
@patch('optimiser.fetch_axle_event', return_value=None)
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.load_state')
async def test_light_monitor_axle_event_concluded_resets_discharge_slot(
    mock_load_state, mock_telemetry, mock_fetch_axle, mock_axle_enabled, mock_set_discharge, mock_save_state
):
    now_utc = datetime.now(timezone.utc)
    slot_start = now_utc - timedelta(minutes=10)
    slot_end = now_utc + timedelta(minutes=20)

    mock_telemetry.return_value = {"soc": 30, "load_power": 400.0}
    # Event finished 10 minutes ago
    past_end = now_utc - timedelta(minutes=10)
    past_start = now_utc - timedelta(minutes=70)
    mock_load_state.return_value = {
        "planned_soc_schedule": [
            {"start": slot_start.isoformat(), "end": slot_end.isoformat(), "soc": 30.0}
        ],
        "active_axle_event_end": past_end.isoformat(),
        "tracked_axle_event_start": past_start.isoformat(),
        "axle_precharge_triggered": past_start.isoformat(),
    }

    replan_needed = await optimiser.run_light_monitor()
    assert replan_needed is False
    assert mock_set_discharge.called
    assert mock_set_discharge.call_args[0] == (None, None)
    assert mock_save_state.called
    saved_state = mock_save_state.call_args[0][0]
    assert "active_axle_event_end" not in saved_state
    assert "tracked_axle_event_start" not in saved_state
    assert "axle_precharge_triggered" not in saved_state


@pytest.mark.asyncio
@patch('optimiser.save_state')
@patch('optimiser.set_inverter_discharge_slot')
@patch('optimiser.is_axle_enabled', return_value=True)
@patch('optimiser.fetch_axle_event', return_value=None)
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.load_state')
async def test_light_monitor_cancelled_axle_event_resets_discharge_and_replans(
    mock_load_state, mock_telemetry, mock_fetch_axle, mock_axle_enabled, mock_set_discharge, mock_save_state
):
    now_utc = datetime.now(timezone.utc)
    slot_start = now_utc - timedelta(minutes=10)
    slot_end = now_utc + timedelta(minutes=20)

    mock_telemetry.return_value = {"soc": 70, "load_power": 400.0}
    # Tracked event was scheduled for 2 hours in the future, but fetch_axle_event returned None (cancelled)
    future_start = now_utc + timedelta(hours=2)
    future_end = now_utc + timedelta(hours=3)
    mock_load_state.return_value = {
        "planned_soc_schedule": [
            {"start": slot_start.isoformat(), "end": slot_end.isoformat(), "soc": 70.0}
        ],
        "active_axle_event_end": future_end.isoformat(),
        "tracked_axle_event_start": future_start.isoformat(),
    }

    replan_needed = await optimiser.run_light_monitor()
    assert replan_needed is True
    assert mock_set_discharge.called
    assert mock_set_discharge.call_args[0] == (None, None)
    assert mock_save_state.called
    saved_state = mock_save_state.call_args[0][0]
    assert "active_axle_event_end" not in saved_state
    assert "tracked_axle_event_start" not in saved_state


@pytest.mark.asyncio
@patch('optimiser.save_state')
@patch('optimiser.is_axle_enabled', return_value=True)
@patch('optimiser.fetch_axle_event')
@patch('optimiser.load_state')
async def test_check_axle_events_direct_detects_event_and_triggers_replan(
    mock_load_state, mock_fetch_axle, mock_axle_enabled, mock_save_state
):
    now_utc = datetime.now(timezone.utc)
    mock_load_state.return_value = {}
    event_start = now_utc + timedelta(hours=3)
    event_end = now_utc + timedelta(hours=4)
    mock_fetch_axle.return_value = {
        "start": event_start,
        "end": event_end,
        "import_export": "export"
    }

    result = await optimiser.check_axle_events()
    assert result is True
    assert mock_save_state.called
    saved = mock_save_state.call_args[0][0]
    assert saved["tracked_axle_event_start"] == event_start.isoformat()
    assert saved["active_axle_event_end"] == event_end.isoformat()


@pytest.mark.asyncio
@patch('optimiser.is_axle_enabled', return_value=False)
async def test_check_axle_events_when_disabled_returns_false(mock_axle_enabled):
    result = await optimiser.check_axle_events()
    assert result is False


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
async def test_build_plan_candidates_post_1600_uses_tomorrow_timedelta():
    """Verify build_plan_candidates handles evening/post-16:00 runs where timedelta is needed for tomorrow's dip."""
    from simulation import build_plan_candidates
    from datetime import datetime, timezone, timedelta

    today = datetime.now(timezone.utc).replace(hour=17, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    slots = [
        {"start": today, "end": today + timedelta(minutes=30), "price": 45.0},
        {"start": today + timedelta(hours=1), "end": today + timedelta(hours=1, minutes=30), "price": 48.0},
        {"start": tomorrow.replace(hour=11, minute=0), "end": tomorrow.replace(hour=11, minute=30), "price": 12.0},
        {"start": tomorrow.replace(hour=12, minute=0), "end": tomorrow.replace(hour=12, minute=30), "price": 11.5},
    ]

    candidates = build_plan_candidates(
        day_classification="MEDIUM_SOLAR_PARTIAL_PRECHARGE",
        current_soc=40.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=10.0,
        solar_comparison={"diff_kwh": 1.0},
        export_rate=12.0,
        total_import_kwh=2.0
    )

    assert len(candidates) == 3
    c2 = candidates[1]
    assert c2['id'] == 2
    assert len(c2['slots']) > 0
    assert c2['slots'][0][0].date() == tomorrow.date()


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
    # Guarantees full pre-charge (100% default or 95%) so battery is never stranded at 4%
    assert c1['target_soc'] in (95, 100)
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
    assert kwargs.get('charge_target') in (55, 95, 100)

    last_plan = state._last_plan
    assert 'candidate_options' in last_plan
    assert len(last_plan['candidate_options']) == 3
    assert last_plan['selected_candidate_id'] == 1
    assert "defensive floor" in last_plan['llm_reason']
    assert last_plan['llm_score'] == 9


@pytest.mark.asyncio
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_veto_fallback_low_solar_preserves_safety_plan(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots
):
    """H1 Fix: On deficit/overcast days, LLM veto MUST fallback to Option 1 deterministic safety, NOT 0 charge."""
    slots = [_make_slot(2, 0, 11.0), _make_slot(2, 30, 10.5)]
    for h in range(4, 15):
        slots.append(_make_slot(h, 0, 25.0))
        
    mock_telemetry.return_value = {"soc": 20.0, "pv_power": 0.0, "load_power": 600.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})  # 0 solar forecast
    mock_export.return_value = 10.0
    mock_set_slots.return_value = True

    # LLM vetoes! (approve=False)
    mock_select.return_value = (
        False,  # approve
        1,      # sel_id
        3,      # score
        "I do not recommend charging.",
        "Cloudy"
    )

    await optimiser.run_optimization()

    # Inverter slots MUST be programmed with Option 1 safety plan rather than leaving battery at 0%
    assert mock_set_slots.called
    called_slots, kwargs = mock_set_slots.call_args
    assert called_slots[0] is not None
    assert len(called_slots[0]) > 0


@pytest.mark.asyncio
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_veto_fallback_high_solar_disables_charge(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots
):
    """On high solar days with no deficit, veto falls back to solar-only (Option 3)."""
    slots = [_make_slot(2, 0, 15.0)]
    for h in range(4, 15):
        slots.append(_make_slot(h, 0, 25.0))
        
    mock_telemetry.return_value = {"soc": 80.0, "pv_power": 2500.0, "load_power": 400.0}
    mock_rates.return_value = slots
    # Large solar forecast (15 kWh across daytime)
    solar_fc = [
        {"time": slots[0]['start'] + timedelta(hours=i), "kwh": 3.0}
        for i in range(5)
    ]
    mock_solar.return_value = (solar_fc, {})
    mock_export.return_value = 10.0
    mock_set_slots.return_value = True

    # LLM vetoes candidate
    mock_select.return_value = (False, 1, 4, "Too much solar to charge.", "Sunny")

    await optimiser.run_optimization()

    assert mock_set_slots.called
    called_slots, _ = mock_set_slots.call_args
    assert called_slots[0] is None  # Zero grid charge applied (Option 3)


@pytest.mark.asyncio
@patch('optimiser.save_state')
@patch('optimiser.load_state')
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_planned_soc_schedule_reflects_charging(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots,
    mock_load_state, mock_save_state
):
    """H2 Fix: planned_soc_schedule must reflect applied charging slots to prevent false drift loops."""
    mock_load_state.return_value = {}
    slots = [_make_slot(2, 0, 11.0), _make_slot(2, 30, 10.5)]
    for h in range(4, 12):
        slots.append(_make_slot(h, 0, 25.0))

    mock_telemetry.return_value = {"soc": 20.0, "pv_power": 0.0, "load_power": 400.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 10.0
    mock_select.return_value = (True, 1, 9, "Option 1 precharge", "clear")
    mock_set_slots.return_value = True

    await optimiser.run_optimization()

    assert mock_save_state.called
    saved_state = mock_save_state.call_args[0][0]
    assert 'planned_soc_schedule' in saved_state
    soc_points = [p['soc'] for p in saved_state['planned_soc_schedule']]
    # SoC trajectory must rise from initial 20% due to grid charge blocks
    assert max(soc_points) > 20.0


def test_daytime_replan_filters_pre_peak_slots():
    """H4 Fix: A re-plan at 10:00 AM must select daytime pre-peak slots, not tomorrow morning."""
    from simulation import build_plan_candidates
    now = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    # Generate 24 hours of slots from 10:00 today to 10:00 tomorrow
    slots = []
    for i in range(48):
        s_time = now + timedelta(minutes=30 * i)
        # Cheap dip at 13:00 (12p), expensive peak at 17:00 (35p)
        price = 12.0 if 13 <= s_time.hour < 15 else 30.0
        slots.append({
            'start': s_time,
            'end': s_time + timedelta(minutes=30),
            'price': price
        })

    candidates = build_plan_candidates(
        day_classification="LOW_SOLAR_DEFENSIVE_PRECHARGE",
        current_soc=15.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=1.0,
        solar_comparison={},
        export_rate=12.0,
        total_import_kwh=5.0
    )

    opt1 = candidates[0]
    assert opt1['slots'] is not None and len(opt1['slots']) > 0
    # First slot must be scheduled TODAY before the 16:00 peak, not tomorrow
    first_slot_start = opt1['slots'][0][0]
    assert first_slot_start.date() == now.date()
    assert first_slot_start.hour < 16


@pytest.mark.asyncio
@patch('optimiser.save_daily_stats')
@patch('optimiser.save_state')
@patch('optimiser.load_state')
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_run_energy_optimization_persists_daily_stats(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots,
    mock_load_state, mock_save_state, mock_save_daily_stats
):
    """Verify that every optimization run updates and persists daily stats with timestamps."""
    mock_load_state.return_value = {}
    slots = [_make_slot(2, 0, 11.0), _make_slot(2, 30, 10.5)]
    for h in range(4, 12):
        slots.append(_make_slot(h, 0, 25.0))

    mock_telemetry.return_value = {"soc": 25.0, "pv_power": 0.0, "load_power": 450.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 12.0
    mock_select.return_value = (True, 1, 9, "Option 1 precharge", "clear")
    mock_set_slots.return_value = True

    await optimiser.run_optimization()

    assert mock_save_daily_stats.called
    saved_stats = mock_save_daily_stats.call_args[0][0]
    assert saved_stats['runs'] >= 1
    assert "date" in saved_stats
    assert "timestamp" in saved_stats
    assert "T" in saved_stats["timestamp"]
    assert saved_stats['end_soc'] == 25.0


def test_build_plan_candidates_with_power_up_session():
    """Verify that Octopus Octoplus Power Up sessions produce Candidate 1 targeting 100% SoC."""
    from simulation import build_plan_candidates
    now = datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(48):
        s_time = now + timedelta(minutes=30 * i)
        is_pup = (18 <= s_time.hour < 19)
        price = 0.0 if is_pup else 24.0
        slots.append({
            'start': s_time,
            'end': s_time + timedelta(minutes=30),
            'price': price,
            'is_power_up': is_pup
        })

    candidates = build_plan_candidates(
        day_classification="HIGH_SOLAR_SELF_CONSUMPTION",
        current_soc=40.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=15.0,
        solar_comparison={},
        export_rate=12.0,
        total_import_kwh=0.0
    )

    opt1 = candidates[0]
    assert opt1['strategy'] == "power_up"
    assert opt1['target_soc'] == 100
    assert "Power Up" in opt1['name']
    assert opt1['avg_price'] == 0.0
    assert opt1['estimated_cost_pence'] == 0.0


def test_build_plan_candidates_preserves_raw_tariff_rate_for_power_up():
    """Verify that Octopus Octoplus Power Up sessions evaluate avg_price=0.0 and cost=0.0
    while leaving raw Agile tariff prices intact on the slots.
    """
    from simulation import build_plan_candidates
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(24):
        s_time = now + timedelta(minutes=30 * i)
        is_pup = (14 <= s_time.hour < 15)
        raw_price = 38.5  # Real Agile rate during promotional slot
        slots.append({
            'start': s_time,
            'end': s_time + timedelta(minutes=30),
            'price': raw_price,
            'tariff_price': raw_price,
            'is_power_up': is_pup
        })

    candidates = build_plan_candidates(
        day_classification="LOW_SOLAR_GRID_PRECHARGE",
        current_soc=20.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=3.0,
        solar_comparison={},
        export_rate=12.0,
        total_import_kwh=5.0
    )

    opt1 = candidates[0]
    assert opt1['strategy'] == "power_up"
    assert opt1['avg_price'] == 0.0
    assert opt1['estimated_cost_pence'] == 0.0
    # Crucially verify raw tariff rate was NOT mutated to 0.0
    pup_slots = [s for s in slots if s.get('is_power_up')]
    assert all(s['price'] == 38.5 for s in pup_slots)


def test_evening_overnight_planning_includes_2200_and_2230_slots():
    """Verify that evening planning (>= 16:00) considers late evening slots (e.g. 22:30 at 22.4p)
    as part of overnight pre-charging rather than strictly slots where hour < 7.
    """
    from simulation import build_plan_candidates
    now_local = datetime.now().astimezone().replace(hour=18, minute=0, second=0, microsecond=0)
    now_utc = now_local.astimezone(timezone.utc)
    slots = []
    for i in range(48):
        s_time = now_utc + timedelta(minutes=30 * (i + 1))
        local_s = s_time.astimezone()
        if local_s.hour == 22 and local_s.minute == 30 and local_s.date() == now_local.date():
            price = 22.4
        elif local_s.hour == 2 and local_s.minute == 30:
            price = 27.3
        else:
            price = 32.0
        slots.append({
            'start': s_time,
            'end': s_time + timedelta(minutes=30),
            'price': price,
        })

    candidates = build_plan_candidates(
        day_classification="MEDIUM_SOLAR_PARTIAL_PRECHARGE",
        current_soc=86.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=14.0,
        solar_comparison={},
        export_rate=12.0,
        total_import_kwh=5.37
    )

    opt1 = candidates[0]
    assert opt1['strategy'] == "scheduled"
    assert "Deficit" in opt1['name']
    assert "22:30" in opt1['slots_desc']



@pytest.mark.asyncio
@patch('axle.is_axle_enabled', return_value=True)
@patch('optimiser.save_daily_stats')
@patch('optimiser.save_state')
@patch('optimiser.load_state')
@patch('optimiser.set_inverter_discharge_slot')
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.fetch_axle_event')
async def test_run_energy_optimization_programs_axle_discharge_slot(
    mock_axle, mock_telemetry, mock_rates, mock_solar, mock_export, mock_select,
    mock_set_slots, mock_set_discharge, mock_load_state, mock_save_state, mock_save_daily_stats,
    mock_axle_enabled
):
    mock_load_state.return_value = {}
    now = datetime.now(timezone.utc)
    slots = []
    for i in range(12):
        s_time = now + timedelta(minutes=30 * (i + 1))
        slots.append({
            'start': s_time,
            'end': s_time + timedelta(minutes=30),
            'price': 20.0
        })

    mock_telemetry.return_value = {"soc": 90.0, "pv_power": 500.0, "load_power": 400.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 12.0
    mock_select.return_value = (True, 1, 9, "Option 1 axle flexibility", "clear")
    mock_set_slots.return_value = True
    mock_set_discharge.return_value = True

    # Event starting within 10-minute arming lead window should arm Timed Export immediately
    event_start = now + timedelta(minutes=5)
    event_end = now + timedelta(hours=1, minutes=5)
    mock_axle.return_value = {
        "start": event_start,
        "end": event_end,
        "import_export": "export"
    }

    await optimiser.run_optimization()

    assert mock_set_discharge.called
    d_start, d_end = mock_set_discharge.call_args[0][:2]
    assert d_start == event_start.astimezone()
    assert d_end == event_end.astimezone()


@pytest.mark.asyncio
@patch('axle.is_axle_enabled', return_value=True)
@patch('optimiser.save_daily_stats')
@patch('optimiser.save_state')
@patch('optimiser.load_state')
@patch('optimiser.set_inverter_discharge_slot')
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
@patch('optimiser.fetch_axle_event')
async def test_run_energy_optimization_defers_axle_timed_export_when_far_away(
    mock_axle, mock_telemetry, mock_rates, mock_solar, mock_export, mock_select,
    mock_set_slots, mock_set_discharge, mock_load_state, mock_save_state, mock_save_daily_stats,
    mock_axle_enabled
):
    """Verify Timed Export is deferred (>10m away) so inverter stays in Eco mode for peak & Power Down."""
    mock_load_state.return_value = {}
    now = datetime.now(timezone.utc)
    slots = [
        {'start': now + timedelta(minutes=30 * (i + 1)), 'end': now + timedelta(minutes=30 * (i + 2)), 'price': 20.0}
        for i in range(12)
    ]

    mock_telemetry.return_value = {"soc": 90.0, "pv_power": 500.0, "load_power": 400.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 12.0
    mock_select.return_value = (True, 1, 9, "Option 1 axle flexibility", "clear")
    mock_set_slots.return_value = True
    mock_set_discharge.return_value = True

    event_start = now + timedelta(hours=3)
    event_end = now + timedelta(hours=4)
    mock_axle.return_value = {
        "start": event_start,
        "end": event_end,
        "import_export": "export"
    }

    await optimiser.run_optimization()

    # Must clear/keep Eco mode (None, None) instead of arming Timed Export 3 hours early
    mock_set_discharge.assert_called_with(None, None)
    saved_state = mock_save_state.call_args[0][0]
    assert saved_state["tracked_axle_event_start"] == event_start.isoformat()
    assert "axle_export_armed" not in saved_state


@pytest.mark.asyncio
@patch('optimiser.is_axle_enabled', return_value=True)
@patch('optimiser.set_inverter_discharge_slot')
@patch('optimiser.save_state')
@patch('optimiser.load_state')
@patch('optimiser.fetch_axle_event')
async def test_check_axle_events_arms_timed_export_within_lead_window(
    mock_axle, mock_load_state, mock_save_state, mock_set_discharge, mock_axle_enabled
):
    """Verify the 5-minute Axle poll arms Timed Export automatically at T-10m."""
    now = datetime.now(timezone.utc)
    event_start = now + timedelta(minutes=5)
    event_end = now + timedelta(hours=1, minutes=5)
    mock_axle.return_value = {
        "start": event_start,
        "end": event_end,
        "import_export": "export",
    }
    mock_load_state.return_value = {
        "tracked_axle_event_start": event_start.isoformat(),
        "active_axle_event_end": event_end.isoformat(),
        "axle_precharge_triggered": event_start.isoformat(),
    }
    mock_set_discharge.return_value = True

    await optimiser.check_axle_events(realtime_soc=100)

    assert mock_set_discharge.called
    d_start, d_end = mock_set_discharge.call_args[0][:2]
    assert d_start == event_start.astimezone()
    assert d_end == event_end.astimezone()
    saved_state = mock_save_state.call_args[0][0]
    assert saved_state["axle_export_armed"] == event_start.isoformat()


def test_is_winter_season():
    """Verify winter season detection for Nov-Feb (months 11, 12, 1, 2)."""
    from simulation import is_winter_season
    assert is_winter_season(datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)) is True
    assert is_winter_season(datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc)) is True
    assert is_winter_season(datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)) is True
    assert is_winter_season(datetime(2027, 2, 28, 12, 0, tzinfo=timezone.utc)) is True
    assert is_winter_season(datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)) is False
    assert is_winter_season(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)) is False
    assert is_winter_season(datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)) is False


def test_winter_100_percent_overnight_precharge():
    """Verify that in winter overnight planning, Candidate 1 targets 100% SoC."""
    from simulation import build_plan_candidates
    # Mock overnight slots starting at 02:00 in November
    base_dt = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(8):
        s_time = base_dt + timedelta(minutes=30 * i)
        slots.append({
            'start': s_time,
            'end': s_time + timedelta(minutes=30),
            'price': 15.6 if i < 6 else 25.8,
        })

    candidates = build_plan_candidates(
        day_classification="LOW_SOLAR_GRID_PRECHARGE",
        current_soc=30.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=1.2,
        solar_comparison={},
        export_rate=12.0,
        total_import_kwh=4.5
    )

    opt1 = candidates[0]
    assert opt1['id'] == 1
    assert opt1['target_soc'] == 100
    assert "Winter Full Pre-Charge" in opt1['name']
    assert opt1['strategy'] == "scheduled"
    assert opt1['kwh'] > 0


def test_daytime_replan_capped_to_peak_protection():
    """Verify that daytime re-planning (e.g. 10:00) allows top-up up to 100%
    to cover large evening deficits & Power Down sessions.
    """
    from simulation import build_plan_candidates
    # Daytime slots from 10:00 to 16:00
    base_dt = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(12):
        s_time = base_dt + timedelta(minutes=30 * i)
        slots.append({
            'start': s_time,
            'end': s_time + timedelta(minutes=30),
            'price': 25.8,
        })

    candidates = build_plan_candidates(
        day_classification="LOW_SOLAR_GRID_PRECHARGE",
        current_soc=20.0,
        battery_capacity=9.5,
        upcoming_slots=slots,
        total_solar_kwh=1.0,
        solar_comparison={},
        export_rate=12.0,
        total_import_kwh=8.5  # Large deficit requiring full 100% top-up
    )

    opt1 = candidates[0]
    assert opt1['id'] == 1
    # Target SoC can reach 100% when deficit warrants full peak/Power Down protection
    assert opt1['target_soc'] == 100
    assert "Peak-Protection Daytime Top-Up" in opt1['name']
    assert opt1['strategy'] == "scheduled"


@pytest.mark.asyncio
@patch('optimiser.is_axle_enabled', return_value=True)
@patch('optimiser.trigger_force_export')
@patch('optimiser.set_inverter_discharge_slot')
@patch('optimiser.save_state')
@patch('optimiser.load_state')
@patch('optimiser.fetch_axle_event')
async def test_check_axle_events_triggers_force_export_when_event_live(
    mock_axle, mock_load_state, mock_save_state, mock_set_discharge, mock_force_export, mock_axle_enabled
):
    """Verify check_axle_events triggers /forceExport when the Axle export event is live."""
    now = datetime.now(timezone.utc)
    event_start = now - timedelta(minutes=2)
    event_end = now + timedelta(minutes=58)
    mock_axle.return_value = {
        "start": event_start,
        "end": event_end,
        "import_export": "export",
    }
    mock_load_state.return_value = {
        "tracked_axle_event_start": event_start.isoformat(),
        "active_axle_event_end": event_end.isoformat(),
        "axle_precharge_triggered": event_start.isoformat(),
        "axle_export_armed": event_start.isoformat(),
    }
    mock_force_export.return_value = True

    await optimiser.check_axle_events(realtime_soc=100)

    assert mock_force_export.called
    dur_mins = mock_force_export.call_args[0][0]
    assert 55 <= dur_mins <= 60
    saved_state = mock_save_state.call_args[0][0]
    assert saved_state["axle_force_export_triggered"] == event_start.isoformat()


@pytest.mark.asyncio
async def test_check_octoplus_events_discovers_power_down_triggers_replan():
    """Verify that check_octoplus_events discovers a newly announced Power Down session
    and returns True to trigger an immediate full re-plan.
    """
    from simulation import check_octoplus_events
    now = datetime.now(timezone.utc)
    mock_sessions = [
        {
            "session_type": "power_down",
            "code": "EVENT_DFS_TEST_1",
            "event_id": "dfs_1",
            "start": now + timedelta(hours=2),
            "end": now + timedelta(hours=3),
            "is_joined": True,
            "reward_points": 100
        }
    ]
    mock_state = {}

    with patch('optimiser.clear_octoplus_cache'), \
         patch('optimiser.fetch_live_octoplus_sessions', return_value=mock_sessions), \
         patch('optimiser.load_state', side_effect=lambda: mock_state), \
         patch('optimiser.save_state', side_effect=lambda s: mock_state.update(s)):

        result = await check_octoplus_events()
        assert result is True
        assert 'tracked_power_down_events' in mock_state
        assert len(mock_state['tracked_power_down_events']) == 1

        # Second call with the same session should not re-trigger re-plan
        result_second = await check_octoplus_events()
        assert result_second is False


@pytest.mark.asyncio
async def test_check_octoplus_events_power_up_joined_vs_unjoined():
    """Verify that check_octoplus_events ignores unjoined Happy Hour events,
    but triggers a re-plan when a joined Happy Hour is confirmed.
    """
    from simulation import check_octoplus_events
    now = datetime.now(timezone.utc)
    unjoined_session = [
        {
            "session_type": "power_up",
            "code": "HAPPY_HOUR_1",
            "event_id": "hh_1",
            "start": now + timedelta(hours=1),
            "end": now + timedelta(hours=2),
            "is_joined": False
        }
    ]
    mock_state = {}

    with patch('optimiser.clear_octoplus_cache'), \
         patch('optimiser.fetch_live_octoplus_sessions', return_value=unjoined_session), \
         patch('optimiser.load_state', side_effect=lambda: mock_state), \
         patch('optimiser.save_state', side_effect=lambda s: mock_state.update(s)):

        # Unjoined -> Should NOT trigger re-plan
        assert await check_octoplus_events() is False

    # Now simulate user booked the slot -> is_joined = True
    joined_session = [
        {
            "session_type": "power_up",
            "code": "HAPPY_HOUR_1",
            "event_id": "hh_1",
            "start": now + timedelta(hours=1),
            "end": now + timedelta(hours=2),
            "is_joined": True
        }
    ]
    with patch('optimiser.clear_octoplus_cache'), \
         patch('optimiser.fetch_live_octoplus_sessions', return_value=joined_session), \
         patch('optimiser.load_state', side_effect=lambda: mock_state), \
         patch('optimiser.save_state', side_effect=lambda s: mock_state.update(s)):

        # Joined -> Should trigger re-plan for 100% pre-charge!
        assert await check_octoplus_events() is True
        assert 'tracked_power_up_events' in mock_state
        assert len(mock_state['tracked_power_up_events']) == 1


@pytest.mark.asyncio
async def test_run_light_monitor_triggers_on_new_octoplus_event():
    """Verify that run_light_monitor detects a new Octoplus session and returns True for re-planning."""
    from simulation import run_light_monitor
    with patch('optimiser.get_inverter_telemetry', return_value={"soc": 80, "load_power": 500, "pv_power": 200}), \
         patch('optimiser.load_state', return_value={"planned_soc_schedule": []}), \
         patch('optimiser.check_axle_events', return_value=False), \
         patch('optimiser.check_octoplus_events', return_value=True):
        assert await run_light_monitor() is True


def test_classify_day_composable_tiers():
    """Verify extracted pure classify_day() across negative, high-solar, and winter tiers."""
    from simulation import classify_day
    summer_dt = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    summer_slots = [{"start": summer_dt, "end": summer_dt + timedelta(minutes=30), "price": 20.0}]
    high_imports = [{"slot": summer_slots[0], "solar": 10.0, "load": 1.0}]

    assert classify_day(summer_slots, high_imports, 20.0, 9.5, 25.0, [{"price": -2.0}]) == "NEGATIVE_RATE_OPPORTUNITY"
    assert classify_day(summer_slots, high_imports, 20.0, 9.5, 25.0, []) == "HIGH_SOLAR_SELF_CONSUMPTION"

    winter_dt = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    winter_slots = [{"start": winter_dt, "end": winter_dt + timedelta(minutes=30), "price": 25.8}]
    low_imports = [{"slot": winter_slots[0], "solar": 0.5, "load": 1.0}]
    assert classify_day(winter_slots, low_imports, 20.0, 9.5, 6.0, []) == "LOW_SOLAR_GRID_PRECHARGE"


def test_clear_axle_state_removes_all_tracking_keys():
    """Verify _clear_axle_state removes every Axle tracking key while preserving unrelated state."""
    from simulation import _clear_axle_state
    st = {
        "active_axle_event_end": "2026-11-24T21:00:00+00:00",
        "tracked_axle_event_start": "2026-11-24T20:00:00+00:00",
        "axle_precharge_triggered": "2026-11-24T20:00:00+00:00",
        "axle_export_armed": "2026-11-24T20:00:00+00:00",
        "axle_force_export_triggered": "2026-11-24T20:00:00+00:00",
        "last_plan_date": "2026-11-24",
    }
    _clear_axle_state(st)
    assert st == {"last_plan_date": "2026-11-24"}


def test_flux_full_offpeak_window_always_covered():
    """On Octopus Flux (02:00-05:00 @ 15.6p vs 25.8p day), Option 1 must cover the ENTIRE 02:00-05:00 window
    even when SoC is already high (77% or 100%) so the house runs off 15.6p grid and exits 05:00 at 100%."""
    from simulation import build_plan_candidates

    base = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(48):
        st = base + timedelta(minutes=30 * i)
        h = st.hour
        if 2 <= h < 5:
            price = 15.6
        elif 16 <= h < 19:
            price = 36.0
        else:
            price = 25.8
        slots.append({"start": st, "end": st + timedelta(minutes=30), "price": price, "is_power_down": False, "is_power_up": False})

    for soc in (77.0, 100.0):
        cands = build_plan_candidates(
            day_classification="LOW_SOLAR_GRID_PRECHARGE",
            current_soc=soc,
            battery_capacity=9.52,
            upcoming_slots=slots,
            total_solar_kwh=1.8,
            solar_comparison={},
            export_rate=10.7,
            total_import_kwh=10.0,
        )
        opt1 = next(c for c in cands if c["id"] == 1)
        assert opt1["target_soc"] == 100
        assert len(opt1["slots"]) == 1
        start_dt, end_dt = opt1["slots"][0]
        assert start_dt.astimezone(timezone.utc).hour == 2
        assert start_dt.astimezone(timezone.utc).minute == 0
        assert end_dt.astimezone(timezone.utc).hour == 5
        assert end_dt.astimezone(timezone.utc).minute == 0


@pytest.mark.asyncio
@patch('optimiser.fetch_live_octoplus_sessions', return_value=[])
@patch('simulation.is_winter_season', return_value=True)
@patch('simulation.is_winter_full_charge_enabled', return_value=True)
@patch('optimiser.set_inverter_charge_slots')
@patch('optimiser.chatgpt_select_candidate')
@patch('optimiser.fetch_export_rate')
@patch('optimiser.fetch_parallel_solar_forecasts')
@patch('optimiser.fetch_agile_rates')
@patch('optimiser.get_inverter_telemetry')
async def test_winter_offpeak_guarantee_applies_even_when_option3_selected(
    mock_telemetry, mock_rates, mock_solar, mock_export, mock_select, mock_set_slots, mock_winter, mock_winter_season, mock_octoplus
):
    """In winter on Octopus Flux, even if LLM selects Option 3 (Pure Solar / no charge),
    the full 02:00-05:00 15.6p off-peak window must still be scheduled at 100% SoC."""
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        st = tomorrow + timedelta(minutes=30 * i)
        h = st.hour
        price = 15.6 if 2 <= h < 5 else (36.0 if 16 <= h < 19 else 25.8)
        slots.append({"start": st, "end": st + timedelta(minutes=30), "price": price})

    mock_telemetry.return_value = {"soc": 80, "pv_power": 0.0, "load_power": 400.0}
    mock_rates.return_value = slots
    mock_solar.return_value = ([], {})
    mock_export.return_value = 10.7
    # Force LLM to pick Option 3 (Pure Solar / zero grid draw)
    mock_select.return_value = (True, 3, 9, "solar ok", "clear")
    mock_set_slots.return_value = True

    await optimiser.run_optimization()

    assert mock_set_slots.called
    called_slots = mock_set_slots.call_args[0][0]
    called_target = mock_set_slots.call_args[1]["charge_target"]
    assert called_target == 100
    assert any(
        s.astimezone(timezone.utc).hour == 2 and e.astimezone(timezone.utc).hour == 5
        for s, e in called_slots
    )


def test_flux_peak_export_rate_does_not_trigger_false_arbitrage():
    """At 17:00 during Octopus Flux peak (export_rate=29.18p), Option 1 must NOT use the 17:00
    peak export rate to classify 19:00-16:00 (25.8p) as arbitrage; it must schedule 02:00-05:00."""
    from simulation import build_plan_candidates

    base = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(48):
        st = base + timedelta(minutes=30 * i)
        h = st.hour
        if 2 <= h < 5:
            price, exp = 15.6, 4.54
        elif 16 <= h < 19:
            price, exp = 36.0, 29.18
        else:
            price, exp = 25.8, 10.70
        slots.append({
            "start": st, "end": st + timedelta(minutes=30),
            "price": price, "export_rate": exp,
            "is_power_down": False, "is_power_up": False,
        })

    cands = build_plan_candidates(
        day_classification="LOW_SOLAR_GRID_PRECHARGE",
        current_soc=14.0,
        battery_capacity=9.52,
        upcoming_slots=slots,
        total_solar_kwh=1.3,
        solar_comparison={},
        export_rate=29.18,  # Live 17:00 Flux peak export rate
        total_import_kwh=20.0,
    )
    opt1 = next(c for c in cands if c["id"] == 1)
    assert opt1["strategy"] != "arbitrage"
    assert len(opt1["slots"]) == 1
    s0, e0 = opt1["slots"][0]
    assert s0.astimezone(timezone.utc).hour == 2
    assert e0.astimezone(timezone.utc).hour == 5


@pytest.mark.asyncio
async def test_axle_pre_event_notification_fires_once_within_20m():
    """Axle VPP export must send a persistent + mobile HA alert ~15-20m before start, once per event."""
    import simulation

    now = datetime.now(timezone.utc)
    ev = {
        "start": now + timedelta(minutes=15),
        "end": now + timedelta(minutes=75),
        "import_export": "export",
    }
    saved = {}

    def fake_load():
        return dict(saved)

    def fake_save(d):
        saved.clear()
        saved.update(d)

    with patch("optimiser.is_axle_enabled", return_value=True), \
         patch("optimiser.fetch_axle_event", return_value=ev), \
         patch("optimiser.load_state", side_effect=fake_load), \
         patch("optimiser.save_state", side_effect=fake_save), \
         patch("givtcp.get_effective_export_rate_w", return_value=3600), \
         patch("rewards.send_ha_notification", return_value=True) as mock_notify:
        await simulation.check_axle_events(realtime_soc=100)
        assert mock_notify.call_count == 1
        title, msg = mock_notify.call_args[0]
        assert "Axle VPP Export" in title
        assert "3.6 kW" in msg
        assert mock_notify.call_args[1]["also_mobile"] is True

        # Second 5m tick must NOT re-send the same alert
        await simulation.check_axle_events(realtime_soc=100)
        assert mock_notify.call_count == 1


@pytest.mark.asyncio
async def test_octoplus_pre_event_alert_and_non_registration_power_up():
    """Power Down sends a 15m heads-up alert once; non-registration Power Up (is_joined=None) triggers re-plan."""
    import simulation

    now = datetime.now(timezone.utc)
    sessions = [
        {
            "session_type": "power_down",
            "code": "EVENT_PD_1",
            "start": now + timedelta(minutes=15),
            "end": now + timedelta(minutes=75),
            "reward_points": 136,
        },
        {
            "session_type": "power_up",
            "code": "FREE_ELEC_AUTO",
            "start": now + timedelta(hours=3),
            "end": now + timedelta(hours=4),
            # No is_joined key -> automatic non-registration Free Electricity / Power Up
        },
        {
            "session_type": "power_up",
            "code": "HAPPY_HOUR_UNBOOKED",
            "start": now + timedelta(hours=5),
            "end": now + timedelta(hours=6),
            "is_joined": False,
        },
    ]
    saved = {}

    def fake_load():
        return dict(saved)

    def fake_save(d):
        saved.clear()
        saved.update(d)

    with patch("optimiser.fetch_live_octoplus_sessions", return_value=sessions), \
         patch("optimiser.clear_octoplus_cache"), \
         patch("optimiser.load_state", side_effect=fake_load), \
         patch("optimiser.save_state", side_effect=fake_save), \
         patch("rewards.send_ha_notification", return_value=True) as mock_notify:
        replan = await simulation.check_octoplus_events()
        assert replan is True
        assert mock_notify.call_count == 1
        assert "Power Down" in mock_notify.call_args[0][0]
        assert mock_notify.call_args[1]["also_mobile"] is True
        # Automatic Power Up tracked, unbooked Happy Hour skipped
        assert any("FREE_ELEC_AUTO" in k for k in saved["tracked_power_up_events"])
        assert not any("HAPPY_HOUR_UNBOOKED" in k for k in saved["tracked_power_up_events"])

        # Second 5m tick must not re-alert or re-plan
        replan2 = await simulation.check_octoplus_events()
        assert replan2 is False
        assert mock_notify.call_count == 1
















