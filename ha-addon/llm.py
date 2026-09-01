"""OpenAI ChatGPT integration module (Plan veto validator & daily summary reports)."""
import os
import json
import logging

import config

_openai_client = None


def test_openai_connection():
    """Test OpenAI API key at startup and initialise the client if valid."""
    global _openai_client
    openai_key = os.environ.get('OPENAI_API_KEY', '').strip() or getattr(config, 'OPENAI_API_KEY', '').strip()

    if not openai_key:
        logging.info("OpenAI API key not configured — ChatGPT audit DISABLED.")
        return False

    try:
        import openai
        client = openai.OpenAI(api_key=openai_key)
        client.models.list()
        _openai_client = client
        model_name = get_openai_model()
        logging.info(f"✓ OpenAI API connected successfully (model: {model_name}) — ChatGPT audit ENABLED.")
        return True
    except Exception as e:
        logging.warning(f"✗ OpenAI API connection test FAILED: {e}")
        logging.warning("  Check your API key in the Configuration tab. ChatGPT audit DISABLED.")
        return False


def get_openai_model():
    """Return configured OpenAI model name (from environment, config.py, or fallback default)."""
    model = os.environ.get('OPENAI_MODEL', '').strip() or getattr(config, 'OPENAI_MODEL', 'gpt-4o-mini').strip()
    return model if model else 'gpt-4o-mini'


