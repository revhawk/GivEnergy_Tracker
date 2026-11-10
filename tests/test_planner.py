"""Unit & contract tests for the cost-based planner (ADR 0016)."""

import math
from datetime import datetime, timedelta, timezone

import pytest

import planner
from contracts import PlanResult, CandidatePlanOption


CAP = 10.0
RES = 1.0
RATE = 1.5


def _base(n=4, solar=0.0, load=0.0, price=20.0, export=10.0, start=5.0, **kw):
    d = dict(
        load=[load] * n,
        prices=[price] * n,
        export_rates=[export] * n,
        capacity=CAP,
        start_energy=start,
        reserve_energy=RES,
        max_rate_kwh=RATE,
        iboost_kwh=0.0,
        value_rate=0.0,
    )
    d.update(kw)
    return [solar] * n, d


def test_idle_no_flows():
    solar, kw = _base()
    r = planner.simulate_plan(solar, **kw)
    assert r['import_kwh'] == 0 and r['export_kwh'] == 0
    assert r['end_energy'] == pytest.approx(5.0)
    PlanResult(**r)  # contract


def test_discharge_losses_and_reserve_floor():
    solar, kw = _base(n=10, load=1.0, start=3.0)
    r = planner.simulate_plan(solar, efficiency=0.81, **kw)  # eff per leg 0.9
    assert r['end_energy'] == pytest.approx(RES)
    # usable 2 kWh stored delivers 1.8 kWh; remaining 8.2 kWh imported
    assert r['import_kwh'] == pytest.approx(10.0 - 1.8)
    assert r['min_soc'] == pytest.approx(10.0)


def test_grid_charge_respects_target_and_rate_with_losses():
    solar, kw = _base(n=3, start=2.0)
    r = planner.simulate_plan(solar, efficiency=1.0, charge_mask=[True] * 3, target_energy=4.0, **kw)
    assert r['end_energy'] == pytest.approx(4.0)
    assert r['grid_charge_kwh'] == pytest.approx(2.0)
    r2 = planner.simulate_plan(solar, efficiency=0.81, charge_mask=[True, False, False], target_energy=10.0, **kw)
    assert r2['grid_charge_kwh'] == pytest.approx(RATE)
    assert r2['end_energy'] == pytest.approx(2.0 + RATE * 0.9)


def test_grid_covers_load_during_charge_slot():
    solar, kw = _base(n=1, load=0.5, start=2.0)
    r = planner.simulate_plan(solar, efficiency=1.0, charge_mask=[True], target_energy=3.0, **kw)
    assert r['import_kwh'] == pytest.approx(1.0 + 0.5)
    assert r['end_energy'] == pytest.approx(3.0)


def test_solar_spill_iboost_then_export():
    solar, kw = _base(n=1, solar=3.0, start=9.5, iboost_kwh=1.0)
    r = planner.simulate_plan(solar, efficiency=1.0, **kw)
    assert r['end_energy'] == pytest.approx(10.0)
    assert r['iboost_kwh'] == pytest.approx(1.0)
    assert r['export_kwh'] == pytest.approx(1.5)
    assert r['cost_p'] == pytest.approx(-15.0)


def test_force_export_with_losses():
    solar, kw = _base(n=1, start=5.0, force_export_mask=[True])
    r = planner.simulate_plan(solar, efficiency=0.81, **kw)
    assert r['export_kwh'] == pytest.approx(RATE * 0.9)
    assert r['end_energy'] == pytest.approx(5.0 - RATE)


def test_end_value_rewards_leftover_energy():
    solar, kw = _base(n=1, start=5.0)
    kw['value_rate'] = 10.0
    r = planner.simulate_plan(solar, efficiency=1.0, **kw)
    assert r['end_value_p'] == pytest.approx((5.0 - RES) * 10.0)
    assert r['net_cost_p'] == pytest.approx(-40.0)


