# GivEnergy Tariff Optimiser — Add-on User Guide

Welcome to the **GivEnergy Tariff Optimiser**! This Home Assistant add-on automates your GivEnergy battery storage, solar PV panels, and hot water heating around your dynamic **Octopus Energy** electricity tariffs (such as Octopus Agile and Outgoing Export).

---

## 🌟 What This Add-on Does For You

- ⚡ **Automated Overnight Pre-Charging**: Automatically charges your battery during the cheapest half-hour grid slots overnight so your home runs on cheap power during expensive day & evening peak hours.
- 💰 **Profitable Solar Arbitrage**: When electricity rates dip lower than your solar export price, the add-on charges your battery from cheap grid power so you can sell more of your daytime solar generation back to the grid for profit.
- 🤖 **Smart AI Plan Review**: Uses OpenAI's ChatGPT as an independent sanity check to score your daily plan out of 10 and veto any unusual charge decisions.
- 🧺 **Washing Machine Usage Dashboard**: Monitors smart plug power draw (`sensor.washing_machine_power`), tracks daily wash cycles & kWh, calculates exact wash costs in pence, and recommends the top 3 cheapest Agile windows to run laundry.
- ♨️ **Smart Hot Water & Heating Control**: Directs excess solar energy into your hot water cylinder via iBoost immersion diversion and automatically pauses your Hive gas boiler when electric/solar heating is active—saving gas while guaranteeing a warm morning shower.
- 📊 **Daily Financial Savings Audit**: Generates a daily summary every evening detailing your actual energy savings compared to standard electricity tariffs.

---

## 🚀 Quick Setup Guide

### 1. Installation

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click **⋮ (three dots) → Repositories** in the top right.
3. Add the repository URL:
   ```text
   https://github.com/revhawk/GivEnergy_Tracker
   ```
4. Find **GivEnergy Tariff Optimiser** in the store list and click **Install**.

### 2. Configuration Settings

Open the **Configuration** tab of the add-on to customize your settings:

| Setting | Recommended Default | What It Means for You |
| :--- | :---: | :--- |
| **`interval_minutes`** | `30` | **Leave as `30`**. Wake-up check cadence matching Octopus 30-minute tariff slots. |
| **`run_once`** | `false` | **Keep `false`** for 24/7 background mode. Set to `true` only if you want to run 1 test pass and exit. |
| **`debug_logging`** | `false` | **Keep `false`** for clean, easy-to-read logs. Set to `true` if you need detailed HTTP connection traces for troubleshooting. |
| **`openai_api_key`** | `""` *(Optional)* | Enter your OpenAI API key (`sk-proj-...`) to enable ChatGPT plan scoring & daily audit reports. Optional—the planner works 100% without it. |
| **`openai_model`** | `gpt-4o-mini` | Select `gpt-4o-mini` (cheap, fast, and highly accurate). |
| **`daily_plan_hour`** | `17` *(5:00 PM)* | The hour when tomorrow's charge plan is calculated. Octopus publishes rates at 16:30, so 17:00 programs your inverter 7 hours before night charging. |
| **`daily_audit_hour`** | `23` *(11:00 PM)* | The hour when your daily financial savings report is generated. |
| **`log_file_path`** | `/share/nas_logs/givenergy_tracker.log` | **NAS Log Location**. The network / SMB file path where timestamped logs and daily performance history (`/share/nas_logs/history/`) are saved. |
| **`hive_water_heater_entity`** | `water_heater.hive_hot_water` | Your Hive hot water entity in Home Assistant. Automatically pauses gas boiler schedule when electric/solar heating is active. |

---

## ⏰ How Your System Works Throughout the Day

Once installed, the add-on runs quietly in the background without spamming your network or inverter:

```mermaid
gantt
    title Daily Optimiser Schedule
    dateFormat  HH:mm
    axisFormat %H:%M

    section Octopus Tariff
    Tomorrow's Rates Published (16:00-16:30) :milestone, m1, 16:30, 0min

    section Add-on Tasks
    Daily Planning Run (Calculates plan & programs inverter) :active, p1, 17:00, 30min
    Light Status Checks (Periodic battery SoC checks) :m2, 17:30, 5h
    Daily Financial Savings Audit (ChatGPT Audit Report) :crit, a1, 23:00, 30min
    Inverter Executes Scheduled Overnight Charging :done, c1, 02:00, 3h
```

### The 3 Routine Modes:

1. **Daily Planner (Fires at 17:00)**: Fetches tomorrow's 48 Octopus Agile rates and solar forecasts, simulates 24 hours of home energy demand, programs optimal charge slots into your inverter via GivTCP, and pauses Hive gas boiler when appropriate.
2. **Light Status Checks (Every 30 mins)**: Reads current battery percentage quietly without modifying inverter settings or making unnecessary internet calls.
3. **Daily Savings Audit (Fires at 23:00)**: Calculates your estimated savings for the day and logs a clear summary report.

---

## 🌤️ Weather-Adaptive Solar Strategy & Export Arbitrage

