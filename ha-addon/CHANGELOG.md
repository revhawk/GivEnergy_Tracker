# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.22.4] - 2026-09-03

### Senior Developer Code & Architectural Review
- **Weather-Adaptive Day Classifier (`DayClassification`)**: Implemented dynamic day classification (`HIGH_SOLAR_SELF_CONSUMPTION`, `MEDIUM_SOLAR_PARTIAL_PRECHARGE`, `LOW_SOLAR_GRID_PRECHARGE`, `NEGATIVE_RATE_OPPORTUNITY`) based on live solar forecast vs house load & battery space, eliminating fixed calendar seasons.
- **ChatGPT AI Co-Pilot & Weather Risk Commentary**: Updated OpenAI integration to evaluate weather risk, cloud cover cushions, and day classification.
- **Daily Performance & AI Learning Archive**: Implemented `archive_daily_stats_history` storing daily metrics and AI recommendations in `/share/nas_logs/history/daily_stats_YYYY-MM-DD.json`.
- **GivTCP REST API Robustness**: Extended HTTP timeout to 25s (`GIVTCP_TIMEOUT`) with 1.0s backoff pause to prevent GivTCP slot write timeouts.
- **Test Suite Execution**: 100% test pass rate across 77 tests in pytest (`test_optimization.py`, `test_state_logging.py`, `test_appliance.py`, `test_config_options.py`, `test_contracts.py`, `test_api_parsing.py`, `test_helpers.py`, `test_write_slots.py`).

### Added
- **Weather-Adaptive Classifier**: `DayClassification` Pydantic v2 model & `MEDIUM_SOLAR_PARTIAL_PRECHARGE` headroom allocation.
- **ChatGPT Weather Risk Commentary**: Structured AI recommendation field for solar forecast risk evaluation.
- **Daily History JSON Archiver**: Persistent daily stats stored in `/share/nas_logs/history/`.

### Fixed
- **GivTCP Slot Timeout**: Increased HTTP REST timeout from 10s to 25s to avoid socket read timeouts during multi-slot writes.

---

## [1.0.22.3] - 2026-09-03

### Senior Developer Code & Architectural Review
- **Import Fix (`TimedRotatingFileHandler`)**: Added `from logging.handlers import TimedRotatingFileHandler` to `ha-addon/state.py` line 11, fixing startup `NameError`.
- **Logging Contract Test Suite (`tests/test_state_logging.py`)**: Created dedicated test suite validating `setup_logging()` initialization and `archive_daily_stats_history()` contract to prevent any logger startup exceptions or missing import regressions.
- **Home Assistant UI Configuration (`log_file_path`)**: Added `log_file_path` option to `ha-addon/config.yaml` schema, enabling non-technical users to customize their NAS log share path directly from the Home Assistant UI without touching python code.
- **Mandatory Test Review Policy**: Added Rule 4 to `.agents/AGENTS.md` requiring test suite expansion whenever code or dependencies are modified.
- **Test Suite Execution**: 100% test pass rate across 76 tests in pytest (`test_state_logging.py`, `test_appliance.py`, `test_config_options.py`, `test_contracts.py`, `test_api_parsing.py`, `test_helpers.py`, `test_optimization.py`, `test_write_slots.py`).

### Added
- **Dedicated Logger Contract Tests**: `tests/test_state_logging.py` covering handler setups and JSON archiving.
- **UI NAS Path Setting**: `log_file_path` added to `config.yaml` UI configuration schema.

### Fixed
- **NameError on Startup**: Fixed missing `TimedRotatingFileHandler` import in `ha-addon/state.py`.

---

## [1.0.22.2] - 2026-09-03

### Senior Developer Code & Architectural Review
- **Daily Midnight Log Rotation (`TimedRotatingFileHandler`)**: Upgraded `state.py` logging setup to use `TimedRotatingFileHandler` rotating daily at midnight (`when='midnight'`), retaining 90 days of date-stamped log files (`/share/nas_logs/givenergy_tracker.log.YYYY-MM-DD`).
- **Daily Performance Record Archiving (`/share/nas_logs/history/`)**: Implemented `archive_daily_stats_history()` to archive complete daily financial summaries, solar generation, grid import/export kWh, and ChatGPT audit reports into date-stamped JSON files (`/share/nas_logs/history/daily_stats_YYYY-MM-DD.json`) every evening at 23:00.
- **Test Suite Execution**: 100% test pass rate across 74 tests in pytest (`test_appliance.py`, `test_config_options.py`, `test_contracts.py`, `test_api_parsing.py`, `test_helpers.py`, `test_optimization.py`, `test_write_slots.py`).

