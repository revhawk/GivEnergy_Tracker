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

## 💡 How The Add-on Saves You Money

### 1. Overnight Deficit Charging
If tomorrow's solar forecast is low and won't cover your estimated home energy demand, the optimiser calculates exact shortfall kWh and schedules charging during the absolute cheapest overnight Agile slots.

### 2. Solar Arbitrage
When an Agile rate slot is cheaper than your export price (for example, import at 8p/kWh vs export at 12p/kWh), the tracker charges your battery from grid power. This frees up 100% of your daytime solar power to be exported to the grid for maximum profit.

### 3. Octopus Octoplus Sessions (Power Down & Power Up)
- **Power Down Sessions (Saving Sessions)**: Enforces 0.0 kWh grid import during peak demand events and pre-charges your battery beforehand at cheaper rates.
- **Power Up Sessions (Free Electricity)**: Automatically schedules maximum battery charging during Octopus Free Electricity sessions.

### 4. Smart Hot Water Control
- **Excess Solar Diversion**: Excess solar power beyond home load and battery capacity is directed into your hot water cylinder via iBoost.
- **Gas Savings with Morning Shower Safety**: Pauses your Hive gas boiler when overnight electric pre-charging or solar heating is active. At 05:00 AM, it performs a temperature check ($\ge 45^\circ\text{C}$ threshold) to guarantee a hot morning shower.

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

### How do I check add-on logs?
Go to **Settings → Add-ons → GivEnergy Tariff Optimiser → Log** tab.

If you have configured Network Storage at `/share/nas_logs/`, rotating logs and state files are saved automatically for long-term historical records:
- `/share/nas_logs/givenergy_tracker.log`
- `/share/nas_logs/givenergy_state.json`
- `/share/nas_logs/givenergy_daily_stats.json`

---

## 💬 Support & Feedback

For updates, questions, or issues, visit the [GivEnergy Tracker GitHub Repository](https://github.com/revhawk/GivEnergy_Tracker).