def chatgpt_veto_plan(current_soc, battery_capacity_kwh, solar_total_kwh,
                     export_rate, upcoming_slots, charge_start, charge_end,
                     required_kwh, avg_price):
    """Structured decision validator. Returns (approve: bool, score: int|None, reason: str). Fails open."""
    if _openai_client is None:
        return True, None, "LLM disabled"

    rates_lines = "\n".join(
        f"  {s['start'].astimezone().strftime('%H:%M')}  {s['price']:.2f}p"
        for s in upcoming_slots[:48]
    )

    if isinstance(charge_start, list):
        slots_desc = [f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in charge_start]
        action = f"CHARGE in slots: {', '.join(slots_desc)} — {required_kwh:.1f} kWh at avg {avg_price:.2f}p/kWh"
    elif charge_start and charge_end:
        action = (
            f"CHARGE from {charge_start.strftime('%H:%M')} to "
            f"{charge_end.strftime('%H:%M')} — {required_kwh:.1f} kWh at avg {avg_price:.2f}p/kWh"
        )
    else:
        action = "NO CHARGE — rely on solar and existing battery"

    max_upcoming_price = max((s['price'] for s in upcoming_slots), default=0.0)
    arbitrage_break_even = export_rate * 0.90
    effective_charge_cost = (avg_price / 0.90) if avg_price > 0 else avg_price

    system_msg = (
        f"You validate battery charging plans for a UK home with solar + battery.\n\n"
        f"HARD FACTS — MUST BE FOLLOWED EXACTLY:\n"
        f"1. Import tariff: Octopus Agile (half-hourly rates). Prices can go NEGATIVE (grid pays us to import energy).\n"
        f"2. Export tariff: Octopus Outgoing at {export_rate:.2f}p/kWh (FLAT all day).\n"
        f"3. Battery round-trip efficiency ≈ 90% (10% energy loss when charging and discharging).\n"
        f"4. TWO VALID CHARGING MODES:\n"
        f"   a) ARBITRAGE: Charge when import price < {arbitrage_break_even:.2f}p/kWh (export_rate × 0.90).\n"
        f"   b) DEFICIT / POWER-DOWN PRE-CHARGE: Charge at cheaper rates (e.g. {avg_price:.2f}p/kWh) to cover peak home load or Octoplus Power Down slots later when peak import rates reach {max_upcoming_price:.2f}p/kWh. "
        f"Effective charge cost is {avg_price:.2f}p / 0.90 = {effective_charge_cost:.2f}p/kWh. "
        f"If {effective_charge_cost:.2f}p < {max_upcoming_price:.2f}p, PRE-CHARGING SAVES MONEY and MUST BE APPROVED.\n"
        f"5. ALWAYS approve charging when import rate is negative or when pre-charging avoids higher peak import rates later.\n\n"
        f"Reply ONLY with valid JSON:\n"
        f"  'approve' (bool) - would you apply this action?\n"
        f"  'score'   (int 1-10) - 1=terrible, 10=optimal\n"
        f"  'reason'  (string ≤ 120 chars) - reference concrete numbers from data."
    )
    user_msg = (
        f"Battery: {current_soc}% of {battery_capacity_kwh} kWh\n"
        f"Solar forecast today: {solar_total_kwh:.1f} kWh\n"
        f"Flat export rate: {export_rate:.2f}p/kWh  |  Arbitrage break-even: {arbitrage_break_even:.2f}p/kWh\n"
        f"Peak upcoming import rate: {max_upcoming_price:.2f}p/kWh\n"
        f"Proposed pre-charge avg price: {avg_price:.2f}p/kWh  (Effective post-90% efficiency: {effective_charge_cost:.2f}p/kWh)\n"
        f"Upcoming Agile rates:\n{rates_lines}\n\n"
        f"Proposed action: {action}\n\n"
        f"Rate 1-10 and approve=true if this action is economically sound (either arbitrage or peak deficit/Power Down pre-charge)."
    )

    try:
        response = _openai_client.chat.completions.create(
            model=get_openai_model(),
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=150,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip()
        parsed = json.loads(content)
        approve = bool(parsed.get("approve", True))
        score = parsed.get("score")
        if isinstance(score, (int, float)):
            score = max(1, min(10, int(score)))
        else:
            score = None
        reason = str(parsed.get("reason", ""))[:200]
        return approve, score, reason
    except Exception as e:
        logging.warning(f"ChatGPT veto call failed ({e}); defaulting to approve=True")
        return True, None, f"LLM error: {e}"


def generate_daily_summary(stats):
    """Send previous day's stats to ChatGPT. Log the summary and improvement suggestions."""
    if _openai_client is None:
        logging.info("OpenAI not configured — skipping daily summary.")
        return

    date = stats.get('date', 'unknown')
    windows = stats.get('charge_windows', [])

    if windows:
        windows_desc = "\n".join([
            f"  • {w.get('start','')} → {w.get('end','')} "
            f"at avg {w.get('avg_price', 0):.2f}p/kWh  ({w.get('kwh', 0):.1f} kWh)"
            for w in windows
        ])
    else:
        windows_desc = "  • No grid charging was required all day"

    min_r = stats.get('min_rate_seen', 0)
    max_r = stats.get('max_rate_seen', 0)
    min_r_str = f"{min_r:.2f}" if min_r != float('inf') else "n/a"
    max_r_str = f"{max_r:.2f}" if max_r != float('-inf') else "n/a"

    prompt = (
        f"You are a UK home energy cost optimisation AI reviewing a full day of battery management.\n\n"
        f"DATE: {date}\n\n"
        f"=== BATTERY ===\n"
        f"Starting SoC: {stats.get('start_soc', '?')}%\n"
        f"Ending SoC:   {stats.get('end_soc', '?')}%\n"
        f"Optimiser runs today: {stats.get('runs', 0)} (every 30 min)\n\n"
        f"=== ENERGY ===\n"
        f"Peak solar forecast seen: {stats.get('solar_kwh_forecast', 0):.1f} kWh\n"
        f"Peak iBoost diversion forecast: {stats.get('iboost_kwh_forecast', 0):.1f} kWh\n"
        f"Total kWh charged from grid: {stats.get('total_charged_kwh', 0):.1f} kWh\n"
        f"Of which at negative rates: {stats.get('negative_rate_kwh', 0):.1f} kWh\n\n"
        f"=== OCTOPUS AGILE RATES ===\n"
        f"Cheapest rate seen: {min_r_str}p/kWh\n"
        f"Most expensive rate seen: {max_r_str}p/kWh\n\n"
        f"=== CHARGE WINDOWS SCHEDULED ===\n"
        f"{windows_desc}\n\n"
        f"Please respond with EXACTLY these three sections:\n\n"
        f"**DAILY SUMMARY**\n"
        f"3 sentences: How well did the system perform? Was money saved vs default behaviour? Any concerns?\n\n"
        f"**ESTIMATED SAVING**\n"
        f"Calculate the estimated £ saving today vs charging at the peak rate seen ({max_r_str}p/kWh). Show your working briefly.\n\n"
        f"**OPTIMISATION SUGGESTIONS**\n"
        f"Give 2-3 specific, technical suggestions to improve the Python algorithm based on today's data. "
        f"Be concrete — reference actual logic changes, thresholds, or new data sources. No generic advice."
    )

    try:
        response = _openai_client.chat.completions.create(
            model=get_openai_model(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.4,
        )
        summary = response.choices[0].message.content.strip()

        border = "=" * 55
        logging.info("")
        logging.info(border)
        logging.info(f"  📊 DAILY SUMMARY — {date}")
        logging.info(border)
        for line in summary.split('\n'):
            logging.info(line)
        logging.info(border)
        logging.info("")

    except Exception as e:
        logging.warning(f"Daily summary ChatGPT call failed: {e}")
