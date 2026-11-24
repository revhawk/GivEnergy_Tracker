"""End-to-End Guardrail Test Suite for GivEnergy Tariff Optimiser.

Exercises real multi-module flows (simulation + planner + givtcp + octoplus + state)
against temporary state files and mocked HTTP endpoints (zero live network calls)
to permanently guard against production regressions:
  1. Power Down + Axle VPP same-day lifecycle (Eco -> T-10m Timed Export -> T=0 /forceExport -> Eco restore)
  2. Unbooked vs. booked Octopus Happy Hour confirmation gate
  3. Intraday Power Down auto-join + 100% daytime top-up capability
  4. Winter overnight pre-charge + planned SoC trajectory drift stability
  5. Fail-safe resilience when OpenAI fails or negative rates override LLM vetoes
"""
import json
import pytest
import responses
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import optimiser
import givtcp
import octoplus
import simulation
import state as state_mod

GIVTCP_URL = "http://192.0.2.1:6345"


def _mock_givtcp_all(max_bat_rate_w=3600, soc=95):
    """Register standard GivTCP v3 GET/POST endpoints on responses."""
    responses.add(
        responses.GET,
        f"{GIVTCP_URL}/getCache",
        json={
            "Power": {"Power": {"PV_Power": 200, "Load_Power": 500, "SOC": soc}},
            "Inverter": {
                "Invertor_Type": "Hybrid_gen3",
                "Invertor_Max_Bat_Rate": max_bat_rate_w,
                "Invertor_Max_Inv_Rate": 5000,
                "Battery_Capacity_kWh": 9.52,
            },
        },
    )
    for path in (
        "/setChargeSlot", "/setChargeTarget", "/setBatteryMode",
        "/enableChargeTarget", "/enableChargeSchedule",
        "/setDischargeSlot", "/setDischargeSlot1", "/setDischargeTarget",
        "/setDischargeRate", "/setEcoMode", "/enableDischargeTarget",
        "/enableDischargeSchedule", "/forceExport",
    ):
        responses.add(responses.POST, f"{GIVTCP_URL}{path}", json={"result": "ok"})


def _make_flux_slots(start_utc, count=48):
    """Build 48 realistic Octopus Flux half-hourly slots starting from start_utc."""
    slots = []
    for i in range(count):
        s = start_utc + timedelta(minutes=30 * i)
        e = s + timedelta(minutes=30)
        h = s.hour
        if 2 <= h < 5:
            price = 15.6
        elif 16 <= h < 19:
            price = 36.0
        else:
            price = 25.8
        slots.append({"start": s, "end": e, "price": price})
    return slots


