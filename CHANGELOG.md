# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

[Unreleased]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.4...HEAD
[1.0.4]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/revhawk/GivEnergy_Tracker/compare/v1.0.1...v1.0.3
[1.0.1]: https://github.com/revhawk/GivEnergy_Tracker/releases/tag/v1.0.1
