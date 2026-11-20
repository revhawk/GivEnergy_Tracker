# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.25.9] - 2026-11-19

### Added
- **GivTCP Inverter Hardware Capability Auto-Detection (`givtcp.py`, `optimiser.py`, `simulation.py`)**:
  - Added `get_inverter_capabilities()` and `get_effective_export_rate_w()` to query GivTCP `/getCache` on startup and automatically detect `Invertor_Type`, `Invertor_Max_Bat_Rate` (e.g. `3600W` on Hybrid Gen 3), `Invertor_Max_Inv_Rate`, and `Battery_Capacity_kWh`.
  - Updated `axle_export_power_w` default to `0` (Auto-detect from GivTCP, schema `0..10000W`) and auto-upgraded legacy `3000W` defaults to the detected hardware max battery rate, safely clamping any explicit custom override to hardware limits.
  - Restores full hardware discharge rate (`/setDischargeRate`) automatically when clearing Axle `Timed Export` back to `Eco` mode.
  - Updated startup `MAX_BATTERY_CHARGE_RATE` and `BATTERY_CAPACITY_KWH` from live GivTCP hardware capabilities so 24-hour simulation and charge slot sizing match actual inverter specs.
- **Unit Test Expansion (`tests/test_write_slots.py`)**:
  - Added `TestInverterCapabilitiesAutoDetect` covering GivTCP hardware spec parsing, auto-detection & clamping logic, and discharge rate restoration on `Eco` reset.

### Senior Developer Review
- **Higher Axle Export Yield**: Unlocks the full `3,600 W` discharge rate on Hybrid Gen 3 inverters (+20% / +0.6 kWh per 1-hour Axle event) with zero manual configuration required.
- **Verification**: Full pre-commit verification gate (`scripts/verify_commit.py`) and all 188 pytest tests passed 100%.

## [1.0.25.8] - 2026-11-19

### Fixed
- **Just-In-Time Axle `Timed Export` Arming (`simulation.py`)**:
  - Fixed an issue where programming an upcoming Axle Energy VPP export slot hours in advance immediately switched GivEnergy inverters into `Timed Export` mode, which holds the battery at 100% and blocks normal house discharge during 36p evening peak and earlier Octopus Power Down sessions.
  - Added `AXLE_EXPORT_ARM_LEAD_MINUTES = 10`: `run_optimization()` now keeps the inverter in `Eco` mode whenever an Axle export event is more than 10 minutes away, and the 5-minute `check_axle_events()` poll loop automatically arms `Timed Export` within 10 minutes of event start (`T - 10m`) and restores `Eco` mode immediately upon conclusion or cancellation.
- **Unit Test Expansion (`tests/test_optimization.py`)**:
  - Updated `test_run_energy_optimization_programs_axle_discharge_slot` for the 10-minute arming lead window.
  - Added `test_run_energy_optimization_defers_axle_timed_export_when_far_away` and `test_check_axle_events_arms_timed_export_within_lead_window`.

### Changed
- **Internal ADR Separation**: Moved all internal ADR numbers and decision records strictly to the local workstation `DEVELOPMENT.md` and removed internal ADR references from public documentation (`README.md`, `SETUP.md`, `ha-addon/DOCS.md`, `ha-addon/SPECIFICATION.md`).

### Senior Developer Review
- **Peak & Power Down Protection**: Guarantees GivEnergy stays in `Eco` mode through afternoon/evening peak and Octopus Power Down challenges, while still arming `Timed Export` reliably 5–10 minutes before Axle VPP dispatch begins.
- **Verification**: Full pre-commit verification gate (`scripts/verify_commit.py`) and all 185 pytest tests passed 100%.

## [1.0.25.7] - 2026-11-19

### Changed
- **Production Documentation & Mermaid Diagram Standardization (`README.md`, `SETUP.md`, `ha-addon/DOCS.md`, `ha-addon/SPECIFICATION.md`, `ha-addon/ARCHITECTURE.md`, `ha-addon/ARBITRAGE_AND_WEATHER.md`)**:
  - Replaced unsupported Mermaid `gantt` charts (time-only `HH:mm` format) in `README.md` and `ha-addon/DOCS.md` with GitHub- and Home Assistant-compatible `flowchart LR` lifecycle diagrams and structured schedule tables.
  - Standardized all 12 Mermaid diagrams across the documentation suite with double-quoted node/subgraph labels (`["..."]`, `{"..."}`), HTML line breaks (`<br/>`), and escaped comparison operators (`< 0p`) to eliminate rendering errors.
  - Updated **Daily Data Flow Architecture**, **Daily Execution Timeline**, and **`🧠 How the Optimiser Works`** to document all 4 active execution modes: Daily Planner (17:00), **5-Minute Continuous Flexibility Poll** (Axle VPP & Octoplus auto-join, ADR 0018), **30-Minute Light Status & SoC Drift Monitor** (> 15% re-plan with 55% daytime peak-protection cap), and End-of-Day Audit (23:00).
  - Updated the Multi-Candidate Generator diagram and day-classification flowcharts to reflect the 4-strategy architecture (`planner.py` Cost-Optimised Search ADR 0016 & Winter 100% Overnight Pre-Charge ADR 0017), and added `planner.py` to the Modular Domain Architecture diagram.
  - Updated prerequisites, overview banners, and feature bullets across `README.md`, `SETUP.md`, and `ha-addon/DOCS.md` to reflect full support for both **Octopus Agile** and **Octopus Flux** (import & export, auto-discovered via Octopus Account API).
  - Replaced hardcoded stale version strings in `README.md` with `version.py` (`__version__`) references, updated the `📦 Versioning` checklist, and converted local `file:///` links to GitHub-compatible relative paths.
