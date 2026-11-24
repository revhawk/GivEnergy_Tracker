# Test Suite

Automated tests for the **GivEnergy Tariff Optimiser** Home Assistant App (**213 tests** across 14 test modules).

For the full testing pyramid, E2E guardrail diagrams, and AST mutation testing benchmarks, see [`ha-addon/TESTING.md`](../ha-addon/TESTING.md).

## Running Tests in Local Environment

```bash
# 1. From project root, create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies for both the App and test suite
pip install -r ha-addon/requirements.txt -r tests/requirements.txt

# 3. Execute the full test suite
pytest -v

# 4. Run the full pre-commit verification gate (version sync, leak scan, AppArmor, pytest)
python scripts/verify_commit.py
```

## Coverage Summary

- **End-to-End Guardrails** (`test_e2e_guardrails.py`) — 5 multi-step production lifecycles: Axle VPP (`Eco` → `T-10m` `Timed Export` → `T=0` `/forceExport` → `Eco` restore), unbooked vs. booked Happy Hour gate, intraday Power Down detection + 100% daytime top-up, winter overnight pre-charge drift stability, and negative-rate LLM fail-open resilience.
- **Core Optimization & Monitors** (`test_optimization.py`) — 24h simulation, winter 100% pre-charge, daytime top-ups, 5-min Axle/Octoplus polling, 30-min SoC drift, and composable pipeline helpers (`classify_day`, `_clear_axle_state`).
- **Cost-Optimised Planner** (`test_planner.py`) — pure side-effect-free `simulate_plan()`, blended normal/pessimistic `score_plan()`, and `search_best_plan()`.
- **GivTCP Hardware Write Path** (`test_write_slots.py`) — same-day & midnight-spanning charge slots, Just-In-Time `Timed Export`, `/setEcoMode`, `/forceExport`, hardware capability auto-detection (`3,600 W` Hybrid Gen 3), and Modbus fallback.
- **Axle Energy VPP** (`test_axle.py`) — token discovery, event parsing, `+1h` default end time, 5-minute cache TTL, and HA sensor fallback.
- **Octoplus & Rewards** (`test_helpers.py`) — ISO 8601 parsing, Power Down / Power Up session matching, adjacent-slot & midnight-crossing boundaries, Wheel of Fortune, and Monday scratchcards.
- **Hive Hot Water Safety** (`test_hive.py`) — exact `45.0°C` morning shower safety threshold, `15.0 kWh` solar gas-suppression threshold, and HA REST error handling.
- **External API Parsing** (`test_api_parsing.py`) — Octopus Agile & Flux import/export rates, account auto-discovery, open-ended `valid_to: None` slots, Forecast.Solar, and Open-Meteo.
- **AI Co-Pilot & LLM Validation** (`test_llm.py`) — candidate strategy selection, negative-rate override, fail-open fallbacks, and daily savings summaries.
- **Contracts, Config, State & Appliances** (`test_contracts.py`, `test_config_options.py`, `test_state_logging.py`, `test_appliance.py`, `test_pre_commit_requirements.py`) — Pydantic v2 schemas, `config.yaml` sync, NAS state persistence, washing machine telemetry, AppArmor, and GPLv3 hygiene.

## Design Notes

- All tests are **side-effect-free** — no live API calls and no writes to `/share/nas_logs/` (use pytest's `tmp_path` fixture for filesystem tests).
- HTTP calls are mocked via `responses` or `unittest.mock.patch`; OpenAI calls are mocked at the client boundary.
- CI runs automatically on every push and pull request via [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) across Python 3.11 and 3.12 (`TZ=Europe/London`).

## Adding a Test

1. Add or update a `test_*.py` file in this directory.
2. Import target modules directly (path is configured in `conftest.py`).
3. Run `pytest -v -k <name>` to iterate, then run `python scripts/verify_commit.py` before committing.
