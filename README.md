# ⚡ GivEnergy Tariff Optimiser

A Home Assistant add-on that automatically schedules your GivEnergy battery to charge during the cheapest Octopus Agile half-hour slots, taking into account your solar forecast and current battery state of charge.

> **Designed for:** Octopus Agile tariff customers with GivEnergy inverters and solar panels running Home Assistant.

---

## ✨ Features

- 🔋 **Reads live battery SoC** via GivTCP REST API (falls back to direct Modbus TCP)
- ☀️ **Solar forecast integration** via [Forecast.Solar](https://forecast.solar) free API
- ⚡ **Octopus Agile rate fetching** — pulls all upcoming 30-minute pricing slots
- 💱 **Live Octopus export rate** — fetches the current Outgoing Variable rate so arbitrage decisions self-adjust when Octopus changes their tariff
- 📊 **24-hour simulation** — models battery/solar/load to estimate true import need
- 🎯 **Arbitrage-aware optimiser** — charges from grid whenever import < export rate (after ~90% round-trip efficiency), even when solar could cover demand
- 🧠 **Sliding window search** — finds the cheapest contiguous charge window
- 🤖 **ChatGPT plan validator** — scores each plan 1-10 and can veto poor charge decisions; produces an English-language end-of-day audit
- 📅 **Once-per-day planning** — daily planner + light monitor + audit split cuts API calls from ~192/day to ~2/day
- 📋 **NAS log rotation** — rotates logs automatically (5 MB max, 3 backups)
- 🔁 **Configurable schedule** — set the daily plan and audit hours from HA UI
- 🏠 **Native Home Assistant add-on** — installs directly from a local repository

---

## 🏗️ Architecture

```
GivEnergy_Tracker/
├── ha-addon/                      ← The Home Assistant add-on package
│   ├── config.yaml                ← Add-on manifest (name, version, options)
│   ├── Dockerfile                 ← Builds the add-on container (python:3.11-slim)
│   ├── run.sh                     ← Entrypoint: reads HA options, launches optimiser
│   ├── config.py.example          ← Template — copy to config.py and fill in
│   ├── config.py                  ← Gitignored; your real credentials & settings
│   ├── optimiser.py               ← Core optimization engine
│   ├── tracker.py                 ← Lightweight Octopus API connection test
│   ├── requirements.txt           ← Python dependencies
│   └── DOCS.md                    ← Add-on user documentation
├── tests/                         ← Test suite (see Development)
├── README.md
├── CHANGELOG.md
├── LICENSE
└── .gitignore                     ← Excludes ha-addon/config.py and _legacy/
```

### Data Flow

```
                            Once per day (daily planner @ 17:00)
Octopus Agile rates   ──┐
Octopus Export rate   ──┤
Forecast.Solar        ──┼──► optimiser.py ─┬─► Deterministic Plan  ─┐
GivTCP (SoC)          ──┤     Simulation   │   (arbitrage + deficit)│
Persisted state       ──┘                  └─► ChatGPT Veto (score) ┘
                                                    │
                                            ┌───────┴─────────┐
                                     approve│                 │veto
                                            ▼                 ▼
                                     GivTCP REST         Clear slots
                                     (write plan)             │
                                            │                 │
                                            └────────┬────────┘
                                                     ▼
                                          state.json + rotating log
                                                     │
                          Every 30 min (light monitor): read SoC only
                                                     │
                       End of day (audit @ 23:00): ChatGPT verdict + suggestions
```

---

## 📋 Prerequisites

| Component | Requirement |
|-----------|------------|
| Home Assistant | 2024.1+ with Supervisor |
| GivEnergy Inverter | Any model supported by GivTCP |
| GivTCP Add-on | Installed & running in Home Assistant |
| Octopus Energy | Agile tariff with API key |
| Solar Panels | With known kWp, tilt (declination), and azimuth |
| Network Share (Optional) | For persistent log files (NAS, Samba, etc.) |

---

## 🚀 Installation

### 1. Add the Local Repository

1. In Home Assistant, go to **Settings → Apps → ⋮ → Repositories**
2. Add the path to the `ha-addon/` directory on your Samba share:
   ```
   /addons/givenergy_tracker
   ```
3. The **GivEnergy Tariff Optimiser** app will appear under **Local apps**.

### 2. Configure `config.py`

Before installing the add-on, populate `ha-addon/config.py` with your credentials.
Copy from the template:

```bash
cp config.py.example ha-addon/config.py
```

Then fill in your values (see [Configuration Reference](#-configuration-reference) below).

> ⚠️ **`config.py` is listed in `.gitignore` and will never be committed to Git.** It contains your API keys and passwords. Keep it safe.

### 3. Install & Start

1. Click **Install** on the add-on card
2. Go to the **Configuration** tab and set your options
3. Click **Start**
4. Check the **Log** tab to confirm it's running

---

## ⚙️ Configuration Reference

### Home Assistant Options (via UI)

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `interval_minutes` | int | `30` | Daemon wake-up cadence. Full re-plan fires once per day; other ticks are cheap SoC-only reads. |
| `run_once` | bool | `false` | Exit after one planning pass (useful for testing) |
| `openai_api_key` | str | `""` | OpenAI API key. Enables plan scoring/veto and end-of-day audit. |
| `daily_plan_hour` | int | `17` | Local hour to fire the daily planner (typically after Octopus publishes tomorrow's rates) |
| `daily_audit_hour` | int | `23` | Local hour to fire the end-of-day audit |

### `config.py` Settings

```python
# ─── Inverter ───────────────────────────────────────────────────────────────
INVERTER_IP    = "192.168.1.xx"   # Static IP of your GivEnergy inverter
INVERTER_PORT  = 8899             # Modbus TCP port (usually 8899 or 502)

# ─── GivTCP ─────────────────────────────────────────────────────────────────
GIVTCP_URL     = "http://192.168.1.xx:6345"   # GivTCP REST API URL
                                               # Set to None to force Modbus only

# ─── Octopus — Import (Agile) ───────────────────────────────────────────────
OCTOPUS_API_KEY    = "sk_live_xxxxxxxxxxxx"
OCTOPUS_ACCOUNT_ID = "A-XXXXXXXX"
ELEC_IMPORT_MPAN   = "1419xxxxxxxxx"          # Import meter point (Agile)
ELEC_SERIAL        = "19Kxxxxxxxx"            # Shared serial for both directions
AGILE_PRODUCT_CODE = "AGILE-24-10-01"
AGILE_TARIFF_CODE  = "E-1R-AGILE-24-10-01-E"  # Change final letter for your region

# ─── Octopus — Export (Outgoing Variable) ───────────────────────────────────
ELEC_EXPORT_MPAN       = "1470xxxxxxxxx"      # Export meter point
EXPORT_PRODUCT_CODE    = "OUTGOING-VAR-24-10-26"
EXPORT_TARIFF_CODE     = "E-1R-OUTGOING-VAR-24-10-26-E"
EXPORT_RATE_P_FALLBACK = 12.0                 # Used only if live fetch fails
ARBITRAGE_MARGIN_P     = 0.5                  # Import must be below (export − margin)
                                              # for arbitrage to fire

# ─── Solar Array ─────────────────────────────────────────────────────────────
LATITUDE          = 52.7073   # Your home latitude
LONGITUDE         = -2.7553   # Your home longitude
SOLAR_DECLINATION = 35        # Panel tilt in degrees (0 = flat, 90 = vertical)
SOLAR_AZIMUTH     = 0         # 0 = South, -90 = East, +90 = West
SOLAR_KWP         = 10.0      # Total array capacity in kWp

# ─── Battery ─────────────────────────────────────────────────────────────────
BATTERY_CAPACITY_KWH     = 9.5    # Usable battery capacity in kWh
MAX_BATTERY_CHARGE_RATE  = 3000   # Max charge rate in Watts

# ─── Home Load ───────────────────────────────────────────────────────────────
BASE_LOAD_W             = 1000   # Baseline home consumption in Watts. Set from
                                 # your true overnight draw — under-estimating
                                 # causes the tracker to skip needed charges.
IBOOST_MAX_DIVERT_RATE  = 3000   # Solar iBoost immersion heater diversion cap (W)

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_FILE_PATH = "/share/nas_logs/givenergy_tracker.log"   # NAS mount path
LOG_LEVEL     = "INFO"   # DEBUG | INFO | WARNING | ERROR

# ─── Optional: OpenAI ────────────────────────────────────────────────────────
OPENAI_API_KEY = ""   # Leave blank to disable ChatGPT scoring/veto/audit
```

---

## 🧠 How the Optimiser Works

The daemon runs on a `interval_minutes` cycle (default 30 min) but does full planning **only once per day**. Each tick picks one of three modes:

### Daily planner (once/day at `daily_plan_hour`, default 17:00)

1. **Fetch Agile import rates** — public Octopus product endpoint, all upcoming half-hour slots
2. **Fetch Outgoing Variable export rate** — cached 6h; used for arbitrage decisions
3. **Fetch solar forecast** — Forecast.Solar hourly kWh estimate
4. **Read battery SoC** — GivTCP REST (Modbus fallback)
5. **Simulate 24h** without any grid charge, using `BASE_LOAD_W` as continuous load
6. **Decide action:**
   - **Deficit charge** if predicted import > 0.2 kWh — cheapest contiguous window
   - **Arbitrage charge** if any slot is below `(export_rate − ARBITRAGE_MARGIN_P)` — profitable even when solar could cover, because imported cheap energy displaces solar that then exports at 12p
   - **Negative-rate override** — always fill battery when grid pays you
   - **No charge** otherwise, clear slots
7. **LLM veto** (if OpenAI configured) — ChatGPT reviews the plan and returns `{approve, score 1-10, reason}` in JSON. Vetoes clear the slots as a fallback. Fails-open on error/timeout.
8. **Write to inverter** — via GivTCP `setChargeEnable` / `setChargeTarget` / `setChargeSlot1` / `setChargeSlot2` (window splits across midnight if needed)
9. **Persist snapshot** — writes the day's plan to `/share/nas_logs/givenergy_state.json` for the audit

### Light monitor (every other tick, ~46×/day)

Just reads SoC from GivTCP and logs it. No LLM, no re-planning, no inverter writes.

### End-of-day audit (once/day at `daily_audit_hour`, default 23:00)

Loads the plan snapshot + daily stats, then sends the day's context to ChatGPT for an English-language verdict and algorithm-tuning suggestions. Logged to file.

### Cost / arbitrage model

- **Import cost** = `charge_kwh × avg_slot_price`
- **Export income** = live-fetched Outgoing Variable rate (currently 12p flat)
- **Round-trip battery efficiency** ≈ 90%, giving a break-even at `~10.8p`
- **Arbitrage margin** (`ARBITRAGE_MARGIN_P`, default 0.5p) keeps the tracker from chasing marginal opportunities

---

## 🔌 GivTCP Connectivity

The optimizer supports two methods to communicate with the inverter:

| Method | How | When Used |
|--------|-----|-----------|
| **GivTCP REST API** | HTTP to `GIVTCP_URL` | Preferred (when GivTCP add-on is running) |
| **Direct Modbus TCP** | TCP to `INVERTER_IP:INVERTER_PORT` | Fallback (requires `givenergy-modbus` package) |

If `GIVTCP_URL` is set and reachable, REST is always tried first. Direct Modbus is only used as a fallback.

---

## 📦 Versioning

This project follows [Semantic Versioning](https://semver.org/):

| Version Range | Meaning |
|--------------|---------|
| `0.0.x` | Test builds — bug fixes and config tweaks |
| `0.x.0` | Beta builds — new features |
| `1.0.0+` | Stable production releases |

When the version number in `ha-addon/config.yaml` is bumped, Home Assistant will show an **Update** badge on the add-on card — no reinstall required.

---

## 🔐 Security Notes

**`config.py` contains live credentials.** Handle it accordingly:

- It is listed in `.gitignore` and should never be committed. Rely on this only as a first line of defence — a `git add -f` still bypasses it.
- Its contents are stored **in plaintext** on the Home Assistant host and inside the running container. Anyone with SSH/terminal access to the host can read it.
- HA add-on options set via the UI (like `openai_api_key`) live in `/data/options.json` on the host — also plaintext, contrary to what earlier docs implied.
- **Never paste `config.py` contents into a chat log, forum post, screenshot, or LLM conversation.** Once transmitted, treat the secrets as compromised and rotate them (Octopus dashboard, OpenAI dashboard, NAS admin).
- **Rotation is cheaper than an audit.** If in doubt, rotate.

### What's in it that matters

| Secret | Blast radius if leaked |
|---|---|
| `OCTOPUS_API_KEY` | Full account access — bills, tariffs, PII |
| `OPENAI_API_KEY` | Billable API access at your expense |
| `NAS_USER` / `NAS_PASSWORD` | SMB read/write on your NAS |
| `ELEC_MPAN` / `ELEC_SERIAL` / `GAS_MPRN` | UK utility identifiers — usable for change-of-supplier fraud |
| `LATITUDE` / `LONGITUDE` | Precise home coordinates |

---

## 🛠️ Development

### First-time setup

```bash
git clone https://github.com/revhawk/GivEnergy_Tracker.git
cd GivEnergy_Tracker/ha-addon
cp config.py.example config.py
# Edit config.py with your credentials, MPANs, and site geometry
```

### Editing the add-on

1. Edit files under `ha-addon/`.
2. **Rebuild** the add-on in Home Assistant (Settings → Add-ons → GivEnergy Tariff Optimiser → ⋮ → **Rebuild**). Restart alone won't pick up changes because `config.py` and `optimiser.py` are baked into the container image at build time.
3. Watch the **Log** tab. The startup banner shows the effective config values so you can verify your edits took.

### Running one planning pass on demand

Set `run_once: true` in the add-on's Configuration tab, then Start it. It'll do a full plan, write to the inverter, and exit.

### Tests

```bash
cd tests/
pip install -r requirements.txt
pytest
```

Test targets and coverage are documented in [`tests/README.md`](tests/README.md).

---

## 📜 Licence

This project is licensed under the Apache 2.0 License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgements

- [GivTCP](https://github.com/britkat1980/giv_tcp) — excellent GivEnergy integration for Home Assistant
- [Octopus Energy Developer API](https://developer.octopus.energy/docs/api/) — free, open tariff data
- [Forecast.Solar](https://forecast.solar) — free solar generation forecasts
- [givenergy-modbus](https://github.com/dewet22/givenergy-modbus) — Python Modbus library for GivEnergy inverters
