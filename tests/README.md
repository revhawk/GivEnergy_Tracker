# Test Suite

Tests for the GivEnergy Tariff Optimiser add-on.

## Running

```bash
cd tests
pip install -r requirements.txt
pytest -v
```

## Coverage

### Implemented ✅

- **Pure helpers** (`test_helpers.py`)
  - `parse_utc_iso` — ISO 8601 parsing, timezone handling
  - `find_key_recursive` — nested dict/list search
  - `get_solar_kwh_for_slot` — hourly forecast → half-hourly slot mapping

- **External API parsing** (`test_api_parsing.py`)
  - `fetch_agile_rates` — sorts rates chronologically, filters bad rows
  - `fetch_export_rate` — picks the currently-active tariff, uses fallback on error, caches for 6 h
  - `fetch_solar_forecast` — handles rate-limit (429) responses gracefully

### Planned 🚧

- **Arbitrage decision logic**
  - The decision path is currently embedded in `run_optimization()` and hard to
    test in isolation. First refactor step: extract the "should we charge?" and
    "which window?" logic into pure functions taking `(rates, soc, forecast,
    export_rate)` → `(action, window)`. Then unit-test:
    - Deficit path fires when predicted import > 0.2 kWh
    - Arbitrage path fires when any slot < `export_rate − ARBITRAGE_MARGIN_P`
    - Arbitrage path skips when best window still averages above export
    - Negative-rate override always charges
    - Battery-full path returns no charge

- **State persistence**
  - `load_state` / `save_state` round-trip
  - `_record_plan` populates the module global correctly
  - `run_end_of_day_audit` reads yesterday's plan

- **LLM veto**
  - Given a mocked OpenAI response, the veto is respected
  - Malformed JSON from LLM → fails open (approve=True)
  - Timeout → fails open

- **Integration**
  - Full `run_optimization` run against a mocked GivTCP + Octopus + OpenAI stack
  - End-to-end state file lifecycle (planner → monitor → audit)

## Design notes

- All tests must be **side-effect-free** — no real API calls, no file writes to
  `/share/nas_logs/`. Use `tmp_path` fixture for anything file-system.
- Mock the OpenAI client at module level (`_openai_client`); don't hit the real
  API.
- Mock Octopus/Forecast.Solar with `responses` or `requests-mock`; assertions
  should verify the URL, query parameters, and how the parser handles the
  response body.
- Tests should be fast — the whole suite should run in <5 seconds. If something
  is slow, mock it out.

## Adding a test

1. Drop a `test_*.py` file in this directory.
2. Import the target from `optimiser` (path is set up in `conftest.py`).
3. Use `pytest -v -k <name>` to iterate on a single test.