For full architectural details, see the dedicated [Weather-Adaptive Solar & Arbitrage Guide](file:///home/reg/Coding/GivEnergy_Tracker/ha-addon/ARBITRAGE_AND_WEATHER.md) subpage.

The add-on uses a **4-Tier Day Classifier** to dynamically adapt to daily UK weather without static calendar months:

```mermaid
flowchart TD
    A[Daily Planning Run at 17:00] --> B[Calculate Net Solar Surplus\nForecast Solar − House Load]
    
    B --> C{Agile Rates Negative < 0p?}
    C -->|YES| D[⚡ NEGATIVE_RATE_OPPORTUNITY\nImport Grid Power (Grid pays you!)]
    
    C -->|NO| E{Net Solar Surplus vs Battery Space?}
    
    E -->|Surplus ≥ Battery Deficit| F[☀️ HIGH_SOLAR_SELF_CONSUMPTION\n0 W Grid Draw\nFree solar PV fills battery]
    
    E -->|Surplus 40% to 99% of Deficit| G[⛅ MEDIUM_SOLAR_PARTIAL_PRECHARGE\nPartial Pre-Charge for Peak Hours\nLeaves 30%-50% headroom for solar surges]
    
    E -->|Surplus < 40% of Deficit| H[🌧️ LOW_SOLAR_GRID_PRECHARGE\nFull Grid Pre-Charge\nGuarantees 100% SoC before 16:00 peak]
```

### How The Add-on Saves You Money:

1. **Overnight & Midday Deficit Charging**: If tomorrow's solar forecast is low (`LOW_SOLAR_GRID_PRECHARGE`), the optimiser pre-charges the battery during cheap Agile slots to protect against 45p–50p peak evening rates.
2. **Medium Solar Headroom (`MEDIUM_SOLAR_PARTIAL_PRECHARGE`)**: On partial solar days, partial pre-charging covers peak demand while leaving **30%–50% top battery headroom** to capture live daytime solar surges (like a 7.1 kW solar surge when the oven is running!).
3. **High Solar Self-Consumption (`HIGH_SOLAR_SELF_CONSUMPTION`)**: On sunny days, grid charging is **suppressed (0 W grid draw)** so your battery fills 100% for free.
4. **Octopus Octoplus Sessions (Power Down & Power Up)**:
   - **Power Down Sessions (Saving Sessions)**: Enforces 0.0 kWh grid import during peak demand events and pre-charges your battery beforehand at cheaper rates.
   - **Power Up Sessions (Free Electricity)**: Automatically schedules maximum battery charging during Octopus Free Electricity sessions.
5. **Smart Hot Water Control**:
   - **Excess Solar Diversion**: Excess solar power beyond home load and battery capacity is directed into your hot water cylinder via iBoost.
   - **Gas Savings with Morning Shower Safety**: Pauses your Hive gas boiler when electric pre-charging or solar heating is active, performing a 05:00 AM temperature check ($\ge 45^\circ\text{C}$) for shower safety.

---

## 🚀 Future Hardware Enhancements

The current add-on version includes full software simulation and fail-safe logic. Upcoming hardware integrations include:

- **⚡ Direct iBoost Immersion Relay Override**: Hardware relay contact integration to force 3 kW immersion hot water heating during negative/plunge electricity rate slots ($< 0.0\text{p/kWh}$).
- **🌡️ Hot Water Cylinder Temperature Probes**: Integration with 1-Wire DS18B20 or Zigbee multi-point cylinder sensors to measure exact stored thermal energy ($\text{kWh}$).

---

## ❓ Frequently Asked Questions & Troubleshooting

### Why is no grid charging scheduled today?
1. **Solar covers your needs**: Your solar forecast is strong enough to cover home load and fill your battery without needing grid power.
2. **Import rates are higher than export**: Every import slot is above your export arbitrage threshold, so grid charging would cost more than exporting solar.

### How do I check add-on logs and access NAS files?
Go to **Settings → Add-ons → GivEnergy Tariff Optimiser → Log** tab in Home Assistant.

For long-term storage, your add-on saves timestamped logs and performance history into `/share/nas_logs/`.

#### 🎛️ 1. Setting the NAS Log Location in Home Assistant UI
In the add-on **Configuration** tab, enter the path as a Unix-style Home Assistant container path:
- **HA UI Setting (`log_file_path`)**: `/share/nas_logs/givenergy_tracker.log` *(Default)*

#### 📂 2. Accessing NAS Files from Your PC / Mac (Windows vs Unix Style)

| Operating System / Client | Network / Folder Access Path |
| :--- | :--- |
| **🪟 Windows File Explorer** | `\\<YOUR_HA_IP>\share\nas_logs\` *(e.g. `\\192.168.1.96\share\nas_logs\`)* |
| **🐧 Linux / Unix System** | `/share/nas_logs/` or `smb://192.168.1.96/share/nas_logs/` |
| **🍎 macOS Finder** | `smb://192.168.1.96/share/nas_logs/` *(Go → Connect to Server)* |
| **🏠 Home Assistant File Editor** | Open **File Editor** add-on $\rightarrow$ navigate to `/share/nas_logs/` |

Files generated in your NAS directory:
- **`givenergy_tracker.log`**: Full timestamped log file (`YYYY-MM-DD HH:MM:SS - INFO - ...`).
- **`history/daily_stats_YYYY-MM-DD.json`**: Historical performance summaries and ChatGPT audit reports for each day.
- **`givenergy_state.json`**: Rolling 14-day load profile history and planned SoC schedule.

---

## 💬 Support & Feedback

For updates, questions, or issues, visit the [GivEnergy Tracker GitHub Repository](https://github.com/revhawk/GivEnergy_Tracker).