### Added
- **Daily Log Rotation**: Midnight rotation keeping 90 days of complete daily log files on your NAS.
- **Historical Daily Performance Archive**: Created `/share/nas_logs/history/daily_stats_YYYY-MM-DD.json` for long-term financial performance comparison.

---

## [1.0.22.1] - 2026-09-03

### Senior Developer Code & Architectural Review
- **Explicit Telemetry Timestamps (`[HH:MM:SS]`)**: Updated `state.py` (`setup_logging`) and `simulation.py` (`run_light_monitor`) to enforce `%(asctime)s` formatting across stdout and log files, appending explicit local time headers `⏰ [HH:MM:SS]` to every light monitor telemetry check.
- **Test Suite Execution**: 100% test pass rate across 74 tests in pytest (`test_appliance.py`, `test_config_options.py`, `test_contracts.py`, `test_api_parsing.py`, `test_helpers.py`, `test_optimization.py`, `test_write_slots.py`).

### Added
- **Explicit Telemetry Timestamping**: Every 30-minute status check log now includes the exact local time stamp (e.g. `11:30:00 - INFO - ⏰ [11:30:00] Battery SoC: 43% vs planned 28%...`).

---

## [1.0.22.0] - 2026-09-02

### Senior Developer Code & Architectural Review
- **Appliance Telemetry & Washing Machine Dashboard (`appliance.py`)**: Created dedicated domain module (`ha-addon/appliance.py`) monitoring Home Assistant smart plug power sensors (`sensor.washing_machine_power`), detecting wash states (`idle`, `washing`, `spinning`, `heating`), calculating cycle costs in pence, and recommending cheap Agile wash windows.
- **Oven Cooking Peak Pre-Charge (16:00–18:00)**: Evaluates 16:00–20:00 dinner preparation load early during afternoon runs (13:00–14:30), scheduling pre-charging at 25.9p (14:30) to guarantee full battery before 41.0p–50.9p peak rates start.
- **Structured AI Co-Planner (`LLMPlannerRecommendation`)**: Upgraded ChatGPT from a simple veto engine to a structured Pydantic v2 Co-Planner that receives tariff, solar, cooking, and washing load context and returns executable slot recommendations.
- **Architectural Decision Records (ADRs 0011–0012)**: Documented ADR 0011 (Appliance Telemetry & Washing Machine Dashboard) and ADR 0012 (Early Afternoon Pre-Charge Evaluation for Evening Oven Cooking Peak) in `ha-addon/SPECIFICATION.md`.
- **Test Suite Execution**: 100% test pass rate across 74 tests in pytest (`tests/test_appliance.py`, `tests/test_config_options.py`, `tests/test_contracts.py`, `tests/test_api_parsing.py`, `tests/test_helpers.py`, `tests/test_optimization.py`, `tests/test_write_slots.py`).

### Added
- **Appliance Telemetry Module**: Created `ha-addon/appliance.py` for cycle detection, cost math, and Washing Machine Dashboard rendering.
- **Washing Machine Test Suite**: Created `tests/test_appliance.py` testing wash cycle state detection, cost calculations, and slot recommendation algorithms.
- **Pydantic v2 Appliance & Co-Planner Contracts**: Added `WashingMachineTelemetry`, `RecommendedSlot`, and `LLMPlannerRecommendation` to `ha-addon/contracts.py`.

---

## [1.0.21.3] - 2026-09-02