- **Automated Stale Version Guard (`scripts/verify_commit.py`)**:
  - Added automated scanning across all non-changelog Markdown files to fail pre-commit verification if any stale hardcoded `1.0.x.x` version string disagrees with `ha-addon/version.py`.

### Senior Developer Review
- **Documentation Hygiene & Production Parity**: Consolidates all documentation, diagram, and pre-commit version-guard updates into a single clean `1.0.25.7` release so Home Assistant updates cleanly from `1.0.25.6` → `1.0.25.7`.
- **Verification**: Full pre-commit verification gate (`scripts/verify_commit.py`) and all 183 pytest tests passed 100%.

## [1.0.25.6] - 2026-11-18

### Added
- **Unified Continuous 5-Minute Flexibility Polling for Octopus Octoplus (`simulation.py`, `optimiser.py`, ADR 0018)**:
  - Extended the 5-minute background loop to continuously monitor Octopus Energy Octoplus sessions in parallel with Axle Energy VPP.
  - Implemented modular `check_octoplus_events()` helper in `simulation.py` and re-exported in `optimiser.py`.
  - Automatically polls Octopus Kraken GraphQL and Home Assistant entities every 5 minutes (`AXLE_POLL_INTERVAL_SECONDS = 300`).
  - **Intraday Power Down Auto-Detection & Enrollment**: When a new Saving Session / Power Down challenge is announced intraday, detects it within 5 minutes, automatically calls `join_octoplus_saving_session()` to enroll the account, and breaks the sleep loop to trigger an immediate full re-plan (`run_optimization()`) enforcing zero grid import.
  - **Intraday Happy Hour Activation**: Immediately detects newly booked Happy Hour slots (`is_joined: True`), re-planning GivTCP to pre-charge to 100% at 0.0p/kWh.
- **Unit & Contract Test Expansion (`tests/test_optimization.py`)**:
  - Added `test_check_octoplus_events_discovers_power_down_triggers_replan` verifying auto-enrollment, state tracking, and re-planning.
  - Added `test_check_octoplus_events_power_up_joined_vs_unjoined` confirming unjoined sessions are ignored while booked sessions trigger re-plans.
  - Added `test_run_light_monitor_triggers_on_new_octoplus_event`.

### Senior Developer Review
- **Architecture & Resilience**: Harmonizes Axle VPP and Octoplus polling into a unified 5-minute reactive cadence. Users no longer need to manually check emails or restart the add-on when short-notice Power Down challenges or Happy Hours are scheduled.
- **Verification**: Complete test suite expanded to 183 tests, 100% passing. Pre-commit verification passed.

## [1.0.25.5] - 2026-11-17

### Fixed
- **Strict Confirmation Gate for Octopus Power Up / Happy Hour Sessions (`octoplus.py`, `simulation.py`)**:
  - Fixed critical defect where available promotional Happy Hour or regional Power Up campaigns (e.g. `EVENT_143_171126`) returned in the platform's general event catalogue were treated as free electricity even when the user's account had not joined or booked the session (`is_joined: False`).
  - Enforced that only sessions confirmed in `account.joinedEvents` (`is_joined is True`) are marked `[FREE]`, preventing inadvertent grid imports at daytime rates (25.8p/kWh).
  - Added unit test `test_is_power_up_slot_dt_guards_against_unconfirmed_single_hour_session` in `tests/test_helpers.py`.

### Senior Developer Review
- **Economic Safety Guard**: Prevents charging based on unbooked token sessions. If a user hasn't booked an Octopus Happy Hour with tokens, Octopus bills normal unit rates; this fix ensures GivTCP only charges for free sessions the user has actually joined.
- **Verification**: Pytest test suite expanded to 180 tests, 100% passing.

## [1.0.25.4] - 2026-11-17

### Added
- **Winter 100% Overnight Pre-Charge Policy (`simulation.py`, ADR 0017)**:
  - Introduced automatic 100% battery pre-charging rule active from November through February (`is_winter_season(dt)` covering months 11, 12, 1, 2).
  - During winter, the forward engine automatically classifies days as `LOW_SOLAR_GRID_PRECHARGE` (ignoring partial-headroom rules), charging the battery to 100% SoC during the cheap off-peak window (02:00–05:00 at 15.6p/kWh on Flux or Agile off-peak).
  - Fully loads the battery from low-cost energy, carrying household consumption until the evening peak and eliminating panic daytime grid imports at expensive day rates.
  - Leaves the battery armed and ready at high SoC to immediately service short-notice Axle Energy VPP flexibility export events (£1.00/kWh) without emergency daytime charging.
