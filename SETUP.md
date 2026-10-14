# 🛠️ GivEnergy Tariff Optimiser — Setup & Installation Guide

Welcome! This guide walks you through setting up and configuring the **GivEnergy Tariff Optimiser** add-on for Home Assistant.

---

## 📋 System Prerequisites

Before installing the add-on, make sure you have:

1. **Home Assistant OS or Supervised**: Running on Raspberry Pi, Home Assistant Green/Yellow, ODROID, or x86/x64 server.
2. **GivEnergy Inverter & Battery**: Gen 1, Gen 2, Gen 3, All-in-One, or AC Coupled inverter.
3. **GivTCP Add-on (v2 or v3)**: Installed and running in Home Assistant, providing local REST endpoints (`http://<GIVTCP_HOST>:6345`).
4. **Octopus Energy Account**: Subscribed to dynamic tariffs (e.g., **Octopus Agile** for import and **Octopus Outgoing** for export).
5. **(Recommended) Samba Share Add-on**: For logging state and daily statistics to `/share/nas_logs` accessible from your PC or NAS.
6. **(Optional) OpenAI API Key**: If you want ChatGPT AI Co-Pilot plan scoring and weather commentary (`gpt-4o-mini`).

---

## 🚀 Step-by-Step Installation

### Step 1: Add the Repository to Home Assistant

1. In Home Assistant, navigate to **Settings** → **Add-ons** → **Add-on Store** (bottom-right button).
2. Click the **⋮ (three dots)** menu in the top-right corner and select **Repositories**.
3. In the text box, paste the repository URL:
   ```text
   https://github.com/revhawk/GivEnergy_Tracker
   ```
4. Click **Add**, then click **Close**.

---

### Step 2: Install the Add-on

1. Scroll down the Add-on Store (or refresh the page) to find the **GivEnergy Tariff Optimiser** card under "revhawk Add-ons".
2. Click on **GivEnergy Tariff Optimiser**.
3. Click the blue **Install** button. Installation takes about 1–2 minutes while dependencies are built.

---

### Step 3: Provide Your `config.py` Credentials & Inverter Settings

Because `config.py` contains private local network IP addresses and Octopus credentials, it is not bundled inside the public container image. You must provide it in one of two locations before starting:

1. **Option A (Recommended for Samba users)**:
   Place your completed `config.py` at `/share/givenergy_config.py`.
2. **Option B (Addon persistent data directory)**:
   Place your completed `config.py` at `/data/config.py`.

Use the provided [`ha-addon/config.py.example`](file:///home/reg/Coding/GivEnergy_Tracker/ha-addon/config.py.example) as your template. Fill in:
- `GIVTCP_URL`: URL to your GivTCP instance (e.g. `http://homeassistant.local:6345`).
- `OCTOPUS_API_KEY` & `OCTOPUS_ACCOUNT_ID`.
- `ELEC_IMPORT_MPAN` & `AGILE_TARIFF_CODE`.
- (Optional) `TUMBLE_DRYER_LOAD_W = 1000` & `LAUNDRY_ACTIVE_LOAD_W = 800` if your household runs multiple laundry cycles daily.

---

### Step 4: Configure Add-on Options

Click on the **Configuration** tab at the top of the add-on page to view the settings.

| Setting | Recommended Value | Description |
| :--- | :---: | :--- |
| **`interval_minutes`** | `30` | How often the background loop performs a light status check (matches Octopus 30-minute tariff slots). |
| **`run_once`** | `false` | Set to `true` only for a one-off debugging run. Keep `false` for 24/7 autonomous scheduling. |
| **`debug_logging`** | `false` | Keep `false` for clean logs. Set to `true` if you need detailed HTTP connection traces for troubleshooting. |
| **`openai_api_key`** | `""` *(Optional)* | Paste your OpenAI API key (`sk-proj-...`) to enable AI strategy selection and daily savings reports. Optional! |
| **`openai_model`** | `gpt-4o-mini` | Cost-effective and highly accurate model for energy strategy analysis. |
| **`daily_plan_hour`** | `17` | The hour (24h format) when tomorrow's charge plan is generated. 17:00 (5 PM) is ideal because Octopus publishes Agile rates between 16:00 and 16:30. |
| **`daily_audit_hour`** | `23` | The hour (24h format) when the financial savings audit is calculated and archived to your NAS. |
| **`log_file_path`** | `/share/nas_logs/givenergy_tracker.log` | Path for persistent rotating log files and history records (`/share/nas_logs/history/`). |

Click **Save** after entering your settings.

---

### Step 5: Start the Add-on & Verify Logs

1. Return to the **Info** tab.
2. Toggle **Start on boot** to `ON`.
3. Toggle **Watchdog** to `ON` (automatically restarts the add-on if Home Assistant reboots).
4. Click **Start**.
5. Click on the **Log** tab and refresh after 10–15 seconds. You should see:
   ```text
   ============================================================
   ⚡ GivEnergy Tariff Optimiser Add-on Starting (v1.0.23.3)
   ============================================================
   [GivTCP] Inverter Telemetry: Battery 54% (9.5 kWh capacity)
   [Tariff] Fetched 48 Octopus Agile rate slots
   [Solar] Parallel Ensemble: Forecast.Solar + Open-Meteo
   ✓ Optimisation complete — plan recorded to /share/nas_logs/givenergy_state.json
   ```

---

## 📂 NAS Logging & Samba Network Share Setup

The add-on saves persistent state, daily stats, and 90 days of midnight-rotated logs directly to `/share/nas_logs/`:

- `givenergy_state.json`: Current battery SoC, active plan, candidate options, and LLM reasoning.
- `givenergy_daily_stats.json`: Rolling daily stats (total import kWh, export kWh, estimated savings).
- `history/daily_stats_YYYY-MM-DD.json`: Nightly archived snapshot of financial performance.
- `givenergy_tracker.log`: Rotating application logs.

### Accessing Logs from Windows / Mac / Linux:
1. Install the official **Samba share** add-on from the Home Assistant Add-on Store.
2. In your computer's file explorer, connect to your Home Assistant IP:
   - Windows: `\\homeassistant.local\share\nas_logs` (or `\\<YOUR_HA_IP>\share\nas_logs`)
   - Mac / Linux: `smb://homeassistant.local/share/nas_logs` (or `smb://<YOUR_HA_IP>/share/nas_logs`)

---

## 🔍 First-Run Verification Checklist

- [ ] **GivTCP Connection**: In the add-on log, verify that battery SoC and inverter capacity are correctly detected.
- [ ] **Octopus Rates**: Check that incoming Agile rates are displayed with reasonable pence values.
- [ ] **Solar Forecast**: Check that solar forecast values are returned from Forecast.Solar and Open-Meteo.
- [ ] **State File Created**: Check that `/share/nas_logs/givenergy_state.json` is created and contains today's plan.
- [ ] **Negative Rates & Plunge Pricing**: Verify that any slot with negative rates (< 0.0p) automatically targets 100% SoC.

---

## 📚 Further Reading

- [User Guide & Daily Operation](file:///home/reg/Coding/GivEnergy_Tracker/ha-addon/DOCS.md) — How the optimiser selects candidate strategies and manages daily charging.
- [Weather-Adaptive Solar & Arbitrage](file:///home/reg/Coding/GivEnergy_Tracker/ha-addon/ARBITRAGE_AND_WEATHER.md) — Technical details on solar thresholds and export arbitrage.
- [Architecture & Living Specification](file:///home/reg/Coding/GivEnergy_Tracker/ha-addon/SPECIFICATION.md) — Living ADRs, contracts, and developer specifications.