def test_end_value_rate_cheapest_quarter():
    assert planner.end_value_rate([1, 2, 3, 4, 10, 20, 30, 40]) == pytest.approx(1.5)
    assert planner.end_value_rate([]) == 0.0


def test_slots_in_windows():
    t0 = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
    slots = [{'start': t0 + timedelta(minutes=30 * i)} for i in range(4)]
    mask = planner.slots_in_windows(slots, [(t0 + timedelta(minutes=30), t0 + timedelta(minutes=90))])
    assert mask == [False, True, True, False]
    assert planner.slots_in_windows(slots, None) == [False] * 4


def test_search_charges_cheap_to_cover_expensive_peak():
    # 2 cheap night slots @5p, then 4 peak slots @40p with 1 kWh load each.
    n = 6
    inputs = dict(
        solar=[0.0] * n,
        load=[0.0, 0.0, 1.0, 1.0, 1.0, 1.0],
        prices=[5.0, 5.0, 40.0, 40.0, 40.0, 40.0],
        export_rates=[5.0] * n,
        capacity=CAP, start_energy=RES, reserve_energy=RES,
        max_rate_kwh=RATE, iboost_kwh=0.0, force_export_mask=None, value_rate=0.0,
    )
    st = {'efficiency': 0.92, 'pessimistic_factor': 0.6, 'pessimistic_weight': 0.3, 'min_improvement_p': 2.0}
    best = planner.search_best_plan(inputs, [True] * n, CAP, 10, settings=st)
    assert best['mask'][:2] == [True, True]
    assert not any(best['mask'][2:])
    assert sum(best['central']['imports'][2:]) < 4.0  # peak imports reduced (total includes grid charge)
    none_score, _, _ = planner.score_plan(inputs, [False] * n, None, st)
    assert best['score'] < none_score


def test_search_skips_ineligible_slots():
    n = 3
    inputs = dict(
        solar=[0.0] * n, load=[0.0, 0.0, 2.0], prices=[1.0, 5.0, 40.0], export_rates=[0.0] * n,
        capacity=CAP, start_energy=RES, reserve_energy=RES, max_rate_kwh=RATE,
        iboost_kwh=0.0, force_export_mask=None, value_rate=0.0,
    )
    best = planner.search_best_plan(inputs, [False, True, True], CAP, 10)
    assert best['mask'][0] is False


def test_search_does_nothing_when_charging_cannot_pay():
    n = 4
    inputs = dict(
        solar=[0.0] * n, load=[0.5] * n, prices=[20.0] * n, export_rates=[0.0] * n,
        capacity=CAP, start_energy=5.0, reserve_energy=RES, max_rate_kwh=RATE,
        iboost_kwh=0.0, force_export_mask=None, value_rate=0.0,
    )
    best = planner.search_best_plan(inputs, [True] * n, CAP, 10)
    assert best['target_soc'] is None


def test_settings_defaults_and_clamping(monkeypatch):
    class Cfg:
        BATTERY_ROUND_TRIP_EFFICIENCY = 1.7
        PESSIMISTIC_SOLAR_WEIGHT = -1
    monkeypatch.setattr(planner, 'config', Cfg)
    st = planner.get_planner_settings()
    assert st['efficiency'] == 1.0
    assert st['pessimistic_weight'] == 0.0
    assert st['pessimistic_factor'] == pytest.approx(0.6)
    monkeypatch.setattr(planner, 'config', None)
    assert planner.get_planner_settings()['efficiency'] == pytest.approx(0.92)


def test_candidate_contract_accepts_sim_fields():
    c = CandidatePlanOption(
        id=4, name="Cost-Optimised Search", strategy="cost_optimised", target_soc=60, kwh=3.0,
        avg_price=8.0, headroom_kwh=4.0, slots_desc="02:00-03:00", rationale="cheapest",
        sim_cost_p=120.0, sim_cost_pessimistic_p=150.0, sim_score_p=129.0, sim_min_soc=10.0,
    )
    assert c.sim_score_p == 129.0
