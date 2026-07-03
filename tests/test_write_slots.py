"""Tests for set_inverter_charge_slots (the GivTCP write path).

Verifies the correct HTTP requests are sent for each scenario:
- Same-day window → slot 1 populated, slot 2 cleared
- Midnight-spanning window → split across slot 1 (up to 23:59) and slot 2 (from 00:00)
- No window (clear) → both slots cleared and charging disabled
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
    async def test_same_day_window_populates_slot1_and_clears_slot2(self):
        # Mock all four expected endpoints
        for path in ("/setChargeEnable", "/setChargeTarget",
                     "/setChargeSlot1", "/setChargeSlot2"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        start = datetime(2026, 7, 4, 2, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 4, 30, tzinfo=timezone.utc)

        ok = await optimiser.set_inverter_charge_slots(start, end, charge_target=90)
        assert ok is True

        # Verify all four calls fired
        paths = [c.request.url.replace(GIVTCP_URL, "") for c in responses.calls]
        assert "/setChargeEnable" in paths
        assert "/setChargeTarget" in paths
        assert "/setChargeSlot1" in paths
        assert "/setChargeSlot2" in paths

        # Slots must be written BEFORE enable/target (inverter firmware requirement)
        slot1_idx  = next(i for i, c in enumerate(responses.calls) if c.request.url.endswith("/setChargeSlot1"))
        slot2_idx  = next(i for i, c in enumerate(responses.calls) if c.request.url.endswith("/setChargeSlot2"))
        target_idx = next(i for i, c in enumerate(responses.calls) if c.request.url.endswith("/setChargeTarget"))
        enable_idx = next(i for i, c in enumerate(responses.calls) if c.request.url.endswith("/setChargeEnable"))
        assert slot1_idx < target_idx, "slot1 must be set before setChargeTarget"
        assert slot2_idx < target_idx, "slot2 must be cleared before setChargeTarget"
        assert target_idx < enable_idx, "setChargeTarget must fire before setChargeEnable"

        # Enable should be "enable"
        enable_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeEnable"))
        assert _body(enable_call) == {"state": "enable"}

        # Target should be 90%
        target_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeTarget"))
        assert _body(target_call) == {"chargeToPercent": "90"}

        # Slot 1 should carry the window
        slot1_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeSlot1"))
        body1 = _body(slot1_call)
        assert body1["start"] == "0200"
        assert body1["finish"] == "0430"
        assert body1["chargeToPercent"] == "90"

        # Slot 2 should be cleared to 0000-0000
        slot2_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeSlot2"))
        body2 = _body(slot2_call)
        assert body2["start"] == "0000"
        assert body2["finish"] == "0000"

    @responses.activate
    async def test_midnight_spanning_window_splits_across_two_slots(self):
        for path in ("/setChargeEnable", "/setChargeTarget",
                     "/setChargeSlot1", "/setChargeSlot2"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        # 23:30 today → 02:00 tomorrow (crosses midnight)
        start = datetime(2026, 7, 4, 23, 30, tzinfo=timezone.utc)
        end = datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc)

        ok = await optimiser.set_inverter_charge_slots(start, end, charge_target=100)
        assert ok is True

        slot1_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeSlot1"))
        body1 = _body(slot1_call)
        assert body1["start"] == "2330"
        assert body1["finish"] == "2359"  # capped at end-of-day

        slot2_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeSlot2"))
        body2 = _body(slot2_call)
        assert body2["start"] == "0000"
        assert body2["finish"] == "0200"

    @responses.activate
    async def test_none_window_clears_both_slots_and_disables(self):
        for path in ("/setChargeEnable", "/setChargeSlot1", "/setChargeSlot2"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        ok = await optimiser.set_inverter_charge_slots(None, None)
        assert ok is True

        enable_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeEnable"))
        assert _body(enable_call) == {"state": "disable"}

        # Both slots should be cleared to 0000-0000 with 0% target
        for path in ("/setChargeSlot1", "/setChargeSlot2"):
            call = next(c for c in responses.calls if c.request.url.endswith(path))
            body = _body(call)
            assert body["start"] == "0000"
            assert body["finish"] == "0000"
            assert body["chargeToPercent"] == "0"

    @responses.activate
    async def test_target_percentage_defaults_to_100(self):
        for path in ("/setChargeEnable", "/setChargeTarget",
                     "/setChargeSlot1", "/setChargeSlot2"):
            responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})

        start = datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 5, 0, tzinfo=timezone.utc)
        # Note: no charge_target passed — should default to 100
        await optimiser.set_inverter_charge_slots(start, end)

        target_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeTarget"))
        assert _body(target_call) == {"chargeToPercent": "100"}
        slot1_call = next(c for c in responses.calls if c.request.url.endswith("/setChargeSlot1"))
        assert _body(slot1_call)["chargeToPercent"] == "100"

    @responses.activate
    async def test_givtcp_error_and_no_modbus_returns_false(self):
        """When GivTCP fails and the Modbus package isn't installed, the
        function must return False (not silently pretend the write succeeded).
        This prevents silent bad decisions when the write path is broken.
        """
        responses.add(responses.POST, f"{GIVTCP_URL}/setChargeEnable", status=500)

        start = datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 5, 0, tzinfo=timezone.utc)
        ok = await optimiser.set_inverter_charge_slots(start, end)

        # HAS_MODBUS is False in test env → previously this silently returned True (mock);
        # now it must return False so the caller can detect the failure.
        assert ok is False
