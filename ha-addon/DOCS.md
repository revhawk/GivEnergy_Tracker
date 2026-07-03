# GivEnergy Tariff Optimiser — Add-on Documentation

## Overview

This add-on connects to your GivEnergy battery inverter via GivTCP and manages grid-charging around the Octopus Agile (import) and Outgoing Variable (export) tariffs. It:

- Schedules the cheapest overnight charge window when your battery needs to be topped up
- Opportunistically charges the battery from **any** Agile slot cheaper than your export rate (arbitrage), so imported cheap power lets more of your solar go to grid at the higher export price
- Uses ChatGPT as an independent second opinion — it rates every plan out of 10 and can veto a poor charge decision
- Produces a plain-English end-of-day audit summarising the day's decisions and estimated savings

Since v1.0.3 the add-on runs **one planning pass per day** (not every 30 minutes) — the rest of the day is a lightweight status check. This keeps API usage and inverter traffic minimal.

---

## Options

Configure these under the add-on's **Configuration** tab.

### `interval_minutes` *(int, default: 30)*

How often the daemon wakes up. On each tick it decides whether to run the daily planner, the end-of-day audit, or a light monitor read.

**Recommended:** `30` — matches Agile slot granularity.

### `run_once` *(bool, default: false)*

Runs a single planning pass then exits. Useful for testing or triggering from a Home Assistant automation.

### `openai_api_key` *(string, default: "")*

Your OpenAI API key for the ChatGPT plan-scoring and end-of-day audit features. Leave blank to disable — the deterministic planner works perfectly without it.

> ⚠️ **Storage note:** Home Assistant stores this in `/data/options.json` on the host filesystem in **plaintext** (it is *not* encrypted, contrary to what earlier versions of this document implied). Anyone with SSH/terminal access to the host can read it. This is a Home Assistant limitation, not an add-on choice — treat the key accordingly.

### `daily_plan_hour` *(int 0–23, default: 17)*

The hour of local time at which the tracker runs its daily planning pass. Tomorrow's Agile rates typically publish between 16:00 and 20:00, so `17` is a sensible default. If the plan hour hasn't yet been crossed on a fresh day, the tracker will still plan on startup with whatever rates are currently available.

### `daily_audit_hour` *(int 0–23, default: 23)*

The hour at which the end-of-day audit fires. It re-reads the day's plan snapshot and, if the OpenAI key is set, produces an English-language verdict.

---

## How It Works

### The three modes

The daemon wakes every `interval_minutes` and picks one of three modes based on time-of-day and stored state:

#### 1. Daily planner (fires once per day)

Runs on startup and on the first tick after `daily_plan_hour` each new day. This is the only mode that does the full optimisation:

1. **Fetch Octopus Agile rates** for all upcoming half-hour slots (the public product endpoint — no auth needed).
2. **Fetch the current Octopus Outgoing Variable export rate** (cached 6 hours).
3. **Fetch solar forecast** from Forecast.Solar for your latitude/longitude/panel geometry.
4. **Read current battery SoC** from GivTCP (with direct Modbus TCP as fallback).
5. **Simulate 24 hours** of battery/solar/home-load evolution without any grid charge, using `BASE_LOAD_W` as the continuous baseline.
6. **Decide the action:**
   - **Deficit charge**: if the simulation predicts >0.2 kWh of grid import, schedule the cheapest contiguous Agile window that covers it (plus 10% margin).
   - **Arbitrage charge**: if any upcoming slot is priced below `(export_rate − ARBITRAGE_MARGIN_P)`, fill available battery capacity from that window — even if solar could have covered demand. Rationale: importing at 5p and letting more solar export at 12p yields ~7p/kWh profit after round-trip losses.
   - **Negative-rate override**: if any slot has a negative price (grid pays you), fill the battery aggressively.
   - **No charge**: if none of the above are worthwhile, clear all charge slots.
7. **LLM veto**: send the plan to ChatGPT in a structured JSON prompt. It returns `approve` (bool), `score` (1–10), and `reason`. If it disapproves a charge plan, slots are cleared as a fallback. The LLM fails-open — a timeout or bad response defaults to approving the deterministic plan.
8. **Write to inverter**: via GivTCP REST (`setChargeEnable`, `setChargeTarget`, `setChargeSlot1`, `setChargeSlot2`), splitting the window across two inverter slots if it crosses midnight.
9. **Persist snapshot**: the full plan (window, kWh, rates, LLM verdict) is written to `/share/nas_logs/givenergy_state.json` for the audit to read later.

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

# Export tariff (live-fetched, but with a fallback)
EXPORT_PRODUCT_CODE     = "OUTGOING-VAR-24-10-26"
EXPORT_TARIFF_CODE      = "E-1R-OUTGOING-VAR-24-10-26-E"
EXPORT_RATE_P_FALLBACK  = 12.0
ARBITRAGE_MARGIN_P      = 0.5   # Import must be below (export - margin) to arbitrage
```

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

## Support

- Issues: please include the last ~50 lines of the addon log when reporting bugs.
- The tracker logs are safe to paste publicly (no secrets are written) — but double-check before sharing.
