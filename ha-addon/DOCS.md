# GivEnergy Tariff Optimiser — Add-on Documentation

## Overview

This add-on connects to your GivEnergy battery inverter via GivTCP and manages grid-charging around the Octopus Agile (import) and Outgoing Variable (export) tariffs. It:

- Schedules the cheapest overnight charge window when your battery needs to be topped up
- Opportunistically charges the battery from **any** Agile slot cheaper than your export rate (arbitrage), so imported cheap power lets more of your solar go to grid at the higher export price
- Uses ChatGPT as an independent second opinion — it rates every plan out of 10 and can veto a poor charge decision
- Produces a plain-English end-of-day audit summarising the day's decisions and estimated savings

Since v1.0.3 the add-on runs **one planning pass per day** (not every 30 minutes) — the rest of the day is a lightweight status check. This keeps API usage and inverter traffic minimal.

---

## 📥 Installation & Repository Setup

### Option 1: 1-Click Add-on Store Setup

Click the button below to add this repository directly to your Home Assistant Add-on Store:

[![Open your Home Assistant instance and show the add-on store with a specific repository filled in.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frevhawk%2FGivEnergy_Tracker)

### Option 2: Manual Add-on Store Setup

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click **⋮ (three dots) → Repositories** (top right).
3. Paste the repository URL:
   ```text
   https://github.com/revhawk/GivEnergy_Tracker
   ```
4. Click **Add**, then select **GivEnergy Tariff Optimiser** from the store list to install.

---

## Version Control Checklist

When incrementing the version of this add-on for a release, the version number **must** be updated in `ha-addon/version.py` and `ha-addon/config.yaml` at the same time:

1. **`ha-addon/version.py`**: Single source of truth for python code (`__version__ = "1.0.20"`).
   ```python
   __version__ = "1.0.20"
   ```
2. **`ha-addon/config.yaml`**: The `version:` field must match the target release (used by Home Assistant Add-on Store to detect updates).
   ```yaml
   version: "1.0.20"
   ```
3. **`ha-addon/CHANGELOG.md`**: Add the release header and list all notable changes.
   ```markdown
   ## [1.0.20] - 2026-09-01
   ```

*Note: Home Assistant validates `config.yaml` and `optimiser.py` versions on startup. If they do not match, the add-on will log a warning on startup.*

---

## ⏰ Daily Schedule & Timeline

Here is what happens throughout the day automatically once the add-on is installed:

```mermaid
gantt
    title Daily Optimiser Execution Timeline
    dateFormat  HH:mm
    axisFormat %H:%M

    section Octopus Tariff
    Tomorrow's Rates Published (16:00-16:30) :milestone, m1, 16:30, 0min

    section Daemon Tasks
    Daily Planning Run (Fetch rates, solar & program inverter) :active, p1, 17:00, 30min
    Light Monitor Checks (Periodically verify battery SoC) :m2, 17:30, 5h
    End-of-Day Audit (ChatGPT Financial Savings Report) :crit, a1, 23:00, 30min
    Inverter Executes Scheduled Overnight Charging :done, c1, 02:00, 3h
```

---

## ⚙️ Configuration Options Guide (First-Time User)

Configure these settings under the add-on's **Configuration** tab in Home Assistant.

### Quick Reference Table

| Option | Default | Required? | First-Time User Guidance |
| :--- | :---: | :---: | :--- |
| **`interval_minutes`** | `30` | Yes | **Leave as `30`**. Controls how often the add-on checks status. Matches Octopus 30-minute pricing slots. |
| **`run_once`** | `false` | No | **Leave as `false`**. When `true`, runs 1 test pass then stops. Keep `false` for 24/7 automatic operation. |
| **`openai_api_key`** | `""` | Optional | **OpenAI API Key** (`sk-...`). Enables ChatGPT plan scoring & daily audit report. Optional — planner works 100% without it. |
| **`openai_model`** | `gpt-4o-mini` | Optional | **AI Model Selection**. Default (`gpt-4o-mini`) is fast, highly accurate, and costs less than 1p/month. |
| **`daily_plan_hour`** | `17` | Yes | **Daily Planning Hour (5:00 PM)**. Octopus publishes rates at 16:30, so 17:00 programs your inverter 7 hours before night charging. |
| **`daily_audit_hour`** | `23` | Yes | **Daily Audit Hour (11:00 PM)**. Summarizes today's actual performance and financial savings right before midnight. |

---

### Detailed Option Explanations

#### `interval_minutes` *(int, default: 30)*
- **What it does:** Sets how often the background daemon wakes up to check battery status.
- **Why it exists:** Aligns with Octopus Agile's 30-minute electricity pricing windows.
- **First-Time User Advice:** Do not change this unless requested during troubleshooting.

#### `run_once` *(bool, default: false)*
- **What it does:** Executes a single optimization run on container start and immediately exits.
- **Why it exists:** Designed for developer testing or Home Assistant automation triggers.
- **First-Time User Advice:** Ensure this toggle is **OFF** (`false`) so the add-on runs 24/7 in the background.

#### `openai_api_key` *(string, default: "")*
- **What it does:** Your private secret key from OpenAI (`sk-proj-...`).
- **Why it exists:** Unlocks ChatGPT AI plan evaluation and English daily financial summary reports.
- **First-Time User Advice:** Leave blank if you don't use OpenAI. The deterministic optimization engine works 100% standalone without an API key.

#### `openai_model` *(radio selection, default: "gpt-4o-mini")*
- **What it does:** Selects which OpenAI model handles plan evaluation.
- **Choices:** `gpt-4o-mini` (recommended, cheap & fast), `gpt-4o` (flagship model), `gpt-3.5-turbo` (legacy model).
- **First-Time User Advice:** Select `gpt-4o-mini`.

#### `daily_plan_hour` *(int 0–23, default: 17)*
- **What it does:** The hour (0 to 23) when tomorrow's Agile charging plan is calculated and sent to your inverter.
- **Why default is 17 (5:00 PM):** Octopus publishes tomorrow's 48 half-hour rates between 16:00 and 16:30 every afternoon. Planning at 17:00 programs your inverter 7+ hours before overnight charging begins.

#### `daily_audit_hour` *(int 0–23, default: 23)*
- **What it does:** The hour (11:00 PM) when the end-of-day audit fires to generate your daily financial savings report.
- **First-Time User Advice:** Leave as `23`.

---

## How It Works

### The three modes

The daemon wakes every `interval_minutes` and picks one of three modes based on time-of-day and stored state:

#### 1. Daily planner (fires once per day at 17:00)

Runs on startup and on the first tick after `daily_plan_hour` each new day. This is the only mode that does the full optimisation:

1. **Fetch Octopus Agile rates** for all upcoming half-hour slots (the public product endpoint — no auth needed).
2. **Fetch the current Octopus Outgoing Variable export rate** (cached 6 hours).
3. **Fetch solar forecast** from Forecast.Solar (primary) and Open-Meteo (shadow parallel provider).
4. **Read current battery SoC** from GivTCP (with direct Modbus TCP as fallback).
5. **Simulate 24 hours** of battery/solar/home-load evolution using dynamic load profiling (rolling half-hour telemetry history & hourly baseline profiles: overnight 400 W, daytime 700 W, evening peak 1200 W).
6. **Decide the action:**
   - **Deficit charge**: if the simulation predicts grid import is required, schedule the cheapest Agile slot combination that covers it.
   - **Power Down session pre-charge**: if an Octoplus Power Down session is active (e.g. 18:00-19:00), enforce 0.0 kWh grid import and pre-charge battery at cheaper prior rates.
   - **Arbitrage charge**: if any upcoming slot is priced below `(export_rate × 0.90 - ARBITRAGE_MARGIN_P)`, fill available battery capacity from that window.
   - **Negative-rate override**: if any slot has a negative price (grid pays you), fill the battery aggressively.
   - **No charge**: if none of the above are worthwhile, clear all charge slots (set Eco mode).
7. **LLM veto**: send the plan to ChatGPT in a structured JSON prompt. It returns `approve` (bool), `score` (1–10), and `reason`. If it disapproves a charge plan, slots are cleared as a fallback. The LLM fails-open — a timeout or bad response defaults to approving the deterministic plan.
8. **Write to inverter**: via GivTCP REST v2/v3 (`/setChargeSlot`, `/setChargeTarget`, `/setBatteryMode`), programming up to 10 slots and setting Timed Demand mode.
9. **Persist snapshot**: the full plan (window, kWh, rates, LLM verdict) is written to state for the audit to read later.

Total external calls per planning run: ~2 Octopus, 1 forecast.solar, 1 OpenAI. Under 5 seconds end-to-end.

#### 2. Light monitor (fires every other tick)

- Reads current SoC from GivTCP
- Logs one line: `Battery SoC: XX%`
- No LLM, no re-planning, no inverter writes.

This is the boring, safe default that runs 46 times a day. It doesn't interfere with the plan already programmed into the inverter.

#### 3. End-of-day audit (fires once per day)

Runs on the first tick after `daily_audit_hour`:

- Reloads the day's plan snapshot from state.
- Reads daily statistics (charge windows scheduled, rates seen, SoC changes).
- Sends the day's data to ChatGPT for an English-language verdict: what worked, estimated savings vs peak-rate baseline, suggestions for algorithm tuning.
- Logs the report to file.

---

## The Algorithm's Economic Model

- **Import cost** = charge_kwh × avg_slot_price
- **Export income** = 12p/kWh (currently — fetched live at each planning run)
- **Round-trip battery efficiency** ≈ 90% (built into the arbitrage margin)
- **Profit break-even** for arbitrage: `import_price < 12p × 0.90 ≈ 10.8p`
- **Arbitrage margin** (`ARBITRAGE_MARGIN_P`, default 0.5p): reduces the threshold to `11.5p` and stops the tracker chasing marginal opportunities.

If your export rate changes (Octopus updates the Outgoing Variable tariff), the tracker will pick it up automatically at the next planning run — no config edit required. The cached rate refreshes every 6 hours.

---

## Config file (`config.py`) — key knobs

Edit `ha-addon/config.py` and **Rebuild** the add-on (not just Restart — `config.py` is baked into the image at build time).

```python
# Home load baseline
BASE_LOAD_W = 1000   # Continuous home load in Watts. Set from your true overnight
                     # draw — under-estimating causes the tracker to schedule too
                     # little grid charge.

# Export tariff (Octopus Outgoing Variable — verify via account API if it changes)
EXPORT_PRODUCT_CODE     = "OUTGOING-VAR-24-10-26"
EXPORT_TARIFF_CODE      = "E-1R-OUTGOING-VAR-24-10-26-E"
EXPORT_RATE_P_FALLBACK  = 12.0
ARBITRAGE_MARGIN_P      = 0.5   # Import must be below (export - margin) to arbitrage

# Octopus Octoplus sessions (ADR 0004 compliance)
OCTOPLUS_POWER_DOWN_ENTITY = "sensor.octopus_energy_power_down_sessions"
OCTOPLUS_POWER_UP_ENTITY   = "sensor.octopus_energy_power_up_sessions"
```

---

## Octoplus Session Integration (ADR 0004)

Per `HomeAssistant-OctopusEnergy` ADR 0004:
- **Saving Sessions** are renamed to **Power Down** (`sensor.octopus_energy_power_down_sessions`, `event.octopus_energy_octoplus_power_down_events`, `calendar.octopus_energy_octoplus_power_down_sessions`).
- **Free Electricity Sessions** are renamed to **Power Up** (`sensor.octopus_energy_power_up_sessions`, `event.octopus_energy_octoplus_power_up_events`, `calendar.octopus_energy_octoplus_power_up_sessions`).

The add-on resolves entity names via `get_octoplus_entity_name()` and `parse_octoplus_session()`, providing backwards compatibility for legacy `saving_sessions` and `free_electricity_sessions` entities until January 2027.

### Multi-Slot & Non-Contiguous Optimisation

When multiple cheap sessions or Agile slots exist across the day:
1. **Opportunistic / Arbitrage & Power Up (Free Electricity)**: Sub-threshold and free slots are merged into blocks and sorted by average price, programming up to the 10 cheapest charge blocks into GivTCP.
2. **Deficit Charging**: The tracker evaluates contiguous charge windows alongside the cheapest $N$ non-contiguous slots, selecting non-contiguous blocks whenever they yield a lower overall cost.

---

## Log locations

The add-on writes to two places:

- **HA add-on Log tab** — real-time, last few hundred lines.
- **`/share/nas_logs/givenergy_tracker.log`** — rotating file, 5 MB × 3 backups, only present if Home Assistant Network Storage is mounted at `/share/nas_logs/`. See below.
- **`/share/nas_logs/givenergy_state.json`** — the day's plan snapshot (audit reads this).
- **`/share/nas_logs/givenergy_daily_stats.json`** — rolling daily statistics.

### Enabling NAS-backed logs

The add-on writes to `/share/nas_logs/` which is only available if you've added a Network Storage entry named `nas_logs` in **Settings → System → Storage**. If you haven't, logs stay in the add-on container (visible in the Log tab) but no persistent file is written — that's fine.

---

## Troubleshooting

### `Load 0.15` in every simulation row

This is a symptom of the pre-1.0.3 default (`BASE_LOAD_W = 300`). If you see it after installing 1.0.3, the container is running a stale image — **Rebuild** the add-on (Configuration tab → ⋮ menu → Rebuild), don't just Restart. `config.py` is copied into the image at build time.

### Startup config banner is missing

Same issue — you're running a pre-1.0.3 image. Rebuild.

### GivTCP connection errors

The add-on tries `GIVTCP_URL` in `config.py` first. If unreachable, it falls back to direct Modbus TCP at `INVERTER_IP:INVERTER_PORT`. Update `GIVTCP_URL` in `config.py` to your GivTCP container's IP.

### Forecast.Solar 429 responses

The free tier is 12 calls/hour per IP. Since 1.0.3 the tracker only calls forecast.solar during the daily planner (once per day), so this should no longer be an issue.

### No charge slots today

Two common causes:

1. **Today has no arbitrage-worthy slots** — every Agile rate is above `(export_rate − margin)`, so no import is cheaper than what you'd get by exporting solar. The tracker correctly does nothing.
2. **Solar forecast covers home load** — with `BASE_LOAD_W` set correctly, the tracker still won't schedule a charge if solar alone can meet the day's demand plus fill the battery.

Look at the log for `Total Grid Import Needed` and the arbitrage section. If `Total Grid Import Needed` is nonzero but no slot appears, check for an `LLM VETOED` line — the model may have overridden the plan.

### ChatGPT audit is not appearing

- Check `openai_api_key` is set in the Configuration tab
- Check your OpenAI account has active credit
- Check that the startup banner shows `✓ OpenAI API connected successfully`

---

## Local Development & Testing

To set up a local testing environment in `.venv` and execute unit tests:

```bash
# 1. Create & activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install requirements for both add-on and test suite
pip install -r ha-addon/requirements.txt -r tests/requirements.txt

# 3. Run unit test suite
pytest -v
```

---

## Support

- Issues: please include the last ~50 lines of the addon log when reporting bugs.
- The tracker logs are safe to paste publicly (no secrets are written) — but double-check before sharing.