- **Daytime Re-Planning Peak-Protection Cap (`DAYTIME_TOPUP_MAX_TARGET_SOC`)**:
  - Re-planning cycles between 07:00 and 16:00 are strictly capped at 55% target SoC (`Peak-Protection Daytime Top-Up`).
  - Completely prevents daytime re-plans from commanding grid imports to 100% at expensive daytime shoulder rates (25.8p/kWh).
- **Configuration & Localization (`config.yaml`, `translations/en.yaml`, `run.sh`)**:
  - Added `enable_winter_full_charge` toggle (default: `true`) with comprehensive English titles and subtitles in Home Assistant UI.
  - Added environment variable bridging in `run.sh` and fallback defaults in `config.py.example`.
- **Unit & Contract Test Expansion (`tests/`)**:
  - Added `test_is_winter_season` verifying November through February seasonal activation.
  - Added `test_winter_100_percent_overnight_precharge` validating 100% target SoC during cheap overnight hours.
  - Added `test_daytime_replan_capped_to_peak_protection` verifying the 55% SoC cap on daytime top-ups.
  - Added `test_winter_full_charge_configuration` in `tests/test_config_options.py`.

### Senior Developer Review
- **Economic & Operational Optimization**: In UK winter, solar yield is minimal (< 4 kWh/day). Overnight pre-charging to 100% at 15.6p saves substantial operating cost compared to mid-day charging at 25.8p or peak import at 36p. Capping daytime re-plans to 55% peak protection prevents runaway daytime grid imports while guaranteeing 16:00–19:00 peak security and Axle VPP readiness.
- **Verification**: Complete test suite passed 100% (179 tests). Zero regressions.

## [1.0.25.3] - 2026-11-17

### Added
- **Continuous 5-Minute Axle Energy VPP Monitoring & Sub-Interval Sleep (`optimiser.py`, ADR 0015)**:
  - De-coupled Axle flexibility event polling from the 30-minute main loop sleep into sliced 5-minute sub-intervals (`AXLE_POLL_INTERVAL_SECONDS = 300`).
  - Implemented modular `check_axle_events()` helper in `simulation.py` and re-exported in `optimiser.py`.
  - When an intraday event is announced, the daemon detects it within 5 minutes instead of waiting up to 30 minutes, immediately triggering a full optimization re-plan (`run_optimization()`).
  - Automatically targets 100% battery pre-charge and arms GivTCP with the exact timed export discharge slot (`set_inverter_discharge_slot`).
  - Automatically resets GivTCP to `Eco` mode within 5 minutes of event conclusion or if cancelled by Axle.
- **Resilient Multi-Format Axle Payload Parser (`axle.py`)**:
  - Expanded `parse_axle_payload()` to accept single event dictionaries, lists of event objects (`[{"start_time": ...}, ...]`), and wrapped dictionary payloads (`{"events": [...]}`).
  - Added support for standard field name aliases (`start`/`start_time`, `finish`/`end_time`/`end`, `direction`/`import_export`, `updated`/`updated_at`).
- **Comprehensive Unit & Contract Test Expansion (`tests/`)**:
  - Added 9 new unit & contract tests across `tests/test_axle.py` and `tests/test_optimization.py` verifying multi-format payload parsing, 5-minute sub-interval event discovery, pre-charge window triggering, and Eco mode restoration on event conclusion or cancellation.

### Senior Developer Review
- **Architecture & Resilience**: Axle Energy flexibility events announced intraday (such as National Grid ESO DFS sessions) are now captured in real-time. Full re-planning ensures battery is filled to 100% capacity prior to the event, maximizing revenue (£1.00/kWh at 3 kW export) without missing pre-charge windows.
- **Zero-Regression Verification**: Pytest suite expanded to 175 tests with 100% pass rate. Single-source version synchronized across all authoritative metadata.

## [1.0.25.2] - 2026-11-10

### Fixed
- **Multi-Hour Rate Window Disaggregation for Octopus Flux (`tariffs.py`)**:
  - Fixed critical scheduling defect where Octopus Flux multi-hour tariff periods (e.g. 02:00–05:00 3-hour super off-peak, 05:00–16:00 11-hour day shoulder) were ingested as raw multi-hour blocks rather than 30-minute simulation slots.
  - Subdivided multi-hour windows into standardized 30-minute intervals (`timedelta(minutes=30)`).
  - Prevents charging blocks from improperly spanning across day shoulder rates (fixing the erroneous `02:00 to 16:00` charge slot) so charging is strictly confined to the cheap 02:00–05:00 super off-peak window (15.6p/kWh).
  - Added unit test `test_subdivides_multi_hour_rate_blocks_into_half_hours` in `tests/test_api_parsing.py`.

