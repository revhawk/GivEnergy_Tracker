"""Tests for set_inverter_charge_slots (the GivTCP write path).

Verifies the correct HTTP requests are sent for each scenario:
- Same-day window → slot 1 populated, slots 2-10 cleared
- Midnight-spanning window → split across slot 1 (up to 23:59) and slot 2 (from 00:00)
- No window (clear) → all slots cleared and charging disabled
- Charge target percentage honoured
- GivTCP error → graceful fallback (returns True in mock mode)

These tests mock HTTP; nothing hits a real GivTCP.
"""
import json
from datetime import datetime, timezone, timedelta

import pytest
import responses

import optimiser


GIVTCP_URL = "http://192.0.2.1:6345"


def _body(call) -> dict:
    """Extract the JSON body from a captured request."""
    return json.loads(call.request.body)


@pytest.mark.asyncio
class TestSetInverterChargeSlotsGivTCP:
    """All tests here exercise the GivTCP REST path.

    conftest.py sets config.GIVTCP_URL to a fake URL. We mock the responses
    so no real network calls happen.
    """

    @responses.activate
    async def test_same_day_window_populates_slot1_and_clears_slot2_to_10(self):
        # Mock expected endpoints
        for path in ("/setChargeEnable", "/setChargeTarget", "/enableChargeTarget",
                     "/enableChargeSchedule", "/setChargeSlot"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        start = datetime(2026, 7, 4, 2, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 4, 30, tzinfo=timezone.utc)

        ok = await optimiser.set_inverter_charge_slots(start, end, charge_target=90)
        assert ok is True

        # Verify calls fired
        paths = [c.request.url.replace(GIVTCP_URL, "") for c in responses.calls]
        assert "/setChargeEnable" in paths
        assert "/setChargeTarget" in paths
        assert "/enableChargeTarget" in paths
        assert "/enableChargeSchedule" in paths
        assert "/setChargeSlot" in paths

        # Slots must be written BEFORE enable/target (inverter firmware requirement)
        slot_calls = [c for c in responses.calls if c.request.url.endswith("/setChargeSlot")]
        assert len(slot_calls) == 10

        target_idx = next(i for i, c in enumerate(responses.calls) if c.request.url.endswith("/setChargeTarget"))
        slot_indices = [i for i, c in enumerate(responses.calls) if c.request.url.endswith("/setChargeSlot")]
        for idx in slot_indices:
            assert idx < target_idx, "slots must be set before setChargeTarget"

        # Enable should be "enable"
        enable_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeEnable"))
        assert _body(enable_call) == {"state": "enable"}

        schedule_call = next(c for c in responses.calls if c.request.url.endswith("/enableChargeSchedule"))
        assert _body(schedule_call) == {"state": "enable"}

        # Target should be 90
        target_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeTarget"))
        assert _body(target_call) == {"chargeToPercent": 90}

        # Slot 1 should carry the window
        slot1_body = _body(slot_calls[0])
        assert slot1_body["start"] == "02:00"
        assert slot1_body["finish"] == "04:30"
        assert slot1_body["slot"] == "1"
        assert slot1_body["chargeToPercent"] == 90

        # Slot 2 should be cleared to 00:00-00:00 without chargeToPercent
        slot2_body = _body(slot_calls[1])
        assert slot2_body["start"] == "00:00"
        assert slot2_body["finish"] == "00:00"
        assert slot2_body["slot"] == "2"
        assert "chargeToPercent" not in slot2_body

    @responses.activate
    async def test_midnight_spanning_window_splits_across_two_slots(self):
        for path in ("/setChargeEnable", "/setChargeTarget", "/enableChargeTarget",
                     "/enableChargeSchedule", "/setChargeSlot"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        # 23:30 today → 02:00 tomorrow (crosses midnight)
        start = datetime(2026, 7, 4, 23, 30, tzinfo=timezone.utc)
        end = datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc)

        ok = await optimiser.set_inverter_charge_slots(start, end, charge_target=100)
        assert ok is True

        slot_calls = [c for c in responses.calls if c.request.url.endswith("/setChargeSlot")]
        
        body1 = _body(slot_calls[0])
        assert body1["start"] == "23:30"
        assert body1["finish"] == "23:59"
        assert body1["slot"] == "1"

        body2 = _body(slot_calls[1])
        assert body2["start"] == "00:00"
        assert body2["finish"] == "02:00"
        assert body2["slot"] == "2"

    @responses.activate
    async def test_none_window_clears_all_slots_and_disables(self):
        for path in ("/setChargeEnable", "/enableChargeSchedule", "/setChargeSlot"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        ok = await optimiser.set_inverter_charge_slots(None, None)
        assert ok is True

        enable_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeEnable"))
        assert _body(enable_call) == {"state": "disable"}

        schedule_call = next(c for c in responses.calls if c.request.url.endswith("/enableChargeSchedule"))
        assert _body(schedule_call) == {"state": "disable"}

        slot_calls = [c for c in responses.calls if c.request.url.endswith("/setChargeSlot")]
        assert len(slot_calls) == 10
        for i, call in enumerate(slot_calls):
            body = _body(call)
            assert body["start"] == "00:00"
            assert body["finish"] == "00:00"
            assert body["slot"] == str(i + 1)
            assert "chargeToPercent" not in body

    @responses.activate
    async def test_target_percentage_defaults_to_100(self):
        for path in ("/setChargeEnable", "/setChargeTarget", "/enableChargeTarget",
                     "/enableChargeSchedule", "/setChargeSlot"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        start = datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 5, 0, tzinfo=timezone.utc)
        await optimiser.set_inverter_charge_slots(start, end)

        target_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeTarget"))
        assert _body(target_call) == {"chargeToPercent": 100}
        
        slot_calls = [c for c in responses.calls if c.request.url.endswith("/setChargeSlot")]
        assert _body(slot_calls[0])["chargeToPercent"] == 100

    @responses.activate
    async def test_givtcp_error_and_no_modbus_returns_false(self):
        responses.add(responses.POST, f"{GIVTCP_URL}/setChargeSlot", status=500)

        start = datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 5, 0, tzinfo=timezone.utc)
        ok = await optimiser.set_inverter_charge_slots(start, end)
        assert ok is False