### Senior Developer Code & Architectural Review
- **Architecture Decomposition**: Passed Senior Developer review. Decomposed monolithic `optimiser.py` into high-cohesion domain modules: `simulation.py`, `state.py`, `octoplus.py`, and `hive.py`.
- **Architectural Decision Records (ADRs 0001–0010)**: Formalized local ADRs in `SPECIFICATION.md` following the ADR 0004 standard.
- **Contract & Option Testing**: Verified 100% test pass rate across 69 tests (`test_config_options.py`, `test_contracts.py`, `test_api_parsing.py`, `test_helpers.py`, `test_optimization.py`, `test_write_slots.py`).
- **Future Hardware Roadmap**: Documented planned hardware extensions for direct iBoost physical relay override and DS18B20 hot water cylinder temperature probes.

### Added
- **Local Architectural Decision Records**: Added Section 8 (ADRs 0001 – 0010) in `ha-addon/SPECIFICATION.md`.
- **Add-on Configuration Options Test Suite**: Created `tests/test_config_options.py` testing all Home Assistant UI options (`interval_minutes`, `run_once`, `debug_logging`, `openai_api_key`, `openai_model`, `daily_plan_hour`, `daily_audit_hour`).
- **Expanded Pydantic v2 Contract Tests**: Extended `tests/test_contracts.py` with boundary validation for all Pydantic v2 data models.
- **Hardware Integration Roadmap**: Documented planned hardware extensions in `DOCS.md`, `README.md`, and `SPECIFICATION.md`.

---

## [1.0.20] - 2026-09-01

### Added
- **Single Source Versioning**: Created `ha-addon/version.py` (`__version__ = "1.0.20"`) and synced `config.yaml` to ensure zero version drift.
- **Modular Codebase Architecture**: Modularized `optimiser.py` into clean, human-readable sub-modules under `ha-addon/modules/`:
  - `modules/givtcp.py`: GivTCP REST API v2/v3 endpoints (`/setChargeSlot`, `/setBatteryMode`, `/setChargeTarget`, telemetry).
  - `modules/tariffs.py`: Octopus Agile import pricing & Outgoing export rate fetcher.
  - `modules/solar.py`: Forecast.Solar API client with morning solar forecast damping (`0.65x` before 09:00).
  - `modules/profiler.py`: Dynamic load profiling (`get_load_kwh_for_slot`) & Power Down window detector (`is_power_down_slot`).
  - `modules/llm.py`: Smart ChatGPT veto validator (`chatgpt_veto_plan`) & daily summary generator.
- **Octopus Octoplus Power Down Session Optimization**: Added support for active Power Down / Saving Session windows (`POWER_DOWN_WINDOWS`), enforcing 0.0 kWh grid import during sessions and automatically pre-charging battery at cheaper prior rates.
- **Dynamic Re-Planning Trigger (SoC Drift Check)**: Added 30-minute telemetry check comparing live battery SoC with planned SoC schedule, triggering immediate re-planning if drift exceeds `SOC_DRIFT_THRESHOLD_PCT` (`15.0%`).
- **GivTCP v2/v3 REST API Fixes**: Replaced deprecated `/setChargeEnable` with GivTCP REST API v2/v3 conventions (`/setChargeTarget` & `/setBatteryMode`).

---

## [1.0.19] - 2026-08-17

### Added
- **OpenAI Model Selection Dropdown**: Added `openai_model` Configuration UI dropdown menu (`gpt-4o-mini`, `gpt-4o`, `gpt-3.5-turbo`) allowing users to choose the OpenAI model for ChatGPT plan scoring and daily audits.
- **Dynamic Model Resolution**: Added `get_openai_model()` helper supporting HA options UI $\rightarrow$ `config.py` (`OPENAI_MODEL`) $\rightarrow$ fallback default (`gpt-4o-mini`).

---

## [1.0.18] - 2026-08-17

### Added
- **Octoplus ADR 0004 Entity Renaming**: Adopted `HomeAssistant-OctopusEnergy` ADR 0004 entity naming standards:
  - Renamed **Saving Sessions** to **Power Down** (`sensor.octopus_energy_power_down_sessions`, `event.octopus_energy_octoplus_power_down_events`, `calendar.octopus_energy_octoplus_power_down_sessions`).
  - Renamed **Free Electricity Sessions** to **Power Up** (`sensor.octopus_energy_power_up_sessions`, `event.octopus_energy_octoplus_power_up_events`, `calendar.octopus_energy_octoplus_power_up_sessions`).
  - Added helper functions (`get_octoplus_entity_name`, `parse_octoplus_session`) with legacy fallback support until January 2027.
