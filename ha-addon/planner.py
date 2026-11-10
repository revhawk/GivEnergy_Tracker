"""Cost-based plan simulator and search (ADR 0016).

Pure, side-effect-free functions. Every candidate charging plan is simulated
slot-by-slot with round-trip losses, and scored on

    net cost = import cost - export revenue - value of usable energy left at the end

blended between the central solar forecast and a pessimistic one. Written from
scratch; ideas only (cost-minimising simulation, PV10 weighting) are inspired by
public Predbat documentation.
"""

import math

try:
    import config
except ImportError:  # pragma: no cover
    config = None


def _cfg(name, default):
    return float(getattr(config, name, default)) if config else float(default)


def get_planner_settings():
    """Read planner tunables from config with safe defaults."""
    eff = min(1.0, max(0.5, _cfg('BATTERY_ROUND_TRIP_EFFICIENCY', 0.92)))
    return {
        'efficiency': eff,
        'pessimistic_factor': min(1.0, max(0.0, _cfg('PESSIMISTIC_SOLAR_FACTOR', 0.6))),
        'pessimistic_weight': min(1.0, max(0.0, _cfg('PESSIMISTIC_SOLAR_WEIGHT', 0.3))),
        'min_improvement_p': _cfg('PLAN_SEARCH_MIN_IMPROVEMENT_P', 2.0),
    }


def slots_in_windows(upcoming_slots, windows):
    """Return a bool mask: True where slot start falls inside any (start, end) window."""
    mask = []
    for s in upcoming_slots:
        st = s['start']
        mask.append(any(ws <= st < we for ws, we in (windows or [])))
    return mask


def end_value_rate(prices):
    """p/kWh value of energy left in the battery: mean of the cheapest quarter of import prices."""
    pos = sorted(p for p in prices)
    if not pos:
        return 0.0
    n = max(1, len(pos) // 4)
    return max(0.0, sum(pos[:n]) / n)


def simulate_plan(solar, load, prices, export_rates, capacity, start_energy, reserve_energy,
                  max_rate_kwh, efficiency=0.92, charge_mask=None, target_energy=None,
                  iboost_kwh=0.0, force_export_mask=None, value_rate=None):
    """Simulate one plan over N slots and return energy flows and costs.

    Charge slots: grid charges the battery towards target_energy while the grid
    also covers house load (GivEnergy Timed Charge behaviour). Losses are split
    evenly between charge and discharge (sqrt of round-trip efficiency).
    """
    n = len(solar)
    charge_mask = charge_mask or [False] * n
    force_export_mask = force_export_mask or [False] * n
    target_energy = capacity if target_energy is None else min(capacity, target_energy)
    eff = math.sqrt(max(0.01, efficiency))

    energy = start_energy
    soc, imports, exports = [], [], []
    total_imp = total_exp = total_iboost = grid_charge = cost = 0.0
    min_energy_seen = energy

    for i in range(n):
        net = load[i] - solar[i]
        imp = exp = ib = 0.0

        if force_export_mask[i]:
            take = max(0.0, min(max_rate_kwh, energy - reserve_energy))
            energy -= take
            grid = net - take * eff
            imp, exp = max(0.0, grid), max(0.0, -grid)
        elif charge_mask[i] and energy < target_energy - 1e-6:
            stored = min(max_rate_kwh * eff, target_energy - energy)
            gin = stored / eff
            energy += stored
            grid_charge += gin
            if net < 0:
                spare_rate = max(0.0, max_rate_kwh - gin)
                sc = min(-net * eff, spare_rate * eff, capacity - energy)
                energy += sc
                left = -net - sc / eff
                ib = min(left, iboost_kwh)
                exp = left - ib
                imp = gin
            else:
                imp = gin + net
        elif net < 0:
            sc = min(-net * eff, max_rate_kwh * eff, capacity - energy)
            energy += sc
            left = -net - sc / eff
            ib = min(left, iboost_kwh)
            exp = left - ib
        else:
            take = max(0.0, min(net / eff, max_rate_kwh, energy - reserve_energy))
            energy -= take
            imp = net - take * eff

        imp, exp = max(0.0, imp), max(0.0, exp)
        total_imp += imp
        total_exp += exp
        total_iboost += ib
        cost += imp * prices[i] - exp * export_rates[i]
        min_energy_seen = min(min_energy_seen, energy)
        imports.append(imp)
        exports.append(exp)
        soc.append(energy / capacity * 100.0 if capacity else 0.0)

    vr = end_value_rate(prices) if value_rate is None else value_rate
    end_value = max(0.0, energy - reserve_energy) * eff * vr
    return {
        'soc': soc,
        'imports': imports,
        'exports': exports,
        'import_kwh': total_imp,
        'export_kwh': total_exp,
        'iboost_kwh': total_iboost,
        'grid_charge_kwh': grid_charge,
        'cost_p': cost,
        'end_energy': energy,
        'end_value_p': end_value,
        'net_cost_p': cost - end_value,
        'min_soc': min_energy_seen / capacity * 100.0 if capacity else 0.0,
    }


def score_plan(inputs, charge_mask, target_energy, settings=None):
    """Blend central and pessimistic-solar net costs. Returns (score_p, central, pessimistic)."""
    st = settings or get_planner_settings()
    common = dict(inputs)
    solar = common.pop('solar')
    central = simulate_plan(solar, charge_mask=charge_mask, target_energy=target_energy,
                            efficiency=st['efficiency'], **common)
    pess_solar = [s * st['pessimistic_factor'] for s in solar]
    pess = simulate_plan(pess_solar, charge_mask=charge_mask, target_energy=target_energy,
                         efficiency=st['efficiency'], **common)
    w = st['pessimistic_weight']
    score = (1 - w) * central['net_cost_p'] + w * pess['net_cost_p']
    return score, central, pess


def search_best_plan(inputs, eligible_mask, capacity, reserve_pct, settings=None,
                     soc_step=5, max_slots=None):
    """Grid-search target SoC x cheapest-N eligible slots; return the lowest-score plan.

    Returns dict with target_soc, mask, score, central, pessimistic.
    """
    st = settings or get_planner_settings()
    prices = inputs['prices']
    eligible = [i for i, ok in enumerate(eligible_mask) if ok]
    by_price = sorted(eligible, key=lambda i: prices[i])
    n_slots = len(eligible_mask)
    limit = len(by_price) if max_slots is None else min(max_slots, len(by_price))

    none_mask = [False] * n_slots
    s0, c0, p0 = score_plan(inputs, none_mask, None, st)
    best = {'target_soc': None, 'mask': none_mask, 'score': s0, 'central': c0, 'pessimistic': p0}

    start_pct = int(math.ceil(max(reserve_pct, 0) / soc_step) * soc_step)
    for target in range(max(start_pct, soc_step), 101, soc_step):
        t_energy = capacity * target / 100.0
        for n in range(1, limit + 1):
            chosen = set(by_price[:n])
            mask = [i in chosen for i in range(n_slots)]
            score, c, p = score_plan(inputs, mask, t_energy, st)
            if score < best['score'] - 1e-9:
                best = {'target_soc': target, 'mask': mask, 'score': score, 'central': c, 'pessimistic': p}
    return best