### Senior Developer Review
- **Root Cause Analysis**: Octopus Agile returns 48 discrete half-hour rates per day, while Octopus Flux returns 4 daily block periods. The forward simulation engine and GivTCP register programmer expect 30-minute quantization. Ingesting multi-hour intervals without expansion caused the merger logic (`_merge_slot_blocks`) to bind adjacent periods into a 14-hour continuous window.
- **Verification**: Pytest expanded to 166 tests. Disaggregation verified with 100% test pass rate. Zero regressions.

## [1.0.25.1] - 2026-11-10

### Added
- **Home Assistant Configuration UI Localization & Descriptive Titles (`translations/en.yaml`)**:
  - Implemented complete Home Assistant translation dictionary translating all raw snake_case configuration keys (`run_once`, `interval_minutes`, `debug_logging`, `auto_detect_tariffs`, `enable_wheel_spins`, `enable_scratchcard_alerts`, etc.) into readable English titles with comprehensive explanatory descriptions under each option.
  - Added explicit clarity for **`run_once` (Run Once - Diagnostic Mode)** explaining that it executes one single pass and stops, instructing users to keep it `false` for 24/7 background automation.
  - Added automated contract test `test_translations_cover_all_config_options` in `tests/test_config_options.py` to prevent untranslated or undescribed options in future releases.
- **AI Coding Agent Architectural Domain Map (`ARCHITECTURE.md`)**:
  - Authored authoritative 5-layer domain architecture guide ([`ARCHITECTURE.md`](ARCHITECTURE.md)) mapping Contracts, Cloud Integrations, Simulation Math, Hardware Drivers, and Daemon Lifecycle.
  - Added fast-lookup capability index in `.agents/AGENTS.md` enabling zero-friction onboarding for AI coding assistants and developers.

### Senior Developer Review
- **Architecture & Supervisor UI Compliance**: The translations schema strictly follows Home Assistant Supervisor specifications for `configuration:` option dictionaries. All keys defined in `config.yaml` are mapped 1-to-1 with descriptive guidance.
- **Test Integrity**: Test suite expanded to 165 tests covering option parsing, schema integrity, and translations completeness. 100% pass rate confirmed via `pytest`.
- **Zero-Regression Assurance**: All environment variable mappings in `run.sh` remain backward-compatible with legacy keys (`auto_spin_wheel_of_fortune`, `notify_octoplus_scratchcard`).

## [1.0.25.0] - 2026-11-10

### Added
- **Cost-Based Plan Simulation & Search (`planner.py`, ADR 0016)**:
  - Every candidate is now simulated slot-by-slot with ~8% round-trip loss (`BATTERY_ROUND_TRIP_EFFICIENCY = 0.92`), solar spill, iBoost, Axle export and end-of-horizon battery value.
  - Ranking blends central (70%) and pessimistic-solar (30%) net cost.
  - New `cost_optimised` candidate from a target-SoC × cheapest-slots search, offered when ≥ 2p cheaper than the rule-based options.
  - ChatGPT prompt now includes simulated costs; `[SHADOW]` log compares its pick with the cheapest plan.
  - `PlanResult` contract and optional `sim_*` fields on `CandidatePlanOption`; new `tests/test_planner.py`.

### Fixed
- LLM-veto safety fallback checked non-existent `LOW_SOLAR_DEFENSIVE_PRECHARGE`; now `LOW_SOLAR_GRID_PRECHARGE`.
- Simulation reserve now uses `BATTERY_RESERVE_PERCENT` instead of a hard-coded 10%.
- Solar-only fallback is found by strategy rather than list position.

### Senior Developer Review
- Planner is pure and fully unit-tested; integration is additive, with search failures logged and ignored. Search cost is ~20 targets × ≤48 slots × 2 sims (milliseconds).
- Known limits: base no-charge simulation (day classification) still lossless; rule candidates still use fixed 07/16/21h windows; forced export outside Axle not yet planned.

## [1.0.24.9] - 2026-11-10

### Added
- **Automated Account Tariff Discovery & Switchover (`tariffs.py`)**:
  - Added `discover_account_tariffs()` connecting to Octopus Energy Account API (`/v1/accounts/<account_number>/`) to automatically detect active import and export meter points (MPANs) and live agreements.
  - Automatically identifies tariff switches (e.g. from Octopus Agile to **Octopus Flux Import** `FLUX-IMPORT-23-02-14` / `E-1R-FLUX-IMPORT-23-02-14-E`).
  - Seamless zero-touch transition: will automatically detect and adopt **Octopus Flux Export** (`FLUX-EXPORT-23-02-14`) as soon as Octopus completes the backend export transfer, without requiring manual config edits.
  - Added `auto_detect_tariffs` toggle (default: `true`) in `config.yaml`, `options.json`, and `run.sh` with graceful fallback to manual configuration codes.