def _post_bodies(suffix):
    return [
        json.loads(c.request.body.decode())
        for c in responses.calls
        if c.request.method == "POST" and c.request.url.endswith(suffix)
    ]


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect state.py persistence to a clean temporary directory."""
    state_file = tmp_path / "givenergy_state.json"
    stats_file = tmp_path / "givenergy_daily_stats.json"
    history_dir = tmp_path / "history"
    monkeypatch.setattr(state_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(state_mod, "STATS_FILE", str(stats_file))
    monkeypatch.setattr(state_mod, "HISTORY_DIR", str(history_dir))
    givtcp._INVERTER_CAPABILITIES_CACHE = None
    return state_file


class TestE2EGuardrails:
    """Full end-to-end guardrail scenarios across modules."""

    @pytest.mark.asyncio
    @responses.activate
    async def test_e2e_axle_lifecycle_eco_then_jit_arm_then_force_export_then_restore(
        self, isolated_state, monkeypatch
    ):
        """Guardrail 1: Axle export >10m away stays in Eco mode; T-10m disables Eco & arms
        Timed Export at 3600W; T=0 fires /forceExport; post-event restores Eco + 3600W.
        """
        _mock_givtcp_all(max_bat_rate_w=3600, soc=100)
        monkeypatch.setenv("AXLE_EXPORT_POWER_W", "0")
        monkeypatch.setattr(optimiser, "is_axle_enabled", lambda: True)
        monkeypatch.setattr(simulation.axle, "is_axle_enabled", lambda: True)

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slots = _make_flux_slots(now, 48)
        far_start = now + timedelta(hours=3)
        far_end = far_start + timedelta(hours=1)
        far_event = {"start": far_start, "end": far_end, "import_export": "export"}

        # Phase 1: T - 3h -> run_optimization() MUST keep inverter in Eco mode (NOT Timed Export)
        with patch("optimiser.fetch_agile_rates", return_value=slots), \
             patch("optimiser.fetch_parallel_solar_forecasts", return_value=([], {})), \
             patch("optimiser.fetch_export_rate", return_value=10.7), \
             patch("optimiser.fetch_axle_event", return_value=far_event), \
             patch("optimiser.fetch_live_octoplus_sessions", return_value=[]), \
             patch("optimiser.chatgpt_select_candidate", return_value=(True, 1, 9, "Axle precharge", "clear")):
            await optimiser.run_optimization()

        modes_phase1 = [b["mode"] for b in _post_bodies("/setBatteryMode")]
        assert "Timed Export" not in modes_phase1
        assert modes_phase1[-1] == "Eco"
        assert _post_bodies("/forceExport") == []
        persisted = state_mod.load_state()
        assert persisted["tracked_axle_event_start"] == far_start.isoformat()
        assert "axle_export_armed" not in persisted

        # Phase 2: T - 5m -> check_axle_events() arms Timed Export + disables Eco at 3600W
        responses.calls.reset()
        near_start = datetime.now(timezone.utc) + timedelta(minutes=5)
        near_end = near_start + timedelta(hours=1)
        near_event = {"start": near_start, "end": near_end, "import_export": "export"}
        persisted["tracked_axle_event_start"] = near_start.isoformat()
        persisted["active_axle_event_end"] = near_end.isoformat()
        persisted["axle_precharge_triggered"] = near_start.isoformat()
        state_mod.save_state(persisted)

        with patch("optimiser.fetch_axle_event", return_value=near_event):
            await optimiser.check_axle_events(realtime_soc=100)

        assert {"state": "disable"} in _post_bodies("/setEcoMode")
        assert {"mode": "Timed Export"} in _post_bodies("/setBatteryMode")
        assert {"dischargeRate": 3600} in _post_bodies("/setDischargeRate")
        assert _post_bodies("/forceExport") == []  # Not live yet!
        assert state_mod.load_state()["axle_export_armed"] == near_start.isoformat()

        # Phase 3: T + 2m (LIVE) -> check_axle_events() triggers /forceExport at 3600W
        responses.calls.reset()
        live_start = datetime.now(timezone.utc) - timedelta(minutes=2)
        live_end = live_start + timedelta(hours=1)
        live_event = {"start": live_start, "end": live_end, "import_export": "export"}
        persisted = state_mod.load_state()
        persisted["tracked_axle_event_start"] = live_start.isoformat()
        persisted["active_axle_event_end"] = live_end.isoformat()
        persisted["axle_precharge_triggered"] = live_start.isoformat()
        persisted["axle_export_armed"] = live_start.isoformat()
        state_mod.save_state(persisted)

        with patch("optimiser.fetch_axle_event", return_value=live_event):
            await optimiser.check_axle_events(realtime_soc=100)

        fe_calls = _post_bodies("/forceExport")
        assert len(fe_calls) == 1
        assert 55 <= int(fe_calls[0]["duration"]) <= 60
        assert {"dischargeRate": 3600} in _post_bodies("/setDischargeRate")
        assert state_mod.load_state()["axle_force_export_triggered"] == live_start.isoformat()

        # Phase 4: Event concluded -> check_axle_events() restores Eco + Eco_Mode=enable + 3600W
        responses.calls.reset()
        past_end = datetime.now(timezone.utc) - timedelta(minutes=2)
        persisted = state_mod.load_state()
        persisted["active_axle_event_end"] = past_end.isoformat()
        state_mod.save_state(persisted)

        with patch("optimiser.fetch_axle_event", return_value=None):
            await optimiser.check_axle_events(realtime_soc=60)

        assert {"state": "enable"} in _post_bodies("/setEcoMode")
        assert {"mode": "Eco"} in _post_bodies("/setBatteryMode")
        assert {"dischargeRate": 3600} in _post_bodies("/setDischargeRate")
        final_state = state_mod.load_state()
        assert "active_axle_event_end" not in final_state
        assert "axle_export_armed" not in final_state
        assert "axle_force_export_triggered" not in final_state

    @pytest.mark.asyncio
    async def test_e2e_unbooked_vs_booked_happy_hour_guard(self, isolated_state):
        """Guardrail 2: Unbooked promotional Happy Hours never trigger re-plan or free charging;
        booking 1 slot immediately triggers re-plan for only that booked hour.
        """
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slot_a_start = now + timedelta(hours=2)
        slot_a_end = slot_a_start + timedelta(hours=1)
        slot_b_start = now + timedelta(hours=3)
        slot_b_end = slot_b_start + timedelta(hours=1)

        unbooked = [
            {"session_type": "power_up", "code": "HH_1", "start": slot_a_start, "end": slot_a_end, "is_joined": False},
            {"session_type": "power_up", "code": "HH_2", "start": slot_b_start, "end": slot_b_end, "is_joined": False},
        ]

        with patch("optimiser.fetch_live_octoplus_sessions", return_value=unbooked), \
             patch("optimiser.clear_octoplus_cache"):
            triggered = await optimiser.check_octoplus_events()
        assert triggered is False
        assert octoplus.is_power_up_slot_dt(slot_a_start, slot_a_end, live_sessions=unbooked) is False
        assert octoplus.is_power_up_slot_dt(slot_b_start, slot_b_end, live_sessions=unbooked) is False

        # Now user books slot_a only
        booked = [
            {"session_type": "power_up", "code": "HH_1", "start": slot_a_start, "end": slot_a_end, "is_joined": True},
            {"session_type": "power_up", "code": "HH_2", "start": slot_b_start, "end": slot_b_end, "is_joined": False},
        ]
        with patch("optimiser.fetch_live_octoplus_sessions", return_value=booked), \
             patch("optimiser.clear_octoplus_cache"):
            triggered_booked = await optimiser.check_octoplus_events()
        assert triggered_booked is True
        assert octoplus.is_power_up_slot_dt(slot_a_start, slot_a_end, live_sessions=booked) is True
        assert octoplus.is_power_up_slot_dt(slot_b_start, slot_b_end, live_sessions=booked) is False

    @pytest.mark.asyncio
    async def test_e2e_intraday_power_down_auto_join_and_full_daytime_topup(self, isolated_state, monkeypatch):
        """Guardrail 3: Unjoined intraday Power Down auto-enrolls via API, triggers re-plan,
        and daytime re-plan allows charging up to 100% (no 55% cap stranding).
        """
        monkeypatch.setenv("AUTO_JOIN_SAVING_SESSIONS", "true")
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        pd_start = now + timedelta(hours=4)
        pd_end = pd_start + timedelta(hours=1)

        unjoined_pd = [{
            "session_type": "power_down",
            "code": "EVENT_PD_E2E",
            "event_id": "pd_e2e_1",
            "start": pd_start,
            "end": pd_end,
            "is_joined": False,
            "reward_points": 104,
        }]

        with patch("optimiser.fetch_live_octoplus_sessions", return_value=unjoined_pd), \
             patch("optimiser.clear_octoplus_cache"):
            replan = await optimiser.check_octoplus_events()
            # Second poll with same session must NOT re-trigger a replan loop
            replan_again = await optimiser.check_octoplus_events()

        assert replan is True
        assert replan_again is False
        assert len(state_mod.load_state().get("tracked_power_down_events", [])) == 1

        # Verify daytime candidate allows >55% (up to 100%) to survive peak + Power Down
        daytime_base = datetime(2026, 11, 24, 12, 30, tzinfo=timezone.utc)
        day_slots = _make_flux_slots(daytime_base, 24)
        cands = simulation.build_plan_candidates(
            day_classification="LOW_SOLAR_GRID_PRECHARGE",
            current_soc=30.0,
            battery_capacity=9.5,
            upcoming_slots=day_slots,
            total_solar_kwh=1.5,
            solar_comparison={},
            export_rate=10.7,
            total_import_kwh=8.0,
        )
        assert cands[0]["target_soc"] == 100

    @pytest.mark.asyncio
    @responses.activate
    async def test_e2e_winter_overnight_precharge_and_zero_false_drift(self, isolated_state, monkeypatch):
        """Guardrail 4: Winter Flux planning schedules 02:00 off-peak 100% fill, persists
        planned_soc_schedule, and matching live telemetry does not trigger false drift replans.
        """
        _mock_givtcp_all(max_bat_rate_w=3600, soc=20)
        monkeypatch.setattr(optimiser, "is_axle_enabled", lambda: False)
        monkeypatch.setattr(simulation.axle, "is_axle_enabled", lambda: False)

        # Build 48 Flux slots starting now (includes upcoming 02:00-05:00 at 15.6p)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slots = _make_flux_slots(now, 48)

        with patch("optimiser.fetch_agile_rates", return_value=slots), \
             patch("optimiser.fetch_parallel_solar_forecasts", return_value=([], {})), \
             patch("optimiser.fetch_export_rate", return_value=10.7), \
             patch("optimiser.fetch_live_octoplus_sessions", return_value=[]), \
             patch("optimiser.chatgpt_select_candidate", return_value=(True, 1, 9, "Winter full charge", "low solar")):
            await optimiser.run_optimization()

        # Charge slots programmed and planned_soc_schedule persisted
        charge_targets = _post_bodies("/setChargeTarget")
        assert any(b.get("chargeToPercent") == 100 for b in charge_targets)
        saved = state_mod.load_state()
        schedule = saved.get("planned_soc_schedule", [])
        assert len(schedule) >= 12

        # Run light monitor with live SoC matching planned SoC -> zero false drift replan
        planned_now = schedule[0]["soc"]
        with patch("optimiser.get_inverter_telemetry", return_value={"soc": planned_now, "pv_power": 0.0, "load_power": 300.0}), \
             patch("optimiser.check_axle_events", new_callable=AsyncMock, return_value=False), \
             patch("optimiser.check_octoplus_events", new_callable=AsyncMock, return_value=False):
            replan_needed = await optimiser.run_light_monitor()
        assert replan_needed is False

    @pytest.mark.asyncio
    @responses.activate
    async def test_e2e_negative_rate_and_llm_offline_fails_open(self, isolated_state, monkeypatch):
        """Guardrail 5: If OpenAI fails/is offline during a negative-rate or deficit window,
        the engine fails open deterministically and still programs GivTCP charge slots.
        """
        _mock_givtcp_all(max_bat_rate_w=3600, soc=25)
        monkeypatch.setattr(optimiser, "is_axle_enabled", lambda: False)
        monkeypatch.setattr(simulation.axle, "is_axle_enabled", lambda: False)

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slots = [
            {
                "start": now + timedelta(minutes=30 * i),
                "end": now + timedelta(minutes=30 * (i + 1)),
                "price": -5.0 if 2 <= i < 6 else 25.0,
            }
            for i in range(48)
        ]

        # Simulate LLM returning fail-open fallback (approve=True, option 1) or offline
        with patch("optimiser.fetch_agile_rates", return_value=slots), \
             patch("optimiser.fetch_parallel_solar_forecasts", return_value=([], {})), \
             patch("optimiser.fetch_export_rate", return_value=12.0), \
             patch("optimiser.fetch_live_octoplus_sessions", return_value=[]), \
             patch("optimiser.chatgpt_select_candidate", return_value=(False, 1, 1, "Simulated veto", "n/a")):
            await optimiser.run_optimization()

        # Negative-rate override must override the LLM veto and still enable grid charging!
        assert any(b.get("state") == "enable" for b in _post_bodies("/enableChargeSchedule"))
        assert any(b.get("chargeToPercent") == 100 for b in _post_bodies("/setChargeTarget"))