- **Cheapest Non-Contiguous Slot Selection**: Enhanced deficit charge scheduling to evaluate non-contiguous cheapest $N$ slots alongside contiguous windows, programming non-contiguous slots whenever they yield a lower overall cost and fit within GivTCP's 10-slot limit.

---

## [1.0.17] - 2026-07-04

### Fixed
- **Startup Log Print Fallback**: Updated the fallback baseload value printed in the startup diagnostic logs from `1000 W` to `400 W` when `BASE_LOAD_W` is deleted/not defined in the configuration file.

---

## [1.0.16] - 2026-07-04

### Added
- **10-Slot Arbitrage Planning**: Added support to merge contiguous half-hour periods of cheap import rates (<10.5p) and negative rates into unified charging blocks (up to 10 slots programmed).
- **Omitted SOC Target on Clear**: Prevented GivTCP validation crashes on newer firmware by omitting `chargeToPercent` in `/setChargeSlot` payloads when clearing unused slots.
- **Inverter Telemetry Integration**: Live `SOC`, `PV_Power`, and `Load_Power` are now fetched from the GivTCP cache and injected into the first slot of the simulation loop.

### Changed
- **Default Baseload**: Adjusted default fallback `BASE_LOAD_W` from `1000 W` to `400 W` to align with typical home draw.

---

## [1.0.15] - 2026-07-03

### Added
- **Smart Scheduler Control**: Enabled smart scheduler via `/enableChargeSchedule`.
- **Date Labels in Logs**: Added date labels to simulation output logs.

---

## [1.0.5] - 2026-07-03

### Fixed
- **Silent-mock removed on GivTCP failure.** Previously, if GivTCP was unreachable AND the `givenergy-modbus` package was unavailable, the code would log `[MOCK] Setting charge slot 1: ...` and return `True` — pretending the write succeeded while nothing reached the inverter. Now returns `False` and logs an ERROR so the caller can detect the failure. Similarly for SoC reads: returns `None` and aborts the planning run rather than defaulting to a magic 25% value.
- **Improved Modbus import diagnostic.** The old warning ("givenergy-modbus package not installed") was misleading when the package *was* installed but a submodule import failed (typical after v2.x restructure). New warning logs the actual exception message so the difference between "not installed" and "API mismatch" is visible.

### Changed
- `run_optimization` now bails out early with an ERROR if `get_inverter_soc()` returns `None`, rather than crashing on `None / 100.0`. The tracker will retry on the next tick.

---

## [1.0.4] - 2026-07-03

### Added
- **Startup write-path self-test** (`STARTUP_WRITE_TEST` env / `startup_write_test` add-on option, default `false`). On daemon start it writes a test charge slot 2 hours in the future, reads it back via GivTCP `/getCache`, clears it, verifies cleared. Confirms end-to-end that the write path works in production. Logs each of the 4 steps.
- `read_inverter_charge_slots()` — helper that queries GivTCP for current slot configuration, tolerant of field-name variation across GivTCP versions.

### Changed
- `ARBITRAGE_MARGIN_P` default raised from `0.5` → `1.5` — accounts for the ~10% round-trip battery loss. Import must now be below `~10.5p` (was `11.5p`) to trigger arbitrage, so only genuinely profitable slots reach the LLM validator.
- Tightened `chatgpt_veto_plan` system prompt with explicit "HARD FACTS" section stating the export rate is FLAT and does not vary during the day. The LLM was hallucinating "better rates later for export" — the new prompt bans this specifically. Reason field now required to reference concrete numbers from the input data.
- Fallback default for `BASE_LOAD_W` in the fetch code aligned to `1000` (was `300`) to match the config-level default.

---

## [1.0.3] - 2026-07-03

