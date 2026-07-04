import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import optimiser
import config

# Mock slot rates
def _make_slot(hour, minute, price):
    start = datetime(2026, 7, 4, hour, minute, tzinfo=timezone.utc)
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
@patch('optimiser.fetch_solar_forecast')
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
    mock_solar.return_value = []
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