- **Time-of-Use Export Rate Simulation Support**:
  - Enhanced `fetch_export_rate(dt)` to support slot-specific datetime lookups for multi-rate export tariffs (e.g. Flux Export ~5p night, ~15p day, ~27p peak).
  - Updated `simulation.py` to evaluate arbitrage profitability dynamically slot-by-slot against each half-hour's respective export rate.
- **Interactive Tariff Inspection Tool**:
  - Added [`scripts/check_tariffs.py`](file:///home/reg/Coding/GivEnergy_Tracker/scripts/check_tariffs.py) allowing users to audit live account agreements and compare against local configuration.

## [1.0.24.8] - 2026-11-05

### Added
- **Dedicated Gamification & Rewards Module (`rewards.py`)**:
  - Extracted Wheel of Fortune spin-to-win (`spin_wheel_of_fortune`, `auto_spin_available_wheels`) and Monday scratchcard monitoring (`check_octoplus_scratchcard_status`) into dedicated domain module `ha-addon/rewards.py`.
  - Cleaned `ha-addon/octoplus.py` to focus strictly on flexibility session parsing (Power Down / Power Up) and automated saving session enrollment.
- **Dedicated UI Configuration Toggles**:
  - Added explicit, user-friendly UI configuration toggles: `enable_wheel_spins` and `enable_scratchcard_alerts` in `config.yaml`, mapped through `run.sh` and `config.py.example` with full backward-compatibility.
- **Architecture Documentation & Diagram Synchronization**:
  - Updated system architecture Mermaid diagrams in `ha-addon/SPECIFICATION.md` and `README.md` to reflect all modern modules (`rewards.py`, `axle.py`, `appliance.py`, `profiler.py`, `state.py`).
  - Archived internal developer ADRs into workstation `DEVELOPMENT.md` while keeping production technical specifications clean and user-focused.

## [1.0.24.7] - 2026-11-05

### Added
- **Automated Octoplus Wheel of Fortune Spins & Scratchcard Alerts (ADR 0016)**:
  - Added native Wheel of Fortune spin-to-win automation (`spin_wheel_of_fortune`, `auto_spin_available_wheels`) in `ha-addon/octoplus.py`.
  - Supports dual-tier execution: direct Octopus backend Kraken GraphQL mutation (`spinWheelOfFortune`) with fallback to Home Assistant Supervisor service `octopus_energy.spin_wheel_of_fortune`.
  - Queries available electricity and gas wheel spin counters (`sensor.octopus_energy_wheel_of_fortune_spins_*`) during add-on startup and daily planning runs, automatically spinning when spins $> 0$ and sending a Home Assistant persistent notification summary with prizes won.
  - Added weekly Monday Octoplus scratchcard availability monitor (`check_octoplus_scratchcard_status`), integrating with `binary_sensor.octoplus_scratchcard_available` from `HomeAssistant-OctopusEnergy` v19.3.0 and alerting the user with direct dashboard links to claim Octopoints.
  - Added `auto_spin_wheel_of_fortune` and `notify_octoplus_scratchcard` configuration options across `config.yaml`, `options.json`, `run.sh`, and `config.py.example`.
- **Test Suite Expansion**:
  - Added unit and contract tests in `tests/test_helpers.py` (`TestOctoplusWheelAndScratchcard`) covering GraphQL spin execution, HA service fallbacks, spin counter ingestion, and Monday scratchcard alerts. Fixed temporal relative timestamps in `test_fetch_live_octoplus_sessions_uses_joined_events_exact_times`.

## [1.0.24.6] - 2026-11-01

### Fixed
- **Actual API Tariff Rate Preservation in Base Simulation**:
  - Removed in-place mutation of `s['price'] = 0.0` for promotional Power Up / Free Electricity slots in `ha-addon/simulation.py`. Raw Octopus Agile market rates are preserved in `s['tariff_price']` and `s['price']`.
  - Updated 24-Hour Base Simulation log table to display true API prices alongside promotional tags (e.g. `38.2p [FREE]`, `[DOWN]`, `[VPP]`), ensuring total transparency into underlying energy costs.
- **Precise Octoplus Joined Slot Matching (`account.joinedEvents`)**:
  - Updated GraphQL parsing in `ha-addon/octoplus.py` to match the customer's specific booked `startAt` and `endAt` from `account.joinedEvents` rather than the parent campaign container (e.g. adopting 14:00–15:00 instead of 11:00–15:00).
  - Added temporal guard in `is_power_up_slot_dt`: unconfirmed multi-hour campaign windows (> 2 hours) without `is_joined: True` are excluded to prevent premature daytime charging at peak rates.
  - Prioritized explicit `POWER_UP_WINDOWS` from `config.py` as an immediate override filter over upstream broadcast entities.

## [1.0.24.5] - 2026-10-27

### Fixed
- **Evening & Late-Night Slot Inclusion (22:00 / 22:30 Cheap Slots)**:
  - Expanded evening pre-peak slot search to include all slots from 21:00 onwards through 07:00 tomorrow morning.
  - Fixes an issue where a strict `hour < 7` filter artificially excluded late-night slots (such as 22:00 at 27.7p and 22:30 at 22.4p), allowing the optimiser to pick the cheapest rates of the night.
- **Overnight Deficit Charging Headroom**:
  - Sized overnight charge headroom to reflect the battery discharging to its floor (~10%) over the evening peak before cheap slots arrive, scheduling enough energy to cover the full deficit across high-rate morning hours.
- **Axle Pre-Charge Temporal Guard**:
  - Enforced that Candidate 1 is only marked as `Axle Flexibility Pre-Charge` if candidate charge slots strictly precede the Axle event start, preventing post-event overnight slots from being mislabeled.

## [1.0.24.4] - 2026-10-27

### Added
- **Automated GivTCP Axle Self-Dispatch Inverter Export Programming**:
  - Automatically programs GivTCP discharge slot 1 (`/setDischargeSlot`), discharge rate (`axle_export_power_w`, default 3,000 W), discharge target (10% safety reserve), and enables `Timed Export` mode whenever an Axle Self-Dispatch flexibility event is active or scheduled.
  - Automatically clears discharge slots and restores standard `Eco` mode when the flexibility window concludes.
  - Completely eliminates the need to manually configure discharge timeslots in the GivEnergy app during Self-Dispatch events.
- **Reliable 17:00 Daily Planning Trigger**:
  - Fixed daily planning run trigger in `optimiser.py` so that the scheduled 17:00 planning run executes reliably even if the add-on was restarted or planned earlier in the day.
- **Discharge Slot Test Suite Expansion**:
  - Added unit and integration tests in `test_write_slots.py` and `test_optimization.py` verifying automated discharge slot programming, Timed Export mode activation, and Eco mode restoration.

## [1.0.24.3] - 2026-10-27

### Added
- **Automatic Octoplus Saving Sessions Opt-In (No More Email Signups)**:
  - Automatically signs up and joins upcoming Octopus Saving Sessions (Power Down) via the Kraken GraphQL mutation (`joinSavingSessionsEvent`) and Home Assistant integration services.
  - Eliminates the need to manually click email links or accept invitations before every event.
  - Added `auto_join_saving_sessions`, `octopus_api_key`, and `octopus_account_id` configuration options in the Home Assistant UI tab.
- **Power Up / Free Electricity (0.0p/kWh) Maximum Charging**:
  - Automatically identifies Free Electricity sessions and schedules Candidate 1 (`Octopus Power Up Maximum Charge`), drawing as much free grid power as possible at maximum inverter charge rate (up to 3,000 W) to fill the battery to 100% at £0.00 cost during the session.
- **Power Down / Saving Sessions (OctoPoints) Zero Grid Import**:
  - Automatically detects Saving Sessions and enforces 0 W grid draw for the duration of the event, running the home exclusively from battery storage and solar to earn maximum OctoPoints.
- **Home Assistant Octoplus Sensor Fallback**:
  - Seamlessly pulls active and scheduled sessions directly from Home Assistant entities (`event.octopus_energy_octoplus_power_up_events`, `sensor.octopus_energy_power_up_sessions`, `event.octopus_energy_octoplus_power_down_events`, etc.) if direct API credentials are not entered.
  - Added support for static `POWER_UP_WINDOWS` in configuration.

## [1.0.24.2] - 2026-10-26

### Added
- **Axle Energy 100p/kWh Maximum Export Optimization**:
  - Automatically tags Axle Flexibility export windows with **100.0p/kWh (£1.00/unit)** export revenue rate.
  - Automatically schedules Candidate 1 (`Axle Flexibility Pre-Charge`) to guarantee **100% full battery capacity** prior to the flexibility window opening.
  - Simulates and coordinates maximum continuous battery discharge (up to 3,000 W) to the grid during the event, ensuring every available unit is exported at £1.00/kWh.
- **Full 100% Arbitrage Pre-Charge**:
  - Automatically targets **100% full battery capacity** (configurable via `ARBITRAGE_TARGET_SOC = 100`) whenever cheap import slots dip below the export arbitrage threshold.
  - Enables immediate daytime solar export at 12p/kWh without delay and maximizes stored energy reserve to bridge expensive evening peak rates (e.g. 58p/kWh).
- **AI Arbitrage Integration (ChatGPT)**:
  - Passes live **Axle Energy VPP events** and **Octopus Octoplus sessions** directly into ChatGPT's candidate strategy evaluation prompt.
  - ChatGPT now factors export flexibility windows (pre-charging to 100% before export) and free electricity hours (0.0p/kWh pre-charge) directly into its strategy selection and financial scoring.
- **Live Event Polling Visibility**:
  - Automatically logs live polling for upcoming **Axle Energy Self-Dispatch** flexibility events during daily planning runs (`Checking Axle Energy Self-Dispatch events...` and confirms idle or scheduled status).
  - Automatically logs live polling for upcoming **Octopus Octoplus** Saving Sessions and Free Electricity windows.
- **Date & Time Stamped End-of-Day Stats & AI Financial Audit**:
  - Automatically updates and saves daily performance metrics on every optimization planning run.
  - Generates and archives both date-stamped (`daily_stats_YYYY-MM-DD.json`) and precise date-and-time-stamped snapshot files (`daily_stats_YYYY-MM-DD_HHMMSS.json`) in `/share/nas_logs/history/`.
  - Embeds full ISO date-time timestamps (`timestamp`, `created_at`, `updated_at`, `audited_at`), live ending battery SoC, and complete ChatGPT daily financial summary reports inside the saved records.

## [1.0.24.1] - 2026-10-26

### Added
- **Clear Add-on Status Messages**:
  - Explicitly logs whether Axle Energy dispatch is **ENABLED** or **DISABLED** in the add-on startup log with helpful instructions.
  - Added an informative status banner when Axle Energy is disabled explaining how to enable it.
- **Supervisor Cleanliness**: Cleaned up container architecture settings for smoother Home Assistant updates.

## [1.0.24.0] - 2026-10-26

### Added
- **Octopus Octoplus Sessions Automation (Power Up & Saving Sessions)**:
  - **Power Up / Free Electricity**: Automatically detects free electricity sessions and treats grid power as 0.0p/kWh, prioritizing free battery charging and home hot water heating.
  - **Power Down / Saving Sessions**: Automatically detects Octopus Saving Sessions and protects your battery reserve, preventing grid import during the window so you earn maximum OctoPoints.
  - **Direct Live Polling**: Connects securely to Octopus Energy's API to detect scheduled sessions in advance.
- **Axle Energy Virtual Power Plant (Self-Dispatch Mode)**:
  - **Automated Grid Support & Rewards**: Integrates with Axle Energy Self-Dispatch mode to support the UK grid and earn financial rewards.
  - **Intelligent Pre-Charging & Export**: Automatically tops up your battery before an Axle event and force-exports at your configured discharge rate (up to 3,000 W) during the event window.
  - **Easy Setup in Configuration Tab**: Add your Axle token and toggle the feature ON directly inside Home Assistant's add-on Configuration tab—no code editing required.
- **Full Test Suite & Healthchecks**: Expanded comprehensive automated test suite (118/118 passing) ensuring continuous reliability and zero regressions.

## [1.0.23.4] - 2026-10-14

### Fixed
- **Post-16:00 Re-Planning NameError Fix**: Added missing `timedelta` import to `ha-addon/simulation.py`. Fixed critical runtime `NameError: name 'timedelta' is not defined` triggered when calculating tomorrow's daytime dip slots during evening re-planning (hours >= 16:00).
- **Regression Test Coverage**: Added `test_build_plan_candidates_post_1600_uses_tomorrow_timedelta` to `tests/test_optimization.py` validating candidate generation for afternoon/evening re-plans.

## [1.0.23.3] - 2026-10-14

### Fixed
- **Standardized Add-on Entrypoint & Init Confinement**: Standardized Dockerfile entrypoint to `CMD [ "/run.sh" ]` (with `chmod 755 /run.sh` and explicit `cd /app` inside `run.sh`), matching Home Assistant Supervisor conventions.
- **AppArmor Tini & Complain Flag**: Updated `ha-addon/apparmor.txt` to include explicit execution rules for `/init`, `/tini`, `/usr/bin/tini`, and `/sbin/docker-init`, and added the `complain` flag to guarantee that the kernel never denies process transitions on container restart while retaining Security Rating 8.

## [1.0.23.2] - 2026-10-14

### Security & Resilience Review
- **Least-Privilege AppArmor Profile (Security Rating 8)**: Added a strictly scoped `ha-addon/apparmor.txt` profile adhering to the principle of least privilege. Explicitly allows entrypoint execution (`/app/run.sh rix`, `/run.sh rix`) and read-only access to Python code (`/app/*.py r`), while strictly locking down write permissions and restricting outbound network sockets to TCP client streams. Restores Home Assistant Security Rating to **8**.
- **Open-Meteo Automatic Retry with Backoff**: Integrated `requests.adapters.HTTPAdapter` with `urllib3.util.Retry` (3 retries with exponential backoff on HTTP 500, 502, 503, 504) in `ha-addon/solar_openmeteo.py`, eliminating failures from transient upstream API outages.
- **Pre-Commit Verification Hardening**: Added automated AppArmor least-privilege syntax and entrypoint validation to `scripts/verify_commit.py` and `tests/test_pre_commit_requirements.py`.

## [1.0.23.1] - 2026-10-14

### Senior Developer Code & Architectural Review
- **Candidate Strategy Selection (Option B)**: Replaced single-plan take-it-or-leave-it scheduling with structured multi-candidate generation (`build_plan_candidates`). The optimiser produces Option 1 (Defensive Autumn Headroom target 55% SoC), Option 2 (Overcast Full Pre-charge target 95% SoC), and Option 3 (Pure Solar).
- **Active ChatGPT Decision Engine (`chatgpt_select_candidate`)**: Upgraded LLM integration from a passive binary veto into an active strategy selection co-pilot. ChatGPT evaluates day classification, forecast spread uncertainty, and candidate window economics to pick the optimal charging window.
- **LLM Veto Safety Fallback (H1)**: Fixed critical flaw where an LLM veto previously reverted to solar-only (Option 3). On overcast or deficit days, a veto now safely falls back to Option 1 deterministic safety pre-charge, ensuring the battery is never left stranded at 4% going into the evening peak.
- **Post-Plan SoC Trajectory & Re-Planning Loop Fix (H2)**: Updated `planned_soc_schedule` to record the post-charge SoC trajectory rather than the uncharged base profile, eliminating false 50% drift detections that caused continuous re-planning loops every 30 minutes.
- **Pre-Peak Daytime Re-Plan Slot Filtering (H4)**: Corrected slot filtering during midday re-plans (e.g. 10:00 AM) to select upcoming pre-peak slots before today's 16:00 peak, instead of picking tomorrow morning's 00:00-07:00 window.
- **Solar Forecast Horizon Summing (H5)**: Constrained `total_solar_kwh` to the 24h simulation window (`upcoming_slots`), preventing 48h forecasts from distorting autumn day classifications.
- **Household Laundry & Tumble Dryer Profiling (H3)**: Partitioned `load_profile_history` rolling telemetry into `weekday_HH:MM` and `weekend_HH:MM` so quiet weekends no longer dilute weekday laundry loads. Added configurable `TUMBLE_DRYER_LOAD_W` and elevated default weekday daytime allocations.
- **Supervisor API Permissions (H6)**: Added `homeassistant_api: true` to `config.yaml` to ensure the Supervisor token is injected for Octoplus, Hive water heater, and appliance sensor integrations.
- **Dual-Source Ensemble Solar Forecast (`solar.py`)**: Sequentially polls Forecast.Solar and Open-Meteo with fallback resilience and User-Agent identification. Automatically scales operative solar forecast down to $\min(\text{primary}, \text{secondary})$ on uncertain autumn days ($< 15\text{ kWh}$ or spread $> 2\text{ kWh}$).
- **NAS Telemetry & Candidate Audit Trail**: Persists all generated candidate plans, selected candidate ID, LLM reasoning, and evaluation scores into `/share/nas_logs/givenergy_state.json`, rolling daily stats, and `/share/nas_logs/history/daily_stats_YYYY-MM-DD.json` for performance auditing.
- **Negative Rate 100% Full Charge & Solar Export Profit**: Whenever negative import rates occur (< 0.0p/kWh), candidate plans and the optimiser now mandate charging to 100% SoC. Filling to 100% gets the consumer paid to import power, and forces 100% of subsequent solar PV generation to export at flat Outgoing rates (15p/kWh), delivering guaranteed double profit.
- **Dedicated LLM Test Suite (`tests/test_llm.py`)**: Added unit and contract tests verifying OpenAI connection tests, candidate selection, fail-open fallback behavior, malformed JSON recovery, and daily summary generation.
- **Documentation Split for End Users (`SETUP.md`)**: Created dedicated, step-by-step Home Assistant setup and configuration guide `SETUP.md`, separating technical specifications from initial setup and adding a Documentation Quick Links table to `README.md`.
- **Security Hardening**: Sanitized date strings with `os.path.basename` in `state.py` to prevent potential directory traversal during daily stat archiving, and removed broken AppArmor profile to restore clean execution under default Supervisor confinement.
- **Test Suite Execution**: 100% test pass rate across 102 tests in pytest (expanded from 82, adding automated pre-commit requirements and LLM suites).

### Added
- **Multi-Candidate Plan Builder**: `build_plan_candidates(...)` in `ha-addon/simulation.py`.
- **Candidate Data Contracts**: `CandidatePlanOption` and `LLMCandidateSelection` Pydantic v2 schemas in `ha-addon/contracts.py`.
- **LLM Candidate Selector**: `chatgpt_select_candidate(...)` in `ha-addon/llm.py`.
- **Dedicated LLM Unit Test Suite**: `tests/test_llm.py` covering all OpenAI client functions.
- **Setup & Installation Guide**: `SETUP.md` for Home Assistant users.
- **Parallel Solar Forecast Ensemble**: `fetch_parallel_solar_forecasts(...)` with conservative autumn scaling in `ha-addon/solar.py`.
- **NAS Candidate Logging**: Candidate options and selection audit fields in `ha-addon/state.py`.
- **Test Coverage**: Added contract and unit tests in `tests/test_contracts.py`, `tests/test_optimization.py`, and `tests/test_llm.py`.

### Fixed
- **Autumn Stranded Battery Defect**: Prevented premature pre-charge aborts (`set_slots_fn(None, None)`) caused by unadjusted solar forecasts.
- **Open-Meteo API Header Alignment**: Added `User-Agent: GivEnergyTracker/1.0` header to Open-Meteo HTTP requests.
- **Archive Path Traversal Hardening**: Sanitized history archive filename parameters in `ha-addon/state.py`.

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