### Added
- Live Octopus export-rate fetch via `fetch_export_rate()` — pulls the current Outgoing Variable rate (12p at time of release) from Octopus, cached 6 h
- `EXPORT_PRODUCT_CODE` / `EXPORT_TARIFF_CODE` / `EXPORT_RATE_P_FALLBACK` / `ARBITRAGE_MARGIN_P` in `config.py`
- Arbitrage logic: opportunistic grid charging any time an Agile slot is below (`export_rate − margin`), not just on negative-rate slots; safety check skips arbitrage if the cheapest window still averages above the export rate
- Per-run economics line in the log: cost, profit-per-kWh vs export, estimated daily gain
- Structured LLM veto (`chatgpt_veto_plan`) — returns approve/score/reason JSON; on `approve=false` the algorithm's charge plan is overridden and slots are cleared
- Startup config banner listing effective `BASE_LOAD_W`, tariff codes, live export rate, arbitrage margin, GivTCP URL
- End-of-day audit (`run_end_of_day_audit`) — runs once daily at `DAILY_AUDIT_HOUR` and calls `generate_daily_summary` for an English-language report
- Light monitor mode (`run_light_monitor`) — cheap SoC check with no LLM or inverter write for non-planning ticks
- Persistent state file `/share/nas_logs/givenergy_state.json` tracking `last_plan_date`, `last_audit_date`, and the day's plan snapshot
- Env vars `DAILY_PLAN_HOUR` (default 17) and `DAILY_AUDIT_HOUR` (default 23) for scheduling

### Changed
- `BASE_LOAD_W` default raised from 300 W to 1000 W to match observed overnight consumption from Octopus half-hourly CSV analysis
- MPAN labelling in `config.py`: `ELEC_MPAN` replaced by `ELEC_IMPORT_MPAN` (import) and `ELEC_EXPORT_MPAN` (export) — old label was pointing at the export meter
- Main loop no longer runs `run_optimization` every 30 min — it fires once per day as the daily planner; every other tick runs the light monitor
- Consolidated to a single LLM call per run (removed redundant `run_chatgpt_audit` invocations)

### Fixed
- Reduced OpenAI API burn from ~96 calls/day to ~2 (planner + audit)

---

## [1.0.1] - 2026-07-02

### Added
- Initial test build of the GivEnergy Tariff Optimiser as a Home Assistant local add-on
- Octopus Agile API integration — fetches all upcoming half-hour price slots
- Forecast.Solar integration — free solar generation forecast for next 24 hours
- GivTCP REST API integration — reads battery SoC and writes charge slots
- Direct Modbus TCP fallback — uses `givenergy-modbus` if GivTCP is unavailable
- 24-hour simulation engine — models battery/solar/home load/iBoost/export without grid charge
- Sliding window optimiser — finds the cheapest contiguous Agile charge window
- Optional ChatGPT (GPT-4o) audit — explains the daily optimization decision in plain English
- Rotating file logger — saves to NAS share with 5 MB cap and 3 backup files
- Daemon mode with configurable polling interval (default: 30 minutes)
- `run_once` mode for one-shot testing from Home Assistant UI
- `openai_api_key` option exposed in Home Assistant Configuration tab
- Midnight-spanning charge slot support (splits across GivTCP Slot 1 & Slot 2)
- `config.py.example` template so secrets are never committed to Git

### Architecture
- Home Assistant add-on container: `python:3.11-slim`
- Entrypoint: `run.sh` reads `options.json`, exports env vars, launches `optimizer.py`
- Config: `config.py` (gitignored) holds all credentials and hardware parameters
- Supports: `aarch64`, `amd64`, `armv7` architectures

### Known Limitations
- GivTCP container must be reachable by hostname or IP from within the add-on container
- Forecast.Solar free tier has a rate limit (12 calls/hour); the optimizer respects this with a `429` check
- Direct Modbus path requires `givenergy-modbus` package; currently mocked if not installed
- Solar azimuth and declination are static; seasonal adjustment not yet implemented

---

[Unreleased]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.19...HEAD
[1.0.19]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.18...v1.0.19
[1.0.18]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.17...v1.0.18
[1.0.17]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.16...v1.0.17
[1.0.16]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.15...v1.0.16
[1.0.15]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.5...v1.0.15
[1.0.5]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.1...v1.0.3
[1.0.1]: https://github.com/revhawk/GivEnergy_Tracker/releases/tag/v1.0.1
